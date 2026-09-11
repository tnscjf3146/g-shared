import assert from "node:assert/strict";
import { artifacts, network } from "hardhat";
import { encodeDeployData, formatEther, getContract } from "viem";
import { ESCROW_PARAMETER_PATH, readChainConfig, readEscrowParameters, requireDeploymentKey } from "./ssafy-config.js";

/**
 * 에스크로 배포 전 점검. 트랜잭션을 보내지 않는다.
 *
 * 시한 둘은 배포 뒤 immutable 이라 고칠 수 없으므로, 배포 직전에 사람이 눈으로
 * 확인할 수 있도록 paymentToken · acceptTimeout · executionTimeout 을 반드시 출력한다.
 */
const { chainId } = readChainConfig();
requireDeploymentKey();
assert.ok(
  !process.env.ESCROW_CONTRACT_ADDRESS?.trim(),
  "Clear ESCROW_CONTRACT_ADDRESS before preparing a deployment; verification must use the Ignition record.",
);
const { paymentToken, acceptTimeout, executionTimeout } = await readEscrowParameters();
const connection = await network.create("ssafy");
try {
  const client = await connection.viem.getPublicClient();
  assert.equal(await client.getChainId(), chainId, "RPC chain ID does not match SSAFY_CHAIN_ID");
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
  const [symbol, decimals] = await Promise.all([token.read.symbol(), token.read.decimals()]);
  assert.equal(symbol, "GSC", "paymentToken symbol must be GSC");
  assert.equal(decimals, 18, "paymentToken decimals must be 18");

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
    deployer: wallet.account.address,
    parameterFile: ESCROW_PARAMETER_PATH,
    paymentToken,
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
