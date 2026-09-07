import type { Metadata } from "next";
import localFont from "next/font/local";
import { Providers } from "./providers";
import "./globals.css";

// 서체는 Pretendard 하나만 쓴다(본문·제목·로고·숫자). globals.css 의 --font-sans/-heading/-logo/-mono 가 이 변수를 읽는다
// pretendard 패키지의 가변 woff2 를 next/font/local 로 셀프호스팅 → 외부 요청 없음, 100~900 전 굵기
const pretendard = localFont({
  src: "../../node_modules/pretendard/dist/web/variable/woff2/PretendardVariable.woff2",
  variable: "--font-pretendard",
  weight: "45 920",
  display: "swap",
});

export const metadata: Metadata = {
  title: "G-shared — 유휴 GPU 공유 플랫폼",
  description: "잠자는 그래픽카드가 누군가의 AI가 됩니다. 전국의 유휴 GPU를 연결해 클라우드보다 저렴하게 학습을 돌리고, 정산은 블록체인이 증명합니다.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className={`${pretendard.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
