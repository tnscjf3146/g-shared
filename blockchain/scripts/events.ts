/**
 * SSAFY 체인에 배포된 에스크로의 이벤트를 **체인에서 직접** 읽어 시간순으로 보여 준다.
 *
 * 우리 화면은 지금 어디에서도 체인을 직접 읽지 않는다. 작업 상세의 체인 화면도 백엔드
 * DB 의 상태를 그린다. 그런데 SSAFY 블록 탐색기는 403 으로 막혀 있다. RPC 는 열려 있으니
 * 탐색기가 하는 일 중 필요한 만큼만 여기서 한다. 탐색기도 결국 같은 RPC 를 읽는다.
 *
 *   npm run events                    배포 이후 전체
 *   npm run events -- --job 13        작업 하나만 (escrow jobId)
 *   npm run events:follow             새 블록을 계속 따라간다 (발표 시연 옆 화면용)
 *   npm run events:stats              체인 기준 집계 (발표 대본의 [N] 값)
 *
 *   npm run events:jobs               작업별 장부 — 단계마다 한 문장, 토큰이 들고 난 기록, 계약에 남은 돈
 *   npm run events:jobs -- --job 15   작업 하나 (--all 이면 전체, 기본은 최근 5건)
 *   npm run events:watch              새 기록을 문장과 토큰 이동으로 실시간 표시
 *   npm run events:watch -- --from 11365390   그 블록부터 다시 흘려 보기 (리허설·시험용)
 *
 * 키가 필요 없다. 읽기만 한다. RPC 는 SSAFY_RPC_URL 이 있으면 그것을 쓴다.
 */
import { readFileSync } from "node:fs";
import {
  createPublicClient,
  decodeEventLog,
  formatUnits,
  http,
  parseAbiItem,
  type Abi,
  type Hex,
  type Log,
} from "viem";

const deployment = JSON.parse(
  readFileSync(new URL("../deployments/ssafy-31221/GPUJobEscrow.json", import.meta.url), "utf8"),
) as { chainId: number; address: Hex; abi: Abi };

const RPC_URL = process.env.SSAFY_RPC_URL?.trim() || "https://rpc.ssafy-blockchain.com";
const ESCROW = deployment.address;
const ABI = deployment.abi;

/**
 * 에스크로 배포 트랜잭션(`0xcc71721a…`, Ignition 저널)이 들어간 블록.
 * 이보다 앞에는 이 계약의 이벤트가 있을 수 없다. 배포 기록에 블록 번호가 없어 영수증에서 읽었다.
 */
const DEPLOY_BLOCK = 11_268_954n;

/** eth_getLogs 한 번에 읽는 블록 폭. 20만 블록도 되지만 노드를 덜 괴롭히는 쪽을 고른다. */
const CHUNK = 50_000n;

const TOKEN_DECIMALS = 18;
const STATE_NAME = ["NONE", "FUNDED", "ASSIGNED", "COMPLETED", "CANCELLED"];

/** 이벤트 이름을 사람이 읽는 말로. 정산은 결과 제출과 같은 트랜잭션에서 일어난다. */
const LABEL: Record<string, string> = {
  JobFunded: "예치",
  JobAccepted: "수락",
  ResultSubmitted: "결과 제출",
  JobCompleted: "정산",
  JobCancelled: "환불",
  JobAborted: "반납",
  JobTimedOut: "기한 초과",
  OperatorUpdated: "대행자 지정",
};

// batch: 같은 틱에 나간 호출을 JSON-RPC 배치 한 번으로 묶는다. SSAFY RPC 는 배치를 받지만
// Multicall3 는 배포되어 있지 않아(표준 주소의 코드가 0x) multicall 로는 묶을 수 없다.
const client = createPublicClient({ transport: http(RPC_URL, { retryCount: 2, batch: true }) });

// ── 출력 ─────────────────────────────────────────────────────────────

const short = (v: string) => `${v.slice(0, 6)}…${v.slice(-4)}`;
const gsc = (wei: bigint) => `${formatUnits(wei, TOKEN_DECIMALS)} GSC`;
const num = (n: bigint) => n.toLocaleString("en-US");

/** 한글은 화면에서 두 칸을 차지한다. padEnd 는 글자 수로 세므로 줄이 어긋난다. */
function pad(text: string, width: number): string {
  let w = 0;
  for (const ch of text) w += /[ㄱ-힣]/.test(ch) ? 2 : 1;
  return text + " ".repeat(Math.max(0, width - w));
}

const timeFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const blockTime = new Map<bigint, string>();
/** 블록 시각(초). 장부에서 작업이 걸린 시간을 셀 때 쓴다. timeOf 가 함께 채운다. */
const blockSeconds = new Map<bigint, bigint>();
async function timeOf(block: bigint): Promise<string> {
  const cached = blockTime.get(block);
  if (cached) return cached;
  const { timestamp } = await client.getBlock({ blockNumber: block });
  blockSeconds.set(block, timestamp);
  // ko-KR 은 "09. 18. 11:18:15" 로 찍는다. 조각을 받아 "09-18 11:18:15" 로 맞춘다.
  const p = Object.fromEntries(
    timeFormat.formatToParts(new Date(Number(timestamp) * 1000)).map((x) => [x.type, x.value]),
  );
  const text = `${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
  blockTime.set(block, text);
  return text;
}

type Decoded = { name: string; args: Record<string, unknown>; log: Log };

function decode(log: Log): Decoded | null {
  try {
    const { eventName, args } = decodeEventLog({ abi: ABI, data: log.data, topics: log.topics });
    return { name: eventName ?? "?", args: (args ?? {}) as Record<string, unknown>, log };
  } catch {
    return null; // 이 계약의 ABI 에 없는 이벤트. 지금 계약에는 없지만 버리고 넘어간다.
  }
}

/** 이벤트마다 발표에서 짚을 값 하나둘만 보인다. 전부 보이면 화면에서 안 읽힌다. */
function detail({ name, args }: Decoded): string {
  const a = args as Record<string, bigint & string & boolean>;
  switch (name) {
    case "JobFunded":
      return `${gsc(a.price)} · 의뢰자 ${short(a.client)} → 공급자 ${short(a.provider)}`;
    case "JobAccepted":
      return `Worker ${short(a.operator)}`;
    case "ResultSubmitted":
      return `결과 Hash ${short(a.resultHash)}`;
    case "JobCompleted":
      return `${gsc(a.payout)} → 공급자 ${short(a.provider)}`;
    case "JobCancelled":
      return `${gsc(a.refund)} → 의뢰자 ${short(a.client)}`;
    case "JobAborted":
      return `Worker ${short(a.operator)} 가 반납`;
    case "JobTimedOut":
      return `${STATE_NAME[Number(a.fromState)] ?? a.fromState} 에서 기한이 지나 settleTimeout`;
    case "OperatorUpdated":
      return `${short(a.provider)} → ${short(a.operator)} ${a.allowed ? "허용" : "해제"}`;
    default:
      return "";
  }
}

async function print(e: Decoded): Promise<void> {
  const block = e.log.blockNumber ?? 0n;
  const job = "jobId" in e.args ? `job ${String(e.args.jobId).padStart(2)}` : "      ";
  const line = [
    `블록 ${num(block)}`,
    await timeOf(block),
    pad(LABEL[e.name] ?? e.name, 11),
    job,
    pad(detail(e), 50),
    `tx ${short(e.log.transactionHash ?? "0x")}`,
  ].join("  ");
  console.log(line);
}

// ── 읽기 ─────────────────────────────────────────────────────────────

/** from ~ to 의 에스크로 이벤트를 블록·로그 순서대로. 노드 한도를 넘지 않게 나눠 읽는다. */
async function readEvents(from: bigint, to: bigint): Promise<Decoded[]> {
  const out: Decoded[] = [];
  for (let start = from; start <= to; start += CHUNK) {
    const end = start + CHUNK - 1n < to ? start + CHUNK - 1n : to;
    const logs = await client.getLogs({ address: ESCROW, fromBlock: start, toBlock: end });
    for (const log of logs) {
      const d = decode(log);
      if (d) out.push(d);
    }
  }
  return out;
}

function forJob(events: Decoded[], job: bigint | null): Decoded[] {
  if (job === null) return events;
  return events.filter((e) => "jobId" in e.args && (e.args.jobId as bigint) === job);
}

function header(latest: bigint): void {
  console.log(
    `SSAFY 체인 ${deployment.chainId} · 에스크로 ${short(ESCROW)} · 최신 블록 ${num(latest)} · RPC ${new URL(RPC_URL).host}`,
  );
  console.log("우리 서버를 거치지 않고 블록체인 노드에서 직접 읽은 값입니다.\n");
}

// ── 명령 ─────────────────────────────────────────────────────────────

async function list(job: bigint | null): Promise<void> {
  const latest = await client.getBlockNumber();
  header(latest);
  const events = forJob(await readEvents(DEPLOY_BLOCK, latest), job);
  for (const e of events) await print(e);
  console.log(`\n${events.length}건`);
}

async function follow(job: bigint | null, tail: number, intervalSec: number): Promise<void> {
  let latest = await client.getBlockNumber();
  header(latest);
  const past = forJob(await readEvents(DEPLOY_BLOCK, latest), job);
  for (const e of past.slice(-tail)) await print(e);
  console.log(`\n── 여기부터 새 블록 (${intervalSec}초마다 확인, 끝내려면 Ctrl+C) ──\n`);

  const tty = process.stdout.isTTY;
  let next = latest + 1n;
  for (;;) {
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
    try {
      latest = await client.getBlockNumber();
      if (latest < next) continue;
      const fresh = forJob(await readEvents(next, latest), job);
      if (tty) process.stdout.write("\r\x1b[2K");
      for (const e of fresh) await print(e);
      next = latest + 1n;
      if (tty) process.stdout.write(`블록 ${num(latest)} 까지 확인 · 대기 중`);
    } catch (err) {
      // 노드가 잠깐 안 받는 일은 흔하다. 멈추지 않고 다음 주기에 같은 구간부터 다시 읽는다.
      if (tty) process.stdout.write("\r\x1b[2K");
      console.log(`RPC 응답 없음 — ${intervalSec}초 뒤 다시 시도 (${(err as Error).message.split("\n")[0]})`);
    }
  }
}

async function stats(): Promise<void> {
  const latest = await client.getBlockNumber();
  header(latest);

  const nextJobId = (await client.readContract({
    address: ESCROW, abi: ABI, functionName: "nextJobId",
  })) as bigint;

  // 상태는 이벤트로 짐작하지 않고 jobs() 로 계약에 직접 묻는다. 한꺼번에 보내 배치 한 번으로 간다.
  const ids = Array.from({ length: Number(nextJobId - 1n) }, (_, i) => BigInt(i + 1));
  const jobs = (await Promise.all(
    ids.map((id) => client.readContract({ address: ESCROW, abi: ABI, functionName: "jobs", args: [id] })),
  )) as (readonly unknown[])[];
  const byState = new Map<string, number>();
  for (const job of jobs) {
    const state = STATE_NAME[Number(job[1])] ?? String(job[1]);
    byState.set(state, (byState.get(state) ?? 0) + 1);
  }

  const events = await readEvents(DEPLOY_BLOCK, latest);
  const count = (name: string) => events.filter((e) => e.name === name).length;
  const total = Number(nextJobId - 1n);
  const open = (byState.get("FUNDED") ?? 0) + (byState.get("ASSIGNED") ?? 0);

  console.log(`체인에 기록된 작업     ${total}건`);
  console.log(`  완료 (COMPLETED)     ${byState.get("COMPLETED") ?? 0}`);
  console.log(`  환불 (CANCELLED)     ${byState.get("CANCELLED") ?? 0}    그중 settleTimeout ${count("JobTimedOut")} · 반납 ${count("JobAborted")}`);
  console.log(`  진행 중              ${open}`);
  console.log(`\n이벤트 ${events.length}건`);
  for (const name of Object.keys(LABEL)) {
    const n = count(name);
    if (n > 0) console.log(`  ${pad(LABEL[name], 12)} ${String(n).padStart(3)}   ${name}`);
  }
}

// ── 장부: 작업별로 묶고, 돈이 들고 난 기록을 붙인다 ────────────────────
//
// 이벤트에 적힌 금액을 옮겨 적지 않는다. 결제 토큰 계약이 남긴 Transfer·Approval 을 따로 읽어
// 토큰이 실제로 계약에 들어오고 나간 기록을 보인다. 그래야 들어온 돈 − 나간 돈을 계약의
// balanceOf 와 맞춰 볼 수 있다. 결제 토큰 주소도 상수로 두지 않고 계약에 묻는다.

const TRANSFER = parseAbiItem("event Transfer(address indexed from, address indexed to, uint256 value)");
const APPROVAL = parseAbiItem("event Approval(address indexed owner, address indexed spender, uint256 value)");
const BALANCE_OF = [parseAbiItem("function balanceOf(address) view returns (uint256)")] as const;

type Move = { from: Hex; to: Hex; value: bigint; tx: Hex; block: bigint; logIndex: number };
type Grant = { owner: Hex; value: bigint; tx: Hex; block: bigint; logIndex: number };
type JobOnChain = { client: Hex; state: string; provider: Hex; operator: Hex; price: bigint; resultHash: Hex };

const ZERO = "0x0000000000000000000000000000000000000000";
const CIRCLE = "①②③④⑤⑥⑦⑧⑨";
const same = (a?: string | null, b?: string | null) => !!a && !!b && a.toLowerCase() === b.toLowerCase();
const clock = (block: bigint) => (blockTime.get(block) ?? "").slice(6); // "MM-DD HH:mm:ss" 의 시각만

let paymentToken: Hex | undefined;
async function tokenAddress(): Promise<Hex> {
  paymentToken ??= (await client.readContract({ address: ESCROW, abi: ABI, functionName: "paymentToken" })) as Hex;
  return paymentToken;
}

/** 지금 계약이 쥐고 있는 토큰. 이벤트로 셈하지 않고 토큰 계약에 잔액을 묻는다. */
async function lockedNow(): Promise<bigint> {
  return client.readContract({ address: await tokenAddress(), abi: BALANCE_OF, functionName: "balanceOf", args: [ESCROW] });
}

/** 계약으로 들어오고 나간 토큰 이동과, 계약에 준 사용 승인. */
async function readMoney(from: bigint, to: bigint): Promise<{ moves: Move[]; grants: Grant[] }> {
  const address = await tokenAddress();
  const moves: Move[] = [];
  const grants: Grant[] = [];
  for (let start = from; start <= to; start += CHUNK) {
    const range = { fromBlock: start, toBlock: start + CHUNK - 1n < to ? start + CHUNK - 1n : to };
    const [ins, outs, approvals] = await Promise.all([
      client.getLogs({ address, event: TRANSFER, args: { to: ESCROW }, ...range }),
      client.getLogs({ address, event: TRANSFER, args: { from: ESCROW }, ...range }),
      client.getLogs({ address, event: APPROVAL, args: { spender: ESCROW }, ...range }),
    ]);
    for (const l of [...ins, ...outs]) {
      moves.push({ from: l.args.from!, to: l.args.to!, value: l.args.value!, tx: l.transactionHash!, block: l.blockNumber!, logIndex: l.logIndex! });
    }
    for (const l of approvals) {
      grants.push({ owner: l.args.owner!, value: l.args.value!, tx: l.transactionHash!, block: l.blockNumber!, logIndex: l.logIndex! });
    }
  }
  return { moves, grants };
}

async function readJobs(ids: bigint[]): Promise<Map<bigint, JobOnChain>> {
  const raw = (await Promise.all(
    ids.map((id) => client.readContract({ address: ESCROW, abi: ABI, functionName: "jobs", args: [id] })),
  )) as (readonly unknown[])[];
  return new Map(ids.map((id, i) => {
    const j = raw[i];
    return [id, {
      client: j[0] as Hex, state: STATE_NAME[Number(j[1])] ?? String(j[1]), provider: j[3] as Hex,
      operator: j[4] as Hex, price: j[5] as bigint, resultHash: j[6] as Hex,
    }];
  }));
}

/** 주소를 역할로 부른다. 모르는 주소는 줄여서 그대로 보인다. */
function party(addr: string, job?: JobOnChain): string {
  if (same(addr, ESCROW)) return "계약";
  if (job && same(addr, job.client)) return "수요자";
  if (job && same(addr, job.provider)) return "공급자";
  return short(addr);
}

/** 이벤트 하나를 무슨 일이 있었는지 한 문장으로. */
function sentence(e: Decoded): string {
  const a = e.args as Record<string, bigint & string & boolean>;
  switch (e.name) {
    case "JobFunded": return `수요자가 ${gsc(a.price)} 를 계약에 맡김`;
    case "JobAccepted": return "공급자의 Worker 가 작업을 맡음";
    case "ResultSubmitted": return `결과 Hash ${short(a.resultHash)} 를 체인에 기록`;
    case "JobCompleted": return `계약이 ${gsc(a.payout)} 를 공급자에게 지급`;
    case "JobCancelled": return `계약이 ${gsc(a.refund)} 를 수요자에게 돌려줌`;
    case "JobAborted": return "Worker 가 작업을 포기함";
    case "JobTimedOut":
      return Number(a.fromState) === 1
        ? "아무도 수락하지 않은 채 기한이 지나 settleTimeout 으로 정리"
        : "기한 안에 결과가 없어 settleTimeout 으로 정리";
    case "OperatorUpdated":
      return `공급자 ${short(a.provider)} 가 Worker ${short(a.operator)} 를 ${a.allowed ? "승인" : "해제"}`;
    default: return e.name;
  }
}

function moneyLine(m: Move, job?: JobOnChain): string {
  return `토큰  ${party(m.from, job)} → ${party(m.to, job)}  ${gsc(m.value)}`;
}

function status(job: JobOnChain, steps: Decoded[]): string {
  switch (job.state) {
    case "COMPLETED": return "완료";
    case "FUNDED": return "예치됨 · 수락 대기";
    case "ASSIGNED": return "학습 중";
    case "CANCELLED":
      if (steps.some((e) => e.name === "JobTimedOut")) return "환불 · 기한 초과";
      if (steps.some((e) => e.name === "JobAborted")) return "환불 · Worker 반납";
      return "환불 · 수요자 취소";
    default: return job.state;
  }
}

function duration(from: bigint, to: bigint): string {
  const s = Number((blockSeconds.get(to) ?? 0n) - (blockSeconds.get(from) ?? 0n));
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}

const byTx = <T extends { tx: Hex }>(items: T[]) => {
  const map = new Map<string, T[]>();
  for (const it of items) (map.get(it.tx) ?? map.set(it.tx, []).get(it.tx)!).push(it);
  return map;
};

function printCard(id: bigint, job: JobOnChain, steps: Decoded[], money: Map<string, Move[]>, grant?: Grant): void {
  const first = steps[0]?.log.blockNumber ?? 0n;
  const last = steps.at(-1)?.log.blockNumber ?? 0n;
  const open = job.state === "FUNDED" || job.state === "ASSIGNED";
  const span = `${blockTime.get(first) ?? ""} → ${open ? "진행 중" : `${clock(last)} (${duration(first, last)})`}`;
  console.log("─".repeat(78));
  console.log(`작업 #${id}   ${status(job, steps)}   ${gsc(job.price)}   ${span}`);
  const who = [`수요자 ${short(job.client)}`, `공급자 ${short(job.provider)}`];
  if (!same(job.operator, ZERO)) who.push(`Worker ${short(job.operator)}`);
  console.log(`  ${who.join("   ")}\n`);

  if (grant) {
    // 승인은 예치보다 한참 전에 넉넉히 해 둘 수 있다. 날짜가 다르면 날짜까지 보여 같은 흐름으로 착각하지 않게 한다
    const sameDay = (blockTime.get(grant.block) ?? "").slice(0, 5) === (blockTime.get(first) ?? "").slice(0, 5);
    const when = sameDay ? clock(grant.block) : blockTime.get(grant.block);
    console.log(`  ·  ${when}  ${pad("승인", 10)} 수요자가 계약이 ${gsc(grant.value)} 까지 가져가도록 허락`);
  }
  let n = 0;
  let inflow = 0n;
  let outflow = 0n;
  for (const [tx, events] of byTx(steps.map((e) => ({ ...e, tx: e.log.transactionHash! })))) {
    const mark = CIRCLE[n++] ?? "·";
    events.forEach((e, i) => {
      const head = i === 0 ? `  ${mark}  ${clock(e.log.blockNumber!)}` : " ".repeat(13); // 같은 트랜잭션의 둘째 줄은 번호·시각을 비운다
      console.log(`${head}  ${pad(LABEL[e.name] ?? e.name, 10)} ${sentence(e)}`);
    });
    for (const m of money.get(tx) ?? []) {
      console.log(`${" ".repeat(26)}${moneyLine(m, job)}`);
      if (same(m.to, ESCROW)) inflow += m.value; else outflow += m.value;
    }
  }
  console.log(`\n  돈   들어옴 ${gsc(inflow)} · 나감 ${gsc(outflow)} · 계약에 남음 ${gsc(inflow - outflow)}`);
}

async function jobsView(only: bigint | null, all: boolean): Promise<void> {
  const latest = await client.getBlockNumber();
  const nextJobId = (await client.readContract({ address: ESCROW, abi: ABI, functionName: "nextJobId" })) as bigint;
  if (only !== null && only >= nextJobId) throw new Error(`작업 #${only} 은 체인에 없습니다. 지금까지 #${nextJobId - 1n} 까지 있습니다.`);
  const ids = Array.from({ length: Number(nextJobId - 1n) }, (_, i) => BigInt(i + 1));

  const [events, { moves, grants }, jobs, locked] = await Promise.all([
    readEvents(DEPLOY_BLOCK, latest), readMoney(DEPLOY_BLOCK, latest), readJobs(ids), lockedNow(),
  ]);
  const blocks = new Set<bigint>([...events.map((e) => e.log.blockNumber!), ...grants.map((g) => g.block)]);
  await Promise.all([...blocks].map(timeOf)); // 블록 시각을 한 번에 (배치)

  header(latest);

  // 요약 — 들어온 돈과 나간 돈을 토큰 기록으로 세고, 남아야 할 돈을 실제 잔액과 맞춰 본다
  const completedTx = new Set(events.filter((e) => e.name === "JobCompleted").map((e) => e.log.transactionHash));
  let inflow = 0n, paid = 0n, refunded = 0n;
  for (const m of moves) {
    if (same(m.to, ESCROW)) inflow += m.value;
    else if (completedTx.has(m.tx)) paid += m.value;
    else refunded += m.value;
  }
  const count = (label: string) => [...jobs.values()].filter((j) => j.state === label).length;
  const openJobs = ids.filter((id) => ["FUNDED", "ASSIGNED"].includes(jobs.get(id)!.state));
  const expected = inflow - paid - refunded;

  console.log(`  지금 계약에 잠긴 돈   ${gsc(locked)}   (토큰 계약에 직접 물은 잔액)`);
  for (const id of openJobs) console.log(`                        └ 작업 #${id} · ${status(jobs.get(id)!, [])} · ${gsc(jobs.get(id)!.price)}`);
  console.log(`  체인에 기록된 작업   ${ids.length}건  (완료 ${count("COMPLETED")} · 환불 ${count("CANCELLED")} · 진행 중 ${openJobs.length})`);
  console.log(`  누적 토큰 흐름       들어옴 ${gsc(inflow)} · 공급자에게 지급 ${gsc(paid)} · 수요자에게 환불 ${gsc(refunded)}`);
  console.log(`  장부 대조            들어옴 − 나감 = ${gsc(expected)}  →  실제 잔액과 ${expected === locked ? "일치 ✓" : `불일치 ✗ (차이 ${gsc(locked - expected)})`}`);
  console.log();

  const shown = only !== null ? [only] : all ? [...ids].reverse() : [...ids].reverse().slice(0, 5);
  const money = byTx(moves);
  for (const id of shown) {
    const job = jobs.get(id)!;
    const steps = forJob(events, id);
    const fund = steps.find((e) => e.name === "JobFunded");
    // 예치 직전에 같은 수요자가 계약에 준 승인 — 트랜잭션 넷 중 토큰 계약 쪽 한 건
    const grant = fund && grants.filter((g) => same(g.owner, job.client) && (g.block < fund.log.blockNumber!
      || (g.block === fund.log.blockNumber! && g.logIndex < fund.log.logIndex!))).at(-1);
    printCard(id, job, steps, money, grant);
  }
  console.log("─".repeat(78));
  if (only === null && !all && ids.length > shown.length) {
    console.log(`최근 ${shown.length}건만 보였습니다. 전체는 --all, 하나는 --job N`);
  }
}

/** from 을 주면 그 블록부터 다시 흘려 보낸다. 리허설 때 지난 시연을 실제처럼 다시 보이거나 이 화면을 시험할 때 쓴다. */
async function watch(intervalSec: number, from?: bigint): Promise<void> {
  let latest = await client.getBlockNumber();
  header(latest);
  let locked = await lockedNow();
  console.log(`  지금 계약에 잠긴 돈   ${gsc(locked)}`);
  const since = from === undefined ? "여기부터 새 기록" : `블록 ${num(from)} 부터 다시 보기`;
  console.log(`\n── ${since} (${intervalSec}초마다 확인, 끝내려면 Ctrl+C) ──\n`);

  const tty = process.stdout.isTTY;
  let next = from ?? latest + 1n;
  for (;;) {
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
    try {
      latest = await client.getBlockNumber();
      if (latest < next) continue;
      const [events, { moves, grants }] = await Promise.all([readEvents(next, latest), readMoney(next, latest)]);
      const ids = [...new Set(events.filter((e) => "jobId" in e.args).map((e) => e.args.jobId as bigint))];
      const jobs = await readJobs(ids);
      await Promise.all([...new Set([...events.map((e) => e.log.blockNumber!), ...grants.map((g) => g.block)])].map(timeOf));
      if (tty) process.stdout.write("\r\x1b[2K");

      // 승인과 에스크로 기록을 한 줄로 합쳐 블록·로그 순서대로 찍는다. 여러 블록을 한 번에 읽어도 순서가 섞이지 않는다
      const eventTx = new Set(events.map((e) => e.log.transactionHash));
      const groups = byTx(events.map((e) => ({ ...e, tx: e.log.transactionHash! })));
      const timeline = [
        ...grants.filter((g) => !eventTx.has(g.tx)).map((g) => ({ block: g.block, index: g.logIndex, grant: g, group: undefined })),
        ...[...groups.values()].map((group) => ({ block: group[0].log.blockNumber!, index: group[0].log.logIndex!, grant: undefined, group })),
      ].sort((x, y) => (x.block === y.block ? x.index - y.index : x.block < y.block ? -1 : 1));

      // 돈이 움직일 때마다 그 시점에 계약이 쥔 돈을 보인다. 지금 잔액에서 이번에 읽은 이동을 되돌려 출발점을 잡는다
      const money = byTx(moves);
      const net = (ms: Move[]) => ms.reduce((sum, m) => sum + (same(m.to, ESCROW) ? m.value : -m.value), 0n);
      locked = await lockedNow();
      let running = locked - net(moves);
      for (const item of timeline) {
        if (item.grant) {
          const g = item.grant;
          console.log(`${blockTime.get(g.block)}   ${pad("승인", 10)} ${short(g.owner)} 가 계약이 ${gsc(g.value)} 까지 가져가도록 허락`);
          continue;
        }
        const group = item.group!;
        for (const e of group) {
          const id = "jobId" in e.args ? (e.args.jobId as bigint) : null;
          const tag = id === null ? "" : `작업 #${id}  `;
          console.log(`${blockTime.get(e.log.blockNumber!)}   ${pad(LABEL[e.name] ?? e.name, 10)} ${tag}${sentence(e)}`);
        }
        const withJob = group.find((x) => "jobId" in x.args);
        const job = withJob ? jobs.get(withJob.args.jobId as bigint) : undefined;
        const txMoves = money.get(group[0].tx) ?? [];
        for (const m of txMoves) console.log(`${" ".repeat(30)}${moneyLine(m, job)}`);
        if (txMoves.length > 0) {
          running += net(txMoves);
          console.log(`${" ".repeat(30)}→ 계약에 잠긴 돈 ${gsc(running)}`);
        }
      }
      next = latest + 1n;
      if (tty) process.stdout.write(`블록 ${num(latest)} 까지 확인 · 계약에 잠긴 돈 ${gsc(locked)} · 대기 중`);
    } catch (err) {
      if (tty) process.stdout.write("\r\x1b[2K");
      console.log(`RPC 응답 없음 — ${intervalSec}초 뒤 다시 시도 (${(err as Error).message.split("\n")[0]})`);
    }
  }
}

// ── 진입점 ───────────────────────────────────────────────────────────

/**
 * 정수 옵션을 읽는다. 옵션이 없으면 undefined, 있는데 값이 빠졌거나 정수가 아니면 멈춘다.
 * `--job` 만 쓰고 번호를 빠뜨렸을 때 조용히 전체를 보여 주면 걸러졌다고 착각하게 된다.
 * `--interval abc` 를 그냥 두면 NaN 이 1ms 로 바뀌어 RPC 를 쉬지 않고 두드린다.
 */
function intOption(name: string, min: number, what: string): number | undefined {
  const i = process.argv.indexOf(name);
  if (i < 0) return undefined;
  const raw = process.argv[i + 1];
  if (raw === undefined || !/^\d+$/.test(raw) || Number(raw) < min) {
    throw new Error(`${name} 에는 ${what}(${min} 이상의 정수)를 넣으세요. 받은 값: ${raw ?? "(없음)"}`);
  }
  return Number(raw);
}

async function main(): Promise<void> {
  const jobArg = intOption("--job", 1, "에스크로 작업 번호");
  const job = jobArg === undefined ? null : BigInt(jobArg);

  if (process.argv.includes("stats")) return stats();
  if (process.argv.includes("jobs")) return jobsView(job, process.argv.includes("--all"));
  if (process.argv.includes("watch")) {
    const from = intOption("--from", Number(DEPLOY_BLOCK), "다시 보기 시작 블록");
    return watch(intOption("--interval", 1, "확인 간격 초") ?? 3, from === undefined ? undefined : BigInt(from));
  }
  if (process.argv.includes("--follow")) {
    return follow(job, intOption("--tail", 0, "앞서 보일 줄 수") ?? 5, intOption("--interval", 1, "확인 간격 초") ?? 3);
  }
  return list(job);
}

main().catch((err) => {
  console.error((err as Error).message);
  process.exit(1);
});
