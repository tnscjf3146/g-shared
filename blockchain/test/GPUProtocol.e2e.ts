import assert from "node:assert/strict";
import { after, describe, it } from "node:test";

import { network } from "hardhat";
import { parseEventLogs, parseUnits } from "viem";

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

  const ACCEPT_TIMEOUT = 7200n; // 2시간
  const EXECUTION_TIMEOUT = 3600n; // 1시간

  /** 상태 번호. 온체인 enum 과 같은 순서다. */
  const FUNDED = 1;
  const ASSIGNED = 2;
  const COMPLETED = 3;
  const CANCELLED = 4;

  const RESULT_HASH =
    "0x1111111111111111111111111111111111111111111111111111111111111111" as const;

  /** 백엔드가 발급하는 조회용 참조값. 인증 수단이 아니다. */
  const EXTERNAL_REF =
    "0x2222222222222222222222222222222222222222222222222222222222222222" as const;

  async function deployProtocol() {
    const [deployer, client, provider, keeper] = await viem.getWalletClients();

    const token = await viem.deployContract("GCoin", [client.account.address]);
    const escrow = await viem.deployContract("GPUJobEscrow", [
      token.address,
      ACCEPT_TIMEOUT,
      EXECUTION_TIMEOUT,
    ]);

    return { token, escrow, deployer, client, provider, keeper };
  }

  /**
   * 대금을 예치하며 작업을 만든다. 승인과 예치가 트랜잭션 두 번인 것을 그대로 보여준다.
   * 공급자는 예치할 때 함께 지정한다. 화면에서 의뢰자가 고른 결과가 이 자리에 들어온다.
   *
   * jobId 는 반드시 JobFunded 이벤트에서 읽는다. nextJobId 를 미리 읽어 두면
   * 다른 사용자의 예치가 사이에 끼어들 때 다른 작업을 가리키게 된다.
   * 프론트와 백엔드도 같은 방식으로 읽어야 한다.
   */
  async function createAndFund(ctx: Awaited<ReturnType<typeof deployProtocol>>) {
    const { token, escrow, client, provider } = ctx;

    await token.write.approve([escrow.address, PRICE], { account: client.account });
    const hash = await escrow.write.createAndFundJob(
      [EXTERNAL_REF, PRICE, provider.account.address],
      { account: client.account },
    );

    return jobIdFrom(hash, escrow);
  }

  /** 예치 영수증의 JobFunded 로그에서 실제 jobId 를 꺼낸다. */
  async function jobIdFrom(hash: `0x${string}`, escrow: any): Promise<bigint> {
    const publicClient = await viem.getPublicClient();
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    const events = parseEventLogs({
      abi: escrow.abi,
      logs: receipt.logs,
      eventName: "JobFunded",
    }) as unknown as Array<{ args: { jobId: bigint; externalRef: `0x${string}` } }>;
    const event = events[0];
    assert.ok(event, "JobFunded 이벤트를 찾지 못했습니다");
    assert.equal(event.args.externalRef, EXTERNAL_REF, "externalRef 가 그대로 실려 와야 합니다");
    return event.args.jobId;
  }

  async function stateOf(escrow: any, jobId: bigint): Promise<number> {
    const job = await escrow.read.jobs([jobId]);
    return Number(job[1]);
  }

  async function deadlineOf(escrow: any, jobId: bigint): Promise<bigint> {
    const job = await escrow.read.jobs([jobId]);
    return BigInt(job[2]);
  }

  it("정상 흐름 — 결과를 제출하는 트랜잭션에서 정산까지 끝난다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider } = ctx;

    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);

    const jobId = await createAndFund(ctx);

    assert.equal(await stateOf(escrow, jobId), FUNDED);
    assert.equal(await token.read.balanceOf([escrow.address]), PRICE);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY - PRICE);

    await escrow.write.acceptJob([jobId], { account: provider.account });
    assert.equal(await stateOf(escrow, jobId), ASSIGNED);

    // 승인 단계가 없다. 제출과 동시에 대금이 나간다.
    await escrow.write.submitResult([jobId, RESULT_HASH], { account: provider.account });

    assert.equal(await stateOf(escrow, jobId), COMPLETED);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY - PRICE);
    assert.equal(await token.read.balanceOf([provider.account.address]), PRICE);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });

  it("반납 흐름 — 공급자가 실패를 알리면 시한을 기다리지 않고 환불된다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider } = ctx;

    const jobId = await createAndFund(ctx);
    await escrow.write.acceptJob([jobId], { account: provider.account });

    // 학습이 실패했다. 시한이 지나기를 기다리지 않는다.
    await escrow.write.abortJob([jobId], { account: provider.account });

    assert.equal(await stateOf(escrow, jobId), CANCELLED);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([provider.account.address]), 0n);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });

  it("환불 흐름 — 공급자가 응답하지 않으면 제3자가 정산해도 의뢰자에게 돌아간다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, provider, keeper } = ctx;

    const jobId = await createAndFund(ctx);
    await escrow.write.acceptJob([jobId], { account: provider.account });

    // 공급자 PC 가 응답 불능이 되어 제출 시한이 지난다
    const deadline = await deadlineOf(escrow, jobId);
    await networkHelpers.time.increaseTo(Number(deadline) + 1);

    // 당사자가 아닌 키퍼가 정산한다
    await escrow.write.settleTimeout([jobId], { account: keeper.account });

    assert.equal(await stateOf(escrow, jobId), CANCELLED);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([provider.account.address]), 0n);
    assert.equal(await token.read.balanceOf([keeper.account.address]), 0n);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });

  it("미수락 흐름 — 아무도 수락하지 않으면 수락 시한 뒤 환불된다", async () => {
    const ctx = await networkHelpers.loadFixture(deployProtocol);
    const { token, escrow, client, keeper } = ctx;

    const jobId = await createAndFund(ctx);

    // 지정된 PC 가 꺼져 있어 수락이 일어나지 않는다
    const deadline = await deadlineOf(escrow, jobId);
    await networkHelpers.time.increaseTo(Number(deadline) + 1);

    await escrow.write.settleTimeout([jobId], { account: keeper.account });

    assert.equal(await stateOf(escrow, jobId), CANCELLED);
    assert.equal(await token.read.balanceOf([client.account.address]), SUPPLY);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
  });
});
