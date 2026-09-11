import hardhatToolboxViem from "@nomicfoundation/hardhat-toolbox-viem";
import { configVariable, defineConfig } from "hardhat/config";
import { loadEnvFile } from "node:process";
import { existsSync } from "node:fs";

const envFile = new URL("./.env", import.meta.url);
if (existsSync(envFile)) loadEnvFile(envFile);

const compiler = {
  version: "0.8.28",
  settings: {
    evmVersion: "paris",
    optimizer: { enabled: true, runs: 200 },
  },
};

export default defineConfig({
  plugins: [hardhatToolboxViem],
  networks: {
    // 로컬 개발 체인. `npx hardhat node` 로 띄운 노드에 붙는다.
    localhost: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
    },
    ssafy: {
      type: "http",
      chainType: "l1",
      url: configVariable("SSAFY_RPC_URL"),
      chainId: process.env.SSAFY_CHAIN_ID ? Number(process.env.SSAFY_CHAIN_ID) : undefined,
      accounts: process.env.SSAFY_PRIVATE_KEY ? [configVariable("SSAFY_PRIVATE_KEY")] : [],
      gasPrice: 0n,
      ignition: { gasPrice: 0n, disableFeeBumping: true },
    },
  },
  solidity: { profiles: { default: compiler, production: compiler } },
});
