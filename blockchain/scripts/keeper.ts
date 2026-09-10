/**
 * 시한이 지난 작업을 정리하는 키퍼의 최소 구현.
 *
 * `settleTimeout` 은 누구나 부를 수 있지만 **아무도 자동으로 부르지 않는다.**
 * 공개 체인이라면 보상을 노린 봇이 정리해 주는데, 가스가 0 이고 보상도 없어
 * 부를 이유가 있는 주체가 없다. 백엔드 스케줄러가 이 일을 맡는다.
 *
 * 백엔드가 web3j 로 옮길 참고물이다. 실제로는 30~60초 주기로 돌면서
 * DB 에 보관한 만료 후보만 조회하면 되고, 여기처럼 전수를 훑을 필요가 없다.
 *
 *   npm run dev:keeper
 *
 * 키퍼 지갑은 의뢰자나 공급자와 달라야 한다. 다만 **수령자와 금액을 바꿀 권한이
 * 없어** 키가 노출돼도 예치금을 임의 주소로 가져갈 수 없다. 컨트랙트가 상태로
 * 결정하기 때문이다.
 */
import { readFileSync } from "node:fs";
import { network } from "hardhat";
import { formatUnits } from "viem";

/** 정산할 수 있는 상태. COMPLETED 와 CANCELLED 는 이미 끝난 작업이다. */
const SETTLEABLE = new Set([1, 2]); // FUNDED, ASSIGNED

const STATE_NAME = ["NONE", "FUNDED", "ASSIGNED", "COMPLETED", "CANCELLED"];

async function main() {
  const dev = JSON.parse(readFileSync("deployments/localhost/dev-chain.json", "utf8"));

  const connection = await network.connect({ network: "localhost" });
  const { viem } = connection;

  const publicClient = await viem.getPublicClient();
  const wallets = await viem.getWalletClients();
  const keeper = wallets[4];

  const escrow = await viem.getContractAt(
    "GPUJobEscrow",
    dev.contracts.GPUJobEscrow as `0x${string}`,
  );
  const token = await viem.getContractAt("GCoin", dev.contracts.GCoin as `0x${string}`);

  const block = await publicClient.getBlock();
  const now = block.timestamp;
  const nextJobId = await escrow.read.nextJobId();

  console.log(`블록 시각 ${now}`);
  console.log(`작업 1 ~ ${nextJobId - 1n} 검사\n`);

  let settled = 0;

  for (let id = 1n; id < nextJobId; id++) {
    const [client, state, deadline] = await escrow.read.jobs([id]);
    const s = Number(state);

    if (!SETTLEABLE.has(s)) {
      console.log(`  ${id}  ${STATE_NAME[s]}  건너뜀`);
      continue;
    }

    // 정각에는 아직 정산할 수 없다. 정상 경로가 우선한다.
    if (now <= (deadline as bigint)) {
      const left = (deadline as bigint) - now;
      console.log(`  ${id}  ${STATE_NAME[s]}  ${left}초 남음`);
      continue;
    }

    const before = await token.read.balanceOf([client as `0x${string}`]);
    await escrow.write.settleTimeout([id], { account: keeper.account });
    const after = await token.read.balanceOf([client as `0x${string}`]);

    console.log(
      `  ${id}  ${STATE_NAME[s]}  정산 완료 — 의뢰자 환불 ${formatUnits(after - before, 18)} GSC`,
    );
    settled++;
  }

  console.log(`\n${settled}건 정산`);
  console.log(`키퍼 잔액 ${formatUnits(await token.read.balanceOf([keeper.account.address]), 18)} GSC`);
  console.log(`에스크로  ${formatUnits(await token.read.balanceOf([escrow.address]), 18)} GSC`);

  // 키퍼는 수령자가 아니다. 잔액이 0 이어야 정상이다.

  await connection.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
