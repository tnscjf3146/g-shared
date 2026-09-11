/**
 * 로컬 개발 체인을 한 번에 준비한다.
 *
 * 워커와 백엔드가 온체인 연동을 개발하려면 붙을 컨트랙트가 필요한데, 에스크로는
 * 아직 SSAFY 체인에 배포되어 있지 않다. 그래서 로컬에 같은 구성을 세운다.
 *
 * SSAFY 체인 대신 로컬을 쓰는 결정적인 이유는 **시각을 조작할 수 있다**는 점이다.
 * abortJob 과 시한 만료 경로를 시험하려면 시계를 앞으로 돌려야 하는데 SSAFY 체인에서는
 * 못 한다. 여기서는 시한을 초 단위로 짧게 잡아 몇 분 안에 전 경로를 확인한다.
 *
 * 먼저 다른 터미널에서 노드를 띄운다.
 *
 *   npx hardhat node
 *
 * 그다음 이 스크립트를 돌린다.
 *
 *   npm run dev:chain
 *
 * 끝나면 deployments/localhost/dev-chain.json 에 주소와 계정이 적힌다.
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { network } from "hardhat";
import { formatUnits, parseUnits } from "viem";

/** 개발용 시한. 짧게 잡아야 시한 만료를 기다릴 수 있다. */
const ACCEPT_TIMEOUT = 60n; // 1분
const EXECUTION_TIMEOUT = 300n; // 5분

/** 의뢰자에게 나눠 줄 GSC. 데모를 여러 번 돌려도 남을 만큼 준다. */
const CLIENT_ALLOWANCE = parseUnits("10000", 18);

async function main() {
  const connection = await network.connect({ network: "localhost" });
  const { viem } = connection;

  const publicClient = await viem.getPublicClient();
  const chainId = await publicClient.getChainId();

  if (chainId === 31221) {
    throw new Error(
      "SSAFY 체인에 붙어 있습니다. 이 스크립트는 로컬 개발 체인 전용입니다.",
    );
  }

  const [deployer, client, provider, operator, keeper] = await viem.getWalletClients();

  console.log(`체인 ID ${chainId}\n`);

  // ── 1. GCoin ────────────────────────────────────────────────
  // 배포자가 100만 GSC 를 전부 갖고 시작한다. 외부 발행 함수가 없어
  // 여기서 나눠 주지 않으면 의뢰자가 예치할 수 없다.
  const token = await viem.deployContract("GCoin", [deployer.account.address]);
  console.log(`GCoin        ${token.address}`);

  // ── 2. 에스크로 ─────────────────────────────────────────────
  const escrow = await viem.deployContract("GPUJobEscrow", [
    token.address,
    ACCEPT_TIMEOUT,
    EXECUTION_TIMEOUT,
  ]);
  console.log(`Escrow       ${escrow.address}`);
  console.log(`  acceptTimeout    ${ACCEPT_TIMEOUT}초`);
  console.log(`  executionTimeout ${EXECUTION_TIMEOUT}초\n`);

  // ── 3. GSC 분배 ─────────────────────────────────────────────
  // 공급자와 키퍼에게는 주지 않는다. 공급자는 대금을 받는 쪽이고,
  // 키퍼는 settleTimeout 만 부르므로 토큰이 필요 없다.
  await token.write.transfer([client.account.address, CLIENT_ALLOWANCE], {
    account: deployer.account,
  });
  const clientBalance = await token.read.balanceOf([client.account.address]);
  console.log(`의뢰자 잔액   ${formatUnits(clientBalance, 18)} GSC`);

  // ── 4. operator 승인 ────────────────────────────────────────
  // 실제 서비스에서는 소유자가 화면에서 MetaMask 로 서명하는 자리다.
  await escrow.write.setOperator([operator.account.address, true], {
    account: provider.account,
  });
  const approved = await escrow.read.isOperator([
    provider.account.address,
    operator.account.address,
  ]);
  console.log(`operator 승인 ${approved ? "완료" : "실패"}\n`);

  // ── 5. 기록 ─────────────────────────────────────────────────
  const record = {
    chainId,
    rpcUrl: "http://127.0.0.1:8545",
    generatedAt: new Date().toISOString(),
    contracts: {
      GCoin: token.address,
      GPUJobEscrow: escrow.address,
    },
    timeouts: {
      acceptTimeout: Number(ACCEPT_TIMEOUT),
      executionTimeout: Number(EXECUTION_TIMEOUT),
    },
    accounts: {
      deployer: deployer.account.address,
      client: client.account.address,
      provider: provider.account.address,
      operator: operator.account.address,
      keeper: keeper.account.address,
    },
    note: "로컬 개발 전용. 노드를 다시 띄우면 주소가 바뀝니다.",
  };

  mkdirSync("deployments/localhost", { recursive: true });
  writeFileSync(
    "deployments/localhost/dev-chain.json",
    `${JSON.stringify(record, null, 2)}\n`,
    "utf8",
  );

  console.log("계정");
  console.log(`  의뢰자    ${record.accounts.client}`);
  console.log(`  정산 주소  ${record.accounts.provider}`);
  console.log(`  워커 키    ${record.accounts.operator}`);
  console.log(`  키퍼      ${record.accounts.keeper}`);
  console.log(`\n기록  deployments/localhost/dev-chain.json`);
  console.log(`다음  npm run dev:agent`);

  await connection.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
