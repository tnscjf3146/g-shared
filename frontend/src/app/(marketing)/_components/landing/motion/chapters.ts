import type { Timeline } from 'animejs'
import { P } from '../progress'

// 챕터 정의 — ../landing.md §2. 챕터 하나 = 함수 하나(타임라인에 트윈을 얹는다). 새 챕터는 여기에 함수를 추가하고 landingScene 에서 호출.
// 1–2 는 인트로(시간 ms, 로드 시 1회), 3 부터 스크롤(ms 단위 스크롤 sync).

/** 인트로 챕터 시작 시각(ms) */
const I = { top: 0, toFront: 1400, dark: 1650, hero: 2000, reveal: 3400, end: 5000 } // 빔이 팬들을 훑음 → 빔 꺼지고 잠깐 암전(그 사이 정면으로 틸트다운) → 빔 켜지며 로고 훑기 → 리빌
/** 스크롤 챕터 시작 시각(ms) */
export const T = { hold: 1500, fans: 1500, fansEnd: 3000, smoke: 4050, smokeEnd: 5500, shroud: 4500, shroudEnd: 6500, orbit: 8000, orbitEnd: 9000, hsLift: 10000, hsLiftEnd: 10500, hsOff: 12000, hsOffEnd: 13000, top: 14500, topEnd: 16000, stack: 18000, stackEnd: 20500, assemble: 21000, end: 23000 } // 챕터별 시작/끝(ms). 사이는 정지. 0–hold 는 인트로 직후 정지 구간(조립 상태 그대로) — 스크롤이 조금 움직여도 부품이 반응하지 않게

const CAM_INTRO_TOP = { yaw: Math.PI / 2, tilt: 0.315, dist: 3.3, lookY: -0.12 } // lookY 를 낮춰(타깃을 카드 아래로) 카드가 화면 위쪽에 오게 // 인트로 첫 컷: +X 끝 위 낮은 각(18°)에서 카드 길이 방향으로 내려다봄 — 가까운 팬이 크게, 나머지가 멀어지는 구도(Blender 레퍼런스)
const CAM_HERO = { yaw: Math.PI / 2, tilt: 0, dist: 2.35, lookY: 0.0225, shift: -0.05 } // 끝 캡 정면, G 원이 화면 정중앙

/** 0 INTRO TOP — +X 끝 위 낮은 각에서 카드 길이 방향을 보는 컷. 배경은 이 챕터만 딥 블랙(#deep), 이후는 dark(#333). 로고와 같은 스윕 빔이 카드 위를 −X→+X 로 지나가며 팬 3개를 차례로 비춘다(sweep 0→1).
 *  스윕이 끝나면 완전 암전(#blackout 검은 오버레이 — LED 점까지 안 보임), 그 사이 카메라가 끝 캡 정면으로 틸트다운한다(HERO 시작에 오버레이가 걷히고 빔이 켜짐). */
export function introTop(intro: Timeline) {
  intro.add(P, { yaw: [CAM_INTRO_TOP.yaw, CAM_INTRO_TOP.yaw], tilt: [CAM_INTRO_TOP.tilt, CAM_INTRO_TOP.tilt], dist: [CAM_INTRO_TOP.dist, CAM_INTRO_TOP.dist], lookY: [CAM_INTRO_TOP.lookY, CAM_INTRO_TOP.lookY], shift: [0, 0], env: [0, 0], duration: I.toFront - I.top, ease: 'linear' }, I.top)
  intro.add(P, { beam: [0, 1], duration: 250, ease: 'out(2)' }, I.top)
  intro.add(P, { sweep: [0, 1], duration: I.toFront - I.top, ease: 'inOut(2)' }, I.top)
  intro.add('#blackout', { opacity: [0, 1], duration: I.dark - I.toFront, ease: 'out(2)' }, I.toFront) // 먼저 완전 암전(카메라는 아직 그대로)
  intro.add('#deep', { opacity: [1, 0], duration: I.dark - I.toFront, ease: 'linear' }, I.toFront) // INTRO TOP 전용 딥 블랙 바닥을 암전 뒤에서 걷어냄 → 이후 배경은 bg-dark
  intro.add(P, { beam: [1, 0], duration: I.dark - I.toFront, ease: 'out(2)' }, I.toFront)
  // 오버레이가 다 덮인 뒤에야 카메라 이동 — 암전 전에 움직임이 보이지 않게
  intro.add(P, { yaw: [CAM_INTRO_TOP.yaw, CAM_HERO.yaw], tilt: [CAM_INTRO_TOP.tilt, CAM_HERO.tilt], dist: [CAM_INTRO_TOP.dist, CAM_HERO.dist], lookY: [CAM_INTRO_TOP.lookY, CAM_HERO.lookY], shift: [0, CAM_HERO.shift], env: [0, 0], sweep: [1, 2], duration: I.hero - I.dark, ease: 'linear' }, I.dark)
}

/** 1 HERO — 끝 캡 정면·암전. 빔이 다시 켜지며 화면 왼→오로 로고를 훑는다(sweep 2→3). 카메라는 상수 트윈으로 고정. */
export function hero(intro: Timeline) {
  intro.add(P, { yaw: [CAM_HERO.yaw, CAM_HERO.yaw], tilt: [CAM_HERO.tilt, CAM_HERO.tilt], dist: [CAM_HERO.dist, CAM_HERO.dist], lookY: [CAM_HERO.lookY, CAM_HERO.lookY], shift: [CAM_HERO.shift, CAM_HERO.shift], env: [0, 0], duration: I.reveal - I.hero, ease: 'linear' }, I.hero)
  intro.add('#blackout', { opacity: [1, 0], duration: 250, ease: 'out(2)' }, I.hero)
  intro.add(P, { beam: [0, 1], duration: 250, ease: 'out(2)' }, I.hero)
  intro.add(P, { sweep: [2, 3], duration: I.reveal - I.hero }, I.hero)
}

/** 2 REVEAL — 빔 아웃, 위 조명 인, 줌 아웃하며 캡이 우측 하단을 향하는 입체 뷰로. */
export function reveal(intro: Timeline) {
  intro.add(P, { beam: [1, 0], duration: 500, ease: 'out(2)' }, I.reveal)
  intro.add(P, { env: [0, 1], duration: 1400, ease: 'out(2)' }, I.reveal)
  intro.add(P, { yaw: [CAM_HERO.yaw, CAM0.yaw], tilt: [CAM_HERO.tilt, CAM0.tilt], dist: [CAM_HERO.dist, CAM0.dist], lookY: [CAM_HERO.lookY, 0], shift: [CAM_HERO.shift, 0], duration: I.end - I.reveal, ease: 'inOut(3)' }, I.reveal)
}

/** 3 FANS OUT — 정지 구간(0–1500) 뒤, 팬 3개가 **동시에** 위로 가속 상승해 화면 밖으로. */
export function fansOut(scroll: Timeline) {
  scroll.add(P, { fans: [0, 1], duration: T.fansEnd - T.fans, ease: 'in(2)' }, T.fans)
}

/** 4 SMOKE — 백플레이트 가장자리에서 연기가 밀려 나와 그 자리에서 흩어진다(퍼프 형태는 smoke.tsx). 쉬라우드가 다 올라갈 때까지 분사하고 사라진다. */
export function smoke(scroll: Timeline) {
  scroll.add(P, { smoke: [0, 1], duration: T.smokeEnd - T.smoke, ease: 'linear' }, T.smoke)
}

/** 5 SHROUD OFF — 연기가 흩어지는 사이 쉬라우드가 분리되어 위로 가속 상승, 화면 밖으로. */
export function shroudOff(scroll: Timeline) {
  scroll.add(P, { shroud: [0, 1], duration: T.shroudEnd - T.shroud, ease: 'in(2)' }, T.shroud)
}

/** 카메라 트랙 — 스크롤 전 구간의 카메라(yaw/tilt/dist/lookY/shift)를 여기 한 곳에서, 빈틈 없이 정의한다.
 *  anime.js 는 첫 트윈 전에는 값을 안 건드리고 되감기 뒤에는 가장 이른 트윈의 from 값을 남기므로, 정지 구간도 상수 트윈으로 채워
 *  어느 시점·어느 방향으로 스크롤해도 카메라가 한 값으로 정해지게 한다. 시작값 = REVEAL 끝 값. 다른 챕터 함수는 카메라를 만지지 않는다.
 *  (yaw: π/2 = +X 끝 캡 정면, 1.0 = 우하단 3/4, π−1.0 = 좌하단 3/4, 0 = 카메라가 +Z(PCIe 엣지) 쪽) */
const CAM0 = { yaw: 1.0, tilt: 0.55, dist: 6.5 }
const CAM_ORBIT = { yaw: Math.PI - 1.0, tilt: 0.55, dist: 6.5 } // 6 ORBIT: 끝 캡이 좌하단
const CAM_TOP = { yaw: 0, tilt: 0.785, dist: 5.6 } // 9 TOP VIEW: 위에서 −Y 쪽으로 45° 기울여, 살짝 확대
const CAM_STACK = { yaw: Math.PI * 2 - 0.9, tilt: 0.42, dist: 8.0 } // 10 STACK: 낮은 각으로 한 바퀴 돌아 멀리서
const CAM_END = { yaw: Math.PI * 2.5, tilt: 0.22, dist: 4.5 } // 11 ASSEMBLE: 끝 캡(로고) 정면 쪽, 살짝 위
type Cam = typeof CAM0
export function cameraTrack(scroll: Timeline) {
  const hold = (c: Cam, from: number, to: number) => scroll.add(P, { yaw: [c.yaw, c.yaw], tilt: [c.tilt, c.tilt], dist: [c.dist, c.dist], duration: to - from, ease: 'linear' }, from)
  const move = (a: Cam, b: Cam, from: number, to: number) => scroll.add(P, { yaw: [a.yaw, b.yaw], tilt: [a.tilt, b.tilt], dist: [a.dist, b.dist], duration: to - from, ease: 'inOut(2)' }, from)
  // 트랙은 1ms 부터 시작: 스크롤 0(=시간 0)에서 sync 렌더가 카메라를 쓰지 않아야 인트로가 카메라를 온전히 소유한다(첫 스크롤에 인트로는 완료되므로 이후엔 트랙이 소유)
  const START = 1
  scroll.add(P, { lookY: [0, 0], shift: [0, 0], duration: T.end - START, ease: 'linear' }, START)
  hold(CAM0, START, T.orbit)
  move(CAM0, CAM_ORBIT, T.orbit, T.orbitEnd)
  hold(CAM_ORBIT, T.orbitEnd, T.top)
  move(CAM_ORBIT, CAM_TOP, T.top, T.topEnd)
  hold(CAM_TOP, T.topEnd, T.stack)
  move(CAM_TOP, CAM_STACK, T.stack, T.stackEnd)
  hold(CAM_STACK, T.stackEnd, T.assemble)
  move(CAM_STACK, CAM_END, T.assemble, T.end)
}


/** 7 HS LIFT — 히트싱크(핀 66장 + 히트파이프)가 위로, 콜드플레이트·플레이트 브릿지는 그보다 조금만 들려 층이 구분된다. */
export function hsLift(scroll: Timeline) {
  scroll.add(P, { hsLift: [0, 1], duration: T.hsLiftEnd - T.hsLift, ease: 'inOut(2)' }, T.hsLift)
}

/** 8 HS OFF — 들린 히트싱크와 플레이트류가 함께 위로 상승해 화면 밖으로 나간 뒤 숨겨진다. */
export function hsOff(scroll: Timeline) {
  scroll.add(P, { hsOff: [0, 1], duration: T.hsOffEnd - T.hsOff, ease: 'in(1.2)' }, T.hsOff)
}

/** 9 TOP VIEW — 남은 카드(PCB·백플레이트·브라켓)를 위(Blender +Z)에서, 시선을 −Y(PCIe 엣지) 쪽으로 조금 기울여 내려본다.
 *  yaw 0 = 카메라가 glTF +Z(= Blender −Y) 쪽 → 화면 오른쪽 = +X, 아래 = PCIe 엣지. tilt 0.785 = 45°. */
export function topView(scroll: Timeline) {
  // 카메라는 cameraTrack(CAM_TOP). 여기선 탑뷰 동안만 위에서 비추는 조명
  scroll.add(P, { topLight: [0, 1], duration: T.topEnd - T.top, ease: 'out(2)' }, T.top)
}

/** 10 STACK — 햄버거 레시피 구조도. 나갔던 부품까지 전부 돌아와 Blender explode 배치(×STACK_SCALE)로 층층이 떠 있고,
 *  카메라는 낮은 각으로 내려와 한 바퀴 돌며(yaw 0 → 2π−0.9 ≈ 정면 3/4) 멀리서 전체를 담는다. */
export function stack(scroll: Timeline) {
  scroll.add(P, { stack: [0, 1], duration: 1200, ease: 'inOut(2)' }, T.stack)
  scroll.add(P, { topLight: [1, 0], duration: 1200, ease: 'inOut(2)' }, T.stack) // 탑뷰 조명 끔. 카메라는 cameraTrack(CAM_STACK)
}

/** 11 ASSEMBLE — 구조도의 층이 내려와 조립 완료. 카메라는 +X 끝 캡(로고) 정면 쪽으로 돌아와 살짝 위에서 로고를 본다. */
export function assemble(scroll: Timeline) {
  scroll.add(P, { assemble: [0, 1], duration: T.end - T.assemble, ease: 'inOut(2)' }, T.assemble) // 카메라는 cameraTrack(CAM_END)
}
