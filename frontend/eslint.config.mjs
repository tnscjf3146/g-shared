import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "public/mockServiceWorker.js",
  ]),
  // r3f 의 useFrame 은 three 객체(position/scale/uniform)를 매 프레임 직접 바꾸는 방식이 정석 → React 상태 불변성 규칙과 맞지 않아 3D 폴더만 해제
  {
    files: ["src/app/(marketing)/_components/landing/three/**"],
    rules: { "react-hooks/immutability": "off" },
  },
]);

export default eslintConfig;
