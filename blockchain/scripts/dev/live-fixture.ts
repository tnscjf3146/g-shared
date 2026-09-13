/**
 * Backend RPC layer 검증용 온체인 고정물.
 *
 * EscrowChainClient 는 eth_call / eth_getLogs 만 쓴다. 그 두 호출이 진짜 노드의
 * 진짜 응답을 올바로 해석하는지 보려면, 네 가지 상태가 실제로 체인 위에 있어야
 * 한다. 목으로 만든 응답으로는 인코딩을 틀려도 드러나지 않는다.
 *
 *   npx hardhat run scripts/dev/live-fixture.ts --network localhost
 */
import { readFileSync, writeFileSync } from "node:fs";
import { network } from "hardhat";
import { keccak256, toHex, parseUnits } from "viem";

const PRICE = parseUnits("10", 18);

async function main() {
  const connection = await network.connect({ network: "localhost" });
  const { viem } = connection;
  const dev = JSON.parse(readFileSync("deployments/localhost/dev-chain.json", "utf8"));

  const [, client, provider, operator] = await viem.getWalletClients();
  const token = await viem.getContractAt("GCoin", dev.contracts.GCoin);
  const escrow = await viem.getContractAt("GPUJobEscrow", dev.contracts.GPUJobEscrow);
  const publicClient = await viem.getPublicClient();

  await token.write.approve([escrow.address, PRICE * 10n], { account: client.account });

  const made: Record<string, unknown> = {};

  async function fund(label: string) {
    const externalRef = keccak256(toHex(`live-fixture:${label}`));
    const hash = await escrow.write.createAndFundJob(
      [externalRef, PRICE, provider.account.address],
      { account: client.account },
    );
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    const logs = await publicClient.getContractEvents({
      abi: escrow.abi, address: escrow.address, eventName: "JobFunded",
      blockHash: receipt.blockHash,
    });
    const jobId = (logs[0].args as { jobId: bigint }).jobId;
    made[label] = { jobId: jobId.toString(), externalRef };
    return jobId;
  }

  // FUNDED — 예치만 됐다
  await fund("funded");

  // ASSIGNED — operator 가 수락했다
  const assigned = await fund("assigned");
  await escrow.write.acceptJob([assigned], { account: operator.account });

  // COMPLETED — 결과가 기록됐다
  const completed = await fund("completed");
  await escrow.write.acceptJob([completed], { account: operator.account });
  const resultHash = keccak256(toHex("live-fixture-result"));
  await escrow.write.submitResult([completed, resultHash], { account: operator.account });
  (made["completed"] as Record<string, string>).resultHash = resultHash;

  // CANCELLED — 의뢰자가 취소했다 (수락 전에만 가능)
  const cancelled = await fund("cancelled");
  await escrow.write.cancelJob([cancelled], { account: client.account });

  const record = {
    rpcUrl: dev.rpcUrl,
    escrow: escrow.address,
    provider: provider.account.address,
    operator: operator.account.address,
    client: client.account.address,
    jobs: made,
  };
  writeFileSync(
    "deployments/localhost/live-fixture.json",
    `${JSON.stringify(record, null, 2)}\n`, "utf8",
  );
  console.log(JSON.stringify(record, null, 2));
  await connection.close();
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
