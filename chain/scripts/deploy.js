// 토큰과 에스크로를 배포하고 주소·ABI 를 파일로 남긴다.
//
//   npx hardhat run scripts/deploy.js --network localhost
//   npx hardhat run scripts/deploy.js --network ssafy
//
// 실체인에서는 MockSSF 를 배포하지 않고 SSF_ADDRESS 를 그대로 쓴다.

const fs = require("fs");
const path = require("path");
const { ethers, network, artifacts } = require("hardhat");

const SSF_ADDRESS = "0xeC5e4d81A0ee55f1d6B6A66Af18e86dcbb191F87"; // decimals 0
const EXEC_DEADLINE = 60 * 60;      // 수락 후 제출까지 1시간
const APPROVE_DEADLINE = 30 * 60;   // 제출 후 승인까지 30분
const MOCK_SUPPLY = 1_000_000;

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log(`network   ${network.name}`);
  console.log(`deployer  ${deployer.address}`);

  // 로컬에서만 토큰을 새로 배포한다. 실체인에서는 SSF 를 그대로 쓴다.
  let tokenAddress;
  if (network.name === "ssafy") {
    tokenAddress = SSF_ADDRESS;
    console.log(`token     ${tokenAddress}  (실제 SSF)`);
  } else {
    const MockSSF = await ethers.getContractFactory("MockSSF");
    const token = await MockSSF.deploy("Mock SSAFY", "mSSF", MOCK_SUPPLY);
    await token.waitForDeployment();
    tokenAddress = await token.getAddress();
    console.log(`token     ${tokenAddress}  (MockSSF, supply ${MOCK_SUPPLY})`);
  }

  const Escrow = await ethers.getContractFactory("GPUJobEscrow");
  const escrow = await Escrow.deploy(tokenAddress, EXEC_DEADLINE, APPROVE_DEADLINE);
  await escrow.waitForDeployment();
  const escrowAddress = await escrow.getAddress();

  console.log(`escrow    ${escrowAddress}`);
  console.log(`deadline  exec ${EXEC_DEADLINE}s · approve ${APPROVE_DEADLINE}s`);

  writeDeployment(escrowAddress, tokenAddress, deployer.address);
  await writeAbi();
}

function writeDeployment(escrow, token, deployer) {
  const out = path.join(__dirname, "..", "deployments", `${network.name}.json`);
  fs.writeFileSync(
    out,
    JSON.stringify(
      {
        network: network.name,
        chainId: network.config.chainId ?? null,
        escrow,
        token,
        deployer,
        execDeadline: EXEC_DEADLINE,
        approveDeadline: APPROVE_DEADLINE,
      },
      null,
      2
    ) + "\n"
  );
  console.log(`\n주소 저장  deployments/${network.name}.json`);
}

// 컴파일 결과에서 ABI 만 뽑아 abi/ 에 남긴다.
// 프론트와 백엔드 인덱서, 공급자 노드가 이 파일을 쓴다.
async function writeAbi() {
  for (const name of ["GPUJobEscrow", "MockSSF"]) {
    const { abi } = await artifacts.readArtifact(name);
    const out = path.join(__dirname, "..", "abi", `${name}.json`);
    fs.writeFileSync(out, JSON.stringify(abi, null, 2) + "\n");
    console.log(`ABI 저장   abi/${name}.json`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
