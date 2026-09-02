const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-network-helpers");

const EXEC_DEADLINE = 60 * 60;       // 수락 후 제출까지 1시간
const APPROVE_DEADLINE = 30 * 60;    // 제출 후 승인까지 30분
const PRICE = 10;
const RESULT_HASH = "0x" + "ab".repeat(32);

const State = {
  NONE: 0n, CREATED: 1n, FUNDED: 2n, ASSIGNED: 3n,
  SUBMITTED: 4n, COMPLETED: 5n, CANCELLED: 6n,
};

describe("GPUJobEscrow", function () {
  let token, escrow, client, provider, other;

  beforeEach(async function () {
    [client, provider, other] = await ethers.getSigners();

    const MockSSF = await ethers.getContractFactory("MockSSF");
    token = await MockSSF.deploy("Mock SSAFY", "mSSF", 1000);

    const Escrow = await ethers.getContractFactory("GPUJobEscrow");
    escrow = await Escrow.deploy(await token.getAddress(), EXEC_DEADLINE, APPROVE_DEADLINE);
  });

  async function createAndFund() {
    await escrow.connect(client).createJob(PRICE);
    await token.connect(client).approve(await escrow.getAddress(), PRICE);
    await escrow.connect(client).fundJob(1);
  }

  describe("createJob", function () {
    it("작업이 만들어지고 CREATED 가 된다", async function () {
      await escrow.connect(client).createJob(PRICE);
      const job = await escrow.jobs(1);
      expect(job.client).to.equal(client.address);
      expect(job.price).to.equal(PRICE);
      expect(job.state).to.equal(State.CREATED);
    });

    it("price 가 0 이면 실패한다", async function () {
      await expect(escrow.connect(client).createJob(0)).to.be.reverted;
    });

    it("JobCreated 를 emit 한다", async function () {
      await expect(escrow.connect(client).createJob(PRICE))
        .to.emit(escrow, "JobCreated").withArgs(1, client.address, PRICE);
    });
  });

  describe("fundJob", function () {
    it("approve 없이 부르면 실패한다", async function () {
      await escrow.connect(client).createJob(PRICE);
      await expect(escrow.connect(client).fundJob(1)).to.be.reverted;
    });

    it("예치하면 돈이 에스크로로 옮겨진다", async function () {
      await createAndFund();
      expect(await token.balanceOf(await escrow.getAddress())).to.equal(PRICE);
      expect(await token.balanceOf(client.address)).to.equal(1000 - PRICE);
      expect((await escrow.jobs(1)).state).to.equal(State.FUNDED);
    });

    it("의뢰자가 아니면 실패한다", async function () {
      await escrow.connect(client).createJob(PRICE);
      await token.connect(client).approve(await escrow.getAddress(), PRICE);
      await expect(escrow.connect(other).fundJob(1)).to.be.reverted;
    });
  });

  describe("acceptJob", function () {
    it("예치된 작업을 잡으면 ASSIGNED 가 되고 시한이 걸린다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      const job = await escrow.jobs(1);
      expect(job.provider).to.equal(provider.address);
      expect(job.state).to.equal(State.ASSIGNED);
      expect(job.deadline).to.be.greaterThan(await time.latest());
    });

    it("예치 전에는 못 잡는다", async function () {
      await escrow.connect(client).createJob(PRICE);
      await expect(escrow.connect(provider).acceptJob(1)).to.be.reverted;
    });

    it("두 번째 수락은 revert 된다 — 정상 상황이다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await expect(escrow.connect(other).acceptJob(1)).to.be.reverted;
    });

    it("의뢰자는 자기 작업을 못 잡는다", async function () {
      await createAndFund();
      await expect(escrow.connect(client).acceptJob(1)).to.be.reverted;
    });
  });

  describe("submitResult", function () {
    beforeEach(async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
    });

    it("결과 해시가 저장되고 SUBMITTED 가 된다", async function () {
      await escrow.connect(provider).submitResult(1, RESULT_HASH);
      const job = await escrow.jobs(1);
      expect(job.resultHash).to.equal(RESULT_HASH);
      expect(job.state).to.equal(State.SUBMITTED);
    });

    it("배정받지 않은 자는 제출할 수 없다", async function () {
      await expect(escrow.connect(other).submitResult(1, RESULT_HASH)).to.be.reverted;
    });

    it("빈 해시는 거부한다", async function () {
      await expect(escrow.connect(provider).submitResult(1, ethers.ZeroHash)).to.be.reverted;
    });
  });

  // ───────── 경로 1 · 정상 ─────────
  describe("경로 1 — 정상", function () {
    it("승인하면 공급자에게 지급된다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await escrow.connect(provider).submitResult(1, RESULT_HASH);
      await escrow.connect(client).approveResult(1);

      expect(await token.balanceOf(provider.address)).to.equal(PRICE);
      expect(await token.balanceOf(await escrow.getAddress())).to.equal(0);
      expect((await escrow.jobs(1)).state).to.equal(State.COMPLETED);
    });

    it("의뢰자가 아니면 승인할 수 없다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await escrow.connect(provider).submitResult(1, RESULT_HASH);
      await expect(escrow.connect(other).approveResult(1)).to.be.reverted;
    });
  });

  // ───────── 경로 2 · 취소 ─────────
  describe("경로 2 — 취소", function () {
    it("예치 후 수락 전이면 전액 환불된다", async function () {
      await createAndFund();
      await escrow.connect(client).cancelJob(1);

      expect(await token.balanceOf(client.address)).to.equal(1000);
      expect(await token.balanceOf(await escrow.getAddress())).to.equal(0);
      expect((await escrow.jobs(1)).state).to.equal(State.CANCELLED);
    });

    it("예치 전에도 취소할 수 있다", async function () {
      await escrow.connect(client).createJob(PRICE);
      await escrow.connect(client).cancelJob(1);
      expect((await escrow.jobs(1)).state).to.equal(State.CANCELLED);
    });

    it("수락된 뒤에는 취소할 수 없다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await expect(escrow.connect(client).cancelJob(1)).to.be.reverted;
    });
  });

  // ───────── 경로 3 · 공급자 미제출 만료 ─────────
  describe("경로 3 — 공급자 미제출 만료", function () {
    it("시한이 지나면 의뢰자에게 환불된다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);

      await time.increase(EXEC_DEADLINE + 1);
      await escrow.connect(other).settleTimeout(1);   // 누구나 부를 수 있다

      expect(await token.balanceOf(client.address)).to.equal(1000);
      expect(await token.balanceOf(provider.address)).to.equal(0);
      expect((await escrow.jobs(1)).state).to.equal(State.CANCELLED);
    });

    it("시한 전에는 실패한다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await expect(escrow.connect(other).settleTimeout(1)).to.be.reverted;
    });
  });

  // ───────── 경로 4 · 의뢰자 미승인 만료 ─────────
  describe("경로 4 — 의뢰자 미승인 만료", function () {
    it("시한이 지나면 공급자에게 지급된다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await escrow.connect(provider).submitResult(1, RESULT_HASH);

      await time.increase(APPROVE_DEADLINE + 1);
      await escrow.connect(other).settleTimeout(1);

      expect(await token.balanceOf(provider.address)).to.equal(PRICE);
      expect(await token.balanceOf(client.address)).to.equal(1000 - PRICE);
      expect((await escrow.jobs(1)).state).to.equal(State.COMPLETED);
    });

    it("시한 전에는 실패한다", async function () {
      await createAndFund();
      await escrow.connect(provider).acceptJob(1);
      await escrow.connect(provider).submitResult(1, RESULT_HASH);
      await expect(escrow.connect(other).settleTimeout(1)).to.be.reverted;
    });
  });

  it("끝난 작업은 다시 정산되지 않는다", async function () {
    await createAndFund();
    await escrow.connect(provider).acceptJob(1);
    await escrow.connect(provider).submitResult(1, RESULT_HASH);
    await escrow.connect(client).approveResult(1);
    await time.increase(APPROVE_DEADLINE + 1);
    await expect(escrow.connect(other).settleTimeout(1)).to.be.reverted;
  });
});
