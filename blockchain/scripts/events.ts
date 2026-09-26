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
 * 키가 필요 없다. 읽기만 한다. RPC 는 SSAFY_RPC_URL 이 있으면 그것을 쓴다.
 */
import { readFileSync } from "node:fs";
import {
  createPublicClient,
  decodeEventLog,
  formatUnits,
  http,
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
async function timeOf(block: bigint): Promise<string> {
  const cached = blockTime.get(block);
  if (cached) return cached;
  const { timestamp } = await client.getBlock({ blockNumber: block });
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
  if (process.argv.includes("--follow")) {
    return follow(job, intOption("--tail", 0, "앞서 보일 줄 수") ?? 5, intOption("--interval", 1, "확인 간격 초") ?? 3);
  }
  return list(job);
}

main().catch((err) => {
  console.error((err as Error).message);
  process.exit(1);
});
