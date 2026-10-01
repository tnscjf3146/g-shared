# 프론트엔드 테스트 도입 계획 — Vitest · Playwright

| 항목 | 값 |
| --- | --- |
| 브랜치 | `fe-test/vitest-playwright` (`origin/develop` `f06057e` 에서 분기) |
| Jira | 에픽 [S15P21E204-250](https://ssafy.atlassian.net/browse/S15P21E204-250) · 스토리 [S15P21E204-260](https://ssafy.atlassian.net/browse/S15P21E204-260) Vitest 도입과 순수 로직 테스트 · [S15P21E204-261](https://ssafy.atlassian.net/browse/S15P21E204-261) Playwright E2E |
| 작성 | 2026-10-01. 같은 날 사용자 승인(§7 확정 결정). 단계별로 구현하고 단계마다 멈춘다 |
| 리뷰어 | 프론트 담당 조가을 님 — `package.json`·`pnpm-lock.yaml` 을 바꾸므로 두 MR 모두 리뷰어로 건다 |

---

## 1. 목표와 포트폴리오에서 보여 줄 것

### 목표

1. 테스트가 0개인 `frontend/` 에 **실행 도구부터** 들인다. 로컬에서 `pnpm test` 한 줄로 돌아가게 한다.
2. 돈과 데이터에 직접 닿는 **순수 로직**을 먼저 덮는다. 데이터셋 토큰 근사·SHA-256, 어댑터 tar 사전 검사, zod 스키마, 상태 라벨, SSE 프레임 파서와 순서 보호가 대상이다.
3. 이미 있는 **MSW 목업을 테스트에서도 재사용**한다. 목업을 새로 만들지 않는다.
4. Playwright 로 **로그인 → 위저드 → 작업 생성 → 지갑 연결 → 예치 → FUNDED** 흐름을 브라우저에서 끝까지 돌린다. MetaMask 와 체인 RPC 는 테스트 안에서 흉내 낸다.
5. 위 모든 작업이 **운영 배포(`pnpm install --frozen-lockfile` → `pnpm build`)를 깨지 않는다.** 단계마다 배포와 같은 명령으로 확인한다.

### 포트폴리오에서 보여 줄 것

| 보여 줄 것 | 근거가 되는 산출물 |
| --- | --- |
| 테스트 0 → N 개, 커버리지 숫자(대상 파일 기준) | 단계 기록 표(§8), `coverage/index.html` 캡처 |
| "목업을 두 번 만들지 않았다" — 개발용 MSW 핸들러를 Node 테스트(`msw/node`)와 브라우저 E2E(Service Worker)에서 그대로 쓴 설계 | §2-3, 단계 3·5 |
| 파서·순서 보호처럼 **버그가 났던 자리**를 테스트로 고정한 사례 — 주석에 남은 과거 사고(타임스탬프 문자열 비교, 다른 작업의 종료 이벤트 잔류)를 회귀 테스트로 옮김 | 단계 3 |
| 지갑 서명을 사람 없이 재현한 E2E — `window.ethereum` 스텁이 받은 calldata 를 해독해 **승인 금액 = 화면 금액**을 검증 | 단계 7 |
| 조사로 찾은 신뢰 경계 문제(화면 금액과 서명 금액을 대조하지 않음)를 **실패하는 테스트로 증명**하고 수정은 담당자에게 넘긴 과정 | 단계 7 `test.fixme` |
| Playwright trace, Wireshark 로 "목업 모드에서는 /api 요청이 선 위로 나가지 않는다"를 눈으로 확인한 기록 | 단계 5 직접 해 보기 |

---

## 2. 지금 상태

### 2-1. 기준선 (2026-10-01, 이 브랜치 `f06057e`, 로컬 WSL)

| 명령 | 결과 |
| --- | --- |
| `corepack pnpm -v` | `11.20.0` (`package.json` 의 `packageManager` 와 같음. 전역 `pnpm` 은 없어 `corepack pnpm` 으로 실행) |
| `node -v` | `v24.18.0` (Docker 빌드는 `node:22-alpine`) |
| `corepack pnpm install --frozen-lockfile --config.strict-dep-builds=false` | 통과, 48.7초 |
| `corepack pnpm build` | 통과. 라우트 16개 생성, 타입 검사 포함 |
| `corepack pnpm exec tsc --noEmit` | 통과(오류 0) |
| `corepack pnpm lint` | **이미 실패한다. 오류 10개, 경고 223개.** 오류 9개는 벤더 파일 `public/draco/*.js`, 1개는 `src/features/jobs/use-job-realtime.ts:77`(`react-hooks/set-state-in-effect`) |
| 테스트 | 0개, 실행 도구 없음 |
| `tools/adapter-tar-check.mjs` | 유일한 자동 점검. 프레임워크 없이 `node:assert` 로 `checkAdapterTar` 를 검사하는 손 스크립트 |

> lint 는 배포(Dockerfile)가 돌리지 않는다. 이번 작업은 **기존 오류 10개를 늘리지 않는 것**을 조건으로 삼고, 기존 오류는 고치지 않는다(범위 밖, 담당자 판단). 새 파일만 따로 lint 해서 오류 0을 확인한다.

### 2-2. 테스트 대상 후보 — 파일을 직접 읽고 판정

| 파일 | 줄 수 | 판정 | 근거 |
| --- | --- | --- | --- |
| `src/features/jobs/format.ts` | 142 | **바로 시험 가능 (순수)** | `jobStatusMeta`·`jobStatusSentence`·`formatDuration`·`formatCost`·`formatBytes`·`suggestAdapterName`·`adapterLabel`·`adapterFileName` 이 모두 입력 → 출력 함수다. React·네트워크 의존이 없다. 규칙이 주석에 명시돼 있어 기대값을 코드가 아니라 **주석의 정책**에서 뽑을 수 있다(예: "사용자가 시키지 않은 취소는 전부 실패로", "RUNNING 100% 는 블록체인 기록 중 문장"). `formatDateTime` 은 `Intl` 과 로컬 시간대에 기대므로 `TZ` 를 고정해야 한다 |
| `src/features/jobs/schema.ts` | 341 | **바로 시험 가능 (순수)** | `zod/mini` 스키마 선언뿐이다. 경계값이 분명하다: `adapterNameSchema` 정규식(공백으로 끝나기·경로 구분자·60자), `positiveInt`, `customConfigFormSchema` 의 coerce(빈 문자열 → 0 → 거절), `jobDetailSchema` 의 `priceWei` 숫자 문자열·`hexAddress`, `resultVerificationSchema`·`jobOnchainSchema` 의 "키 하나 빠진 정상 응답이 통째로 죽지 않게" 한 optional·nullable 설계. 후자는 주석에 "제3자 리뷰 차단 #1" 사고가 적혀 있어 회귀 테스트 가치가 크다 |
| `src/features/jobs/dataset-analysis.ts` | 111 | **바로 시험 가능.** `estimateTokens`·`textOfJson`·`sampleText`·`summarize` 는 순수, `hashFile`·`analyzeDataset` 는 `File`·`crypto.subtle`·`TextDecoderStream` 이 필요 | Node 24 에는 `File`·`Blob.stream()`·`TextDecoderStream`·`crypto.subtle` 이 전역으로 있어 **node 환경**에서 그대로 돈다. SHA-256 은 알려진 벡터(`"abc"` → `0xba7816bf…`)로 검증하고, `back datasets.data_hash CHAR(66)` 형식(`0x` + 64자)을 확인한다. 스트리밍 경로는 청크 경계에서 줄이 잘리는 경우(`rest` 이어 붙이기), CSV 헤더 건너뛰기, 빈 줄, 빈 데이터셋(`EMPTY_DATASET`)을 본다. `TOO_BIG_FOR_HASH` 는 `arrayBuffer()` 가 던지는 `File` 대역으로 재현한다 |
| `src/features/jobs/adapter-tar.ts` | 55 | **바로 시험 가능** | `checkAdapterTar(file)` 은 `File.slice().arrayBuffer()` 만 쓴다. `tools/adapter-tar-check.mjs` 에 이미 tar 헤더를 손으로 만드는 함수와 11개 단언이 있어 **그대로 Vitest 로 옮길 수 있다.** 빠진 경우를 보탠다: 1 GiB 초과, `MAX_ENTRIES`(64) 초과, 깨진 크기 필드(8진수 아님) |
| `src/features/jobs/use-job-realtime.ts` | 145 | **파서·순서 보호는 순수하지만 export 되어 있지 않다.** 훅 본체는 jsdom + MSW 로 시험 | `parseFrame(frame)`·`isStale(prev, next)` 은 모듈 내부 함수다. 두 가지 길이 있다. **(A, 권장)** 두 함수 앞에 `export` 만 붙여 직접 단위 테스트한다(동작 변경 없음, 두 단어 수정). **(B)** export 없이 훅을 `renderHook` 으로 돌리고 MSW 로 SSE 스트림을 흘려 간접 검증한다. A 로 경계값을 촘촘히 보고, B 로 훅 한두 경로(재연결 시 `onResync`, 다른 jobId 무시, 404 면 재시도 안 함)만 본다. 경로 메모: CLAUDE.md 에 적힌 `hooks/use-job-realtime.ts` 가 아니라 `features/jobs/use-job-realtime.ts` 에 있다 |

이번 범위에서 **제외**하는 것: 3D 랜딩(`(marketing)/_components/landing/three/**`), 화면 컴포넌트 단위 테스트(위저드·예치 화면은 E2E 로 덮는다), `features/wallet/ethereum.ts` 의 RPC 호출부. 다만 `ethereum.ts` 의 `formatUnits`·calldata 인코딩(`approveGsc`·`createAndFundJob` 이 만드는 `data`)은 순수에 가까워 단계 2 의 여유 항목으로 둔다.

### 2-3. MSW 목업을 어떻게 재사용하나

지금 구조(파일 근거):

- `src/mocks/handlers.ts` 가 4개 도메인 핸들러(`jobs`·`auth`·`wallet`·`workers`)를 하나의 배열로 묶는다.
- `src/mocks/server.ts` 는 이미 `setupServer(...handlers)`(`msw/node`)를 만들어 둔다. 지금은 `src/instrumentation.ts` 가 목업 모드일 때 Next 서버 쪽에서만 쓴다.
- `src/mocks/browser.ts` 의 `setupWorker` 는 `NEXT_PUBLIC_API_MOCKING=enabled` 일 때 `msw-provider.tsx` 가 브라우저에서 띄운다. `public/mockServiceWorker.js` 가 있다.
- 핸들러 URL 은 `API_BASE_URL` 기준이다. 이 값은 `typeof window === 'undefined'` 이면 `${BACKEND_ORIGIN || 'http://localhost:8080'}/api`, 브라우저면 `/api` 다(`lib/fetcher.ts`).

재사용 방법:

| 쓰임 | 방법 | 주의할 점 |
| --- | --- | --- |
| Vitest node 환경(단계 2) | 대부분 순수 함수라 MSW 가 필요 없다 | — |
| Vitest jsdom 환경, 훅 테스트(단계 3) | `src/test/msw.ts` 에서 **기존 `src/mocks/server.ts` 의 `server` 를 import** 해 `beforeAll(listen)`·`afterEach(resetHandlers)`·`afterAll(close)`. 테스트별 상황은 `server.use(http.get(..., () => 스트림))` 로 덮어쓴다 | jsdom 환경에서는 `window` 가 있으므로 `API_BASE_URL` 이 `/api` 가 된다. Node `fetch` 는 상대 URL 을 받지 않는다. 그래서 테스트 설정에서 `NEXT_PUBLIC_API_BASE_URL=http://localhost/api` 를 주입한다(앱 코드 수정 없음, 이미 있는 환경 변수 분기) |
| 상태가 있는 핸들러 | jobs 핸들러는 모듈 변수와 `sessionStorage` 에 상태를 쌓고, `setTimeout` 으로 자동 전이한다(`MOCK_ADVANCE_MS`) | 테스트끼리 섞이지 않게 `afterEach` 에서 `sessionStorage.clear()`. 자동 전이 타이머에 기대는 단언은 Vitest 에서 하지 않는다(그건 E2E 몫) |
| Playwright(단계 5~7) | `NEXT_PUBLIC_API_MOCKING=enabled` 로 앱을 띄우면 **브라우저 안의 Service Worker 가 `/api` 를 응답**한다. 새 목업은 만들지 않는다 | 목업 범위 밖이 두 곳 있다. ① MetaMask(`window.ethereum`) ② 공개 RPC `https://rpc.ssafy-blockchain.com`(잔액·allowance·영수증, `ethereum.ts` 가 직접 `fetch`). ①은 `page.addInitScript` 로 EIP-1193 스텁을 심는다. ②는 `context.route` 로 가로채는 것이 1안인데, **MSW Service Worker 가 통과시킨 요청을 Playwright 가 가로챌 수 있는지 단계 5 에서 먼저 실험한다**(§6 위험 R4) |

---

## 3. 도구 선택

버전은 2026-10-01 에 `npm view` 로 확인한 최신 안정판이다. 정확한 버전으로 고정한다(이 저장소는 `^` 없이 고정하는 관례: `next 16.3.4`, `zod 4.5.4` 등).

| 패키지 | 버전 | 스토리 | 왜 이것인가 |
| --- | --- | --- | --- |
| `vitest` | **4.1.11** | 260 | Next 16 이 쓰는 빌드 체계(SWC/Turbopack)와 별개로 Vite 기반이라 설정이 짧다. ESM·TypeScript·`paths` 를 별도 변환기 없이 처리한다. Jest 는 ESM 인 `msw 2`·`zod/mini` 를 돌리려면 변환 설정이 많아진다. 엔진 조건 `node ^20 \|\| ^22 \|\| >=24`. **5.0.3 이 아니라 4.1.11 을 쓰는 이유는 아래 "버전 결정(단계 1 에서 발견)" 참고** |
| `@vitest/coverage-v8` | **4.1.11** | 260 | Vitest 와 버전을 맞춰야 한다(peer 동일 버전). V8 내장 커버리지라 계측 변환이 없다 |
| `jsdom` | 30.1.1 | 260 | 훅 테스트(단계 3)에만 쓴다. 파일 머리에 `// @vitest-environment jsdom` 을 붙인 파일만 jsdom 에서 돈다. 기본 환경은 **node** 로 둔다 — `File.stream()`·`crypto.subtle` 은 Node 24 쪽 구현이 더 온전하다. 엔진 조건 `node ^22.22.2 \|\| ^24.15` — 로컬 충족. Docker 빌드에서는 설치만 되고 실행되지 않는다 |
| `@testing-library/react` | 16.3.3 | 260 | `renderHook` 하나 때문에 들인다. React 19 지원 |
| `@testing-library/dom` | 10.4.2 | 260 | `@testing-library/react` 16 의 필수 peer |
| `@playwright/test` | 1.63.0 | 261 | 브라우저 E2E 표준. `webServer` 로 앱을 띄우고, `addInitScript`·`route`·trace viewer 가 이번 시나리오(지갑 스텁·RPC 가로채기·실패 분석)에 딱 맞는다. 브라우저는 Chromium 하나만(WSL 에 필요한 시스템 라이브러리 `libnss3`·`libgbm` 등은 이미 있다, sudo 불필요) |

**들이지 않는 것과 이유**

| 후보 | 결정 | 이유 |
| --- | --- | --- |
| `@vitejs/plugin-react` 6.1.1 | 안 씀 | peer 로 `vite ^8` 과 `oxc-transform-react` 등을 요구한다. 훅·순수 함수 테스트에는 JSX 변환만 필요하고, 그건 Vitest 기본 변환이 `tsconfig.json` 의 `"jsx": "react-jsx"` 를 읽어 처리한다 |
| `vite-tsconfig-paths` | 안 씀 | `@/*` 별칭 하나뿐이라 `resolve.alias: { '@': src }` 한 줄로 충분하다 |
| `@testing-library/jest-dom`·`user-event` | 이번엔 안 씀 | 화면 컴포넌트 단위 테스트를 이번 범위에서 뺐다. 필요해지면 그때 추가 |
| `happy-dom` | 안 씀 | jsdom 하나로 통일 |
| Vitest `globals: true` | 안 씀 | 전역 타입을 쓰려면 `tsconfig.json` 의 `types` 를 건드려야 하고, 그러면 앱 코드 타입 검사에도 영향이 간다. 테스트 파일마다 `import { describe, it, expect } from 'vitest'` 를 쓴다 |

**버전 결정 (단계 1 에서 발견, 2026-10-01 사용자 승인):** 처음 계획은 Vitest 5.0.3(latest)이었다. 그런데 5.0.3 은 2026-09-30 11:25 UTC 에 릴리스되어 pnpm 11 의 최소 릴리스 경과 검사(`minimumReleaseAge`, 약 1일)에 걸린다. 로컬에서는 `pnpm add` 가 `pnpm-workspace.yaml` 에 `minimumReleaseAgeExclude` 를 자동으로 적어 통과하지만, **운영 Dockerfile 은 `package.json` 과 `pnpm-lock.yaml` 만 복사하므로 그 예외가 없고**, 실제 Alpine 빌드가 `ERR_PNPM_MINIMUM_RELEASE_AGE_VIOLATION`(`vitest`·`@vitest/coverage-v8`·`@vitest/mocker`·`@vitest/spy` 4건)으로 실패했다. 그래서 1일 넘은 **4.1.11(V4 dist-tag 최신)** 으로 낮췄다. 4.1.11 의 `@types/node` peer 는 `^20 || ^22 || >=24` 라서 이 저장소의 `@types/node 20.19.43` 과도 맞아 **R3 경고가 사라진다.** 5.x 로 올리는 것은 릴리스 후 1일이 지나면 버전 숫자만 바꾸면 된다. 교훈: 로컬 `pnpm install` 통과만으로는 부족하고 **Docker build 단계 확인이 필수**다(§5).

---

## 4. 단계와 순서

진행 규칙(사용자 결정 2026-10-01): **한 단계씩 구현하고, 단계가 끝나면 "직접 해 보기" 안내를 보여 준 뒤 멈춘다.** 사용자가 직접 해 보고 결과를 §8 기록 표에 남긴 다음에 다음 단계로 간다. 단계마다 §5 의 배포 검증 명령을 함께 돌린다.

모든 명령은 `frontend/` 에서 실행한다. 전역 `pnpm` 이 없으므로 `corepack pnpm` 으로 쓴다(전역에 깔려 있으면 `pnpm` 으로 바꿔 쳐도 된다).

### MR 1 — S15P21E204-260 Vitest 도입과 순수 로직 테스트

#### 단계 1. Vitest 설치와 연기(smoke) 테스트 한 개

| 바꿀 파일 | 내용 |
| --- | --- |
| `frontend/package.json` | devDependencies(정확한 버전 고정)에 `vitest`·`@vitest/coverage-v8`·`jsdom`·`@testing-library/react`·`@testing-library/dom` 추가. scripts 에 `"test": "vitest run"`, `"test:watch": "vitest"`, `"test:coverage": "vitest run --coverage"` |
| `frontend/pnpm-lock.yaml` | **pnpm 11.20.0 으로** 갱신(`corepack pnpm add -D …`) |
| `frontend/vitest.config.mts` | `environment: 'node'`, `resolve.alias['@']`, `include: ['src/**/*.test.ts?(x)']`, `env.TZ = 'Asia/Seoul'`, coverage 는 `include` 대상 파일 목록만, 기준치(threshold) 없음 |
| `frontend/src/features/jobs/format.test.ts` | 연기 테스트: `formatDuration` 3개 경우 |
| `frontend/.gitignore` | `/coverage` 는 이미 있다. 변경 없음 확인만 |
| `frontend/.dockerignore` | `coverage`·`playwright-report`·`test-results` 추가(이미지 빌드 컨텍스트에 안 실리게) |

검증: `corepack pnpm test` 통과 → §5 배포 검증 전체.

**직접 해 보기**

```shell
cd frontend
corepack pnpm install --frozen-lockfile   # lock 과 package.json 이 맞는지 — 실패하면 lock 갱신이 잘못된 것
corepack pnpm test                         # 기대: "Test Files 1 passed", "Tests 3 passed"
corepack pnpm test:watch                   # 기대: 대기 상태. format.ts 를 열어 formatDuration 의 '분' 을 'min' 으로 바꾸고 저장 → 즉시 빨간 실패. 되돌리면 초록
```

- 깨 보기 1: `format.test.ts` 의 기대값 하나를 틀리게 고쳐 `pnpm test` → 종료 코드가 1인지 `echo $?` 로 확인한다. 나중에 CI 가 생기면 이 종료 코드가 파이프라인을 멈춘다.
- 깨 보기 2: `vitest.config.mts` 의 `TZ` 줄을 지우고 돌려 본다. 지금은 영향이 없지만 단계 2 의 `formatDateTime` 테스트가 시간대에 따라 깨지는 이유를 이때 이해해 둔다.
- 볼 것: `git diff --stat pnpm-lock.yaml` — 몇 줄이 늘었는지 기록한다(vite·rolldown·jsdom 의 플랫폼별 네이티브 패키지가 많이 들어온다).

#### 단계 2. 순수 로직 테스트 — format · schema · dataset-analysis · adapter-tar

| 바꿀 파일 | 덮을 내용 |
| --- | --- |
| `src/features/jobs/format.test.ts` | 상태 8개 × 라벨·문장, `failReason`·`cancelReason` 조합 규칙(사용자 취소만 "취소됨", 나머지는 "실패"), RUNNING 100% 문장, 미지의 `cancelReason` 코드, `formatCost` 천 단위, `formatBytes` 경계(1 MiB·1 GiB), `suggestAdapterName` 이 `adapterNameSchema` 를 **항상 통과하는지**(두 모듈의 계약), `adapterFileName` 확장자(`.tar.gz`·쿼리스트링) |
| `src/features/jobs/schema.test.ts` | `adapterNameSchema` 통과·거절 표, `customConfigFormSchema` 빈 문자열·소수 epochs, `jobDetailSchema` 의 `priceWei` 숫자 문자열 강제, `hexAddress`, `resultVerificationSchema` 에서 요약 숫자 키가 null 이어도 통과, `jobRealtimeEventSchema` 모르는 `type` 거절 |
| `src/features/jobs/dataset-analysis.test.ts` | `estimateTokens`(한글·CJK·영문·공백), `textOfJson` 깊이 4 제한, `sampleText`(JSON 아님 → 원문), `summarize` 빈 입력 오류, `hashFile` 표준 벡터와 `0x`+64자 형식, `isTooBigToHash`, `analyzeDataset` 의 JSONL·CSV(헤더 건너뜀)·JSON 배열·`{data:[…]}`·청크 경계에서 잘린 줄·`onProgress` 단조 증가 |
| `src/features/jobs/adapter-tar.test.ts` | `tools/adapter-tar-check.mjs` 의 11개 단언을 그대로 옮기고, 1 GiB 초과·항목 65개·깨진 크기 필드를 보탠다. 손 스크립트 파일은 **지우지 않는다**(담당자 도구, 정리 여부는 MR 에서 묻는다) |

검증: `corepack pnpm test`, 새 파일만 `corepack pnpm exec eslint src/**/*.test.ts` 오류 0, §5.

**직접 해 보기**

```shell
corepack pnpm test                              # 기대: Test Files 4 passed, 테스트 수 기록
corepack pnpm exec vitest run adapter-tar       # 파일 이름 일부로 하나만
corepack pnpm exec vitest run -t "cancelReason" # 테스트 이름으로 거르기
node tools/adapter-tar-check.mjs                # 기존 손 스크립트 — 같은 대상을 둘이 같이 통과하는지 비교
```

- 깨 보기 1 (정책 회귀): `format.ts` 의 `jobStatusMeta` 에서 `cancelReason !== 'USER_REQUEST'` 조건을 지운다 → "공급자 무응답으로 취소된 작업이 '취소됨'으로 보인다"는 실패 메시지가 나와야 한다. 이것이 주석에 적힌 정책("취소됨은 내가 한 것처럼 읽힌다")을 테스트가 지키고 있다는 증거다.
- 깨 보기 2 (tar 패딩): `adapter-tar.ts` 의 `Math.ceil(entry.size / BLOCK)` 을 `Math.floor` 로 바꾼다 → 1500바이트 항목 다음 헤더를 엉뚱한 곳에서 읽어 "쓸 수 없는 항목" 테스트가 실패해야 한다.
- 깨 보기 3 (해시 형식): `hashFile` 에서 `'0x' +` 를 지운다 → 형식 테스트가 실패해야 한다. 백엔드가 `CHAR(66)` 로 받는 이유와 연결해 기록한다.
- 직접 확인: 아무 JSONL 파일(예: `tools/sample-datasets/` 의 파일)을 위저드에 올려 화면에 나온 토큰 수와, 같은 파일로 `analyzeDataset` 을 부른 테스트 출력이 같은지 비교한다.

#### 단계 3. SSE 파서·순서 보호와 실시간 훅

| 바꿀 파일 | 내용 |
| --- | --- |
| `src/features/jobs/use-job-realtime.ts` | **(선택 A, 승인됨 D1)** `parseFrame`·`isStale` 앞에 `export` 두 단어만 추가. 동작 변경 없음.  |
| `src/features/jobs/use-job-realtime.test.ts` | `parseFrame`: `data:` 여러 줄 결합, 주석(`:`)·`event:` 무시, 깨진 JSON → null, `\r\n`. `isStale`: 종료 뒤 진행률 버림, **소수부 자릿수가 다른 시각(`…03Z` vs `…03.400Z`)** — 주석에 적힌 과거 버그, 시각이 같으면 진행률로 비교, 종료 이벤트는 항상 새것 |
| `src/features/jobs/use-job-realtime.hook.test.ts` | `// @vitest-environment jsdom`. 기존 `src/mocks/server.ts` 를 띄우고 `server.use` 로 SSE 스트림을 흘린다. ① 프레임이 청크 중간에서 잘려 와도 이벤트가 하나로 조립된다 ② 다른 `jobId` 이벤트는 무시 ③ 연결되면 `onResync` 1회 ④ 404 면 재시도하지 않는다 ⑤ `jobId` 가 바뀌면 이전 작업 이벤트가 지워진다. 백오프는 `vi.useFakeTimers()` 로 3초 → 6초 → … 30초 상한 확인 |
| `src/test/msw.ts` | 공통 설정: `server.listen({ onUnhandledRequest: 'error' })` — 테스트에서는 핸들러가 없는 요청을 오류로 만들어 숨은 네트워크 호출을 잡는다 |
| `vitest.config.mts` | `env.NEXT_PUBLIC_API_BASE_URL = 'http://localhost/api'` (§2-3 주의점) |

**직접 해 보기**

```shell
corepack pnpm exec vitest run use-job-realtime
corepack pnpm dev   # 다른 터미널, .env.local 에 NEXT_PUBLIC_API_MOCKING=enabled
```

- 브라우저에서 RUNNING 인 목업 작업 상세를 열고 개발자 도구 → Network → `events` 요청 → **EventStream 탭**(또는 Response 탭)에서 `event: JOB_PROGRESS` / `data: {…}` / 빈 줄로 끝나는 프레임을 직접 본다. 테스트가 만든 문자열과 모양이 같은지 확인한다. 이 요청은 Service Worker 가 응답한 것이라 Network 의 Size 칸에 "(ServiceWorker)" 로 보인다.
- 깨 보기 1: `isStale` 의 `Date.parse` 비교를 문자열 비교(`next.updatedAt < prev.updatedAt`)로 바꾼다 → 소수부 자릿수 테스트가 실패해야 한다. 왜 사전순이면 `'Z' > '.'` 때문에 틀리는지 기록한다.
- 깨 보기 2: 훅의 `setState({ event: null, connected: false })`(77행)를 지운다 → "jobId 가 바뀌면 이전 이벤트가 지워진다" 테스트가 실패해야 한다. 참고로 이 줄이 지금 lint 오류 1개(`set-state-in-effect`)의 원인이다. 테스트가 이 줄의 존재 이유를 지켜 주므로, 나중에 lint 를 고치는 사람이 줄을 지우면 테스트가 막는다.
- **Wireshark 로 보면 좋은 단계 (선택):** 목업 SSE 는 브라우저 안에서 만들어져 선 위로 나가지 않는다. 진짜 프레임을 보려면 운영 서버에서 nginx 뒤 백엔드 SSE 를 `sudo tcpdump -i any -A 'tcp port 8080 and host <backend 컨테이너 IP>' -w sse.pcap` 로 떠서 Wireshark → Follow TCP Stream 으로 본다. `text/event-stream` 응답이 `Transfer-Encoding: chunked` 로 조금씩 흘러오고 프레임이 TCP 세그먼트 경계와 무관하게 잘리는 모습이 보인다 — 훅이 버퍼를 이어 붙이는 이유다. **운영 서버 캡처는 하기 전에 사용자에게 먼저 묻는다.** pcap 에는 `Authorization` 토큰이 들어가므로 저장소에 올리지 않고 확인 뒤 지운다.

#### 단계 4. 커버리지 보고서와 MR 1 마무리

| 바꿀 파일 | 내용 |
| --- | --- |
| `frontend/eslint.config.mjs` | `globalIgnores` 에 `"coverage/**"` 한 줄 추가. 커버리지 HTML 이 만든 JS(`coverage/block-navigation.js` 등)를 eslint 가 읽어 경고가 1개 늘어나는 것을 단계 1 검증에서 확인했다 |
| `vitest.config.mts` | `coverage.include` 를 이번 대상 5개 파일로, `reporter: ['text', 'html']`. **기준치는 걸지 않는다**(보고서만) |
| `frontend/README.md` | "테스트" 절 몇 줄: 명령 3개, 기본 환경 node·jsdom 은 파일 단위, MSW 재사용 위치 |
| `docs/plans/frontend-test.md` | §8 기록 표 채우기 |

**직접 해 보기**

```shell
corepack pnpm test:coverage          # 터미널 표: 파일별 % Stmts / Branch / Funcs / Lines
explorer.exe "$(wslpath -w coverage/index.html)"   # Windows 브라우저로 HTML 보고서 열기
```

- 볼 것: `coverage/index.html` 에서 `use-job-realtime.ts` 를 열고 **빨간 줄**(안 지나간 줄)이 어디인지 본다. 401 재발급 분기처럼 일부러 남긴 곳인지, 빠뜨린 곳인지 기록한다.
- 기록: 파일별 Lines·Branches 수치를 §8 표에 옮긴다. 포트폴리오 캡처는 이 화면으로 한다.

→ MR 1 생성(§7). 사용자 승인 뒤 커밋·푸시.

### MR 2 — S15P21E204-261 Playwright 로 위저드 → 예치 E2E

#### 단계 5. Playwright 설치, 로그인 연기 테스트, 지갑·RPC 가로채기 실험

| 바꿀 파일 | 내용 |
| --- | --- |
| `package.json`·`pnpm-lock.yaml` | `@playwright/test 1.63.0`, scripts `"e2e": "playwright test"`, `"e2e:ui": "playwright test --ui"` |
| `frontend/playwright.config.ts` | Chromium 한 개, `baseURL: http://localhost:3100`, `webServer`: `next dev -p 3100` + `env.NEXT_PUBLIC_API_MOCKING=enabled`(포트를 3000 과 다르게 해 평소 dev 서버와 안 부딪치게), `trace: 'retain-on-failure'`, `testDir: 'e2e'` |
| `frontend/e2e/login.spec.ts` | 목업 계정(`user1`/`1234`, `features/auth/mocks/data.ts`)으로 로그인 → 대시보드 진입 |
| `frontend/e2e/fixtures/wallet.ts` | `addInitScript` 로 심는 EIP-1193 스텁: `eth_requestAccounts` → Hardhat 공개 계정 #0(`MOCK_WALLET_ADDRESS`), `eth_chainId` → `0x79f5`, `personal_sign` → 고정 서명, `eth_sendTransaction` → 고정 해시를 돌려주고 **받은 `{to, data}` 를 `window.__txs` 에 쌓는다** |
| `frontend/e2e/fixtures/rpc.ts` | `rpc.ssafy-blockchain.com` JSON-RPC 대역: `eth_call`(balanceOf → 충분한 잔액, allowance → 0), `eth_getTransactionReceipt` → `status: 0x1` |
| `.gitignore` | `/playwright-report`, `/test-results` |

**실험(이 단계의 핵심 결정):** RPC 요청은 MSW Service Worker 를 거쳐 통과(passthrough)된다. Playwright `context.route` 가 그 요청을 잡는지 확인한다.
- 잡으면 → 1안 그대로.
- 못 잡으면 → 2안: 테스트에서 `serviceWorkers: 'block'` 은 MSW 자체를 끄므로 쓸 수 없다. 대신 E2E 전용으로 `window.__mswRpcHandlers` 같은 훅을 앱에 두지 않고, **`page.evaluate` 로 MSW `worker.use(http.post(rpcUrl, …))` 를 런타임에 덧붙이는 방법**을 먼저 시도한다. 그것도 안 되면 목업 핸들러에 RPC 대역을 추가하는 방안을 사용자와 상의한다(앱의 목업 코드를 바꾸므로).

**직접 해 보기**

```shell
corepack pnpm exec playwright install chromium   # 브라우저 내려받기(sudo 불필요)
corepack pnpm e2e                                  # 기대: 1 passed
corepack pnpm e2e:ui                               # UI 모드: 단계별 스냅샷, 각 동작 전후 DOM
corepack pnpm exec playwright show-report
```

- 볼 것: UI 모드 Network 탭에서 `/api/auth/login` 요청이 **"from service worker"** 로 표시되는지 확인한다. 이 표시는 MSW 가 브라우저 안에서 응답했다는 뜻이다.
- 깨 보기: `login.spec.ts` 의 비밀번호를 `12345` 로 바꿔 실패시킨다 → `test-results/` 에 생긴 `trace.zip` 을 `corepack pnpm exec playwright show-trace test-results/…/trace.zip` 로 열어 실패 순간 화면·콘솔·네트워크(401 `INVALID_CREDENTIALS`)를 본다.
- **Wireshark 로 보면 좋은 단계:** "목업 모드에서는 `/api` 가 선 위로 나가지 않는다"를 눈으로 확인한다.
  1. WSL 에서 `NEXT_PUBLIC_API_MOCKING=enabled corepack pnpm dev` 를 띄우고 Windows 브라우저로 `http://localhost:3000` 에 접속한다.
  2. Windows 에서 `"/mnt/c/Program Files/Wireshark/tshark.exe" -D` 로 인터페이스 번호를 확인한다. WSL 네트워킹 모드에 따라 `vEthernet (WSL)` 또는 `Adapter for loopback traffic capture` 쪽에 트래픽이 보인다.
  3. 표시 필터 `tcp.port == 3000 && http` 로 캡처한 채 로그인·위저드를 진행한다.
  4. 기대 결과: HTML·`/_next/static/*.js`·`/mockServiceWorker.js` 요청은 보이지만 **`/api/...` 요청은 하나도 없다.** `.env.local` 에서 목업을 끄고 다시 하면 `/api/auth/login` 이 평문 HTTP 로 보인다. 비밀번호도 그대로 보이며, 이것이 운영에서 nginx HTTPS 가 필요한 이유다.
  5. 선택: 예치 단계에서 브라우저가 `rpc.ssafy-blockchain.com` 에 가는 요청은 HTTPS 라 암호화돼 보인다. 내용을 보려면 Windows 에서 `SSLKEYLOGFILE` 환경 변수를 두고 Chrome 을 띄워 Wireshark 에 키 파일을 지정한다. 캡처 파일과 키 파일은 저장소에 두지 않는다.

#### 단계 6. 위저드 → 작업 생성 E2E

| 바꿀 파일 | 내용 |
| --- | --- |
| `frontend/e2e/fixtures/dataset.jsonl` | 20줄 안팎의 작은 합성 데이터셋(개인정보처럼 보이는 값 금지) |
| `frontend/e2e/wizard.spec.ts` | 로그인 → `/jobs/new` → 1단계 워크로드 → 2단계 설정(AUTO·BALANCED) → 3단계 데이터셋 업로드(`setInputFiles`) 후 분석 결과(샘플 수 20) 확인 → 4단계 GPU 선택(목업 등급 3개) → 견적 카드 금액 확인 → 생성 → `/jobs/{id}` 로 이동, 상태 "예치 대기" |

선택자는 역할·이름(`getByRole('button', { name: '다음' })`) 위주로 쓴다. 지금 jobs 컴포넌트에 `aria-label`·`data-testid` 가 13곳뿐이다. 역할로 못 잡는 곳이 생기면 앱에 `data-testid` 를 붙이는 대신 **먼저 사용자에게 보고**한다(앱 코드 변경이므로).

**직접 해 보기**

```shell
corepack pnpm exec playwright test wizard --headed   # 실제 창이 떠서 위저드가 저절로 넘어가는 모습을 본다
corepack pnpm exec playwright test wizard --debug    # 한 동작씩 멈추며 진행(Playwright Inspector)
corepack pnpm exec playwright codegen localhost:3100 # 직접 클릭하면 선택자 코드가 만들어진다 — 테스트 코드와 비교
```

- 볼 것: 3단계에서 파일을 올린 직후 화면의 "샘플 수·토큰 수"가 단계 2 의 `analyzeDataset` 테스트와 같은 규칙으로 계산된 값인지 확인한다(단위 테스트와 E2E 가 같은 함수를 양쪽에서 확인하는 구조).
- 깨 보기: `dataset.jsonl` 을 빈 파일로 바꾼다 → 위저드가 `EMPTY_DATASET` 안내를 보여 주고 다음 단계로 못 가는지, 테스트가 그 지점에서 실패하는지 trace 로 확인한다.

#### 단계 7. 지갑 연결 → 예치 → FUNDED E2E

| 바꿀 파일 | 내용 |
| --- | --- |
| `frontend/e2e/fund.spec.ts` | 로그인 → `/wallet` 에서 지갑 연결(challenge → 스텁 `personal_sign` → link; 목업 초기값 `MOCK_WALLET_INITIAL = null` 이라 연결부터 해야 한다) → 단계 6 흐름으로 작업 생성 → 상세의 "N GSC 예치" 버튼 → 스텁에 쌓인 거래 2건 검사 → 상태가 FUNDED("배정 대기")로 바뀌는지 |
| `frontend/e2e/fixtures/calldata.ts` | `data` 해독: 앞 4바이트 셀렉터(`0x095ea7b3` approve, `0xd903f3f5` createAndFundJob) + 32바이트 인자 |

단언:
1. 첫 거래는 `to = GSC 토큰 주소`, 셀렉터 approve, spender = 에스크로 주소, 금액 = `priceWei`(**무한 승인이 아님**).
2. 둘째 거래는 `to = 에스크로 주소`, 셀렉터 createAndFundJob, 인자가 상세 응답의 `externalRef`·`priceWei`·`providerWallet` 과 같다.
3. 버튼에 쓰인 금액(`estimatedCost`)과 서명 금액(`priceWei / 10^18`)이 같다(목업에서는 같게 만들어진다).
4. **`test.fixme` — 신뢰 경계 확인:** `page.route` 로 상세 응답의 `priceWei` 만 10배로 바꿔 보낸다. 바람직한 동작은 "화면 금액과 서명 금액이 다르면 서명 전에 멈춤"이지만 **지금 `fundPanel.tsx` 는 대조하지 않는다**(조사 결과 `01-frontend.md`). 이 테스트는 현재 실패하므로 `fixme` 로 두고, MR 본문에 담당자 판단 사항으로 적는다. 앱 수정은 이번 범위가 아니다.

**직접 해 보기**

```shell
corepack pnpm exec playwright test fund --headed
corepack pnpm exec playwright test fund --ui     # 각 단계의 window.__txs 내용을 Console 에서 확인
```

- 볼 것: UI 모드 Console 에서 `window.__txs` 를 열어 `data` 문자열을 직접 읽어 본다. `0x095ea7b3` + 64자(에스크로 주소) + 64자(금액, 16진수)가 보인다. 금액 64자를 `BigInt('0x…')` 로 바꾸면 `priceWei` 와 같다.
- 깨 보기 1: 스텁의 `eth_requestAccounts` 가 다른 주소를 돌려주게 바꾼다 → 화면에 "연결된 지갑이 계정에 등록된 지갑과 다릅니다"가 뜨고 거래가 0건이어야 한다.
- 깨 보기 2: RPC 대역의 잔액을 0 으로 → "GSC 잔액이 부족합니다" 안내, 거래 0건.
- 깨 보기 3: `fixme` 를 `test` 로 바꿔 실제로 돌려 본다 → 실패하는 모습을 보고, 실패 메시지를 MR 본문에 붙인다.

#### 단계 8. MR 2 마무리

| 바꿀 파일 | 내용 |
| --- | --- |
| `frontend/README.md` | E2E 절: 브라우저 설치, `e2e`·`e2e:ui`, trace 보는 법, 목업 모드로만 돈다는 점 |
| `docs/plans/frontend-test.md` | §8 기록 표 |

**직접 해 보기:** 처음부터 깨끗하게 다시 돌린다 — `rm -rf node_modules .next && corepack pnpm install --frozen-lockfile && corepack pnpm test && corepack pnpm e2e && corepack pnpm build`. 전부 통과하면 MR 2 생성.

---

## 5. 운영 배포를 깨지 않는 방법

운영 Dockerfile(`frontend/Dockerfile`)의 build 단계는 `pnpm install --frozen-lockfile --config.strict-dep-builds=false` → `COPY . .` → `pnpm build` 다. 깨질 수 있는 지점과 막는 방법:

| 지점 | 무엇이 깨지나 | 막는 방법 |
| --- | --- | --- |
| lockfile | `package.json` 만 바꾸고 lock 을 안 바꾸면 `--frozen-lockfile` 이 `ERR_PNPM_OUTDATED_LOCKFILE` 로 실패한다 | 의존성은 반드시 `corepack pnpm add -D <pkg>@<버전>` 로 추가해 **pnpm 11.20.0** 이 lock 을 갱신하게 한다. 다른 pnpm 버전으로 만든 lock 은 형식이 달라질 수 있다. 커밋 전에 `corepack pnpm install --frozen-lockfile` 로 재확인 |
| 최소 릴리스 경과(`minimumReleaseAge`) | pnpm 11 은 릴리스 후 약 1일이 안 된 버전의 lock 을 거절한다. 로컬 `pnpm add` 는 `pnpm-workspace.yaml` 에 예외를 자동 기록해 통과시키지만 Dockerfile 은 그 파일을 복사하지 않는다 | **1일 넘은 버전만 쓴다**(최신 `latest` 가 갓 나온 버전이면 한 단계 아래 선택). `pnpm-workspace.yaml` 에 `minimumReleaseAgeExclude` 가 자동으로 붙었으면 그것이 신호다 → 버전을 낮추고 되돌린다. 확인은 Docker build 단계 |
| 빌드 스크립트 승인 | pnpm 10+ 는 의존성의 postinstall 을 승인 목록(`pnpm-workspace.yaml` 의 `allowBuilds`)으로만 돌린다. 새 패키지가 빌드 스크립트를 가지면 경고가 난다 | Dockerfile 이 `strict-dep-builds=false` 라 실패하지는 않는다. 그래도 새 경고가 생기면 기록하고, `allowBuilds` 추가가 필요한지 판단한다(Vitest·Playwright 는 보통 필요 없음 — `playwright install` 은 수동 명령) |
| `next build` 타입 검사 | `tsconfig.json` 의 `include` 가 `**/*.ts`·`**/*.tsx`·`**/*.mts` 라 **테스트 파일·`vitest.config.mts`·`playwright.config.ts`·`e2e/**` 도 `next build` 가 타입 검사한다.** 타입 오류 하나면 배포 빌드가 실패한다 | ① 테스트 도구 타입은 devDependencies 로 설치되므로 Docker build 단계에서도 있다(`NODE_ENV` 를 build 단계에서 production 으로 두지 않아 dev 의존성도 깔린다 — Dockerfile 확인함). ② 단계마다 `corepack pnpm exec tsc --noEmit` 과 `corepack pnpm build` 를 돌린다. ③ `tsconfig.json` 에서 테스트를 `exclude` 하는 방법은 **쓰지 않는다** — 편집기 타입 검사가 꺼지고, 타입이 틀린 테스트가 몰래 쌓인다 |
| 번들 오염 | 테스트 파일이 앱 번들에 들어가면 안 된다 | `src/**/*.test.ts` 는 어떤 라우트도 import 하지 않으므로 Next 가 번들하지 않는다. `e2e/`·설정 파일은 `src/` 밖이다. `src/test/msw.ts` 도 테스트만 import 한다. 빌드 출력의 라우트 수(16개)·`.next/standalone` 크기를 단계 1 전후로 비교한다 |
| 빌드 컨텍스트 | `COPY . .` 에 `coverage/`·`playwright-report/` 가 실리면 이미지가 커진다 | `.dockerignore` 에 추가(단계 1) |
| 플랫폼별 네이티브 패키지 | Vite 8(rolldown)·esbuild 계열은 OS·CPU·libc 별 선택 의존성을 가진다. WSL(glibc)에서 만든 lock 이 Alpine(musl)에서 맞는 바이너리를 고르는지 | pnpm lock 은 모든 플랫폼 후보를 기록하고 설치 시 고른다. 그래도 **실제 Alpine 에서 한 번 확인한다**: 아래 Docker 명령 |
| engines | `jsdom 30` 은 `node ^22.22.2` 이상을 요구하는데 `node:22-alpine` 의 22 버전 세부값은 이미지 갱신 시점에 따라 다르다 | pnpm 은 기본적으로 engines 불일치에 경고만 낸다(`engine-strict` 꺼짐). 그리고 jsdom 은 build 단계에서 실행되지 않는다. Docker 확인 명령에서 경고 여부를 기록한다 |

**로컬 검증 명령 (단계마다, MR 전에는 필수)**

```shell
cd frontend
corepack pnpm install --frozen-lockfile --config.strict-dep-builds=false   # 1. 배포와 같은 설치
corepack pnpm exec tsc --noEmit                                            # 2. 타입(테스트·설정 포함)
corepack pnpm build                                                        # 3. 배포와 같은 빌드
corepack pnpm test                                                         # 4. 단위 테스트
corepack pnpm exec eslint vitest.config.mts playwright.config.ts e2e 'src/**/*.test.ts' src/test   # 5. 새 파일 lint 오류 0
corepack pnpm lint 2>&1 | tail -3                                          # 6. 전체 lint — 오류 수가 기준선 10 그대로인지
```

**MR 직전 한 번: 실제 Alpine 이미지로 build 단계까지** (G-shared.exe 를 넣는 runner 단계는 건너뛴다)

```shell
docker build --target build -t gshared-fe-buildcheck ./frontend   # 저장소 루트에서
docker image rm gshared-fe-buildcheck
```

결과(통과 여부·소요 시간·경고)를 §8 과 MR 본문에 적는다.

---

## 6. 위험과 되돌리는 방법

| # | 위험 | 가능성 | 되돌리기·대응 |
| --- | --- | --- | --- |
| R1 | lock 갱신이 다른 의존성의 해석까지 바꿔(호이스팅·중복 제거) 앱 동작이 달라진다 | 낮음 | `git diff pnpm-lock.yaml` 에서 기존 `dependencies` 항목 버전이 바뀌었는지 확인한다. 바뀌었으면 `git checkout develop -- pnpm-lock.yaml package.json` 후 `pnpm add` 를 다시 한다. MR 단위로는 MR 을 되돌리면(revert) `package.json`·lock 이 함께 원래대로 돌아간다 |
| R2 | 배포 빌드가 테스트 파일 타입 오류로 깨진다 | 중간 | §5 의 2·3번 명령이 MR 전에 잡는다. 머지 뒤 발견되면 해당 MR revert 한 번으로 복구된다(앱 코드 변경이 거의 없으므로 충돌 없음) |
| R3 | `@types/node 20` 과 Vitest 5 peer 경고 | 해소 | Vitest 4.1.11 로 낮춰 peer 가 맞는다(§3 버전 결정). `@types/node` 22 제안은 D4 대로 MR 본문에만 넣는다 |
| R4 | MSW Service Worker 가 통과시킨 RPC 요청을 Playwright `route` 가 못 잡는다 | 중간 | 단계 5 에서 먼저 실험한다. 대안 순서는 §4 단계 5 에 적었다. 대안이 앱 목업 코드를 바꿔야 하면 멈추고 묻는다 |
| R5 | 목업 자동 전이 타이머(5초·5초·3초)와 3초 폴링 때문에 E2E 가 느리거나 들쭉날쭉하다 | 중간 | `expect.poll`·`toHaveText` 의 기본 대기로 기다리고, 고정 `waitForTimeout` 은 쓰지 않는다. 예치 → FUNDED 는 목업이 `/fund` 응답에서 바로 FUNDED 를 주므로 타이머에 덜 의존한다 |
| R6 | 테스트 때문에 앱 코드를 바꾸게 된다(`export` 추가, `data-testid`) | 확정(작음) | 바꾸는 줄은 계획에 적은 것만 한다(`use-job-realtime.ts` 의 `export` 2개, 사용자 승인 조건). 그 밖에는 멈추고 묻는다 |
| R7 | 기존 lint 오류 10개 때문에 "lint 통과"를 완료 기준으로 쓸 수 없다 | 확정 | 기준을 "새 파일 오류 0, 전체 오류 수 10 유지"로 둔다. 기존 오류 정리는 담당자에게 제안만 한다 |
| R8 | `vitest.config.mts`·`playwright.config.ts` 가 `next build` 에 영향을 준다 | 낮음 | Next 는 이 파일들을 import 하지 않고 타입 검사만 한다. 단계 1 에서 빌드 결과 라우트 목록이 그대로인지 확인한다 |

전체 되돌리기: 이 브랜치는 `frontend/` 안에서만 파일을 더한다. 앱 동작을 바꾸는 줄이 없으므로(R6 제외) **MR 을 revert 하면 완전히 이전 상태**가 된다. 로컬 실험 중 되돌리기는 `git restore --source=HEAD -- frontend/` 와 `rm -rf frontend/node_modules && corepack pnpm install --frozen-lockfile`.

---

## 7. 완료 기준과 MR 나누기

### MR 1 — `[FE] Vitest 도입과 순수 로직 테스트` (S15P21E204-260)

완료 기준(Jira AC 와 맞춘다):

1. `corepack pnpm test` 로 Vitest 가 돌고, 대상 5개 파일(`format`·`schema`·`dataset-analysis`·`adapter-tar`·`use-job-realtime`)에 테스트가 있다. 모두 통과한다.
2. `tools/adapter-tar-check.mjs` 의 단언 11개가 Vitest 테스트에 모두 옮겨져 있다.
3. 훅 테스트는 기존 `src/mocks/server.ts` 의 MSW 서버를 재사용하고, 새 목업 데이터 파일을 만들지 않는다.
4. `corepack pnpm test:coverage` 가 HTML 보고서를 만든다. 대상 파일별 Lines·Branches 수치를 MR 본문에 적는다(기준치는 걸지 않음).
5. §5 로컬 검증 6개 명령과 Docker build 단계 확인이 통과하고, 그 출력 요약을 MR 본문에 적는다. 전체 lint 오류 수는 기준선 10 그대로다.

### MR 2 — `[FE] Playwright 위저드 → 예치 E2E` (S15P21E204-261)

완료 기준:

1. `corepack pnpm e2e` 가 목업 모드로 앱을 띄워 로그인·위저드·예치 테스트를 통과한다.
2. 예치 테스트가 서명 요청 2건의 calldata 를 해독해 대상 주소·셀렉터·금액을 검증한다(무한 승인 아님 포함).
3. 화면 금액과 서명 금액을 대조하지 않는 문제를 `test.fixme` 로 남기고 MR 본문에 담당자 판단 사항으로 적는다.
4. 실패 시 trace 가 남고, README 에 보는 법이 있다.
5. MR 1 과 같은 배포 검증이 통과한다.

### 나누는 이유와 순서

- 스토리 하나에 MR 하나(팀 규칙). MR 1 이 먼저 머지되어야 MR 2 의 `package.json`·lock 충돌이 적다. MR 2 는 MR 1 머지 뒤 `develop` 을 받아 이어 간다.
- 두 MR 모두 리뷰어는 조가을 님. MR 본문 맨 위에 `Jira: https://ssafy.atlassian.net/browse/<키>`, 만든 뒤 Jira 에 MR 링크 코멘트, 상태는 In Progress(전이 21). 머지되면 AC 확인 뒤 Done(전이 31).
- 커밋 메시지 형식은 `[fe-test] <한국어 설명>`. 커밋마다 메시지를 먼저 보여 주고 승인받으며, 푸시는 따로 승인받는다. 이 계획 문서 자체도 승인 뒤 첫 커밋으로 올린다.

### 확정된 결정 (2026-10-01 사용자 승인)

| # | 결정 | 확정 내용 |
| --- | --- | --- |
| D1 | `use-job-realtime.ts` 의 `parseFrame`·`isStale` 에 `export` | **붙인다.** 동작 변경 없음 |
| D2 | `tools/adapter-tar-check.mjs` | **남긴다.** MR 에서 담당자에게 정리 여부를 묻는다 |
| D3 | E2E 실행 방식 | **`next dev`.** CI 가 생기면 다시 정한다 |
| D4 | `@types/node` 20 → 22 | **MR 본문에 제안만 넣고 바꾸지 않는다** |
| D5 | MR 제목 라벨 | **`[FE]` 그대로** |

---

## 8. 단계 기록 (사용자가 직접 해 본 결과)

| 단계 | 날짜 | 확인한 것 | 결과·수치 | 비고 |
| --- | --- | --- | --- | --- |
| 0 기준선 | 2026-10-01 | install·build·tsc·lint | install 48.7s 통과 / build 통과(라우트 16) / tsc 0 / lint 오류 10·경고 223 | 이 문서 작성 중 확인 |
| 1 | 2026-10-01 | 사용자 직접 확인: ① `install --frozen-lockfile` 통과 ② `pnpm test` 3 passed (232ms) ③ watch 에서 `formatDuration` 의 `분`→`min` 으로 고치자 즉시 실패, **1개만 실패**(`1 failed \| 2 passed`) — 나머지 둘은 다른 분기라 통과 ④ 테스트의 정답도 같이 `45min` 으로 맞추면 통과 — 테스트는 "코드와 사람이 적은 정답이 일치하는가"만 본다 ⑤ 정답을 `45시간` 으로 틀리게 하면 종료 코드 **1**, 되돌리면 **0** ⑥ `git diff --stat`: lock +1,094 −0, package.json +10 −2(쉼표로 인한 줄 변경) | 구현 직후 측정: tsc 0 / build 통과(라우트 16) / **Docker `--target build` 통과** / 전체 lint 오류 10 유지 / 신규 파일 lint 오류 0 | 5.0.3 은 Docker 에서 `MINIMUM_RELEASE_AGE_VIOLATION` 으로 실패 → 4.1.11 로 변경(§3). 정답은 코드가 아니라 정책(주석·명세)에서 뽑는다는 점을 단계 2 의 원칙으로 삼음 |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 | | | | |
