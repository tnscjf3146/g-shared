import { getAddress, isAddress, zeroAddress } from "viem";

export function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Set ${name} in blockchain/.env before using the SSAFY commands.`);
  return value;
}

export function publicAddress(value: string, name: string) {
  if (!isAddress(value) || getAddress(value) === zeroAddress) {
    throw new Error(`${name} must be a valid non-zero wallet/contract address.`);
  }
  return getAddress(value);
}

export function readSSAFYConfig() {
  const rpc = required("SSAFY_RPC_URL");
  let validUrl = false;
  try { validUrl = ["http:", "https:"].includes(new URL(rpc).protocol); } catch {}
  if (!validUrl) throw new Error("SSAFY_RPC_URL must be an HTTP(S) URL.");
  const rawChainId = required("SSAFY_CHAIN_ID");
  const chainId = Number(rawChainId);
  if (!/^\d+$/.test(rawChainId) || !Number.isSafeInteger(chainId) || chainId <= 0) {
    throw new Error("SSAFY_CHAIN_ID must be a positive decimal integer.");
  }
  const initialHolder = publicAddress(required("GSC_INITIAL_HOLDER"), "GSC_INITIAL_HOLDER");
  return { chainId, initialHolder };
}

export function requireDeploymentKey() {
  if (!/^0x[0-9a-fA-F]{64}$/.test(required("SSAFY_PRIVATE_KEY"))) {
    throw new Error("SSAFY_PRIVATE_KEY must be 0x followed by 64 hex characters.");
  }
}
