# ── 1단계: 빌드 (pnpm install → next build) ──────────────
FROM node:22-alpine AS build
WORKDIR /app

# pnpm 활성화 (Node에 내장된 corepack 사용)
RUN corepack enable

# 의존성 먼저 설치 (package.json 안 바뀌면 캐시 재사용)
# 느린 네트워크 대비 재시도 여유 + pnpm10+ 빌드스크립트 승인(무시해도 exit1 안 나게)
ENV PNPM_CONFIG_STRICT_DEP_BUILDS=false
COPY package.json pnpm-lock.yaml ./
RUN pnpm config set fetch-retries 5 \
 && pnpm config set fetch-retry-maxtimeout 120000 \
 && pnpm config set network-concurrency 4 \
 && pnpm install --frozen-lockfile --config.strict-dep-builds=false

# 소스 복사 후 빌드 → .next/standalone 생성
COPY . .
RUN pnpm build

# ── 2단계: 실행 (standalone 결과만 담은 가벼운 이미지) ─────
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

# standalone 산출물 + 정적 파일 복사
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
