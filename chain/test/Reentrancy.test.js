const { expect } = require("chai");
const { ethers } = require("hardhat");

const EXEC_DEADLINE = 60 * 60;
const APPROVE_DEADLINE = 30 * 60;
const PRICE = 10;
const RESULT_HASH = "0x" + "ab".repeat(32);

// 재진입 공격 재현
//
// 정산 함수가 토큰을 먼저 보내고 상태를 나중에 바꾸면,
// 수신자가 콜백 안에서 같은 함수를 다시 불러 대금을 여러 번 받을 수 있다.
//
// GPUJobEscrow 는 상태를 먼저 바꾸므로 두 번째 호출이 require 에서 걸린다.
describe("재진입 공격", function () {
  let token, escrow, attacker, client;

  beforeEach(async function () {
    [client] = await ethers.getSigners();

    // 수신자가 컨트랙트면 콜백을 부르는 토큰. 공격 통로를 만든다.
    const Hooked = await ethers.getContractFactory("MockSSFWithHook");
    token = await Hooked.deploy(1000);

    const Escrow = await ethers.getContractFactory("GPUJobEscrow");
    escrow = await Escrow.deploy(await token.getAddress(), EXEC_DEADLINE, APPROVE_DEADLINE);

    const Attacker = await ethers.getContractFactory("ReentrancyAttacker");
    attacker = await Attacker.deploy(await escrow.getAddress());

    // 정상 흐름으로 SUBMITTED 까지 만든다. 공급자가 악성 컨트랙트다.
    await escrow.connect(client).createJob(PRICE);
    await token.connect(client).approve(await escrow.getAddress(), PRICE);
    await escrow.connect(client).fundJob(1);
    await attacker.accept(1);
    await attacker.submit(RESULT_HASH);
  });

  it("정산 중 재진입을 시도해도 대금은 한 번만 나간다", async function () {
    const escrowAddress = await escrow.getAddress();
    const attackerAddress = await attacker.getAddress();

    expect(await token.balanceOf(escrowAddress)).to.equal(PRICE);

    await escrow.connect(client).approveResult(1);

    // 공격자는 정확히 price 만큼만 받는다
    expect(await token.balanceOf(attackerAddress)).to.equal(PRICE);
    expect(await token.balanceOf(escrowAddress)).to.equal(0);
  });

  it("콜백이 실제로 호출되고, 재진입 호출은 revert 된다", async function () {
    await escrow.connect(client).approveResult(1);

    // 콜백은 불렸다 — 공격 통로가 열려 있다는 뜻이다
    expect(await attacker.reenterCount()).to.equal(1);

    // 그런데 그 안에서 부른 에스크로 호출은 실패했다
    expect(await attacker.reenterFailed()).to.equal(true);
  });

  it("정산이 끝난 뒤 상태는 COMPLETED 이고 다시 정산되지 않는다", async function () {
    await escrow.connect(client).approveResult(1);

    const job = await escrow.jobs(1);
    expect(job.state).to.equal(5n); // COMPLETED

    await expect(escrow.connect(client).approveResult(1)).to.be.reverted;
  });

  it("에스크로 잔액을 넘어서는 인출은 발생하지 않는다", async function () {
    const escrowAddress = await escrow.getAddress();
    const before = await token.balanceOf(escrowAddress);

    await escrow.connect(client).approveResult(1);

    const after = await token.balanceOf(escrowAddress);
    expect(before - after).to.equal(BigInt(PRICE));
  });
});
