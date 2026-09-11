import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { artifacts, network } from "hardhat";
import { encodeDeployData, formatEther, formatUnits, getContract, parseUnits } from "viem";
import { publicAddress, readSSAFYConfig, requireDeploymentKey, requireTargetChain } from "./ssafy-config.js";

/**
 * GCoin 배포 전 점검과 배포 후 검증의 실제 로직.
 *
 * 에스크로와 같은 규칙을 따른다. 대상 체인은 환경 변수가 아니라 호출자가 인자로 정하며,
 * 운영 진입점(scripts/preflight-ssafy.ts · scripts/verify-ssafy.ts)은 SSAFY_CHAIN_ID
 * 상수를 넘긴다. 로컬 예행은 scripts/dev/ 아래 별도 진입점이 담당한다.
 */

/** 로컬 예행이 띄우는 하드햇 노드의 Chain ID. 운영 진입점은 이 값을 쓰지 않는다. */
export const LOCAL_TEST_CHAIN_ID = 31337;

/** 배포 전 점검. 트랜잭션을 보내지 않는다. */
export async function runGCoinPreflight(expectedChainId: number): Promise<void> {
  const chainId = requireTargetChain(expectedChainId);
  const { initialHolder } = readSSAFYConfig();
  requireDeploymentKey();
  assert.ok(
    !process.env.GSC_CONTRACT_ADDRESS?.trim(),
    "Clear GSC_CONTRACT_ADDRESS before preparing a deployment; verification must use the Ignition record.",
  );
  const connection = await network.create("ssafy");
  try {
    const client = await connection.viem.getPublicClient();
    assert.equal(await client.getChainId(), expectedChainId, `RPC chain ID does not match the target chain ${expectedChainId}`);
    const block = await client.getBlock();
    assert.equal(block.baseFeePerGas ?? 0n, 0n, "Non-zero base fee: check the zero-gas-price policy");
    const [wallet] = await connection.viem.getWalletClients();
    assert.ok(wallet, "A deployment wallet is required");
    const artifact = await artifacts.readArtifact("GCoin");
    const gas = await client.estimateGas({
      account: wallet.account.address,
      data: encodeDeployData({ abi: artifact.abi, bytecode: artifact.bytecode, args: [initialHolder] }),
      gasPrice: 0n,
      type: "legacy",
    });
    const balance = await client.getBalance({ address: wallet.account.address });
    const parameterDir = new URL("../ignition/parameters/", import.meta.url);
    await mkdir(parameterDir, { recursive: true });
    await writeFile(new URL("ssafy.json", parameterDir), JSON.stringify({
      GCoinModule: { initialHolder },
    }, null, 2) + "\n");
    console.log(JSON.stringify({
      chainId, targetChainId: expectedChainId, deployer: wallet.account.address, initialHolder,
      supply: "1000000 GSC", gasPriceWei: "0", estimatedGas: gas.toString(),
      nativeBalance: formatEther(balance), estimatedFeeWei: "0",
    }, null, 2));
    console.log("Preflight passed. No transaction sent. Ignition parameters prepared.");
  } finally {
    await connection.close();
  }
}

/** 배포 후 검증. 트랜잭션을 보내지 않으며 개인키 없이 실행할 수 있다. */
export async function runGCoinVerify(expectedChainId: number): Promise<void> {
  const chainId = requireTargetChain(expectedChainId);
  const { initialHolder } = readSSAFYConfig();
  let rawAddress = process.env.GSC_CONTRACT_ADDRESS?.trim();
  if (!rawAddress) {
    const path = new URL(`../ignition/deployments/chain-${chainId}/deployed_addresses.json`, import.meta.url);
    const addresses = JSON.parse(await readFile(path, "utf8"));
    rawAddress = addresses["GCoinModule#GCoin"];
  }
  const address = publicAddress(rawAddress ?? "", "GSC_CONTRACT_ADDRESS / Ignition address");
  const connection = await network.create("ssafy");
  try {
    const client = await connection.viem.getPublicClient();
    assert.equal(await client.getChainId(), expectedChainId, `RPC chain ID does not match the target chain ${expectedChainId}`);
    const artifact = await artifacts.readArtifact("GCoin");
    const code = await client.getCode({ address });
    // GCoin 에는 immutable 이 없어 런타임 바이트코드를 그대로 대조할 수 있다.
    assert.ok(code?.toLowerCase() === artifact.deployedBytecode.toLowerCase(), "Deployed bytecode does not match this GCoin build");
    const token = getContract({ address, abi: artifact.abi, client });
    const [name, symbol, decimals, totalSupply, holderBalance] = await Promise.all([
      token.read.name(), token.read.symbol(), token.read.decimals(),
      token.read.totalSupply(), token.read.balanceOf([initialHolder]),
    ]);
    assert.equal(name, "G Coin");
    assert.equal(symbol, "GSC");
    assert.equal(decimals, 18);
    assert.equal(totalSupply, parseUnits("1000000", 18));
    const result = {
      chainId, address, name, symbol, decimals, totalSupply: totalSupply.toString(),
      configuredInitialHolder: initialHolder, holderBalance: formatUnits(holderBalance, decimals),
      verifiedAt: new Date().toISOString(), abi: artifact.abi,
    };
    const output = new URL(`../deployments/ssafy-${chainId}/`, import.meta.url);
    await mkdir(output, { recursive: true });
    await writeFile(new URL("GCoin.json", output), JSON.stringify(result, null, 2) + "\n");
    console.log(`Verified ${name} (${symbol}): ${address} on chain ${chainId}`);
    console.log(`Configured initial holder balance: ${result.holderBalance} GSC (may decrease after transfers).`);
    console.log(`Public address and ABI exported to deployments/ssafy-${chainId}/GCoin.json`);
  } finally {
    await connection.close();
  }
}
