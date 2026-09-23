/**
 * SSAFY 체인에 배포된 에스크로의 이벤트를 **체인에서 직접** 읽어 시간순으로 보여 준다.
 *
 * 우리 화면은 지금 어디에서도 체인을 직접 읽지 않는다. 작업 상세의 체인 화면도 백엔드
 * DB 의 상태를 그린다. 그런데 SSAFY 블록 탐색기는 403 으로 막혀 있다. RPC 는 열려 있으니
 * 탐색기가 하는 일 중 필요한 만큼만 여기서 한다. 탐색기도 결국 같은 RPC 를 읽는다.
 *
 *   npm run events                    배포 이후 전체
 *   npm run events -- --job 13        작업 하나만 (escrow jobId)
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

const client = createPublicClient({ transport: http(RPC_URL, { retryCount: 2 }) });

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

// ── 진입점 ───────────────────────────────────────────────────────────

function option(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main(): Promise<void> {
  const jobArg = option("--job");
  const job = jobArg === undefined ? null : BigInt(jobArg);
  return list(job);
}

main().catch((err) => {
  console.error((err as Error).message);
  process.exit(1);
});
