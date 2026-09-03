# 프론트엔드 기획 (Epic: S15P21E204-22)

## 디자인

### 컬러 팔레트 (NVIDIA 테마)


| 색상                                                       | 역할                          | HEX       | RGB             |
| -------------------------------------------------------- | --------------------------- | --------- | --------------- |
| ![#76B900](https://placehold.co/20x20/76B900/76B900.png) | Primary (NVIDIA Green)      | `#76B900` | (118, 185, 0)   |
| ![#6AB900](https://placehold.co/20x20/6AB900/6AB900.png) | Primary Dark (hover/active) | `#6AB900` | (106, 185, 0)   |
| ![#75FF00](https://placehold.co/20x20/75FF00/75FF00.png) | Accent (강조/포인트)             | `#75FF00` | (117, 255, 0)   |
| ![#30E2AD](https://placehold.co/20x20/30E2AD/30E2AD.png) | Secondary Accent (민트)       | `#30E2AD` | (48, 226, 173)  |
| ![#5E5C63](https://placehold.co/20x20/5E5C63/5E5C63.png) | Muted Text (보조 텍스트)         | `#5E5C63` | (94, 92, 99)    |
| ![#AAAAAA](https://placehold.co/20x20/AAAAAA/AAAAAA.png) | Border / Disabled           | `#AAAAAA` | (170, 170, 170) |
| ![#333333](https://placehold.co/20x20/333333/333333.png) | Dark BG / 본문 텍스트            | `#333333` | (51, 51, 51)    |
| ![#0D0F0B](https://placehold.co/20x20/0D0F0B/0D0F0B.png) | Black (딥 블랙, 그림자 베이스)       | `#0D0F0B` | (13, 15, 11)    |
| ![#1C1E1A](https://placehold.co/20x20/1C1E1A/1C1E1A.png) | Black Soft (푸터/짙은 강조 영역)    | `#1C1E1A` | (28, 30, 26)    |
| ![#F7F9F4](https://placehold.co/20x20/F7F9F4/F7F9F4.png) | White (메인 배경)               | `#F7F9F4` | (247, 249, 244) |
| ![#ECEFE8](https://placehold.co/20x20/ECEFE8/ECEFE8.png) | White Dim (섹션 구분 배경)        | `#ECEFE8` | (236, 239, 232) |


```css
:root {
  --color-primary: #76B900;
  --color-primary-dark: #6AB900;
  --color-accent: #75FF00;
  --color-accent-mint: #30E2AD;
  --color-text-muted: #5E5C63;
  --color-border: #AAAAAA;
  --color-dark: #333333;
  --color-black: #0D0F0B;
  --color-black-soft: #1C1E1A;
  --color-white: #F7F9F4;
  --color-white-dim: #ECEFE8;
}
```

### 레이어 구조 (5단 Elevation)

컴포넌트가 배경 위에 둥둥 떠 있는 느낌을 주기 위해 5단 깊이 체계를 사용한다.
깊이가 올라갈수록 그림자가 커지고 부드러워지며, 배경도 한 톤씩 밝아진다. (라이트 테마 단일 — 다크모드 없음)


| 레벨  | 용도              | 배경        | 그림자                     |
| --- | --------------- | --------- | ----------------------- |
| L0  | 페이지 배경          | `#ECEFE8` | 없음                      |
| L1  | 섹션/패널           | `#F7F9F4` | `shadow-1` (은은하게 뜸)     |
| L2  | 카드/리스트 아이템      | `#FFFFFF` | `shadow-2` (기본 부유감)     |
| L3  | 드롭다운/팝오버/호버된 카드 | `#FFFFFF` | `shadow-3` (뚜렷하게 뜸)     |
| L4  | 모달/토스트/다이얼로그    | `#FFFFFF` | `shadow-4` (최상단, 가장 깊음) |


```css
:root {
  /* 깊이별 그림자 — 위로 갈수록 y-offset/blur를 키우고 투명도는 낮춰서 "멀리 뜬" 느낌 */
  --shadow-1: 0 1px 3px rgba(13, 15, 11, 0.08);
  --shadow-2: 0 2px 8px rgba(13, 15, 11, 0.10), 0 1px 2px rgba(13, 15, 11, 0.06);
  --shadow-3: 0 6px 16px rgba(13, 15, 11, 0.14), 0 2px 4px rgba(13, 15, 11, 0.08);
  --shadow-4: 0 16px 40px rgba(13, 15, 11, 0.20), 0 4px 8px rgba(13, 15, 11, 0.10);

  /* 강조 컴포넌트용 그린 글로우 (선택적) */
  --glow-primary: 0 0 12px rgba(118, 185, 0, 0.25);
}
```

#### 규칙

- 인터랙션 시 한 단계 위로: 카드(L2) hover → L3 그림자로 전환 + `translateY(-2px)`로 떠오르는 모션
- 강조가 필요한 컴포넌트(CTA 카드 등)에만 `--glow-primary`를 그림자에 추가
- 같은 레벨끼리는 겹치지 않게 배치 (겹침이 필요하면 반드시 상위 레벨 사용)
- z-index도 레벨에 맞춰 고정: L0=0, L1=10, L2=20, L3=30(드롭다운), L4=40(모달)

### 등장 모션 (변속 Entrance)

모든 컴포넌트는 등장할 때 **변속(비선형) 모션**으로 나타난다.
등속(linear) 금지 — 빠르게 진입해서 부드럽게 감속하며 제자리에 안착하는 곡선을 기본으로 한다.
레이어 구조와 연동: 아래에서 살짝 떠오르며(fade + rise) 나타나서 "둥둥 뜨는" 컨셉을 유지한다.

```css
:root {
  /* 변속 곡선 */
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);      /* 기본 등장: 급가속 → 긴 감속 */
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);     /* 팝오버/토스트: 살짝 오버슛(통통 튀는 느낌) */

  /* 레벨별 지속시간 — 높이 뜨는(상위 레벨) 컴포넌트일수록 길게 */
  --duration-l1: 300ms;  /* 섹션/패널 */
  --duration-l2: 350ms;  /* 카드 */
  --duration-l3: 400ms;  /* 드롭다운/팝오버 */
  --duration-l4: 450ms;  /* 모달/토스트 */
}

@keyframes enter-float {
  from { opacity: 0; transform: translateY(16px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* 기본 등장 */
.enter { animation: enter-float var(--duration-l2) var(--ease-out-expo) both; }

/* L3~L4(팝오버/모달/토스트)는 스프링 곡선으로 통통 튀게 — L3는 duration-l3, L4는 duration-l4 사용 */
.enter-spring-l3 { animation: enter-float var(--duration-l3) var(--ease-spring) both; }
.enter-spring-l4 { animation: enter-float var(--duration-l4) var(--ease-spring) both; }
```

#### 규칙

- **기본 곡선은 `--ease-out-expo`**: 처음 20%에서 대부분 이동하고 나머지 80%는 미세하게 감속 → 속도 변화가 눈에 보이는 변속감
- L3~L4(팝오버/모달/토스트)는 `--ease-spring`으로 도착 지점을 살짝(약 5%) 지나쳤다 돌아옴 → 떠오르다 살짝 튕기고 안착
- **리스트/그리드는 스태거 등장**: 아이템마다 `animation-delay`를 40ms씩 증가 (최대 8개까지, 이후는 동시 등장)
- 페이지 진입 시 등장 순서: L1 섹션 → L2 카드(스태거) 순으로 아래 레벨부터
- 퇴장은 등장의 절반 시간(150~200ms) + `ease-in`으로 빠르게 사라짐 (지루함 방지)
- `prefers-reduced-motion: reduce`일 땐 이동/스케일 없이 opacity 페이드만 (접근성)

## 컨벤션

### 기술 스택 (확정)

- **Next.js 16** (App Router) + **React 19** + **TypeScript**
- **Tailwind CSS v4** — 디자인 토큰은 CSS 변수로 관리
- 패키지 매니저: **pnpm** (`packageManager` 필드로 버전 고정, `pnpm install --frozen-lockfile`)
- 목업 API: **MSW v2** (devDependency, 환경변수로 on/off)

### 폴더 구조 (확정)

```
src/
  instrumentation.ts         # 서버 런타임 시작 시 MSW server 기동 (모킹 켜진 경우만)
  app/                       # 라우팅 전용
    providers.tsx            # 'use client' Provider 모음 (MswProvider 포함)
    (marketing)/             # 라우트 그룹, URL에 안 나옴
    (app)/
      layout.tsx             # ← Provider 조립 지점 (providers.tsx로 감쌈)
      dashboard/
        page.tsx
        loading.tsx
        error.tsx
        _components/         # 이 라우트에서만 쓰는 컴포넌트
  features/                  # 도메인 단위
    auth/
      components/ hooks/ api.ts schema.ts types.ts
      mocks/                 # ✅ 이 도메인의 목업 (도메인이 소유)
        handlers.ts          # http.get/post 핸들러 → export const authHandlers
        data.ts              # 고정 fixture, schema.parse()로 검증된 값만
    cart/                    # (예시) 도메인 스토어가 있는 경우
      components/ hooks/
      store.ts               # ✅ 도메인 스토어
      api.ts schema.ts types.ts
    editor/                  # (예시) 스토어가 커지면 slice로 분리
      store/
        index.ts
        canvas-slice.ts
        history-slice.ts
  components/
    ui/                      # Button, Input 같은 무상태 프리미티브
    layout/
  lib/
    stores/
      ui-store.ts            # ✅ 전역 스토어 (소수만)
      toast-store.ts
    utils.ts                 # 순수 유틸
    fetcher.ts               # 공통 API fetcher (ApiError throw)
    db.ts                    # db 클라이언트
  hooks/                     # 진짜 전역 훅만
  types/
  mocks/                     # MSW 진입점 — 조립만, 핸들러 본문 금지
    handlers.ts              # features/*/mocks/handlers.ts 를 모아 export
    browser.ts               # setupWorker(...handlers) — 클라이언트 fetch 가로채기
    server.ts                # setupServer(...handlers) — 서버 컴포넌트 fetch 가로채기
    msw-provider.tsx         # 'use client', worker.start() 후 children 렌더
public/
  mockServiceWorker.js       # `pnpm msw init public/` 산출물 — 커밋 대상
```

- `app/`은 라우팅만 담당 — 페이지 로직은 `features/`의 도메인 모듈로. Provider는 `app/providers.tsx`에 모아 `(app)/layout.tsx`에서 children을 감싼다 (layout은 서버 컴포넌트 유지)
- 특정 라우트 전용 컴포넌트는 해당 라우트의 `_components/`에, 도메인 공통은 `features/<도메인>/components/`에, 전역 공통은 `components/`에
- `hooks/`, `types/`는 전역에서 쓰는 것만 — 도메인 종속이면 `features/` 안으로
- **목업 배치**: 핸들러와 데이터는 도메인 소유 → `features/<도메인>/mocks/`. `src/mocks/`는 조립·기동 코드만. 앱 코드(components/hooks/api)에서 `mocks/` import 금지 — 목업 제거는 폴더 삭제로 끝나야 한다
- **스토어 배치**: 도메인 상태는 `features/<도메인>/store.ts`, 커지면 `store/` 폴더로 slice 분리. 전역 스토어는 `lib/stores/`에 두되 UI/토스트 수준의 소수만 허용 — 도메인 상태를 전역에 올리지 않는다

### 파일 네이밍 규칙 (확정)

Next.js 예약 파일(`page.tsx`, `layout.tsx`, `error.tsx` 등)은 소문자 고정. 그 외:


| 대상            | 규칙                     | 예시                              |
| ------------- | ---------------------- | ------------------------------- |
| 컴포넌트 파일       | camelCase              | `userCard.tsx`                  |
| 훅             | kebab-case (`use-` 접두) | `use-cart-total.ts`             |
| 유틸/스토어/스키마/폴더 | kebab-case             | `format-date.ts`, `ui-store.ts` |


→ 요약: **컴포넌트만 camelCase, 나머지 전부 kebab-case** (kebab은 `use-swr-config`처럼 대소문자 고민이 없음)

코드 내 네이밍:

- 변수/함수: `camelCase`
- 컴포넌트 export명: `PascalCase` (React 필수 — 소문자면 JSX가 HTML 태그로 인식)
- **상수: `UPPER_SNAKE_CASE`** (예: `MAX_RETRY_COUNT`, `API_BASE_URL`)
- 타입/인터페이스: `PascalCase`

기타:

- 한 파일 = 한 컴포넌트 (밖에서 안 쓰는 내부 컴포넌트만 같은 파일 허용)
- 배럴 파일(index.ts) 금지 — 예외 2가지: `features/<도메인>/index.ts`(공개 API 경계), `store/index.ts`(slice 조립 지점)
- import 경로 대소문자 불일치는 리눅스 CI에서만 터진다 → CI에 `tsc --noEmit` 필수

### 서버 / 클라이언트 컴포넌트 기준 (확정)

**기본은 서버 컴포넌트.** 훅·이벤트 핸들러·브라우저 API·Context를 쓸 때만 `'use client'`.

- `'use client'`는 경계 선언 — import하는 전부가 클라이언트 번들로 끌려가므로 **트리 잎(leaf) 쪽으로 최대한 밀어내기** (page.tsx 최상단 금지)
- 클라이언트 컴포넌트에 서버 컴포넌트를 넣으려면 import 말고 **children으로 전달**
- 서버→클라이언트 props는 직렬화 가능한 값만, DB 레코드는 필요 필드만 선별 (HTML에 노출됨)
- DB/시크릿 모듈엔 `import 'server-only'`, Provider는 `app/providers.tsx` 한 파일에
- 인터랙션 없는 프리미티브(`ui/card.tsx`, `ui/skeleton.tsx` 등)에 습관적 `'use client'` 금지

Next 16 주의: `params`/`searchParams`는 Promise(`await` 필요), 캐싱은 opt-in(`"use cache"`), `middleware.ts` → `proxy.ts`

### ESLint / Prettier (확정)

- ESLint: `eslint-config-next` 기본 + `eslint-plugin-check-file`(파일 네이밍 강제) + `eslint-plugin-import`(import 순서)
- Prettier: **기본값 그대로** + `prettier-plugin-tailwindcss`(클래스 자동 정렬) — 설정 논쟁 금지, 기본값이 곧 규칙
- 포맷은 저장 시 자동(formatOnSave), 사람이 손으로 정렬하지 않는다
- CI 필수 체크: `eslint` + `tsc --noEmit`

### Import 순서 (확정)

`eslint-plugin-import`의 `import/order`로 자동 정렬 (그룹 사이 빈 줄):

```ts
import { useState } from 'react'                  // 1. react / next
import { create } from 'zustand'                  // 2. 외부 라이브러리
import { Button } from '@/components/ui/button'   // 3. 내부 절대경로 (@/*)
import { formatDate } from './format-date'        // 4. 상대경로
import type { Job } from '@/types/job'            // 5. 타입은 import type으로
```

### 환경변수 관리 (확정)

- 로컬 값은 `.env.local`에만 (gitignore됨) — **환경변수 파일은 절대 커밋 금지**
- 키 목록 공유용으로 `.env.example`을 커밋 (값은 비우거나 더미로), 변수 추가 시 여기에도 반드시 추가
- `NEXT_PUBLIC_` 접두사가 붙은 것만 브라우저에 노출됨 → **시크릿에는 절대 붙이지 않는다**
- 노출 금지 목록: API 시크릿 키, DB 접속 URL, JWT 시크릿, 외부 서비스 토큰
- 서버 전용 환경변수를 읽는 모듈(`lib/db.ts` 등)에는 `import 'server-only'`로 방어

### API 에러 처리 (확정)

- 모든 요청은 공통 fetcher(`lib/fetcher.ts`) 경유 — 비정상 응답이면 공통 `ApiError`를 throw, 개별 화면에서 try/catch 남발 금지
- **조회(GET) 실패** → `error.tsx` 바운더리 or 인라인 "다시 시도" 버튼
- **액션(제출/저장) 실패** → 에러 토스트 (`toast-store`)
- 자동 재시도는 GET 네트워크 오류만 1회. **뮤테이션은 자동 재시도 금지** (중복 제출 위험) — 사용자가 버튼으로 재시도
- 서버 에러 코드 → 한국어 메시지 매핑 테이블 사용, raw 에러 메시지를 사용자에게 노출하지 않는다

### 목업 데이터 / MSW (확정)

**켜고 끄기는 환경변수 하나로.** `.env.local`에 `NEXT_PUBLIC_API_MOCKING=enabled`일 때만 동작, 코드 안에 `if (isDev)` 식 분기 금지.

- **기동 지점은 두 곳**: 클라이언트는 `app/providers.tsx`의 `MswProvider`(worker.start 완료 전엔 children 렌더 보류), 서버 컴포넌트는 `src/instrumentation.ts`에서 `server.listen()`. 둘 다 `await import('@/mocks/...')`로 동적 import → 프로덕션 번들에 msw 미포함
- **URL은 상수로**: 핸들러 경로는 `${API_BASE_URL}/jobs`처럼 실제 fetcher와 같은 상수 사용, 문자열 하드코딩 금지 (실 API 전환 시 경로 불일치 방지)
- **데이터는 스키마를 통과해야 한다**: `data.ts`는 `jobSchema.array().parse([...])`로 export — 목업이 `schema.ts`와 어긋나면 개발 서버에서 즉시 터진다
- **고정 fixture만**: faker/랜덤 금지. 화면 재현·스크린샷 비교가 가능해야 한다. 개인정보처럼 보이는 실제 값(이메일, 전화번호, 이름) 금지 — `user01@example.com` 식 더미
- **에러 응답은 백엔드 포맷 그대로**: `HttpResponse.json({ code: 'JOB_NOT_FOUND', message: ... }, { status: 404 })` — 에러 코드 → 한국어 매핑 테이블이 실제로 검증되도록
- **지연은 명시적으로**: 로딩 UI 확인용 `await delay(800)`은 핸들러 안에 두되, 기본은 `delay()`(실제 네트워크 수준 랜덤) 사용
- **핸들러 네이밍**: 배열 export명은 `<도메인>Handlers`(예: `authHandlers`), `src/mocks/handlers.ts`는 `[...authHandlers, ...jobHandlers]` 스프레드만
- **미처리 요청은 통과**: `onUnhandledRequest: 'bypass'` — 실 API가 준비된 도메인은 해당 핸들러만 지우면 그 요청은 실제 서버로 나간다 (도메인별 점진 전환)
- **서비스 워커 파일은 커밋**: `public/mockServiceWorker.js`는 msw 버전 업 시 `pnpm msw init public/`로 재생성 후 함께 커밋
- **`.env.example`에 키 추가**: `NEXT_PUBLIC_API_MOCKING=` (값 비움). 브라우저 노출되지만 시크릿 아니므로 `NEXT_PUBLIC_` 허용

### 로딩 / 빈 상태 UI (확정)

- **레이아웃이 예측되는 곳** (페이지, 카드 목록) → 스켈레톤 (`loading.tsx`, 레이어/등장 모션 규칙 적용)
- **액션 진행 중** (버튼 클릭 후) → 버튼 비활성 + 인라인 스피너
- **빈 상태**: 아이콘 + 한 줄 설명 + 다음 행동 버튼 — "데이터 없음" 텍스트만 던지기 금지

### API 타입 정의 (확정)

- 시작은 **수동 정의**: `features/<도메인>/types.ts` + zod `schema.ts`로 경계에서 런타임 검증 (서버 응답을 그대로 신뢰하지 않는다)
- 백엔드가 OpenAPI(Swagger) 스펙을 제공하게 되면 `openapi-typescript` 자동 생성으로 전환


<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
