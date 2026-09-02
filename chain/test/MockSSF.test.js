const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("MockSSF", function () {
  let token, owner, alice, bob;

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();
    const MockSSF = await ethers.getContractFactory("MockSSF");
    token = await MockSSF.deploy("Mock SSF", "mSSF", 1000);
  });

  it("배포자가 초기 물량을 전부 갖는다", async function () {
    expect(await token.balanceOf(owner.address)).to.equal(1000);
    expect(await token.totalSupply()).to.equal(1000);
  });

  it("transfer 로 잔액이 옮겨진다", async function () {
    await token.transfer(alice.address, 100);
    expect(await token.balanceOf(owner.address)).to.equal(900);
    expect(await token.balanceOf(alice.address)).to.equal(100);
  });

  it("잔액보다 많이 보내면 실패한다", async function () {
    await expect(token.connect(alice).transfer(bob.address, 1)).to.be.reverted;
  });

  it("approve 하면 allowance 가 기록된다", async function () {
    await token.approve(alice.address, 50);
    expect(await token.allowance(owner.address, alice.address)).to.equal(50);
  });

  it("허락받은 자가 transferFrom 으로 남의 돈을 옮긴다", async function () {
    await token.approve(alice.address, 50);
    await token.connect(alice).transferFrom(owner.address, bob.address, 30);

    expect(await token.balanceOf(owner.address)).to.equal(970);
    expect(await token.balanceOf(bob.address)).to.equal(30);
    expect(await token.allowance(owner.address, alice.address)).to.equal(20);
  });

  it("허락 없이 transferFrom 하면 실패한다", async function () {
    await expect(
      token.connect(alice).transferFrom(owner.address, bob.address, 1)
    ).to.be.reverted;
  });

  it("허락받은 금액보다 많이 옮기면 실패한다", async function () {
    await token.approve(alice.address, 50);
    await expect(
      token.connect(alice).transferFrom(owner.address, bob.address, 51)
    ).to.be.reverted;
  });

  it("두 spender 의 allowance 는 서로 독립이다", async function () {
    await token.approve(alice.address, 10);
    await token.approve(bob.address, 5);
    expect(await token.allowance(owner.address, alice.address)).to.equal(10);
    expect(await token.allowance(owner.address, bob.address)).to.equal(5);
  });
});
