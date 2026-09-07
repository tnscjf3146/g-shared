import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { artifacts, network } from "hardhat";
import { encodeDeployData, formatEther } from "viem";
import { readSSAFYConfig, requireDeploymentKey } from "./ssafy-config.js";

const { chainId, initialHolder } = readSSAFYConfig();
requireDeploymentKey();
assert.ok(!process.env.GSC_CONTRACT_ADDRESS?.trim(), "Clear GSC_CONTRACT_ADDRESS before preparing a deployment; verification must use the Ignition record.");
const connection = await network.create("ssafy");
try {
  const client = await connection.viem.getPublicClient();
  assert.equal(await client.getChainId(), chainId, "RPC chain ID does not match SSAFY_CHAIN_ID");
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
    chainId, deployer: wallet.account.address, initialHolder,
    supply: "1000000 GSC", gasPriceWei: "0", estimatedGas: gas.toString(),
    nativeBalance: formatEther(balance), estimatedFeeWei: "0",
  }, null, 2));
  console.log("Preflight passed. No transaction sent. Ignition parameters prepared.");
} finally {
  await connection.close();
}
