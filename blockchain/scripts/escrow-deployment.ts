import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { artifacts, network } from "hardhat";
import { encodeDeployData, formatEther, getContract, parseUnits } from "viem";
import { ESCROW_PARAMETER_PATH, publicAddress, readChainConfig, readEscrowParameters, requireDeploymentKey } from "./ssafy-config.js";

/**
 * 에스크로 배포 전 점검과 배포 후 검증의 실제 로직.
 *
 * 대상 체인은 환경 변수가 아니라 호출자가 인자로 정한다. 운영 진입점
 * (scripts/preflight-escrow.ts · scripts/verify-escrow.ts) 은 SSAFY_CHAIN_ID 상수를
 * 넘기므로, 환경 변수를 어떻게 설정해도 다른 체인으로 배포되지 않는다.
 * 로컬 예행은 scripts/dev/ 아래 별도 진입점이 LOCAL_TEST_CHAIN_ID 를 넘긴다.
 */

/** 로컬 예행이 띄우는 하드햇 노드의 Chain ID. 운영 진입점은 이 값을 쓰지 않는다. */
export const LOCAL_TEST_CHAIN_ID = 31337;

/** 컴파일 산출물이 알려 주는 immutable 치환 지점. astId 별로 오프셋과 길이를 담는다. */
type ImmutableReferences = Record<string, { start: number; length: number }[]>;

/**
 * immutable 값이 박히는 구간을 0 으로 덮어 런타임 바이트코드를 비교 가능한 형태로 만든다.
 *
 * 생성자 인자 셋이 런타임 코드에 직접 새겨지므로 배포된 코드와 빌드 산출물은 그대로는
 * 일치하지 않는다. 값이 들어가는 자리만 같은 방식으로 지우면 나머지 전 구간을 대조할 수 있다.
 */
function maskImmutables(bytecode: string, references: ImmutableReferences): string {
  const bytes = Buffer.from(bytecode.replace(/^0x/, ""), "hex");
  for (const slots of Object.values(references)) {
    for (const { start, length } of slots) {
      assert.ok(start + length <= bytes.length, "immutable reference is out of range");
      bytes.fill(0, start, start + length);
    }
  }
  return bytes.toString("hex");
}

/** 환경 변수가 가리키는 체인이 이 명령의 대상 체인과 같은지 확인한다. */
function requireTargetChain(expectedChainId: number): number {
  const { chainId } = readChainConfig();
  assert.equal(
    chainId,
    expectedChainId,
    `SSAFY_CHAIN_ID is ${chainId}, but this command only targets chain ${expectedChainId}`,
  );
  return chainId;
}

/**
 * 배포 전 점검. 트랜잭션을 보내지 않는다.
 *
 * 시한 둘은 배포 뒤 immutable 이라 고칠 수 없으므로, 배포 직전에 사람이 눈으로
 * 확인할 수 있도록 paymentToken · acceptTimeout · executionTimeout 을 반드시 출력한다.
 */
export async function runEscrowPreflight(expectedChainId: number): Promise<void> {
  const chainId = requireTargetChain(expectedChainId);
  requireDeploymentKey();
  assert.ok(
    !process.env.ESCROW_CONTRACT_ADDRESS?.trim(),
    "Clear ESCROW_CONTRACT_ADDRESS before preparing a deployment; verification must use the Ignition record.",
  );
  const { paymentToken, acceptTimeout, executionTimeout } = await readEscrowParameters();
  const connection = await network.create("ssafy");
  try {
    const client = await connection.viem.getPublicClient();
    assert.equal(await client.getChainId(), expectedChainId, `RPC chain ID does not match the target chain ${expectedChainId}`);
    const block = await client.getBlock();
    assert.equal(block.baseFeePerGas ?? 0n, 0n, "Non-zero base fee: check the zero-gas-price policy");
    const [wallet] = await connection.viem.getWalletClients();
    assert.ok(wallet, "A deployment wallet is required");

    // 결제 토큰이 이 체인에 실제로 있는 ERC20 인지 확인한다. 주소를 잘못 넣으면
    // 배포는 성공하고 예치만 전부 실패하는데, immutable 이라 되돌릴 수 없다.
    const tokenArtifact = await artifacts.readArtifact("GCoin");
    const tokenCode = await client.getCode({ address: paymentToken });
    assert.ok(tokenCode && tokenCode !== "0x", `No contract code at paymentToken ${paymentToken} on chain ${chainId}`);
    const token = getContract({ address: paymentToken, abi: tokenArtifact.abi, client });
    const [name, symbol, decimals, totalSupply] = await Promise.all([
      token.read.name(), token.read.symbol(), token.read.decimals(), token.read.totalSupply(),
    ]);
    assert.equal(name, "G Coin", "paymentToken name must be G Coin");
    assert.equal(symbol, "GSC", "paymentToken symbol must be GSC");
    assert.equal(decimals, 18, "paymentToken decimals must be 18");
    // GCoin 은 외부 발행도 소각도 없는 고정 공급이라, 총발행량이 다르면 다른 토큰이다.
    assert.equal(totalSupply, parseUnits("1000000", 18), "paymentToken total supply must be 1,000,000 GSC");

    const escrowArtifact = await artifacts.readArtifact("GPUJobEscrow");
    const gas = await client.estimateGas({
      account: wallet.account.address,
      data: encodeDeployData({
        abi: escrowArtifact.abi,
        bytecode: escrowArtifact.bytecode,
        args: [paymentToken, acceptTimeout, executionTimeout],
      }),
      gasPrice: 0n,
      type: "legacy",
    });
    const balance = await client.getBalance({ address: wallet.account.address });
    console.log(JSON.stringify({
      chainId,
      targetChainId: expectedChainId,
      deployer: wallet.account.address,
      parameterFile: ESCROW_PARAMETER_PATH,
      paymentToken,
      paymentTokenName: name,
      paymentTokenSymbol: symbol,
      acceptTimeout: acceptTimeout.toString(),
      executionTimeout: executionTimeout.toString(),
      gasPriceWei: "0",
      estimatedGas: gas.toString(),
      nativeBalance: formatEther(balance),
      estimatedFeeWei: "0",
    }, null, 2));
    console.log(`Deploy parameters — paymentToken ${paymentToken} / acceptTimeout ${acceptTimeout}s / executionTimeout ${executionTimeout}s`);
    console.log("Both timeouts are immutable after deployment. Confirm the three values above before deploying.");
    console.log("Preflight passed. No transaction sent.");
  } finally {
    await connection.close();
  }
}

/**
 * 배포 뒤 검증. 트랜잭션을 보내지 않는다.
 *
 * 온체인 값 셋이 배포 파라미터와 같은지 대조한다. 시한 둘은 immutable 이라
 * 여기서 어긋나면 고치는 길은 재배포뿐이다.
 */
export async function runEscrowVerify(expectedChainId: number): Promise<void> {
  const chainId = requireTargetChain(expectedChainId);
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
    assert.equal(await client.getChainId(), expectedChainId, `RPC chain ID does not match the target chain ${expectedChainId}`);
    const artifact = await artifacts.readArtifact("GPUJobEscrow");
    const code = await client.getCode({ address });
    assert.ok(code && code !== "0x", `No contract code at ${address} on chain ${chainId}`);
    // immutable 셋이 런타임 바이트코드에 박히므로 그대로는 일치하지 않는다. 값이 들어가는
    // 자리만 양쪽에서 지우고 나머지 전 구간을 대조해, 길이만 같은 다른 코드를 걸러낸다.
    const references = (artifact as unknown as { immutableReferences?: ImmutableReferences }).immutableReferences ?? {};
    assert.ok(Object.keys(references).length > 0, "Artifact has no immutableReferences; rebuild with `npm run build`");
    assert.equal(
      maskImmutables(code, references),
      maskImmutables(artifact.deployedBytecode, references),
      "Deployed runtime bytecode does not match this GPUJobEscrow build",
    );

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
      runtimeBytecodeVerified: true,
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
}
