import assert from "node:assert/strict";
import { after, describe, it } from "node:test";

import { network } from "hardhat";
import { parseUnits } from "viem";

/**
 * GCoin 과 GPUJobEscrow 를 함께 배포해 전체 사용자 흐름만 검증한다.
 * 조건 하나짜리 검사는 contracts/escrow/*.t.sol 이 담당하므로 여기서 반복하지 않는다.
 */
describe("GPU Protocol e2e", async () => {
  const connection = await network.create();
  const { viem, networkHelpers } = connection;
  after(async () => connection.close());

  const PRICE = parseUnits("10", 18);
  const SUPPLY = parseUnits("1000000", 18);

  const ACCEPT_DEADLINE = 7200n;  // 2시간
  const EXEC_DEADLINE = 3600n;    // 1시간
  const APPROVE_DEADLINE = 1800n; // 30분

  const RESULT_HASH =
    "0x1111111111111111111111111111111111111111111111111111111111111111" as const;

  async function deployProtocol() {
    const [deployer, client, provider, keeper] = await viem.getWalletClients();

    const token = await viem.deployContract("GCoin", [client.account.address]);
    const escrow = await viem.deployContract("GPUJobEscrow", [
      token.address,
      ACCEPT_DEADLINE,
      EXEC_DEADLINE,
      APPROVE_DEADLINE,
    ]);

    return { token, escrow, deployer, client, provider, keeper };
  }

  /** 작업을 만들고 대금을 예치한다. 승인과 예치가 트랜잭션 두 번인 것을 그대로 보여준다. */
  async function createAndFund(ctx: Awaited<ReturnType<typeof deployProtocol>>) {
    const { token, escrow, client } = ctx;

    const jobId = await escrow.read.nextJobId();
    await escrow.write.createJob([PRICE], { account: client.account });
    await token.write.approve([escrow.address, PRICE], { account: client.account });
    await escrow.write.fundJob([jobId], { account: client.account });

    return jobId;
  }

  async function stateOf(escrow: any, jobId: bigint): Promise<number> {
    const job = await escrow.read.jobs([jobId]);
    return Number(job[1]);
  }

  async function deadlineOf(escrow: any, jobId: bigint): Promise<bigint> {
    const job = await escrow.read.jobs([jobId]);
    return BigInt(job[2]);
  }

  it("정상 흐름 — 예치부터 승인 정산까지 한 바퀴가 돌고 잔액이 맞는다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider } = ctx;

    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);

    const jobId = await createAndFund(ctx);

    assert.equal(await token.read.balanceOf([escrow.address]), PRICE);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY - PRICE);

    await escrow.write.acceptJob([jobId], { account: provider.account });
    await escrow.write.submitResult([jobId, RESULT_HASH], { account: provider.account });
    await escrow.write.approveResult([jobId], { account: client.account });

    assert.equal(await stateOf(escrow, jobId), 5); // COMPLETED

    // 의뢰자가 낸 만큼 공급자가 받았고, 에스크로에 남은 돈이 없다
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY - PRICE);
    assert.equal(await token.read.balanceOf([provider.account.address]), PRICE);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });

  it("환불 흐름 — 공급자가 결과를 내지 않으면 제3자가 정산해도 의뢰자에게 돌아간다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider, keeper } = ctx;

    const jobId = await createAndFund(ctx);
    await escrow.write.acceptJob([jobId], { account: provider.account });

    // 공급자가 아무것도 하지 않은 채 제출 시한이 지난다
    const deadline = await deadlineOf(escrow, jobId);
    await networkHelpers.time.increaseTo(Number(deadline) + 1);

    // 당사자가 아닌 키퍼가 정산한다
    await escrow.write.settleTimeout([jobId], { account: keeper.account });

    assert.equal(await stateOf(escrow, jobId), 6); // CANCELLED

    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([provider.account.address]), 0n);
    assert.equal(await token.read.balanceOf([keeper.account.address]), 0n);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });

  it("무응답 지급 흐름 — 의뢰자가 승인하지 않으면 공급자가 대금을 받는다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider, keeper } = ctx;

    const jobId = await createAndFund(ctx);
    await escrow.write.acceptJob([jobId], { account: provider.account });
    await escrow.write.submitResult([jobId, RESULT_HASH], { account: provider.account });

    // 의뢰자가 승인도 이의 제기도 하지 않은 채 시한이 지난다
    const deadline = await deadlineOf(escrow, jobId);
    await networkHelpers.time.increaseTo(Number(deadline) + 1);

    await escrow.write.settleTimeout([jobId], { account: keeper.account });

    assert.equal(await stateOf(escrow, jobId), 5); // COMPLETED

    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY - PRICE);
    assert.equal(await token.read.balanceOf([provider.account.address]), PRICE);
    assert.equal(await token.read.balanceOf([keeper.account.address]), 0n);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });
});
