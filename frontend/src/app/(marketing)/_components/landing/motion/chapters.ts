import type { Timeline } from 'animejs'
import { P } from '../progress'

// 챕터 정의 — ../landing.md §2. 챕터 하나 = 함수 하나(타임라인에 트윈을 얹는다). 새 챕터는 여기에 함수를 추가하고 landingScene 에서 호출.
// 1–2 는 인트로(시간 ms, 로드 시 1회), 3 부터 스크롤(ms 단위 스크롤 sync).

/** 인트로 챕터 시작 시각(ms) */
const I = { top: 0, toFront: 1400, dark: 1650, hero: 2000, reveal: 3400, end: 5000 } // 빔이 팬들을 훑음 → 빔 꺼지고 잠깐 암전(그 사이 정면으로 틸트다운) → 빔 켜지며 로고 훑기 → 리빌
/** 스크롤 챕터 트리거(landingScene 챕터 스텝 + 오른쪽 챕터 사이드바 공용): 스크롤이 at(ms) 를 넘으면 타임라인을 to(ms) 까지 제 속도로 재생.
 *  실제 재생 시간은 거리×STEP_RATE 를 STEP_MIN~STEP_MAX 로 묶되 duration 이 있으면 그 값(landingScene). label 은 사이드바 표기 */
export const CHAPTERS: { label: string; at: number; to: number; duration?: number; ease?: string }[] = []
/** 사이드바 항목 7개 = 표지(index 0, 인트로 직후 조립 상태) + CHAPTERS 6개.
 *  라벨은 부품 이름이 아니라 서비스 챕터다(2026-09-14 사용자 결정): 01 G-shared(표지) · 02~06 서비스 5장 · 07 시작하기(CTA 자리, 내용 없음).
 *  어느 모션인지는 각 줄 끝 주석(팬 분리·오빗·히트싱크·탑뷰·구조도·조립)으로 남긴다 */
export const CHAPTER_LABELS: string[] = []
/** 스크롤 챕터 시작 시각(ms) */
export const T = { hold: 1000, fans: 1000, fansEnd: 3000, smoke: 1000, smokeEnd: 2500, shroud: 1000, shroudEnd: 3000, orbit: 3500, orbitEnd: 4500, hsLift: 5000, hsLiftEnd: 5500, hsOff: 5500, hsOffEnd: 6100, top: 7500, topEnd: 9000, stack: 9500, stackEnd: 12000, assemble: 12500, assembleEnd: 14500, end: 15000 } // 챕터별 시작/끝(ms). 0–hold(1000) 는 인트로 직후 정지(조립 상태 그대로, 살짝 스크롤해도 부품이 반응하지 않게 — 이 구간 스크롤은 배경 판 1 의 밝기만 올린다). 그 뒤 챕터 사이 정지는 전부 500, ASSEMBLE 뒤에도 500 정지 후 완료(end). 팬·연기·쉬라우드는 한 챕터(같이 분해)
CHAPTERS.push(
  { label: '잠든카드', at: T.fans, to: T.fansEnd }, // 3 팬·연기·쉬라우드
  { label: '사용자', at: T.orbit, to: T.orbitEnd }, // 4 카메라(오빗)
  { label: '공급자', at: T.hsLift, to: T.hsOffEnd, duration: 1000, ease: 'linear' }, // 5+6 들림 → 이탈을 한 챕터로(2026-09-11 합침, 사이 공백 제거). 1초: 들림 ~0.45초 뒤 이탈 ~0.55초(in(3) 가속) — 이탈을 더 빠르게 한 최종값. 커서는 선형 — 이탈 트윈의 가속(in(3))이 그대로 보이게
  { label: '에스크로', at: T.top, to: T.topEnd }, // 7 탑뷰
  { label: '블록체인', at: T.stack, to: T.stackEnd }, // 8 구조도
  { label: '시작하기', at: T.assemble, to: T.end }, // 9 조립 + 로고 스윕 — 내용이 아니라 마무리(CTA 자리)
)
CHAPTER_LABELS.push('G-shared', ...CHAPTERS.map((c) => c.label))
/** 챕터별 설명(사이드바 라벨과 같은 순서 7개) — chapterCopy.tsx 가 화면 왼쪽 아래에 띄운다.
 *  `\n` 은 줄바꿈으로 그대로 나온다(chapterCopy.module.css 의 white-space: pre-line) — 끊을 자리를 카피가 직접 정한다.
 *  05·06 은 지금 구현된 범위까지만 말한다(에스크로 컨트랙트는 SSAFY 체인 미배포, fund API 는 브랜치) — 배포되면 문구를 올린다.
 *  온체인에 남는 것은 참조값·금액·양쪽 지갑·상태·결과 해시뿐이라 06 은 "정산 기록" 이라고만 한다(데이터셋·학습 설정은 백엔드 DB) */
export const CHAPTER_COPY: { title: string; body: string }[] = [
  { title: '남는 GPU를 빌려주고,\n필요한 GPU를 활용해 보세요.', body: '유휴 그래픽카드로 AI 학습을 돌리는 연산 마켓.' },
  { title: 'AI의 발전,\n그에 따른 그래픽카드의 가격 상승', body: '고가의 GPU 카드, 혹시 지금 어떤 작업을 하고 있나요?' },
  { title: '더 이상 비싼 초기비용 고민은 그만!', body: '유형, 베이스 모델, 데이터셋만 고르면,\n공급자가 제공하는 GPU에서 작업 가능!' },
  { title: '사용하지 않는 GPU 자원,\n필요한 사람에게 잠시 빌려주세요', body: '놀리면 0원, 빌려주면 수익.\nPC를 켜 두는 것만으로 돈이 벌립니다!' },
  { title: '모두가 믿을 수 있는 결제 시스템', body: 'AI 견적대로 미리 예치, 끝나면 자동 지급.\n작업이 취소되면 전액 환불됩니다!' },
  { title: '투명한 계약\n거래의 신뢰성', body: '블록체인 기술을 접목해\n정산에 쓰인 기록을 블록에 남깁니다.\n지울 수도, 바꿀 수도 없습니다.' },
  { title: '지금 놀고 있는 카드부터', body: '등록은 프로그램 한 번 실행, 학습은 주문서 한 장.' },
]

const CAM_INTRO_TOP = { yaw: Math.PI / 2, tilt: 0.315, dist: 3.3, lookY: -0.12 } // lookY 를 낮춰(타깃을 카드 아래로) 카드가 화면 위쪽에 오게 // 인트로 첫 컷: +X 끝 위 낮은 각(18°)에서 카드 길이 방향으로 내려다봄 — 가까운 팬이 크게, 나머지가 멀어지는 구도(Blender 레퍼런스)
const CAM_HERO = { yaw: Math.PI / 2, tilt: 0, dist: 2.35, lookY: 0.0225, shift: -0.05 } // 끝 캡 정면, G 원이 화면 정중앙

/** 0 INTRO TOP — +X 끝 위 낮은 각에서 카드 길이 방향을 보는 컷. 배경은 검정(sticky 바닥). 로고와 같은 스윕 빔이 카드 위를 −X→+X 로 지나가며 팬 3개를 차례로 비춘다(sweep 0→1).
 *  스윕이 끝나면 완전 암전(#blackout 검은 오버레이 — LED 점까지 안 보임), 그 사이 카메라가 끝 캡 정면으로 틸트다운한다(HERO 시작에 오버레이가 걷히고 빔이 켜짐). */
export function introTop(intro: Timeline) {
  intro.add(P, { yaw: [CAM_INTRO_TOP.yaw, CAM_INTRO_TOP.yaw], tilt: [CAM_INTRO_TOP.tilt, CAM_INTRO_TOP.tilt], dist: [CAM_INTRO_TOP.dist, CAM_INTRO_TOP.dist], lookY: [CAM_INTRO_TOP.lookY, CAM_INTRO_TOP.lookY], shift: [0, 0], env: [0, 0], duration: I.toFront - I.top, ease: 'linear' }, I.top)
  intro.add(P, { beam: [0, 1], duration: 250, ease: 'out(2)' }, I.top)
  intro.add(P, { sweep: [0, 1], duration: I.toFront - I.top, ease: 'inOut(2)' }, I.top)
  intro.add('#blackout', { opacity: [0, 1], duration: I.dark - I.toFront, ease: 'out(2)' }, I.toFront) // 먼저 완전 암전(카메라는 아직 그대로)
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

/** 배경 금속판(three/backdrop.tsx) — 타임라인에 없다. 판 3장의 등장이 전부 **스크롤 스크럽**이라 landingScene 이 구간 진행률을 P 에 직접 쓴다:
 *  판 1(차콜, 쉬라우드 재질)은 01 카드 구간을 내려가는 정도에 비례해 밝아지고(P.plate, 2026-09-12 사용자 요청 — 02 도착 시 600ms 페이드인이었다),
 *  판 2(검정 단색)는 03 구간 진행률로 아래에서 위로 덮으며(P.plate2W), 판 3(맨들한 스틸)은 04 부터 끝까지 조명이 켜지듯 드러난다(P.light).
 *  검정에 다 덮인 뒤 판 1 을 숨기는 것도 backdrop 이 P.plate2W(스크럽 값)로 판단한다 — 타임라인 되감기(최대 2.8초)를 기다리면
 *  빠른 백스크롤에서 검정이 벗겨진 자리에 판 1 이 아직 없어 바닥이 보이다가 뒤늦게 튀어나왔다(2026-09-12 수정). */

/** 3 FANS + SHROUD OFF — 정지 구간(0–1000) 뒤, 팬 3개와 쉬라우드가 **한 번에** 위로 가속 상승해 화면 밖으로(fansOut·smoke·shroudOff 가 같은 구간에서 함께 돈다). */
export function fansOut(scroll: Timeline) {
  scroll.add(P, { fans: [0, 1], duration: T.fansEnd - T.fans, ease: 'in(2)' }, T.fans)
}

/** 3 SMOKE — 팬·쉬라우드가 올라가는 사이 백플레이트 가장자리에서 연기가 밀려 나와 그 자리에서 흩어진다(퍼프 형태는 smoke.tsx). */
export function smoke(scroll: Timeline) {
  scroll.add(P, { smoke: [0, 1], duration: T.smokeEnd - T.smoke, ease: 'linear' }, T.smoke)
}

/** 3 SHROUD OFF — 팬과 같은 구간에 쉬라우드가 분리되어 위로 가속 상승, 화면 밖으로. */
export function shroudOff(scroll: Timeline) {
  scroll.add(P, { shroud: [0, 1], duration: T.shroudEnd - T.shroud, ease: 'in(2)' }, T.shroud)
}

/** 카메라 트랙 — 스크롤 전 구간의 카메라(yaw/tilt/dist/lookY/shift)를 여기 한 곳에서, 빈틈 없이 정의한다.
 *  anime.js 는 첫 트윈 전에는 값을 안 건드리고 되감기 뒤에는 가장 이른 트윈의 from 값을 남기므로, 정지 구간도 상수 트윈으로 채워
 *  어느 시점·어느 방향으로 스크롤해도 카메라가 한 값으로 정해지게 한다. 시작값 = REVEAL 끝 값. 다른 챕터 함수는 카메라를 만지지 않는다.
 *  (yaw: π/2 = +X 끝 캡 정면, 1.0 = 우하단 3/4, π−1.0 = 좌하단 3/4, 0 = 카메라가 +Z(PCIe 엣지) 쪽) */
const SCROLL_SHIFT = 0.45 // 02~06 에서 카드가 오른쪽으로 밀리는 양(+ = 오른쪽, 1424px 화면에서 약 100px). 표지(01)와 조립 뒤(07)는 0 = 중앙(2026-09-14 사용자 결정). 왼쪽 카피는 카드가 중앙일 때 기준으로 폭을 잡으므로(chapterCopy.module.css) 밀리면 여유만 늘어난다
export const CAM0 = { yaw: 1.0, tilt: 0.55, dist: 6.5 }
export const CAM_ORBIT = { yaw: Math.PI - 1.0, tilt: 0.55, dist: 6.5 } // 4 ORBIT: 끝 캡이 좌하단
export const CAM_TOP = { yaw: 0, tilt: 0.785, dist: 5.6 } // 7 TOP VIEW: 위에서 −Y 쪽으로 45° 기울여, 살짝 확대
export const CAM_STACK = { yaw: Math.PI * 2 - 0.9, tilt: 0.42, dist: 8.0 } // 8 STACK: 낮은 각으로 한 바퀴 돌아 멀리서
export const CAM_END = { yaw: Math.PI * 2.5, tilt: 0.22, dist: 4.5 } // 9 ASSEMBLE: 끝 캡(로고) 정면 쪽, 살짝 위
type Cam = typeof CAM0
export function cameraTrack(scroll: Timeline) {
  const hold = (c: Cam, from: number, to: number) => scroll.add(P, { yaw: [c.yaw, c.yaw], tilt: [c.tilt, c.tilt], dist: [c.dist, c.dist], duration: to - from, ease: 'linear' }, from)
  const move = (a: Cam, b: Cam, from: number, to: number) => scroll.add(P, { yaw: [a.yaw, b.yaw], tilt: [a.tilt, b.tilt], dist: [a.dist, b.dist], duration: to - from, ease: 'inOut(2)' }, from)
  // 트랙은 1ms 부터 시작: 스크롤 0(=시간 0)에서 sync 렌더가 카메라를 쓰지 않아야 인트로가 카메라를 온전히 소유한다(첫 스크롤에 인트로는 완료되므로 이후엔 트랙이 소유)
  const START = 1
  scroll.add(P, { lookY: [0, 0], duration: T.end - START, ease: 'linear' }, START)
  // 카드 가로 위치: 표지(01)는 중앙 → 02(팬·쉬라우드 분리)가 재생되는 동안 SCROLL_SHIFT 만큼 오른쪽으로 → 06 까지 유지 → 07(조립)이 재생되는 동안 다시 중앙으로(2026-09-14 사용자 결정).
  // 정지 구간도 상수 트윈으로 채워 어느 방향으로 되감아도 한 값으로 정해진다
  scroll.add(P, { shift: [0, 0], duration: T.fans - START, ease: 'linear' }, START)
  scroll.add(P, { shift: [0, SCROLL_SHIFT], duration: T.fansEnd - T.fans, ease: 'inOut(2)' }, T.fans)
  scroll.add(P, { shift: [SCROLL_SHIFT, SCROLL_SHIFT], duration: T.assemble - T.fansEnd, ease: 'linear' }, T.fansEnd)
  scroll.add(P, { shift: [SCROLL_SHIFT, 0], duration: T.assembleEnd - T.assemble, ease: 'inOut(2)' }, T.assemble)
  scroll.add(P, { shift: [0, 0], duration: T.end - T.assembleEnd, ease: 'linear' }, T.assembleEnd)
  hold(CAM0, START, T.orbit)
  move(CAM0, CAM_ORBIT, T.orbit, T.orbitEnd)
  hold(CAM_ORBIT, T.orbitEnd, T.top)
  move(CAM_ORBIT, CAM_TOP, T.top, T.topEnd)
  hold(CAM_TOP, T.topEnd, T.stack)
  move(CAM_TOP, CAM_STACK, T.stack, T.stackEnd)
  hold(CAM_STACK, T.stackEnd, T.assemble)
  move(CAM_STACK, CAM_END, T.assemble, T.assembleEnd)
  hold(CAM_END, T.assembleEnd, T.end) // 조립 완료 뒤 500 정지 → DONE
}


/** 5 HS LIFT — 히트싱크(핀 66장 + 히트파이프)가 위로, 콜드플레이트·플레이트 브릿지는 그보다 조금만 들려 층이 구분된다. */
export function hsLift(scroll: Timeline) {
  scroll.add(P, { hsLift: [0, 1], duration: T.hsLiftEnd - T.hsLift, ease: 'inOut(2)' }, T.hsLift)
}

/** 6 HS OFF — 들린 히트싱크와 플레이트류가 함께 위로 상승해 화면 밖으로 나간 뒤 숨겨진다. */
export function hsOff(scroll: Timeline) {
  scroll.add(P, { hsOff: [0, 1], duration: T.hsOffEnd - T.hsOff, ease: 'in(3)' }, T.hsOff) // 천천히 떠오르다 급가속해 화면 밖으로(2026-09-11, in(1.2)→in(3))
}

/** 7 TOP VIEW — 남은 카드(PCB·백플레이트·브라켓)를 위(Blender +Z)에서, 시선을 −Y(PCIe 엣지) 쪽으로 조금 기울여 내려본다.
 *  yaw 0 = 카메라가 glTF +Z(= Blender −Y) 쪽 → 화면 오른쪽 = +X, 아래 = PCIe 엣지. tilt 0.785 = 45°. */
export function topView(scroll: Timeline) {
  // 카메라는 cameraTrack(CAM_TOP). 여기선 탑뷰 동안만 위에서 비추는 조명
  scroll.add(P, { topLight: [0, 1], duration: T.topEnd - T.top, ease: 'out(2)' }, T.top)
}

/** 8 STACK — 햄버거 레시피 구조도. 나갔던 부품까지 전부 돌아와 Blender explode 배치(×STACK_SCALE)로 층층이 떠 있고,
 *  카메라는 낮은 각으로 내려와 한 바퀴 돌며(yaw 0 → 2π−0.9 ≈ 정면 3/4) 멀리서 전체를 담는다. */
export function stack(scroll: Timeline) {
  scroll.add(P, { stack: [0, 1], duration: 1200, ease: 'inOut(2)' }, T.stack)
  scroll.add(P, { topLight: [1, 0], duration: 1200, ease: 'inOut(2)' }, T.stack) // 탑뷰 조명 끔. 카메라는 cameraTrack(CAM_STACK)
}

/** 9 ASSEMBLE — 구조도의 층이 내려와 조립 완료. 카메라는 +X 끝 캡(로고) 정면 쪽으로 돌아와 살짝 위에서 로고를 본다. */
export function assemble(scroll: Timeline) {
  scroll.add(P, { assemble: [0, 1], duration: T.assembleEnd - T.assemble, ease: 'inOut(2)' }, T.assemble) // 카메라는 cameraTrack(CAM_END)
  scroll.add(P, { logoSweep: [0, 1], duration: T.end - T.assembleEnd, ease: 'out(2)' }, T.assembleEnd) // 조립 완료 뒤 정지 구간에 빔이 켜져 로고를 좌우로 계속 훑는다(되감으면 꺼짐)
}
