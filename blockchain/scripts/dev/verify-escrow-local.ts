import { LOCAL_TEST_CHAIN_ID, runEscrowVerify } from "../escrow-deployment.js";

// 로컬 예행 전용 진입점. test-escrow-deployment.mjs 가 띄운 하드햇 노드를 대상으로 한다.
// 운영 명령(verify:escrow:ssafy)은 이 경로를 거치지 않는다.
await runEscrowVerify(LOCAL_TEST_CHAIN_ID);
