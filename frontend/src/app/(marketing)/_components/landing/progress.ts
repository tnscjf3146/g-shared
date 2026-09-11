/** GLB 가 로드되어 첫 프레임을 그릴 수 있을 때 gpuModel 이 window 에 쏘는 이벤트. landingScene 은 이걸 기다려 인트로를 시작한다(Hero 가 없어 첫 화면이 곧 캔버스) */
export const GPU_READY_EVENT = 'gshared:gpu-ready'

// anime.js 타임라인이 스크롤에 맞춰 이 숫자들을 움직이고, r3f useFrame 이 매 프레임 읽는다.
// (React state 를 거치지 않는 단일 공유 상태 — 리렌더 0)
export const P = {
  // 기본값 = 인트로 첫 컷(탑뷰, 암전). 이후는 전부 타임라인 트윈이 [from, to] 로 명시
  yaw: Math.PI / 2, // 카메라 방위각(rad). π/2 = +X 끝 쪽, 0 = +Z(PCIe 엣지) 쪽
  tilt: 0.315, // 카메라 고도각(rad)
  dist: 3.3,
  lookY: -0.12, // 카메라 타깃 높이
  spin: 1, // 팬 회전 속도 배율
  shift: 0, // 카드를 화면 오른쪽으로 미는 양, 0 = 중앙 (HERO 에선 -0.05 로 G 원 중심을 정중앙에)
  env: 0, // 전체 조명 배율. 0 = 암전
  sweep: 0, // 스윕 스팟 경로 0..1 팬 위 화면 왼→오(+Z→−Z) / 1..2 캡 면으로 내려옴 / 2..3 로고 왼→오
  beam: 0, // 스윕 스팟 세기 0..1
  fans: 0, // 팬 상승 이탈 0(제자리) → 1(화면 밖)
  smoke: 0, // 연기 퍼프 진행도 0..1 (smoke.tsx)
  shroud: 0, // 쉬라우드 분리 상승 0(제자리) → 1(화면 밖)
  hsLift: 0, // 히트싱크(핀+히트파이프)는 위로, 콜드플레이트·브릿지는 조금 들림 0..1
  hsOff: 0, // 들린 것들이 함께 위로 상승해 화면 밖으로 나간 뒤 숨김 0..1
  stack: 0, // 햄버거 레시피 구조도: 모든 부품이 Blender explode 배치로 층층이 모여 한눈에 0..1
  assemble: 0, // 구조도의 층 간격이 0 으로 줄어 조립 완료 0..1 (stack 유지 상태에서)
  topLight: 0, // 탑뷰 전용 위 조명 세기 0..1
  logoSweep: 0, // 조립 완료 뒤 로고를 좌우로 계속 훑는 빔 세기 0..1 (lights.tsx, 시간 기반 왕복)
  stars: 0, // 우주 배경 별 표시 0/1 — 인트로(암전·빔) 동안 0, REVEAL 에서 1 (starfield.tsx)
}
