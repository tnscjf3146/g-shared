import { runEscrowPreflight } from "./escrow-deployment.js";
import { SSAFY_CHAIN_ID } from "./ssafy-config.js";

// SSAFY 체인 배포 전 점검. 대상 체인은 상수로 고정되어 있어 환경 변수로 바꿀 수 없다.
await runEscrowPreflight(SSAFY_CHAIN_ID);
