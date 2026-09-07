# G-shared 프론트엔드

유휴 GPU 공유 플랫폼 G-shared 의 웹 프론트엔드. Next.js 16(App Router) + React 19 + TypeScript + Tailwind v4, 패키지 매니저는 pnpm.

## 실행

```bash
pnpm install --frozen-lockfile
cp .env.example .env.local   # BACKEND_ORIGIN 등
pnpm dev                     # http://localhost:3000
```

백엔드 없이 볼 때는 `.env.local` 에서 MSW 목업을 켠다(`.env.example` 참고). `/api/*` 는 `next.config.ts` 의 rewrite 로 `BACKEND_ORIGIN` 에 전달된다.

```bash
pnpm lint        # ESLint
npx tsc --noEmit # 타입 검사
pnpm build       # 프로덕션 빌드 (standalone)
```

## 문서

- `AGENTS.md` — 디자인 시스템(팔레트·서체·버튼·카드·반응형·레이어·모션)과 코드 컨벤션. 작업 전 필독
- `src/app/(marketing)/_components/landing/landing.md` — 랜딩 페이지(3D GPU 스크롤 연출) 설계
- `src/features/auth/auth.md` — 로그인·회원가입·토큰 흐름
- `tools/gpu_model.py` — Blender 로 `public/models/gpu.glb` 를 생성하는 스크립트

## 구조

```
src/
  app/            라우트. (marketing)=랜딩, (auth)=로그인·회원가입
  components/ui/  Button · Input · Field (globals.css 유틸리티 기반)
  features/       도메인별 api · schema · store · components · mocks
  lib/fetcher.ts  공통 fetch (same-origin /api)
  mocks/          MSW 핸들러 모음
```
