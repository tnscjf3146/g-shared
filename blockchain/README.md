# GShared Blockchain — G Coin

현재 구현 범위는 G Coin ERC-20 토큰, 단위 테스트와 로컬 Ignition 배포 모듈이다. Escrow와 SSAFY 체인 배포는 다음 단계다.

## 토큰 정책

| 항목 | 값 |
| --- | --- |
| 컨트랙트 | `GCoin` |
| 이름 / 심볼 | `G Coin` / `GSC` |
| decimals | `18` |
| 총발행량 | `1,000,000 GSC` = `10^24` 최소 단위 |
| 초기 수령자 | 생성자 인자 `initialHolder` |
| 추가 발행 | 외부 mint 함수 없음 |
| 소각 / 관리자 권한 / 업그레이드 | 제공하지 않음 |

초기 수령자는 배포자와 다른 운영 지갑일 수 있다. 영 주소(`address(0)`)를 지정하면 OpenZeppelin ERC-20이 배포를 거부한다. 잔액, 승인 한도와 전송 금액은 모두 정수 최소 단위로 처리한다.

## 실행

Node.js 24 LTS를 권장한다. Node.js 24.18.0 / npm 11.16.0에서 검증했다. 아래 명령은 `blockchain/` 디렉터리에서 실행한다.

```shell
npm ci
npm run check
```

`check`는 컴파일 → TypeScript 타입 검사 → 테스트를 순서대로 실행한다. 생성된 ABI 타입을 사용하므로 최초 타입 검사 전에 컴파일이 필요하다. 의존성은 정확한 버전과 `package-lock.json`으로 고정한다.

```shell
npm run build
npm run typecheck
npm test
```

Solidity는 `0.8.28`, EVM 타깃은 프로젝트 설계의 `paris`를 사용한다. `artifacts/`, `cache/`, `node_modules/`는 생성물이므로 커밋하지 않는다.

## 테스트 범위

`test/GCoin.ts`는 Hardhat의 로컬 네트워크와 테스트 지갑을 사용한다.

- 토큰 이름, 심볼, decimals 및 지정 수령자의 전체 초기 물량
- 영 주소로 배포 시 실패
- 소수 단위 전송, Transfer 이벤트와 총발행량 보존
- 승인 자체로 잔액이 이동하지 않는지 및 위임 전송 후 한도 감소
- 미승인 전송, 승인 한도 초과, 잔액 초과 차단
- 위임 전송 실패 시 잔액과 승인 한도의 원상 유지
- 영 주소로 직접·위임 전송 차단
- 배포자·초기 수령자·제3자의 mint 호출 차단
- Ignition 배포 모듈의 초기 수령자 인자 적용

## 로컬 배포

```shell
npm run deploy:local
```

기본 설정은 일회성 로컬 시뮬레이션 네트워크다. 실제 SSAFY 체인에 배포되지 않으며 프로세스 종료 후 해당 체인 상태는 유지되지 않는다. 기본 초기 수령자는 로컬 첫 번째 테스트 계정이다.

`ignition/modules/GCoin.ts`의 `GCoinModule.initialHolder` 파라미터로 다른 수령자를 지정할 수 있다. 이 경로는 테스트에서도 검증한다.

실제 체인 배포 시에는 운영 지갑 주소, RPC, chain ID와 가스 설정을 확인한 후 HTTP 네트워크 설정을 추가해야 한다. 현재 사용자 제공 네트워크 정책은 가스 가격 0이며, 로컬 테스트의 가스 정책과 구분한다.

## 다음 단계

GSC를 `paymentToken`으로 사용하는 Escrow를 구현하고 예치·권한 검사·정산 테스트를 추가한다. Provider 선정 방식은 팀과 확정한 정책을 컨트랙트 권한 검사에 반영한다.

## 참고

- [Hardhat 3 시작하기](https://hardhat.org/docs/getting-started)
- [OpenZeppelin ERC-20](https://docs.openzeppelin.com/contracts/5.x/erc20)
