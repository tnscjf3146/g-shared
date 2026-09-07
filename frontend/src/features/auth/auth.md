# 회원가입 · 로그인 구현 계획 (프론트)

브랜치: `fe-feature/auth` (develop 최신에서 분기). 백엔드 인증 API 는 `origin/develop` 에 이미 있음(`back/.../auth`). 이 문서는 그 스펙을 프론트에 어떻게 붙일지 정한다. 완료 후엔 이 문서를 구현 기준 설명서로 갱신해 둔다(랜딩의 `landing.md` 와 같은 방식).

## 0. 범위

- **한다**: 회원가입 화면, 로그인 화면, 로그아웃, 액세스 토큰 자동 재발급, 새로고침 시 세션 복구, 로그인 필요 라우트 보호, 목업(MSW) 핸들러.
- **안 한다**: 소셜 로그인, 비밀번호 찾기, 이메일 인증, 프로필 편집, 지갑 연결(`walletAddress` 는 백엔드에만 필드 존재).
- **전제**: 백엔드 응답은 그대로 신뢰하지 않고 zod 로 경계 검증(AGENTS.md). 라이브러리 추가 없음 — 폼은 네이티브 `<form>` + zod, 상태는 `useSyncExternalStore` 기반 작은 스토어(zustand 미설치, 필요 시 그때 도입).

## 1. 백엔드 API (확정 스펙, `back/src/main/java/com/gshared/back/auth`)

| 메서드/경로 | 요청 | 성공 | 실패 코드(`{ code, message }`) |
| --- | --- | --- | --- |
| `POST /api/auth/signup` | `{ email, password, nickname }` | `201` 빈 본문 | `INVALID_INPUT` 400(첫 필드 오류 메시지), `DUPLICATE_EMAIL` 409, `TOO_MANY_REQUESTS` 429 |
| `POST /api/auth/login` | `{ email, password }` | `200 { accessToken }` + `Set-Cookie: refreshToken`(HttpOnly, Path=/, 14d) | `INVALID_CREDENTIALS` 401, `TOO_MANY_REQUESTS` 429 |
| `POST /api/auth/reissue` | 본문 없음, 쿠키 `refreshToken` | `200 { accessToken }` + 새 refresh 쿠키(회전) | `INVALID_TOKEN` 401, `USER_NOT_FOUND` 404 |
| `POST /api/auth/logout` | `Authorization: Bearer <access>` | `204` (refresh 쿠키 삭제, access 블랙리스트) | `UNAUTHORIZED` / `EXPIRED_TOKEN` / `INVALID_TOKEN` 401 |

- 토큰 수명: access **30분**, refresh **14일**. access 는 `Authorization: Bearer` 헤더로만 받는다. 보호 API 에 토큰이 없거나 만료면 401 + 위 코드.
- 서버 검증 규칙(프론트 zod 에 **동일하게** 복제): 이메일 형식 / 비밀번호 6~20자 + 영문·숫자 포함 / 닉네임 2~15자 + 영문·숫자·한글만.
- **없는 것**: 내 정보 조회(`/users/me`) 엔드포인트가 아직 없다. access 토큰 payload 에 `sub`(userId), `role` 만 있고 닉네임은 없음 → §8 결정 필요.

## 2. API 오리진: 항상 같은 오리진 `/api`

배포는 nginx 가 `/api → :8080`, `/ → :3000` 으로 묶는다(`infra/nginx/default`). 로컬도 같은 모양으로 맞춰야 refresh 쿠키가 CORS 없이 동작한다(백엔드 `SecurityConfig` 에 CORS 설정이 없음).

- `next.config.ts` 에 rewrite 추가: `/api/:path*` → `${BACKEND_ORIGIN}/api/:path*` (기본 `http://localhost:8080`). 브라우저는 항상 `localhost:3000/api/...` 만 호출 → 쿠키는 3000 오리진에 저장되고 rewrite 를 타고 백엔드로 전달된다.
- `NEXT_PUBLIC_API_BASE_URL` 기본값을 `http://localhost:8080/api` → **`/api`** 로. 서버 컴포넌트에서 fetch 할 일이 생기면 그때 절대 URL 분기(지금은 인증 호출이 전부 브라우저에서 일어남).
- `.env.example` 에 `BACKEND_ORIGIN=` 추가.

## 3. 파일 구조 (AGENTS.md 골격) — 2026-09-07 로그인 구현 기준

```
src/
  app/
    (auth)/                         로그인/회원가입 라우트 그룹 — 흰 배경 + 초록 블러 원 2개 + 가운데 카드, 헤더 없음
      layout.tsx                    서버 컴포넌트. 배경·카드 컨테이너만                                  ✅
      login/page.tsx                <Suspense><LoginForm/></Suspense> (useSearchParams 때문에 경계 필요)  ✅
      signup/page.tsx               <AuthBackdrop variant="signup"/> + <SignupForm/>                  ✅
      _components/authBackdrop.tsx  초록 블러 원 2개(login: 좌상·우하 / signup: 우상·좌하)                  ✅
    providers.tsx                   <MswProvider><AuthProvider>…                                          ✅
  proxy.ts                          (Next 16: middleware 대체) 보호 경로 가드                              ⬜ (app) 그룹 생길 때
  features/auth/
    auth.md                         이 문서
    api.ts                          login / reissue(단일 비행) / logout / authFetch(Bearer + 401 재발급 1회 → 실패 시 /login?next=&reason=expired)  ✅
    schema.ts                       emailSchema · passwordSchema(6~20, 영문+숫자) · nicknameSchema(2~15) · signupSchema(확인 일치 refine) · loginSchema · tokenResponseSchema  ✅
    types.ts                        z.infer 타입 + AuthState                                                                                   ✅
    errors.ts                       코드 → 문구 표(§6) + RETRYABLE_401                                                                          ✅
    store.ts                        authStore(메모리) + useAuth() — useSyncExternalStore, zustand 없음                                          ✅
    components/loginForm.tsx        'use client'. 피그마 LoginCard 1:1                                                                          ✅
    components/authProvider.tsx     'use client'. 마운트 시 reissue 1회 → 세션 복구                                                              ✅
    components/navAuth.tsx          'use client'. 네비 우측: guest → 로그인 링크 / authed → 로그아웃 버튼(랜딩 siteNav 에서 사용)                 ✅
    components/signupForm.tsx       'use client'. 피그마 SignupCard 1:1 (닉네임·이메일·비밀번호·확인 → 계정 만들기 → /login?signup=done)             ✅
    hooks/use-zod-form.ts           두 폼 공통: 값·필드 오류·폼 오류·제출 중 + zod 검사(제출 시 전체, 오류 필드는 입력 중 재검사)                    ✅
    mocks/handlers.ts               authHandlers: signup(규칙 위반 400 INVALID_INPUT · 중복 409) / login / reissue / logout                       ✅
    mocks/data.ts                   fixture user01@example.com / pass1234 / 유저일                                                             ✅
  components/ui/
    button.tsx (Primary/Secondary/Ghost, href 면 Link) · input.tsx · field.tsx(라벨·오류·aria-describedby)                                      ✅
  mocks/handlers.ts                 [...jobHandlers, ...authHandlers]                                                                          ✅
  lib/fetcher.ts                    브라우저 '/api'(rewrite) / 서버 `${BACKEND_ORIGIN}/api`, 204·빈 본문 처리                                    ✅
next.config.ts                      rewrites: /api/:path* → BACKEND_ORIGIN(기본 http://localhost:8080)                                        ✅
```

네이밍: 컴포넌트 파일 camelCase(AGENTS.md), 훅 `use-*.ts`, 나머지 kebab-case. 배럴 파일 없음.

## 4. 토큰 · 세션 흐름

```
로그인 성공 ─→ accessToken 은 메모리(store), refreshToken 은 HttpOnly 쿠키(브라우저가 관리, JS 접근 불가)
새로고침    ─→ AuthProvider 가 reissue 1회. 단, localStorage 의 세션 표시(`gshared.session`, 로그인 성공 시 기록·로그아웃/실패 시 삭제)가 있을 때만 실제 호출
             → 200 이면 authed, 401 이면 표시 지우고 guest. 표시가 없으면 호출 없이 바로 guest(로그인 전 콘솔 401 노이즈 방지). 그동안 status='loading'
보호 API    ─→ authFetch: Bearer 붙여 호출 → 401(EXPIRED_TOKEN|INVALID_TOKEN|UNAUTHORIZED) 이면 reissue 1회 후 재시도
             → 그래도 401 이면 store 비우고 /login?next=<현재경로> 로
로그아웃    ─→ POST /logout(Bearer) → 성공/실패 무관하게 store 비우고 / 로
```

- access 토큰을 localStorage 에 넣지 않는다(XSS 노출). 탭마다 새로고침 시 reissue 로 복구되므로 충분하다.
- reissue 동시 호출 방지: 진행 중인 reissue Promise 를 하나만 두고 공유(요청 3개가 동시에 401 나도 재발급은 1번).
- `proxy.ts` 는 쿠키 **존재 여부**만 본다(서명 검증은 백엔드). 보호 경로 목록은 `(app)` 그룹이 생길 때 matcher 에 추가. 지금은 `/dashboard` 하나(페이지는 아직 없음 → 로그인 성공 후 이동 목적지는 §8).
- 로그인/회원가입 페이지에 이미 로그인된 상태로 오면 `next` 또는 `/` 로 보낸다.

## 5. 목업(MSW)

`NEXT_PUBLIC_API_MOCKING=enabled` 일 때만. 핸들러 경로는 `${API_BASE_URL}/auth/...` 상수 사용.

- fixture: `user01@example.com / pass1234 / 유저일` (schema.parse 통과). 비밀번호 검사도 fixture 와 비교.
- `signup`: fixture 이메일이면 `409 DUPLICATE_EMAIL`, 규칙 위반이면 `400 INVALID_INPUT` + 서버와 같은 문장, 아니면 `201`.
- `login`: 일치하면 `200 { accessToken: 'mock-access' }` + `Set-Cookie: refreshToken=mock-refresh; Path=/; HttpOnly`, 아니면 `401 INVALID_CREDENTIALS`.
- `reissue`: 요청 쿠키에 `refreshToken` 있으면 200, 없으면 `401 INVALID_TOKEN`.
- `logout`: `Authorization` 없으면 401, 있으면 204 + 쿠키 만료.
- `delay()` 기본값 사용(로딩 UI 확인용). 에러 본문은 백엔드 포맷 `{ code, message }` 그대로.

## 6. 에러 메시지 표 (`errors.ts`)

서버 메시지는 이미 한국어지만 AGENTS.md 규칙대로 raw 노출 없이 코드 → 문구 매핑을 거친다. `INVALID_INPUT` 만 서버가 준 첫 필드 메시지를 그대로 쓴다(필드 규칙 문장이라 안전).

| 코드 | 화면 문구 | 표시 위치 |
| --- | --- | --- |
| `INVALID_CREDENTIALS` | 이메일 또는 비밀번호가 올바르지 않습니다. | 로그인 폼 상단 |
| `DUPLICATE_EMAIL` | 이미 사용 중인 이메일입니다. | 이메일 필드 아래 |
| `INVALID_INPUT` | (서버 메시지) | 폼 상단 |
| `TOO_MANY_REQUESTS` | 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요. | 폼 상단 |
| `EXPIRED_TOKEN` `INVALID_TOKEN` `UNAUTHORIZED` | 다시 로그인해 주세요. | 로그인 페이지 상단(리다이렉트 후) |
| 그 외 / 네트워크 | 잠시 후 다시 시도해 주세요. | 폼 상단 |

뮤테이션은 자동 재시도 금지 — 사용자가 버튼으로 다시 시도.

## 7. 화면 · UX — 피그마 `P2 로그인`(32:333) · `P3 회원가입`(38:346) 기준

파일 `I2kKzmKse6GThMRuKqLFcm` · 페이지 `Screens`. 두 화면은 같은 골격이다: **흰 배경 + 초록 블러 원 2개 + 가운데 카드 440px**. 헤더 없음.

공통 (`app/(auth)/layout.tsx` + `components/ui/*`)
- 배경 `#ffffff`. 장식 원: `#76b900` 10%(420px, blur 80) / 20%(360px, blur 90). 로그인은 좌상·우하, 회원가입은 우상·좌하에 배치 → 레이아웃에서 `data-variant` 로 위치만 바꿈.
- 카드 `LoginCard`/`SignupCard`: 폭 440, 패딩 40, radius 16, 테두리 `#aaaaaa`, 그림자(`shadow-2`), 세로 gap 20(로그인)/18(회원가입). 카드 상단 로고 `G-shared`(Barlow SemiBold 32, `#333333`) + 부제(Inter 15, `#5e5c63`).
- 필드(`components/ui/field.tsx`): 라벨(Inter Medium 13, `#5e5c63`) → 입력(높이 46, radius 12, 테두리 `#aaaaaa`, 패딩 14/16, 플레이스홀더 Inter 15 `#5e5c63`) → 오류 문구(13, `--color-danger`). 라벨-입력 gap 6.
- 버튼(`components/ui/button.tsx`): 피그마 `Button` 컴포넌트와 1:1. Primary = `#76b900` 배경·흰 글자, Secondary = 흰 배경·`#aaaaaa` 테두리. 높이 46, radius 12, 패딩 12/24, 폭은 카드 안에서 FILL. 제출 중엔 비활성 + 인라인 스피너.
- 하단 링크(Inter 14, `#76b900`): 로그인 ↔ 회원가입 상호 이동. 안내문(Inter 12, `#5e5c63`).

로그인 카드 순서(구현): 로고 → (가입 완료/만료 안내) → 이메일 → 비밀번호(보기 토글) → **[로그인]** Primary → `계정이 없으신가요? 회원가입 →` (부제·지갑 안내문은 2026-09-07 제거) — 피그마의 구분선 `또는` + **[🦊 지갑으로 연결]** 은 지갑 기능 브랜치에서 넣기로 하고 뺐다(2026-09-07 결정).

회원가입 카드 순서(구현): 로고 → `계정을 만들고 GPU를 쓰거나, 공유해 보세요` → 닉네임 → 이메일 → 비밀번호(플레이스홀더 `6~20자, 영문+숫자`, 보기 토글은 확인 필드와 공유) → 비밀번호 확인 → **[계정 만들기]** Primary → `이미 계정이 있으신가요? 로그인 →` → `가입 후 지갑을 연결하면 예치·정산 기능이 활성화됩니다.` 성공 시 `/login?signup=done` 으로 이동해 로그인 카드 상단에 초록 안내.

동작
- 검증 타이밍: 제출 시 zod 전체 검사 → 필드별 인라인 오류. 한 번 오류 난 필드는 입력 중 재검사. 비밀번호 확인은 프론트에서만 검사(서버 요청엔 안 보냄). 약관 동의 체크박스는 백엔드(`SignupRequest`)가 받지 않아 두지 않는다(2026-09-07 제거).
- 성공: 로그인 → `next` 로, 회원가입 → 로그인 페이지로 이동하며 "가입 완료" 안내.
- 접근성: `<label htmlFor>`, 오류 `aria-describedby`, 비밀번호 보기 토글은 버튼.
- **지갑 연결 버튼**은 지갑 기능 브랜치에서 — 이번엔 카드에서 뺐다(안내 문구만 유지).
- 랜딩(`/`)의 Nav `로그인` 필이 `/login` 으로 진입하는 유일한 경로(landing.md §0-1). 랜딩 Hero 구현 시 링크 연결.

## 8. 결정 필요

- [x] **비밀번호 규칙 불일치**: zod 는 백엔드(6~20)를 따름(`passwordSchema`). 로그인 폼은 형식 검사 없이 존재만 확인(규칙 노출 방지). 회원가입 플레이스홀더는 `6~20자, 영문+숫자` 로 → 디자이너에게 피그마 수정 요청 남음.
- [x] **로그인 후 이동 목적지**: `?next=` 가 있으면 그곳, 없으면 `/`(임시). `/dashboard` 가 생기면 기본값만 바꾼다.
- [ ] **내 정보(닉네임) 표시**: 백엔드에 `GET /api/users/me` 가 없어 헤더에 닉네임을 못 띄운다. 백엔드에 요청할지(추천), 우선 토큰의 `role` 만 쓸지.
- [ ] **refresh 쿠키 SameSite**: 백엔드 `CookieUtil` 이 SameSite 를 지정하지 않아 브라우저 기본(Lax). 같은 오리진 구조에선 문제없지만, 배포 도메인이 프론트/백 분리되면 `None; Secure` 필요 → 백엔드에 전달.
- [x] **토스트**: 폼 상단 인라인 메시지로 구현. 토스트 스토어가 생기면 교체.
- [ ] **약관 페이지**: 푸터의 `이용약관`/`개인정보처리방침` 링크 대상이 없다. 우선 홈(`/`)으로 보내고 문서 페이지는 별도.

## 9. 작업 순서 (커밋 단위) — 1~4 완료 (2026-09-07). 남은 것: proxy.ts 는 보호할 (app) 페이지가 생길 때

1. `[fe-chore] API 오리진 통일` — next.config rewrite, fetcher 기본 URL `/api`, `.env.example`
2. `[fe-feature] 인증 스키마·API·스토어` — schema/types/errors/api/store, MSW 핸들러 + fixture (화면 없이 목업으로 호출 검증)
3. `[fe-feature] 로그인·회원가입 화면` — ui 프리미티브 3개(button/input/field, 피그마 Button·Field 와 1:1), (auth) 레이아웃(배경 원 2개), 두 폼, use-auth-form
4. `[fe-feature] 세션 복구·보호 라우트·로그아웃` — AuthProvider, authFetch 재발급, proxy.ts, LogoutButton
5. (문서) 이 파일을 구현 결과 기준으로 갱신

검증: 목업 켠 상태에서 4가지 시나리오(가입 성공/중복, 로그인 성공/실패, 새로고침 복구, 로그아웃) + 목업 끄고 로컬 백엔드(`:8080`, Redis 필요)로 한 번. `tsc --noEmit`, `eslint` 통과.
