// 한 바퀴를 순서대로 실행하며 상태와 잔액을 출력한다.
//
//   npx hardhat run scripts/e2e.js
//
// 백엔드 없이 CREATED 에서 COMPLETED 까지 도는 것을 확인한다.

const { ethers } = require("hardhat");

const PRICE = 10;
const EXEC_DEADLINE = 60 * 60;
const APPROVE_DEADLINE = 30 * 60;
const STATE = ["NONE", "CREATED", "FUNDED", "ASSIGNED", "SUBMITTED", "COMPLETED", "CANCELLED"];

async function main() {
  const [client, provider] = await ethers.getSigners();

  const MockSSF = await ethers.getContractFactory("MockSSF");
  const token = await MockSSF.deploy("Mock SSAFY", "mSSF", 1000);
  await token.waitForDeployment();

  const Escrow = await ethers.getContractFactory("GPUJobEscrow");
  const escrow = await Escrow.deploy(await token.getAddress(), EXEC_DEADLINE, APPROVE_DEADLINE);
  await escrow.waitForDeployment();

  const escrowAddress = await escrow.getAddress();

  console.log("배포 완료");
  console.log(`  token    ${await token.getAddress()}`);
  console.log(`  escrow   ${escrowAddress}`);
  console.log(`  의뢰자    ${client.address}`);
  console.log(`  공급자    ${provider.address}\n`);

  const show = async (label) => {
    const job = await escrow.jobs(1);
    const c = await token.balanceOf(client.address);
    const e = await token.balanceOf(escrowAddress);
    const p = await token.balanceOf(provider.address);
    console.log(
      `${label.padEnd(16)} state ${STATE[Number(job.state)].padEnd(10)}` +
        ` 의뢰자 ${String(c).padStart(4)}  에스크로 ${String(e).padStart(3)}  공급자 ${String(p).padStart(3)}`
    );
  };

  console.log("─".repeat(78));
  await show("초기");

  await escrow.connect(client).createJob(PRICE);
  await show("createJob");

  await token.connect(client).approve(escrowAddress, PRICE);
  await show("approve");

  await escrow.connect(client).fundJob(1);
  await show("fundJob");

  await escrow.connect(provider).acceptJob(1);
  await show("acceptJob");

  await escrow.connect(provider).submitResult(1, "0x" + "ab".repeat(32));
  await show("submitResult");

  await escrow.connect(client).approveResult(1);
  await show("approveResult");
  console.log("─".repeat(78));

  const job = await escrow.jobs(1);
  console.log(`\nresultHash  ${job.resultHash}`);
  console.log(`provider    ${job.provider}`);
  console.log(`price       ${job.price} SSF`);

  const ok =
    (await token.balanceOf(provider.address)) === BigInt(PRICE) &&
    (await token.balanceOf(escrowAddress)) === 0n &&
    Number(job.state) === 5;

  console.log(`\n한 바퀴 ${ok ? "성공" : "실패"}`);
  if (!ok) process.exitCode = 1;
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
