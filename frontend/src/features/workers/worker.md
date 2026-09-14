# 워커(공급자 PC) 등록 · 내 워커 목록 구현 계획 (프론트)

브랜치: `fe-feature/worker` (wallet-create 위에서 분기). 백엔드 워커 도메인은 develop `back/.../worker`(등록 REST + WebSocket 실시간 통신, `1058b87`). 워커 프로그램은 `system/worker`(Python, 배포 exe `system/G-shared/G-shared.exe`). 이 문서는 웹이 워커 등록 흐름에서 맡을 부분과 "내 워커" 화면을 정한다. 완료 후엔 `wallet.md` 처럼 구현 기준 설명서로 갱신한다.

## 0. 가장 중요한 전제 — 등록은 웹이 아니라 **워커 프로그램**이 한다

`system/worker/gshared_worker/auth_check.py` 의 흐름이 이미 확정돼 있다:

```
G-shared.exe 실행
 → 환경 진단(check_environment) → GPU 런타임 준비(ensure_runtime, WSL)
 → 저장된 자격 있음?  예: POST /api/workers/authenticate(workerToken)        → 연결
                      아니오: nvidia-smi 로 스펙 읽기(machine_facts, "Nothing is invented")
                            → 벤치마크 측정(benchmarkTokensPerSec)
                            → 앱 안 로그인 창(Tk: 이메일/비밀번호, 회원가입도 가능) → 사용자 access 토큰 1회 사용
                            → POST /api/workers/register(Bearer 사용자토큰, 스펙) → { workerId, workerToken }
                            → workerToken 을 DPAPI 로 봉인해 worker-config.json 에 저장 (사용자 토큰은 폐기)
 → WebSocket wss://…/ws/workers (Bearer workerToken) → REGISTER → ONLINE, 30초 HEARTBEAT
```

- 웹이 `POST /api/workers/register` 를 호출하면 안 되는 이유: (1) 스펙(deviceName/gpuModel/vramMb/driverVersion)은 **그 PC 에서 읽어야** 하고 사람이 타이핑하면 스케줄러가 틀린 카드에 작업을 보낸다(machine_facts 주석). (2) 응답의 `workerToken` 원문은 **딱 한 번** 노출되고 그 PC 에 저장돼야 한다 — 웹이 받으면 사용자가 복사해 파일에 붙이는 과정이 생기고 유출 면이 커진다(`worker-config.example.json` 의 `workerToken` 자리는 PoC 시절 잔재).
- 따라서 **웹의 "워커 등록"은 안내(다운로드 → 실행 → 앱에서 로그인)와 결과 확인(내 워커 목록·상태)**이다. 이 문서의 "워커 등록 페이지"는 그 뜻.

**한다**: 네비 `워커` 탭(authed), `/workers` 보호 라우트, 내 워커 목록(상태·스펙·하트비트), 워커 없을 때 등록 안내 카드, 워커 삭제(등록 해제), 목록 자동 갱신(폴링), MSW 목업, 백엔드 조회·삭제 API 요청, 랜딩 `내 GPU로 수익 내기` CTA 연결.
**안 한다**: 웹에서 직접 register 호출, 워커 이름 변경, 시간당 가격 설정(백엔드 엔티티에 필드 없음), 수익·정산 표시(Escrow 미배포), 웹 WebSocket 구독(폴링으로 충분), 작업(job) 목록(별도 도메인).

## 1. 백엔드 API

### 1-1. 있는 것 (`/api/workers`, develop)

| 메서드/경로 | 호출자 | 요청 | 응답 |
| --- | --- | --- | --- |
| `POST /api/workers/register` | **워커 프로그램**(Bearer 사용자 access) | `{ deviceName*, osVersion, gpuModel*, vramMb(>0), driverVersion, benchmarkTokensPerSec }` | `201 { workerId: "worker-<uuid>", workerToken }` (원문 1회) |
| `WS /ws/workers` | 워커 프로그램(Bearer workerToken) | `REGISTER / HEARTBEAT(30s) / JOB_*` | `REGISTER_ACK / HEARTBEAT_ACK / JOB_ASSIGN / JOB_CANCEL` |

`Worker` 엔티티: `id, owner, workerToken(SHA-256), deviceName, osVersion, gpuModel, vramMb, driverVersion, benchmarkTokensPerSec, status(OFFLINE|ONLINE|RUNNING), currentJobId, lastHeartbeatAt, createdAt, updatedAt`. 하트비트 90초 미수신이면 스위퍼가 OFFLINE.

### 1-2. (해결됨 2026-09-10) `GET /api/workers/me` · `DELETE /api/workers/{workerId}` — develop 에 들어왔다

- `GET /me` 원소에 **`walletLinked: boolean`** 추가. false 면 예치 때 넣을 공급자 주소가 없어 **작업 후보에 오르지 못한다** → 워커 카드 상태 칸에 "지갑 미연결 · 작업을 받을 수 없습니다" 표시.
- `DELETE` 성공 204. 작업 중(`RUNNING` 또는 `currentJobId` 있음)이면 **409 `WORKER_BUSY`** → "작업을 먼저 취소해 주세요".
- 아래는 요청 당시 스펙(참고용).

### 1-2-old. 없는 것 → 백엔드에 요청 (웹 화면의 선행 조건)

웹이 쓸 **조회가 하나도 없다.** PoC 백엔드(`poc/gpu-compute-poc/backend/.../WorkerController`)에는 `GET /api/workers`, `GET /api/workers/{id}` 가 있었지만 본 프로젝트로 이관되지 않았다.

| 요청 | 스펙 | 용도 |
| --- | --- | --- |
| **`GET /api/workers/me`** (필수) | Bearer 사용자 access → `200 [ { workerId, deviceName, osVersion, gpuModel, vramMb, driverVersion, benchmarkTokensPerSec, status, currentJobId, lastHeartbeatAt, createdAt } ]` (없으면 `[]`) | 목록·상태 화면 전부 |
| **`DELETE /api/workers/{workerId}`** (권장) | Bearer 사용자 access, 소유자만. `204`. 남의 워커면 `403`/`404 WORKER_NOT_FOUND` | "등록 해제". 워커 프로그램은 이후 authenticate 에서 `WORKER_NOT_FOUND` 를 받아 "다시 등록" 안내를 띄우도록 이미 설계됨(auth_check `_IN_PLAIN_WORDS`) |

- 이름 변경(`PATCH`)은 요청하지 않는다 — `deviceName` 은 PC 호스트명이고 바꿀 이유가 아직 없다.
- 워커 프로그램이 기대하는 `POST /api/workers/authenticate`, `PUT /api/workers/{id}/gpu` 도 develop 백엔드에 없다(`worker_registry.py`). 프론트 범위는 아니지만 **같은 MR/이슈에서 백엔드·시스템 담당이 맞춰야 할 항목**이라 함께 전달.
- 반영 전에는 MSW 로만 개발(§6). 실 API 연동 시 바꿀 코드는 없다.

## 2. 화면 진입 · 상태 분기

```
네비 [워커](authed 만) / 랜딩 [내 GPU로 수익 내기] 클릭
  ├─ guest  → proxy.ts 가 /login?next=/workers
  └─ authed → /workers
        GET /workers/me (authFetch)
        ├─ loading → 스켈레톤
        ├─ []      → <WorkerRegisterCard/>  "첫 워커 등록하기" 안내 (다운로드·실행·로그인 3단계)
        └─ [..]    → <WorkerList/> 카드들 + 하단 <details> "다른 PC 도 등록하기" (같은 안내, 접힘)
        15초마다 GET 재조회(탭이 보일 때만 — document.visibilityState). 하트비트 30초·타임아웃 90초라 이 주기면 충분
```

- `/workers` 는 `proxy.ts` `PROTECTED` + `matcher` 에 추가(auth.md §4 규칙).
- **2026-09-09 대시보드 통합**: 워커 목록은 `/dashboard/workers` 로(2026-09-10 에 `?tab=workers` 쿼리 → 경로로 변경, 옛 주소는 리다이렉트 호환). 사이드바 [공급] › [GPU 등록하기](`/workers/new`). 네비 `[워커]` 탭은 `[대시보드]` 로 합쳐졌고 `/workers` 는 그 탭으로 리다이렉트. 랜딩 `내 GPU로 수익 내기` → `/dashboard?tab=workers`.
- **2026-09-10 대시보드 개편**: 워커 카드는 `app/(console)/dashboard/_components/dashboardWorkerCard.tsx`(id·OS 킥커, 상태 점, GPU/VRAM/드라이버/벤치마크 2×2, 힌트 + 등록 해제). 지표: 연결된 워커·작업 중 GPU·총 VRAM·평균 벤치마크. `workersPage/workerCard/workerSkeleton` 삭제, `workerRegisterCard` 는 빈 상태·`<details>` 에서 계속 사용. 온도·전력·가동률·수익·수신 토글은 백엔드 요청 후.
- 랜딩 `heroSection.tsx` 의 `내 GPU로 수익 내기` href `/` → `/workers`. 시안(landing.md)의 문구 그대로.
- 등록은 웹 밖(앱)에서 일어나므로 **"등록 완료" 를 웹이 직접 알 수 없다** → 안내 카드에 "앱에서 로그인이 끝나면 여기 자동으로 나타납니다" + 폴링. 별도 "확인" 버튼 없음.

## 3. 파일 구조 (AGENTS.md 골격)

```
src/
  app/(app)/workers/
    page.tsx                          h1 "내 워커" + <WorkersPage/>
    loading.tsx                       <WorkerSkeleton/>
  proxy.ts                            PROTECTED += '/workers', matcher += '/workers/:path*'
  features/auth/components/navAuth.tsx  authed: [워커][지갑][로그아웃]
  app/(marketing)/_components/hero/heroSection.tsx  CTA href → /workers
  components/ui/statusBadge.tsx       무상태 프리미티브(서버 컴포넌트 OK). tone: success|warning|danger|neutral|primary → globals 상태 토큰(--color-*-tint). 지갑 네트워크 배지도 이걸로 교체 가능(지금은 인라인)
  features/workers/
    worker.md                         이 문서
    schema.ts                         WORKERS_PATH('/workers/me') · workerStatusSchema(enum) · workerSchema · workerListSchema(array) — 경로 상수는 여기(wallet.md §3 주의: mocks 가 api.ts 를 import 하면 auth store 가 서버 번들로 끌려감)
    types.ts                          Worker · WorkerStatus
    api.ts                            getMyWorkers() / deleteWorker(id) — authFetch + zod parse
    errors.ts                         WORKER_NOT_FOUND / FORBIDDEN → 문구(§7) workerMessageFor
    format.ts                         relativeTime(iso) — Intl.RelativeTimeFormat('ko') · formatVram(mb) → "12 GB" · formatTokensPerSec → "1,234 tok/s"
    components/workersPage.tsx        'use client'. GET + 15초 폴링(visible 일 때만) → loading | error(다시 시도) | RegisterCard | List
    components/workerRegisterCard.tsx 안내 3단계 + [G-shared 다운로드] primary + 요구사항 한 줄(Windows 10/11 · NVIDIA GPU · 관리자 권한(WSL))
    components/workerCard.tsx         한 워커: deviceName · StatusBadge · gpuModel/VRAM/드라이버 · 벤치마크 · 마지막 하트비트(상대시간) · 현재 작업 · [등록 해제] ghost sm
    components/workerSkeleton.tsx     카드 2개 자리
    mocks/handlers.ts                 workerHandlers: GET me / DELETE {id} — 모듈 배열, 삭제 반영
    mocks/data.ts                     MOCK_WORKERS 2대(ONLINE RTX 4070 12GB · OFFLINE RTX 3060 8GB, 하트비트 각각 20초 전·2시간 전) · MOCK_WORKERS_INITIAL(기본 = 2대, 빈 화면 보려면 [])
  mocks/handlers.ts                   [...jobHandlers, ...authHandlers, ...walletHandlers, ...workerHandlers]
```

네이밍: 컴포넌트 camelCase, 나머지 kebab-case, 배럴 없음. `'use client'` 는 `workersPage.tsx` 이하만. 스토어·훅 파일 없음(화면 하나) — 다른 화면이 워커 상태를 쓰면 승격.

## 4. 등록 안내 카드 (`workerRegisterCard.tsx`) — 웹이 맡는 "등록"

```
[아이콘]  아직 등록된 워커가 없습니다
          PC 에 G-shared 를 설치하고 로그인하면 이 계정의 워커로 등록됩니다.

  1  G-shared 다운로드            [G-shared 다운로드 (Windows)]  ← primary, 파일 링크
  2  실행하면 GPU 를 점검하고 실행 환경을 준비합니다   (첫 실행은 몇 분 걸릴 수 있음, 관리자 권한 요청)
  3  앱 창에서 이 계정으로 로그인   → 이 PC 가 자동 등록되고 여기 나타납니다
  요구사항: Windows 10/11 · NVIDIA GPU(드라이버 설치) · 인터넷
```

- 다운로드 링크: 상수 `WORKER_DOWNLOAD_URL`. 값은 §8 결정. 링크는 `<a href download>` 가 아니라 일반 `<a>`(exe 는 브라우저가 알아서 저장).
- 이메일을 미리 보여 준다("이 계정: user@…")? 백엔드에 `/users/me` 가 없어 이메일을 모른다 → 생략. 나중에 `/users/me` 생기면 한 줄 추가.
- 폴링으로 목록이 생기면 카드가 자연히 목록으로 바뀐다. 목록이 있을 때는 같은 내용을 `<details><summary>다른 PC 도 등록하기</summary>` 로 접어 하단에.

## 5. 워커 카드 (`workerCard.tsx`)

| 요소 | 값 | 비고 |
| --- | --- | --- |
| 제목 | `deviceName` | 호스트명. `title` 에 `workerId` |
| 상태 배지 | `ONLINE` → success "대기 중" / `RUNNING` → primary "작업 중" / `OFFLINE` → neutral "오프라인" | `statusBadge` |
| 스펙 줄 | `gpuModel · 12 GB · 드라이버 560.94 · Windows 11` | null 은 생략 |
| 벤치마크 | `1,234 tok/s` | null 이면 "측정 전" |
| 하트비트 | `20초 전` / OFFLINE 이면 `2시간 전 마지막 연결` | `Intl.RelativeTimeFormat`, `tabular-nums` |
| 현재 작업 | `RUNNING` 일 때만 `job-…` 짧게 | 잡 상세 링크는 jobs 화면 생기면 |
| 액션 | `[등록 해제]` ghost sm → `confirm()` → `DELETE` → 목록에서 제거 | RUNNING 중이면 비활성 + 툴팁 "작업이 끝난 뒤 해제할 수 있습니다" |

카드 그리드 `grid-cols-1 md:grid-cols-2`(AGENTS.md 반응형), `card` + `layer-hover` 없음(정적). 등장은 `animate-enter` 스태거 40ms.

## 6. 목업(MSW) — `workerHandlers`

- `GET ${API_BASE_URL}/workers/me`: `Authorization` 없으면 401, 있으면 현재 배열. 폴링 확인용으로 **호출마다 ONLINE 워커의 `lastHeartbeatAt` 을 now-20s 로 갱신**(살아있는 느낌).
- `DELETE ${API_BASE_URL}/workers/:id`: 없으면 404 `WORKER_NOT_FOUND`, 있으면 배열에서 제거 후 204.
- fixture 는 `workerListSchema.parse()` 통과값만. 새로고침하면 초기값으로(페이지 모듈 변수, wallet.md §7 과 동일).
- 등록 자체는 앱이 하므로 목업에 register 는 없다. 기본은 워커 2대가 바로 보이고, 빈 상태(등록 안내 카드)를 보려면 `MOCK_WORKERS_INITIAL` 을 `[]` 로. (처음엔 "GET 3회째에 워커가 나타나는" 장치를 뒀는데 새로고침·페이지 이동마다 비었다가 30초 뒤 다시 나타나 헷갈려서 2026-09-08 제거.)

## 7. 에러 메시지 (`features/auth/errors.ts` 의 `messageFor` 재사용)

| 코드 | 문구 | 위치 |
| --- | --- | --- |
| `WORKER_NOT_FOUND` 404 | 이미 해제된 워커입니다. 목록을 새로 고칩니다. | 카드 인라인 → 자동 재조회 |
| `FORBIDDEN` 403 | 내 워커만 해제할 수 있습니다. | 카드 인라인 |
| `WORKER_BUSY` 409 | 작업 중인 워커는 해제할 수 없습니다. 작업을 먼저 취소해 주세요. | 등록 해제 후 알림 |
| 토큰 401 | (authFetch 가 처리) | — |
| 조회 실패/네트워크 | 워커 목록을 불러오지 못했습니다. [다시 시도] | 페이지 |

`workers/errors.ts` 로 분리(도메인 소유 원칙). `WORKER_NOT_FOUND` 는 카드 인라인 표시 후 다음 폴링에서 목록이 정리된다.

## 8. 결정 필요

- [x] **다운로드 URL** — 같은 오리진 `/downloads/G-shared.exe` 로 결정. (a) GitLab raw 링크는 저장소가 비공개라 비로그인 사용자가 로그인 페이지(HTML)를 받게 되어 폐기. 원본은 저장소 `system/G-shared/G-shared.exe` 하나이고, 프론트 이미지 빌드 때 `docker-compose.yml` 의 `additional_contexts`(worker)로 `public/downloads/` 에 복사된다. nginx 는 기존 `/ → :3000` 그대로라 수정이 없다. 프론트는 `NEXT_PUBLIC_WORKER_DOWNLOAD_URL` 이 있으면 그 값을, 없으면 `/downloads/G-shared.exe` 를 쓴다.
- [ ] **`GET /api/workers/me` · `DELETE /api/workers/{id}`** — 백엔드 요청(§1-2). 직접 넣을지 요청할지는 사용자 결정(지갑 GET 때와 동일).
- [ ] **경로 이름** — `/workers` vs `/gpus`. 백엔드·워커 프로그램·랜딩 문구가 전부 "워커/Worker" 라 `/workers` 로 간다.
- [ ] **벤치마크가 없는 워커** — 현재 워커 프로그램은 벤치마크 미구현이면 **등록을 중단**한다(auth_check "cannot register this machine yet"). `sys-feature/gpu-benchmark` 머지 전까지는 실기기 E2E 불가 → 목업으로 개발. 화면은 `benchmarkTokensPerSec: null` 도 "측정 전" 으로 그려 둔다.
- [ ] **RUNNING 워커 삭제 정책** — 웹은 비활성으로 막지만 최종 방어는 백엔드. DELETE 스펙에 "RUNNING 이면 409" 를 넣을지 백엔드와 합의.
- [ ] **폴링 15초** — 대시보드가 여러 개 열리면 요청이 늘지만 워커 수가 적어 문제 없음. 서버 부담이 보이면 SSE 로.
- [x] **웹에서 register 호출** — 하지 않는다(§0).

## 9. 진행 상태 (2026-09-08)

- **프론트 구현 완료(목업 기준)**: §3 의 파일 전부. `tsc --noEmit`, `eslint src` 통과.
- **목업 검증(헤드리스 Chrome, `next build` + `next start` 로 MSW 켜서)**: guest → `/login?next=%2Fworkers` / 로그인 → 등록 안내 카드 + 다운로드 링크 / 네비 `워커 | 지갑 | 로그아웃` / 폴링 3회째(30초) 워커 카드 등장(`DESKTOP-4070 · 대기 중 · RTX 4070 · 12 GB · 1,835 tok/s · 20초 전`) / `<details>` 다른 PC 등록 / 등록 해제 → 안내 카드 / 랜딩 `내 GPU로 수익 내기` → `/workers`. 콘솔 오류 0.
- **백엔드 `GET /api/workers/me`·`DELETE /api/workers/{id}` 미반영** → 실서버(목업 off)에서는 `/workers` 가 "불러오지 못했습니다" 상태. 직접 넣을지 요청할지 결정 대기(§8).
- 실서버 확인 절차(백엔드 반영 후): 테스트 계정으로 `POST /api/workers/register` 를 curl 로 한 번 호출해 워커를 만들고(앱 대신) `/workers` 에 뜨는지 → 해제 → 사라지는지.

## 10. 작업 순서 (커밋 단위, 예정)

1. `[fe-feature] 워커 스키마·API·목업 + 보호 경로·네비 탭·랜딩 CTA` — schema/types/api/errors/format, `workerHandlers` + fixture, `mocks/handlers.ts`, `proxy.ts`, `navAuth.tsx`, `heroSection.tsx`, `.env.example`
2. `[fe-feature] 워커 화면: 등록 안내 카드·목록·상태 배지` — `(app)/workers`, `ui/statusBadge`, `workersPage`/`workerRegisterCard`/`workerCard`/`workerSkeleton`, worker.md
3. (백엔드 반영 후) 실서버 확인 → 문서 갱신
