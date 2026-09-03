import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // Docker 배포용: 실행에 필요한 최소 파일만 모아 이미지 경량화
};

export default nextConfig;
