/**
 * 공급자 노드가 온체인에서 해야 할 일의 최소 구현.
 *
 * 워커 프로그램(system/worker)이 파이썬으로 옮겨 갈 참고물이다. 각 단계가 무엇을
 * 왜 하는지 주석으로 남겼으니 그대로 옮기면 된다. 실제 학습은 이 스크립트에 없다.
 * 자리만 비워 두었다.
 *
 * 세 역할이 한 파일에 있다. 실제 시스템에서는 셋이 다른 프로세스다.
 *
 *   0   의뢰자   프론트 + MetaMask         approve → createAndFundJob
 *   0b  백엔드   JobMatcher                JobFunded 를 받아 JOB_ASSIGN 을 만든다
 *   1~  워커     system/worker             이벤트로 작업을 찾고, 대조하고, 수락하고, 제출한다
 *
 * 워커 런타임이 없는 동안은 이 스크립트가 시연에서 공급자 역할을 대신한다.
 * 단, 워커 키로 acceptJob 한 작업은 이 스크립트가 끝낼 수 없다(job.operator 고정).
 *
 *   npm run dev:agent                정상 흐름 (수락 → 제출)
 *   npm run dev:agent:abort          실패 흐름 (수락 → 반납)
 *   npm run dev:agent:idle           응답 불능 (수락하지 않음)
 *
 * hardhat run 이 명령행 인자를 가로채므로 모드는 환경 변수로 받는다.
 * Windows 에서도 돌도록 package.json 은 cross-env 로 넘긴다.
 *
 * 먼저 npm run dev:chain 으로 체인을 준비해야 한다.
 */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { network } from "hardhat";
import { formatUnits, parseEventLogs, parseUnits } from "viem";

const MODE = process.env.AGENT_MODE ?? "submit";

/** 백엔드가 이 작업에 매긴 가격. 실제로는 DB 의 estimated_cost(TOKEN) 를 wei 로 바꾼 값. */
const PRICE = parseUnits("10", 18);

/** 백엔드가 발급한 참조값. 실제로는 keccak256(projectId, dbJobId, nonce) 이다. */
const EXTERNAL_REF =
  "0x2222222222222222222222222222222222222222222222222222222222222222" as const;

type Address = `0x${string}`;

type Dev = {
  contracts: { GCoin: string; GPUJobEscrow: string };
  accounts: { client: string; provider: string; operator: string };
};

/**
 * 백엔드가 JOB_ASSIGN 으로 워커에게 보내는 배정 정보. 규격 9장.
 *
 * 지금 백엔드의 JOB_ASSIGN 에는 이 값들이 없다. 이 다섯이 들어와야 워커가
 * 수락 전 대조를 할 수 있다. 필드명은 규격에 적힌 그대로다.
 */
type Assignment = {
  escrowJobId: bigint;
  externalRef: Address;
  price: bigint; // wei
  clientWallet: Address;
  providerWallet: Address;
};

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

async function main() {
  const dev: Dev = JSON.parse(
    readFileSync("deployments/localhost/dev-chain.json", "utf8"),
  );

  const connection = await network.connect({ network: "localhost" });
  const { viem } = connection;

  const publicClient = await viem.getPublicClient();
  const [, client, provider, operator] = await viem.getWalletClients();
  const token = await viem.getContractAt("GCoin", dev.contracts.GCoin as Address);
  const escrow = await viem.getContractAt(
    "GPUJobEscrow",
    dev.contracts.GPUJobEscrow as Address,
  );

  console.log(`모드 ${MODE}\n`);

  // ── 0. 의뢰자가 작업을 만든다 ────────────────────────────────
  // 실제로는 사용자가 화면에서 MetaMask 로 하는 일이다. 시험할 작업이 필요해 대신 만든다.
  // 워커가 구독을 시작한 블록을 기억해 둔다. 그 뒤로 생긴 이벤트만 보면 된다.
  const fromBlock = await publicClient.getBlockNumber();

  await token.write.approve([escrow.address, PRICE], { account: client.account });
  const hash = await escrow.write.createAndFundJob(
    [EXTERNAL_REF, PRICE, provider.account.address],
    { account: client.account },
  );
  const receipt = await publicClient.waitForTransactionReceipt({ hash });

  // ── 0b. 백엔드가 JobFunded 를 받아 배정 정보를 만든다 ────────
  // 백엔드는 영수증 또는 구독으로 JobFunded 를 받고, externalRef 로 DB 작업을 찾아
  // escrow_job_id 를 채운 뒤, 스케줄러가 고른 워커에게 JOB_ASSIGN 을 보낸다.
  // 여기서는 그 페이로드를 흉내 낸다.
  const funded = parseEventLogs({
    abi: escrow.abi,
    logs: receipt.logs,
    eventName: "JobFunded",
  })[0] as unknown as { args: { jobId: bigint; externalRef: Address } };

  const assignment: Assignment = {
    escrowJobId: funded.args.jobId,
    externalRef: funded.args.externalRef,
    price: PRICE,
    clientWallet: client.account.address,
    providerWallet: provider.account.address,
  };

  console.log("JOB_ASSIGN (백엔드 → 워커)");
  console.log(`  escrowJobId    ${assignment.escrowJobId}`);
  console.log(`  externalRef    ${assignment.externalRef}`);
  console.log(`  price          ${assignment.price} wei`);
  console.log(`  clientWallet   ${assignment.clientWallet}`);
  console.log(`  providerWallet ${assignment.providerWallet}\n`);

  // ── 1. 워커가 이벤트에서 자기 작업을 찾는다 ─────────────────
  // nextJobId 로 "마지막 작업" 을 집으면 안 된다. 다른 의뢰자의 예치가 끼어들면
  // 남의 작업을 집는다. 배정받은 escrowJobId 를 이벤트 로그에서 찾아야 한다.
  //
  // provider 가 indexed 라 자기 소유자의 정산 주소로 필터를 걸면 자기 것만 온다.
  // 실제 워커는 watchEvent 로 구독한다. 여기서는 기억해 둔 블록부터 조회한다.
  // 파이썬이라면 eth_getLogs 에 topics 로 같은 필터를 건다.
  const logs = (await publicClient.getContractEvents({
    address: escrow.address,
    abi: escrow.abi,
    eventName: "JobFunded",
    args: { provider: assignment.providerWallet },
    fromBlock,
  })) as unknown as Array<{ args: { jobId: bigint; externalRef: Address } }>;

  const mine = logs.find((l) => l.args.jobId === assignment.escrowJobId);
  const jobId = assignment.escrowJobId;
  console.log(`내 정산 주소로 예치된 작업 ${logs.length}건. 배정받은 ${jobId} 번을 찾는다`);

  // ── 2. 수락 전에 일곱 항목을 대조한다 (규격 9장) ─────────────
  // 가스가 0 이라 누구나 컨트랙트를 직접 부를 수 있다. 백엔드를 거치지 않은 엉뚱한
  // 작업이 들어올 수 있으므로, 배정 정보와 온체인 상태를 반드시 맞춰 본다.
  const [onChainClient, state, deadline, onChainProvider, , price] =
    await escrow.read.jobs([jobId]);

  // 시각은 블록에서 읽는다. 로컬 시계는 체인과 어긋나고, 컨트랙트는 block.timestamp 로
  // 판정하므로 같은 값을 써야 결과가 일치한다. 규격 5장.
  const block = await publicClient.getBlock();
  const now = block.timestamp;

  const checks: Array<[string, boolean]> = [
    ["① 백엔드가 나에게 배정한 작업이 온체인에 있는가", mine !== undefined],
    ["② externalRef 가 배정 정보와 같은가", mine?.args.externalRef === assignment.externalRef],
    ["③ 의뢰자가 배정 정보와 같은가", same(onChainClient as string, assignment.clientWallet)],
    ["④ 정산 주소가 내 소유자인가", same(onChainProvider as string, assignment.providerWallet)],
    ["⑤ 가격이 배정 정보와 같은가", (price as bigint) === assignment.price],
    ["⑥ 상태가 FUNDED 인가", Number(state) === 1],
    ["⑦ 수락 시한이 남았는가 (블록 시각 기준)", now <= (deadline as bigint)],
  ];

  for (const [label, ok] of checks) {
    console.log(`  ${ok ? "통과" : "실패"}  ${label}`);
  }
  if (checks.some(([, ok]) => !ok)) {
    console.log("\n대조에 실패했습니다. 수락하지 않습니다.");
    await connection.close();
    return;
  }

  // 가격 대조가 특히 중요하다. 의뢰자가 트랜잭션에 가격을 직접 적어 보내므로,
  // 컨트랙트를 직접 부르면 1 GSC 로 적어 놓고 비싼 GPU 를 지정할 수 있다.
  // 자동 수락이면 걸러 줄 사람이 없어 여기가 마지막 방어선이다.

  if (MODE === "idle") {
    console.log("\n응답 불능을 흉내 냅니다. 수락하지 않습니다.");
    console.log("시한이 지나면 키퍼가 settleTimeout 으로 의뢰자에게 환불합니다.");
    await connection.close();
    return;
  }

  // ── 3. 수락한다 ─────────────────────────────────────────────
  // operator 키로 서명한다. 소유자가 setOperator 로 승인해 둔 주소여야 한다.
  // 이 시점에 job.operator 가 확정되고, 이후 제출과 반납은 이 주소만 부를 수 있다.
  // 워커 6.5 단계는 여기까지다(키 생성 · DPAPI 보관 · acceptJob).
  await escrow.write.acceptJob([jobId], { account: operator.account });
  console.log(`\n수락 완료. operator 로 확정됨`);

  // ── 4. 학습 ─────────────────────────────────────────────────
  // 여기가 워커의 본체다. 컨테이너를 띄우고 GPU 를 돌린다.
  // 개인키는 컨테이너에 넘기지 않는다. 결과가 나오면 호스트 프로세스가 서명한다.
  // 이 스크립트는 결과 파일을 흉내만 낸다.
  const artifact = Buffer.from(`lora-adapter-for-job-${jobId}`);

  if (MODE === "abort") {
    // 학습이 실패했다. 시한이 지나기를 기다리지 않고 즉시 반납한다.
    // 실패 사유 원문은 온체인에 남기지 않는다. 백엔드의 fail_reason 에 보관한다.
    const before = await token.read.balanceOf([client.account.address]);
    await escrow.write.abortJob([jobId], { account: operator.account });
    const after = await token.read.balanceOf([client.account.address]);

    console.log(`학습 실패 — 반납`);
    console.log(`의뢰자 환불 ${formatUnits(after - before, 18)} GSC`);
    await connection.close();
    return;
  }

  // ── 5. 결과 해시를 만든다 ───────────────────────────────────
  // 저장소에 업로드하는 파일 그 자체를 SHA-256 으로 해싱한다.
  // 파일이 여럿이면 하나로 묶어 올리고 그 묶음을 해싱한다.
  //
  //   파이썬:
  //     import hashlib
  //     digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
  //     result_hash = "0x" + digest
  const resultHash = `0x${createHash("sha256").update(artifact).digest("hex")}` as const;
  console.log(`결과 해시 ${resultHash}`);

  // ── 6. 제출한다 ─────────────────────────────────────────────
  // 제출과 정산이 한 트랜잭션에서 끝난다. 의뢰자 승인 단계가 없고 되돌릴 수 없다.
  const before = await token.read.balanceOf([provider.account.address]);
  await escrow.write.submitResult([jobId, resultHash], { account: operator.account });
  const after = await token.read.balanceOf([provider.account.address]);

  console.log(`\n제출 완료`);
  console.log(`정산 주소 수령 ${formatUnits(after - before, 18)} GSC`);
  console.log(`워커 키 잔액   ${formatUnits(await token.read.balanceOf([operator.account.address]), 18)} GSC`);
  console.log(`에스크로 잔액  ${formatUnits(await token.read.balanceOf([escrow.address]), 18)} GSC`);

  await connection.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
