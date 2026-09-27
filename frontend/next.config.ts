import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // Docker 배포용: 실행에 필요한 최소 파일만 모아 이미지 경량화
  // 로컬에서도 배포(nginx /api → :8080)와 같은 모양으로: 브라우저는 같은 오리진 /api 만 호출 → refresh 쿠키가 CORS 없이 동작
  async rewrites() {
    // `||` — .env 에 `BACKEND_ORIGIN=` 처럼 값 없이 키만 있으면 빈 문자열이라 `??` 로는 기본값이 안 먹고
    // destination 이 "/api/:path*" 가 돼 모든 API 가 404 가 된다(lib/fetcher.ts 와 같은 이유).
    const origin = process.env.BACKEND_ORIGIN || "http://localhost:8080";
    return [{ source: "/api/:path*", destination: `${origin}/api/:path*` }];
  },
  // 워커 실행파일(public/downloads/G-shared.exe, Dockerfile 이 이미지에 넣는다) — 브라우저가 열지 않고 파일로 저장하게
  async headers() {
    return [
      {
        source: "/downloads/G-shared.exe",
        headers: [
          { key: "Content-Type", value: "application/octet-stream" },
          { key: "Content-Disposition", value: 'attachment; filename="G-shared.exe"' },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
