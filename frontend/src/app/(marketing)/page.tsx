import { LandingScene } from './_components/landing/landingScene'

// 랜딩은 다크. Blender(tools/gpu_model.py)로 만든 3팬 GPU GLB를 r3f로 띄우고, anime.js 로 챕터 연출(_components/landing/landing.md).
export default function LandingPage() {
  return (
    <main className="flex-1 bg-black">
      <LandingScene />
    </main>
  )
}
