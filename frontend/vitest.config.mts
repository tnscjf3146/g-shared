import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// 기본 환경은 node — File·Blob.stream·crypto.subtle 이 Node 24 에 전역으로 있다. 훅 테스트만 파일 머리의 `// @vitest-environment jsdom` 으로 jsdom 을 쓴다.
// globals 는 끈다: 켜면 tsconfig 의 types 를 건드려야 하고 앱 코드 타입 검사에 영향이 간다 → 테스트 파일에서 vitest 를 직접 import 한다.
export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    // formatDateTime 같은 Intl 출력이 실행 환경의 시간대에 따라 달라지지 않게 고정
    env: { TZ: 'Asia/Seoul' },
    coverage: { provider: 'v8', include: ['src/features/jobs/format.ts'] },
  },
})
