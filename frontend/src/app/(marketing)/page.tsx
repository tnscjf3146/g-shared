import { A100Scene } from './_components/a100Scene'

// 랜딩은 다크. Step 2: 스크롤로 카메라가 상판 정면 → 등각 뷰로 이동.
export default function LandingPage() {
  return (
    <main className="flex-1 bg-black">
      <A100Scene />
    </main>
  )
}
