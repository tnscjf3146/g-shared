/**
 * 공급자 노드가 온체인에서 해야 할 일의 최소 구현.
 *
 * 워커 프로그램(system/worker)이 파이썬으로 옮겨 갈 참고물이다. 각 단계가 무엇을
 * 왜 하는지 주석으로 남겼으니 그대로 옮기면 된다. 실제 학습은 이 스크립트에 없다.
 * 자리만 비워 두었다.
 *
 * 워커에 작업 실행 단계가 아직 없어(진단·WSL 런타임·계정 인증까지 구현됨) 온체인
 * 호출이 스프린트 안에 못 들어올 수 있다. 그 경우 이 스크립트가 데모에서 공급자
 * 역할을 대신한다.
 *
 *   npm run dev:agent                정상 흐름 (수락 → 제출)
 *   npm run dev:agent:abort          실패 흐름 (수락 → 반납)
 *   npm run dev:agent:idle           응답 불능 (수락하지 않음)
 *
 * hardhat run 이 명령행 인자를 가로채므로 모드는 환경 변수로 받는다.
 *
 * 먼저 npm run dev:chain 으로 체인을 준비해야 한다.
 */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { network } from "hardhat";
import { formatUnits, parseUnits } from "viem";

const MODE = process.env.AGENT_MODE ?? "submit";

/** 백엔드가 이 작업에 매긴 가격. 실제로는 배정 정보와 함께 받는다. */
const EXPECTED_PRICE = parseUnits("10", 18);

/** 백엔드가 발급한 참조값. 실제로는 keccak256(projectId, dbJobId, nonce) 이다. */
const EXTERNAL_REF =
  "0x2222222222222222222222222222222222222222222222222222222222222222" as const;

type Dev = {
  contracts: { GCoin: string; GPUJobEscrow: string };
  accounts: { client: string; provider: string; operator: string };
};

async function main() {
  const dev: Dev = JSON.parse(
    readFileSync("deployments/localhost/dev-chain.json", "utf8"),
  );

  const connection = await network.connect({ network: "localhost" });
  const { viem } = connection;

  const [, client, provider, operator] = await viem.getWalletClients();
  const token = await viem.getContractAt("GCoin", dev.contracts.GCoin as `0x${string}`);
  const escrow = await viem.getContractAt(
    "GPUJobEscrow",
    dev.contracts.GPUJobEscrow as `0x${string}`,
  );

  console.log(`모드 ${MODE}\n`);

  // ── 0. 의뢰자가 작업을 만든다 ────────────────────────────────
  // 실제로는 사용자가 화면에서 하는 일이다. 여기서는 시험할 작업이 필요해 대신 만든다.
  await token.write.approve([escrow.address, EXPECTED_PRICE], { account: client.account });
  await escrow.write.createAndFundJob(
    [EXTERNAL_REF, EXPECTED_PRICE, provider.account.address],
    { account: client.account },
  );

  // 실제 워커는 JobFunded 이벤트를 구독해 자기 작업을 고른다.
  // provider 가 indexed 라 자기 정산 주소로 필터를 걸면 자기 것만 온다.
  //
  //   escrow.watchEvent.JobFunded({ provider: myProviderAddress }, { onLogs })
  //
  // 여기서는 방금 만든 작업을 바로 집는다.
  const jobId = (await escrow.read.nextJobId()) - 1n;
  console.log(`작업 ${jobId} 감지`);

  // ── 1. 수락 전에 대조한다 ───────────────────────────────────
  // 가스가 0 이라 누구나 컨트랙트를 직접 부를 수 있다. 백엔드를 거치지 않은 엉뚱한
  // 작업이 들어올 수 있으므로, 배정 정보와 온체인 상태를 반드시 맞춰 본다.
  const job = await escrow.read.jobs([jobId]);
  const [onChainClient, state, deadline, onChainProvider, , price] = job;

  const now = BigInt(Math.floor(Date.now() / 1000));
  const checks: Array<[string, boolean]> = [
    ["상태가 FUNDED 인가", Number(state) === 1],
    [
      "정산 주소가 내 소유자인가",
      (onChainProvider as string).toLowerCase() === dev.accounts.provider.toLowerCase(),
    ],
    [
      "의뢰자가 배정 정보와 같은가",
      (onChainClient as string).toLowerCase() === dev.accounts.client.toLowerCase(),
    ],
    ["가격이 백엔드 값과 같은가", (price as bigint) === EXPECTED_PRICE],
    ["수락 시한이 남았는가", BigInt(deadline as bigint) >= now],
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

  // ── 2. 수락한다 ─────────────────────────────────────────────
  // operator 키로 부른다. 소유자가 setOperator 로 승인해 둔 주소여야 한다.
  // 이 시점에 job.operator 가 확정되고, 이후 제출과 반납은 이 주소만 부를 수 있다.
  await escrow.write.acceptJob([jobId], { account: operator.account });
  console.log(`\n수락 완료. operator 로 확정됨`);

  // ── 3. 학습 ─────────────────────────────────────────────────
  // 여기가 워커의 본체다. 컨테이너를 띄우고 GPU 를 돌린다.
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

  // ── 4. 결과 해시를 만든다 ───────────────────────────────────
  // 저장소에 업로드하는 파일 그 자체를 SHA-256 으로 해싱한다.
  // 파일이 여럿이면 하나로 묶어 올리고 그 묶음을 해싱한다.
  //
  //   파이썬:
  //     import hashlib
  //     digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
  //     result_hash = "0x" + digest
  const resultHash = `0x${createHash("sha256").update(artifact).digest("hex")}` as const;
  console.log(`결과 해시 ${resultHash}`);

  // ── 5. 제출한다 ─────────────────────────────────────────────
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
