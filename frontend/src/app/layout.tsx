import type { Metadata } from "next";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import { Providers } from "./providers";
import "./globals.css";

// 서체는 Pretendard 하나만 쓴다(본문·제목·로고·숫자). globals.css 의 --font-sans/-heading/-logo/-mono 가 'Pretendard Variable' 을 읽는다.
// pretendard 패키지의 동적 서브셋 CSS: 가변 폰트를 유니코드 구간별 92조각으로 나눠 unicode-range 로 선언 → 브라우저가 화면에 쓰인 글자 구간만 내려받는다
// (단일 woff2 는 2MB, 서브셋은 한글 랜딩 기준 수백 KB). 파일은 Next 가 /_next/static/media 로 복사해 셀프호스팅

export const metadata: Metadata = {
  title: "G-shared — 유휴 GPU 공유 플랫폼",
  description: "잠자는 그래픽카드가 누군가의 AI가 됩니다. 전국의 유휴 GPU를 연결해 클라우드보다 저렴하게 학습을 돌리고, 정산은 블록체인이 증명합니다.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
