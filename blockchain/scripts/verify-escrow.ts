import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { artifacts, network } from "hardhat";
import { getContract } from "viem";
import { ESCROW_PARAMETER_PATH, publicAddress, readChainConfig, readEscrowParameters } from "./ssafy-config.js";

/**
 * 배포 뒤 검증. 트랜잭션을 보내지 않는다.
 *
 * 온체인 값 셋이 배포 파라미터와 같은지 대조한다. 시한 둘은 immutable 이라
 * 여기서 어긋나면 고치는 길은 재배포뿐이다.
 */
const { chainId } = readChainConfig();
const { paymentToken, acceptTimeout, executionTimeout } = await readEscrowParameters();
let rawAddress = process.env.ESCROW_CONTRACT_ADDRESS?.trim();
if (!rawAddress) {
  const path = new URL(`../ignition/deployments/chain-${chainId}/deployed_addresses.json`, import.meta.url);
  const addresses = JSON.parse(await readFile(path, "utf8"));
  rawAddress = addresses["GPUJobEscrowModule#GPUJobEscrow"];
}
const address = publicAddress(rawAddress ?? "", "ESCROW_CONTRACT_ADDRESS / Ignition address");
const connection = await network.create("ssafy");
try {
  const client = await connection.viem.getPublicClient();
  assert.equal(await client.getChainId(), chainId, "RPC chain ID mismatch");
  const artifact = await artifacts.readArtifact("GPUJobEscrow");
  const code = await client.getCode({ address });
  // immutable 셋이 런타임 바이트코드에 박히므로 전체 문자열 비교는 할 수 없다.
  // 길이가 같은지만 보고, 값 자체는 아래에서 읽어 대조한다.
  assert.ok(code && code !== "0x", `No contract code at ${address} on chain ${chainId}`);
  assert.equal(code.length, artifact.deployedBytecode.length, "Deployed bytecode length does not match this GPUJobEscrow build");

  const escrow = getContract({ address, abi: artifact.abi, client });
  const [onchainToken, onchainAccept, onchainExecution, nextJobId] = await Promise.all([
    escrow.read.paymentToken(),
    escrow.read.acceptTimeout(),
    escrow.read.executionTimeout(),
    escrow.read.nextJobId(),
  ]);
  assert.equal(publicAddress(onchainToken as string, "on-chain paymentToken"), paymentToken, "On-chain paymentToken differs from the parameter file");
  assert.equal(onchainAccept, acceptTimeout, "On-chain acceptTimeout differs from the parameter file");
  assert.equal(onchainExecution, executionTimeout, "On-chain executionTimeout differs from the parameter file");
  assert.equal(nextJobId, 1n, "nextJobId must be 1 on a freshly deployed escrow");

  const result = {
    chainId,
    address,
    contractName: "GPUJobEscrow",
    parameterFile: ESCROW_PARAMETER_PATH,
    paymentToken,
    acceptTimeout: acceptTimeout.toString(),
    executionTimeout: executionTimeout.toString(),
    nextJobId: nextJobId.toString(),
    verifiedAt: new Date().toISOString(),
    abi: artifact.abi,
  };
  const output = new URL(`../deployments/ssafy-${chainId}/`, import.meta.url);
  await mkdir(output, { recursive: true });
  await writeFile(new URL("GPUJobEscrow.json", output), JSON.stringify(result, null, 2) + "\n");
  console.log(`Verified GPUJobEscrow: ${address} on chain ${chainId}`);
  console.log(`paymentToken ${paymentToken} / acceptTimeout ${acceptTimeout}s / executionTimeout ${executionTimeout}s / nextJobId ${nextJobId}`);
  console.log(`Public address and ABI exported to deployments/ssafy-${chainId}/GPUJobEscrow.json`);
} finally {
  await connection.close();
}
