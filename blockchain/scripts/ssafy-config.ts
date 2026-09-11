import { getAddress, isAddress, zeroAddress } from "viem";
import { readFile } from "node:fs/promises";

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

/** RPC 주소와 Chain ID 만 읽는다. 토큰·에스크로 배포가 함께 쓴다. */
export function readChainConfig() {
  const rpc = required("SSAFY_RPC_URL");
  let validUrl = false;
  try { validUrl = ["http:", "https:"].includes(new URL(rpc).protocol); } catch {}
  if (!validUrl) throw new Error("SSAFY_RPC_URL must be an HTTP(S) URL.");
  const rawChainId = required("SSAFY_CHAIN_ID");
  const chainId = Number(rawChainId);
  if (!/^\d+$/.test(rawChainId) || !Number.isSafeInteger(chainId) || chainId <= 0) {
    throw new Error("SSAFY_CHAIN_ID must be a positive decimal integer.");
  }
  return { chainId };
}

export function readSSAFYConfig() {
  const { chainId } = readChainConfig();
  const initialHolder = publicAddress(required("GSC_INITIAL_HOLDER"), "GSC_INITIAL_HOLDER");
  return { chainId, initialHolder };
}

/** SSAFY 체인의 Chain ID. 에스크로 배포 대상을 코드에 못박는다. */
export const SSAFY_CHAIN_ID = 31221;

/**
 * 에스크로를 배포할 체인의 Chain ID 를 돌려준다.
 *
 * SSAFY_CHAIN_ID 환경 변수와 RPC 가 서로 같은지만 보면, 둘이 함께 잘못 설정되었을 때
 * 엉뚱한 체인에 배포된다. 기대값을 상수로 고정해 그 경로를 막는다.
 * 로컬 노드로 배포 경로를 예행하는 경우에만 ESCROW_EXPECT_CHAIN_ID 로 바꿀 수 있다.
 */
export function expectedEscrowChainId(): number {
  const override = process.env.ESCROW_EXPECT_CHAIN_ID?.trim();
  if (!override) return SSAFY_CHAIN_ID;
  const value = Number(override);
  if (!/^\d+$/.test(override) || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error("ESCROW_EXPECT_CHAIN_ID must be a positive decimal integer.");
  }
  return value;
}

export function requireDeploymentKey() {
  if (!/^0x[0-9a-fA-F]{64}$/.test(required("SSAFY_PRIVATE_KEY"))) {
    throw new Error("SSAFY_PRIVATE_KEY must be 0x followed by 64 hex characters.");
  }
}

/** uint64 시한 값을 검사한다. 배포 뒤에는 immutable 이라 고칠 수 없다. */
function timeout(value: unknown, name: string): bigint {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} in the escrow parameter file must be a positive integer number of seconds.`);
  }
  if (BigInt(value) > 2n ** 64n - 1n) throw new Error(`${name} exceeds uint64.`);
  return BigInt(value);
}

export const ESCROW_PARAMETER_PATH = "ignition/parameters/ssafy-escrow.json";

/**
 * 배포 파라미터 파일을 읽어 값 셋을 돌려준다.
 * preflight 와 verify 가 같은 파일을 읽어, 배포 전후로 같은 값인지 대조한다.
 */
export async function readEscrowParameters() {
  const path = new URL(`../${ESCROW_PARAMETER_PATH}`, import.meta.url);
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(path, "utf8"));
  } catch (cause) {
    throw new Error(`Cannot read ${ESCROW_PARAMETER_PATH}: ${(cause as Error).message}`);
  }
  const module_ = (parsed as Record<string, Record<string, unknown>>)?.GPUJobEscrowModule;
  if (!module_) throw new Error(`${ESCROW_PARAMETER_PATH} must contain a GPUJobEscrowModule object.`);
  return {
    paymentToken: publicAddress(String(module_.paymentToken ?? ""), "paymentToken"),
    acceptTimeout: timeout(module_.acceptTimeout, "acceptTimeout"),
    executionTimeout: timeout(module_.executionTimeout, "executionTimeout"),
  };
}
