import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { artifacts, network } from "hardhat";
import { formatUnits, getContract, parseUnits } from "viem";
import { publicAddress, readSSAFYConfig } from "./ssafy-config.js";

const { chainId, initialHolder } = readSSAFYConfig();
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
  assert.equal(await client.getChainId(), chainId, "RPC chain ID mismatch");
  const artifact = await artifacts.readArtifact("GCoin");
  const code = await client.getCode({ address });
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
