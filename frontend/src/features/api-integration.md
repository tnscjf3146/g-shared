# API 연동 현황 — 프론트 ↔ 백엔드 (2026-09-15)

`fe-refactor/api-integration` 착수 전 조사. 기준: `frontend` develop(adbb681) · `back` develop(adbb681).

## 결론

**엔드포인트가 없어서 못 붙이는 프론트 API 는 0 건이다.** 프론트가 부르는 경로 28 개가 전부 백엔드에 있다.
마지막 공백이던 `GET/PATCH /users/me`(요청 ①)와 `GET /network/stats`(요청 ④)가 오늘 `adbb681` 로 들어오면서 메워졌다.

목업(MSW)은 **로컬 전용**이다 — `NEXT_PUBLIC_API_MOCKING=enabled` 일 때만 켜지고, 프론트 Docker 이미지는 이 값을 넣지 않으며 `.dockerignore` 가 `.env*` 를 통째로 뺀다. 배포본은 처음부터 실서버를 본다.
그래서 이번 리팩토링의 일은 "없는 API 를 기다리는 것" 도, "목업을 끄는 것" 도 아니라 **로컬에서 목업을 끈 채 실계약을 한 번 관통시켜, 배포본이 이미 겪고 있을 어긋남을 찾아내는 것**이다.

---

## 1. 전체 매핑

프론트는 브라우저에서 항상 같은 오리진 `/api` 로 호출하고(`next.config.ts` rewrite → `BACKEND_ORIGIN`), 보호 API 는 전부 `authFetch`(Bearer + 401 시 reissue 1 회)를 지난다.

### auth (`features/auth/api.ts`)

| 프론트 호출 | 백엔드 | 비고 |
| --- | --- | --- |
| `POST /auth/signup` | `AuthController` | |
| `POST /auth/login` | `LoginFilter`(`/api/auth/login`) | 컨트롤러가 아니라 시큐리티 필터 |
| `POST /auth/reissue` | `AuthController` | HttpOnly refresh 쿠키 |
| `POST /auth/logout` | `AuthController` | 유일하게 `authenticated()` 인 auth 경로 |
| `GET /users/me` | `UserController` | ✅ **2026-09-15 구현됨** — 프론트 주석은 아직 "미구현" |
| `PATCH /users/me` | `UserController` | ✅ 같음 |

### jobs (`features/jobs/api.ts`)

| 프론트 호출 | 백엔드 | 비고 |
| --- | --- | --- |
| `GET /jobs?status&page&size` | `JobController` | |
| `GET /jobs/{id}` | `JobController` | |
| `POST /jobs` | `JobController` | |
| `POST /jobs/{id}/cancel` | `JobController` | |
| `POST /jobs/gpu-options` | `JobController` | |
| `POST /jobs/estimate` | `JobController` | |
| `POST /jobs/{id}/fund` | `JobController` | FUNDED 200 / PENDING **202** |
| `POST /jobs/{id}/dataset-upload` | `JobController` | |
| `POST /jobs/{id}/dataset-upload/complete` | `JobController` | 본문 선택(`required = false`) — 프론트는 본문 없이 호출, OK |
| `POST /datasets` | `DatasetController` | |
| `GET /datasets/{id}` | `DatasetController` | |
| `GET /workloads` · `GET /base-models` | `CatalogController` | |
| `GET /gpus` | `GpuController` | `hourlyPrice` 는 항상 0(과금이 토큰 기준) |
| `PUT <서명 uploadUrl>` | `StorageController` `/api/storage/**` | 서명이 인증이라 Bearer 없이 원본 `fetch` |
| `POST /jobs/{id}/adapter-upload` | `JobController` | ✅ 2026-09-17 구현 — 입력 어댑터 `.tar` |
| `POST /jobs/{id}/adapter-upload/complete` | `JobController` | ✅ 같음. 본문 `{ sha256(0x 없음), sizeBytes }` + tar 형식 검사 |
| `PATCH /jobs/{id}/adapter-name` | `JobController` | ✅ 2026-09-17 구현 — 결과 어댑터 이름 변경(`job.md` §11-2) |

> **2026-09-17 갱신**: 어댑터 버전·이름·입력 어댑터 업로드(`fe-feature/adapter-versioning`)로 경로 3개가 늘었고 **같은 브랜치에서 백엔드까지 구현**했다.
> `POST /jobs` 는 `adapterName`·`parentJobId`·`hasAdapter` 를 받고, `JobDetail` 은 `hasAdapter`·`adapterUploaded`·`adapterName`·`adapterVersion`·`parentJobId` 를,
> `JobSummary` 는 `adapterName`·`adapterVersion` 을 돌려준다. 세 필드를 안 보내는 예전 요청은 그대로 동작한다(하위 호환 테스트 있음). 상세는 `job.md` §11.


### wallet (`features/wallet/api.ts`)

| 프론트 호출 | 백엔드 |
| --- | --- |
| `GET /users/me/wallet` | `UserWalletController` |
| `POST /users/me/wallet/challenge` | `UserWalletController` |
| `POST /users/me/wallet` | `UserWalletController` |
| `DELETE /users/me/wallet` | `UserWalletController` |

### workers (`features/workers/api.ts`)

| 프론트 호출 | 백엔드 | 비고 |
| --- | --- | --- |
| `GET /workers/me` | `WorkerController` | |
| `DELETE /workers/{id}` | `WorkerController` | |
| `GET /network/stats` | `NetworkController` | ✅ **2026-09-15 구현됨** — 프론트 주석은 아직 "미구현" |

### 백엔드에 있지만 프론트가 안 부르는 것 (부를 일 없음)

`POST /workers/register` · `POST /workers/authenticate` · `PUT /workers/{id}/operator|benchmark` · `/api/worker/jobs/**`(execution-context, result-upload) — 전부 **워커 프로그램(G-shared.exe)** 창구다. 웹은 관여하지 않는다.

### 백엔드를 안 거치는 호출

`features/wallet/ethereum.ts` 가 SSAFY 체인 RPC 로 직접 JSON-RPC 를 쏜다(잔액·컨트랙트 읽기). 연동 대상이 아니다.

---

## 2. 실제로 남은 갭 — 엔드포인트가 아니라 **계약·설정**

### 2-1. ~~거짓말하는 주석~~ → 정리됨(2026-09-15)

`features/auth/{api,schema}.ts` · `features/workers/{api,schema}.ts` · 세 도메인 `mocks/handlers.ts` · `accountView.tsx` 의 "백엔드 미구현" 9 곳을 지웠다. 응답 모양은 원래 일치했다(`MeResponse` = `meSchema`, `NetworkStatsResponse` = `networkStatsSchema`). `format.ts` 의 "(실패 사유 표시는 백엔드 응답 추가 후)" 도 — `failReason` 은 이미 오고 화면도 그린다.

### 2-2. 목업은 로컬만 — 검증이 없는 쪽은 배포본이다

MSW 는 브라우저(`mocks/msw-provider.tsx`)·노드(`instrumentation.ts`) 양쪽 모두 `NEXT_PUBLIC_API_MOCKING === 'enabled'` 일 때만 시작한다.
프론트 `Dockerfile` 은 이 변수를 설정하지 않고 `.dockerignore` 가 `.env*` 를 제외하므로 **배포 이미지에서는 구조적으로 꺼진다.** 로컬 `.env.local` 만 켜져 있다.

문제는 그 반대다 — **배포본은 줄곧 실서버를 봤는데 우리는 목업만 보고 개발했다.** 그래서 오늘 ①④ 가 들어오기 전 배포본에서는:

- `/account` → `GET /users/me` 404 → 프로필 카드가 "다시 시도" 오류 상태 (`accountView.tsx:38`)
- `/dashboard` → `GET /network/stats` 404 → 네트워크 카드만 `—` (의도된 흘려보내기, `dashboardView.tsx:34`)

지금은 둘 다 구현됐으니 이 두 건은 해소됐다. 다만 **로컬에서 목업을 끄고 관통시켜 본 기록이 없다** — `job.md §9` 의 "실서버 연동 상태" 도 2026-09-09 기준이라 이미 낡았다(없다던 카탈로그·estimate 는 지금 다 있다).

참고로 배포에서 `/api` 는 nginx 가 바로 `:8080` 으로 넘긴다(`infra/nginx/default`). Next 의 `rewrites` 는 로컬 편의용이고, 서버 사이드에서 백엔드를 부르는 코드는 현재 없다(`proxy.ts` 는 refresh 쿠키만 읽는다) — 즉 `fetcher.ts` 의 `BACKEND_ORIGIN` 분기는 지금 죽은 길이다.

### 2-3. 백엔드가 주는데 프론트 zod 가 버리는 필드

`z.object` 는 모르는 키를 조용히 **버린다**(에러 아님). 지금 버려지는 것들:

**`GET /jobs/{id}` (10 개)**

| 버려지는 필드 | 쓸 곳 |
| --- | --- |
| ~~`datasetUploaded`~~ | ✅ 수용(2026-09-15). **예치 전 필수 조건** — false 면 상세가 예치 패널 대신 "취소 후 다시 등록" 안내를 띄운다. 이걸 안 보면 MetaMask 로 GSC 를 먼저 잠근 뒤 서버가 409 로 거절해 돈만 묶인다 |
| `fundTxHash` · `acceptTxHash` · `submitTxHash` · `abortTxHash` | 체인 화면(`jobs/[id]/chain`)이 **상태 배지만** 그린다 — 탐색기 링크 재료가 이미 오는데 안 쓴다 |
| `reconciliationReason` | 온체인/DB 불일치 사유 |
| `progressStep` · `progressTotalSteps` · `progressElapsedSec` | 진행률을 step 기준으로 더 정확히 |
| `resultObjectKey` | |

**`POST /jobs` 응답 (7 개)** — `externalRef` · `priceWei` · `providerWallet` · `clientWallet` · `chainId` · `tokenAddress` · `escrowAddress`.
예치에 필요한 값이 **생성 응답에 이미 다 실려 오는데** `createJobResponseSchema` 가 5 개만 받고 버린다 → 예치하려면 `GET /jobs/{id}` 를 한 번 더 부른다(왕복 1 회 낭비).

**`GET /workers/me`** — `wslAvailable` · `clientVersion` · `benchmarkVersion` · `operatorAddress`.
**`POST /jobs/{id}/dataset-upload`** — `objectKey` · `expiresAt`(만료 시각을 몰라 재발급 판단 불가).

### 2-4. ~~에러 코드 매핑 구멍~~ → 정리됨(2026-09-15)

예치 5 종(`PROVIDER_WALLET_MISSING` · `ESCROW_FUNDING_REJECTED` · `FUND_TX_ALREADY_USED` · `INVALID_ESCROW_JOB_ID` · `ESCROW_UNAVAILABLE`)과 업로드 5 종(`DATASET_NOT_UPLOADED` · `DATASET_OBJECT_NOT_FOUND` · `DATASET_UPLOAD_MISMATCH` · `DATASET_METADATA_MISMATCH` · `DATASET_OBJECT_KEY_MISMATCH`)을 `features/jobs/errors.ts` 에 넣었다. `workers/errors.ts` 의 `FORBIDDEN` → `WORKER_FORBIDDEN` 오타도.

같이 고친 것 — **예치 패널의 서버 확인 실패 처리**. 전엔 어떤 실패든 "서버 확인이 지연되고 있습니다 — 재시도" 였다. 확정 거절(4xx)까지 그렇게 안내하면 사용자가 재시도를 무한 반복하고, 'error' 로 떨어지면 [다시 예치 시도] 가 새 트랜잭션을 보내 **두 번 예치**한다. 이제 `isTransientFundError`(503 `ESCROW_UNAVAILABLE` · 429 · 5xx · 네트워크 단절)만 재시도 안내, 그 외는 `rejected` 단계 — 예치 버튼 없이 사유 + 트랜잭션 해시만 남긴다.

남은 미매핑: `ACCESS_DENIED`(auth) — 지금 화면에서 나올 경로가 없어 두었다.

### 2-5. `job.md §8-0` 요청 중 아직 안 온 것

| 요청 | 상태 |
| --- | --- |
| ① `JobDetailResponse.gpuModel` | ❌ 미반영 — 상세는 여전히 `assignedWorkerId`(워커 ID)를 보여준다 |
| ② `trainingPriority` → `autoMode` 이름 통일 | ❌ 갈린 채 — 견적은 `autoMode`, 주문은 `trainingPriority` |
| ③ `effectiveTokens` 를 서버가 계산 | ❌ 프론트가 `totalTokens`(문자 비율 근사)를 그대로 보낸다 — **과금 기준값인데 근사치** |
| ④ `hourlyPrice` 표시 제거 | ✅ 완료(백엔드는 계속 0 을 보냄) |
| `failReason` 노출 | ✅ 백엔드에 있고 프론트도 받는다 |
| 상세에 데이터셋 메타 | ✅ `GET /datasets/{id}` 로 우회 해결 |

### 2-6. 배포 백엔드 왕복 확인(2026-09-15, 비인증 구간)

`https://j15e204.p.ssafy.io/api` 에 직접 쏴 본 결과 — 도달·에러 봉투·코드가 전부 프론트 기대와 맞는다.

| 호출 | 응답 | 프론트 |
| --- | --- | --- |
| `GET /workloads` · `/users/me` · `/network/stats` (토큰 없이) | 401 `{code:'UNAUTHORIZED', message:'인증이 필요합니다.'}` | `fetcher` 가 `ApiError(401,'UNAUTHORIZED')` 로 → `authFetch` 의 reissue 경로 |
| `POST /auth/login` 틀린 비번 | 401 `INVALID_CREDENTIALS` | `auth/errors.ts` 매핑 O |
| `POST /auth/signup` 잘못된 이메일 | 400 `INVALID_INPUT` "이메일 형식이 아닙니다." | 서버 메시지 그대로 노출(설계대로) |
| `POST /auth/reissue` 쿠키 없이 | 401 `INVALID_TOKEN` | `reissue()` 가 세션 표시를 지우고 guest 로 |

인증 뒤 구간(카탈로그 → 견적 → 생성 → 업로드 → 예치)은 계정이 필요해 아직 — 백엔드 DTO 소스와 zod 를 필드 단위로 대조한 결과는 §1·§2-3 대로 일치한다.

### 2-7. 자잘한 것

- `POST /jobs/{id}/fund` 는 PENDING 일 때 **202** 를 준다. `fetcher` 는 상태 코드를 버리고 본문 `status` 로만 판단 — 동작은 맞지만 코드로 구분하는 편이 명확하다.
- `FundJobRequest.escrowJobId`(체인 미설정 로컬 개발용)를 프론트가 안 보낸다 → 로컬에선 온체인 번호가 null 로 기록된다.
- `fetcher` 는 타임아웃·재시도가 없다. 네트워크가 끊기면 `fetch` 가 걸린 채 멈춘다.

---

## 3. 이 브랜치에서 할 일

1. [ ] **로컬에서 목업 끄고 실서버 관통** — 회원가입 → 로그인 → 카탈로그 → 견적 → Job 생성 → 데이터셋 업로드 → 예치 → 상세. 배포본이 이미 겪는 길이므로, 깨지는 지점을 여기 기록한다. (비인증 구간은 §2-6 로 확인, 인증 구간은 계정 필요)
2. [x] 거짓 주석 정리(2-1) + `WORKER_FORBIDDEN` 오타(2-4)
3. [x] 예치·업로드 에러 코드 매핑 + 예치 패널 확정 거절 처리(2-4)
4. [x] `datasetUploaded` 수용 — 상세가 예치 패널을 닫고 안내(2-3). 목업도 같은 흐름(생성 false → complete 통과 true → /fund 가 false 면 409)
5. [ ] `createJobResponseSchema` 확장(2-3) — 생성 직후 예치로 바로 넘어가 왕복 1 회 제거. 위저드가 상세로 이동해 다시 받으므로 지금은 동작에 영향 없음 → 보류
6. [ ] (선택) 체인 화면에 tx 해시·탐색기 링크, `progressStep` 기반 진행률
7. [ ] 백엔드 협의 — `gpuModel`(job.md ①), `autoMode`/`trainingPriority` 이름(②), `effectiveTokens` 서버 계산(③)
