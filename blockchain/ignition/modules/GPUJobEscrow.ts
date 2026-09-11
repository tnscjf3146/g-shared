import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";

/**
 * 에스크로 배포 모듈.
 *
 * 시한 둘을 파라미터로 받는다. 개발 체인은 짧게(초 단위) 잡아 시한 만료를 몇 분 안에
 * 시험하고, 운영 배포는 acceptTimeout 10분 / executionTimeout 24시간을 쓴다.
 *
 * paymentToken 은 이미 배포된 GCoin 주소를 넘긴다. 넘기지 않으면 배포가 실패한다.
 */
export default buildModule("GPUJobEscrowModule", (m) => {
  const paymentToken = m.getParameter<string>("paymentToken");
  const acceptTimeout = m.getParameter<bigint>("acceptTimeout", 600n); // 10분
  const executionTimeout = m.getParameter<bigint>("executionTimeout", 86_400n); // 24시간

  const escrow = m.contract("GPUJobEscrow", [paymentToken, acceptTimeout, executionTimeout]);

  return { escrow };
});
