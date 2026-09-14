# 랜딩 페이지 연출 설계 (animejs.com 식 스크롤 스토리)

대상: `src/app/(marketing)/_components/landing/` (이 폴더, 구조는 §0). 모델: `public/models/gpu.glb` (+X 끝 캡에 G 로고 실버 상감). 재질/모델은 건드리지 않는다.

## 0-1. 페이지 구성 — 피그마 `P1 랜딩 v3 (GPU 모션)` 기준 (2026-09-07 확정)

피그마: 파일 `I2kKzmKse6GThMRuKqLFcm` · 페이지 `Screens` · 프레임 `P1 랜딩 v3 (GPU 모션)`(91:477, 1440×11780). 기존 `P1 랜딩`(35:335)은 참고용으로 남겨 둠. 랜딩은 아래 **세 구간만**으로 끝낸다 — NowStats · Cheaper77 · AiMatch · ChainBand 는 넣지 않는다.

| 구간 | 피그마 | 웹 구현 | 상태 |
| --- | --- | --- | --- |
| ~~**Hero** (밝음, 900)~~ | 91:478 | **2026-09-10 제거** — 페이지가 GPU 모션(스크롤 연출)으로 바로 시작. `_components/hero/` 삭제 | 삭제 |
| **GPU 모션** (`black` #0d0f0b 바닥, sticky — 배경은 캔버스 안 판 3장) | 91:591 `GpuMotion` — 피그마는 움직이지 않으므로 챕터 10개(인트로 0–2 + 스크롤 3–9, 피그마 컷은 팬/연기/쉬라우드 분리 이전의 12장이라 갱신 필요)를 1440×900 풀스크린 **정지 컷**으로 세로 배치. 각 컷 좌상단 칩 = `번호 · 챕터명 · 설명 · 구간(ms)`. 이미지는 실제 페이지 헤드리스 캡처(§4) | 이 폴더 전체 (`landingScene` 등). Hero 가 위에 붙으므로 인트로는 **sticky 스테이지가 뷰포트에 완전히 들어와 고정되는 순간**(IntersectionObserver, threshold 0.98) 1회 재생하며 **재생 중(5초) 스크롤 잠금**(`html overflow:hidden` + `touchmove` 차단, 끝나면 해제, 건너뛰기 없음). 이미 챕터 구간까지 스크롤된 채 진입(`tl.progress > 0.01`)이면 생략 | 구현됨 |
| **Footer** (어둠, 80) | 91:588. 배경 `#0d0f0b`, 좌 `G-shared`(Barlow SemiBold 20, `#f7f9f4`), 우 `SSAFY 15기 특화 프로젝트 · E204 · 이용약관 · 개인정보처리방침`(Inter 13, `#5e5c63`), 패딩 28/64 | `_components/siteFooter.tsx` (서버 컴포넌트) | 구현됨 |

- 폰트: **Pretendard 단일 서체**(제목·본문·로고·숫자 모두). 피그마의 Manrope/Inter/Barlow 표기는 굵기·크기만 참고하고 서체는 Pretendard 로 옮긴다. `app/layout.tsx` 에서 `pretendard` 패키지의 동적 서브셋 CSS 를 import 해 쓰인 글자 구간만 내려받고(AGENTS.md 서체 절), `globals.css` 의 `--font-sans/-heading/-mono/-logo` 토큰이 전부 그 변수를 가리킨다.
- 색은 전부 `globals.css` 토큰(`--color-primary` `#76b900`, `--color-black` `#0d0f0b`, `--color-dark` `#333333`, `--color-text-muted` `#5e5c63`, `--color-white` `#f7f9f4`, `--color-accent-mint` `#30e2ad`).
- Hero/Footer 는 스크롤 연출과 무관한 일반 흐름 요소라 **sticky 캔버스 밖**(형제 섹션)에 둔다. 예전에 검토한 "오버레이 슬롯"은 필요 없어졌다(챕터 카피를 캔버스 위에 얹기로 결정되면 그때 `children` 슬롯 추가).

## 0. 파일 구조 (현재)

관심사별로 나눈다: **motion/** = anime.js(연출), **three/** = r3f(3D), 최상위 = 진입점과 둘이 공유하는 상태. 의존 방향은 `three/ → progress.ts ← motion/` 한 방향(three 와 motion 은 서로 import 하지 않음).

```
src/app/(marketing)/
  page.tsx                        라우트. <LandingScene/> 만 렌더 (서버 컴포넌트)
  _components/landing/            랜딩 연출 전부
    landing.md                    이 문서
    landingScene.tsx    ~200줄    섹션 DOM(sticky 캔버스, 높이 100vh + 챕터 수×150vh) + 배경판 스크럽 값(P.scroll/plate/plate2W/light 을 스크롤 틱마다 기록) + 타임라인 2개(인트로=시간, 스크롤=챕터 스텝: 스크롤이 챕터 시작을 넘으면 animate(cursor)→tl.seek 로 그 챕터를 제 속도로 재생, 되돌아가면 되감기) + 인트로 시작(GPU_READY_EVENT)·스킵·네비 규칙
    chapter-store.ts    28줄      현재 챕터 인덱스·점프 함수 — landingScene 이 쓰고 chapterSidebar 가 읽는 외부 스토어(useSyncExternalStore)
    chapterSidebar.tsx  36줄      오른쪽 챕터 목록(CHAPTER_LABELS). 현재 챕터 상시 강조, 접근 시 글자 등장, 클릭 점프. 스타일은 chapterSidebar.module.css(CSS transition 만)
    progress.ts         ~30줄     GPU_READY_EVENT + 공유 상태 P(카메라 yaw/tilt/dist/lookY/shift, 조명 env/sweep/beam/topLight/logoSweep, 부품 fans/smoke/shroud/hsLift/hsOff/stack/assemble, spin, 배경판 스크럽 scroll/plate/plate2W/light). 타임라인이 쓰고 r3f 가 매 프레임 읽음
    motion/
      chapters.ts       ~130줄    CHAPTERS(트리거·라벨·duration·ease)/CHAPTER_LABELS + 챕터 정의: 상수 I(인트로 ms)/T(스크롤 챕터 시작·끝 ms) + 챕터별 함수 cameraTrack(카메라 전 구간) + introTop·hero·reveal·fansOut·smoke·shroudOff·hsLift·hsOff·topView·stack·assemble ← **연출 수정은 여기**
    three/
      gpuCanvas.tsx     ~55줄     r3f Canvas 조립 + PerformanceMonitor 적응형 dpr(1~1.5) + 뷰포트 밖 frameloop 정지
      backdrop.tsx      ~230줄    배경판 — **지금 마운트되는 건 차콜판(shroud) 한 장뿐**(2026-09-14 사용자 결정: 스크롤 연출 제거, 로고 정중앙 고정). black(검정 와이프)·lit(스틸 조명) 경로와 P.plate2W/light/scroll 은 되돌릴 수 있게 코드에 남아 있다
      cameraRig.tsx     ~20줄     P → 구면좌표 카메라(마우스 시차 없음)
      lights.tsx        ~110줄    Lights(스튜디오 조명 × P.env, 스윕 스팟 경로, 탑뷰 조명) + Studio(Lightformer 환경맵)
      gpuModel.tsx      ~135줄    GLB 로드, 반복 부품 병합(Fin_/Cap_/Mosfet_/VRAM_ → 메시 1개씩), 부품 목록(Blender tag 프로퍼티 있는 오브젝트) + 재질 오버라이드(LOOKS), 매 프레임 위치(팬·쉬라우드 상승, 히트싱크 들림·이탈, 초크 펌핑, 스택/조립, 팬 회전). 튜닝 상수(FAN_LIFT·SHROUD_LIFT·HS_*·CHOKE_*·STACK_*)는 파일 상단
      statusLeds.tsx    ~90줄     PCB 위 붉은 상태 LED 6개 + VRAM 초록 활동 LED 64개(InstancedMesh 2개), 위치 행렬은 마운트 때 1회·프레임엔 색만, 패턴별 점멸
      smoke.tsx        135줄      연기 퍼프(ShaderMaterial 포인트, fbm). 백플레이트 가장자리에서 P.smoke 의 결정론적 함수로 위치·크기·알파
public/models/gpu.glb                     모델(Draco 압축, 디코더는 drei 기본 CDN)
tools/gpu_model.py                        모델 생성기 (Blender). 실행하면 tools/main_gpu.blend 도 만들지만 산출물이라 추적하지 않음. build_pcb: 배선 Traces — 같은 층 교차 없는 구역 배정(기판 좌/우변: 위 VRAM 버스 12 / 가운데 전원·디스플레이 / 아래 VRAM 버스 12, 상변: 위 VRAM 버스, 하변: 아래 VRAM 버스 + 가운데 틈으로 PCIe 24가닥 → 동심 45° 챔퍼 회전 → 핑거), 45° 도그레그·서펜타인, 좌 VRM 전원은 위 VRAM 위 빈 띠(y 0.345)로 우회, Vias(비아 링), 우측 모서리 디커플링 캡 클러스터(SMD_Die), VRAM 저항열(SMD_VRAM), PWM_IC(초크 열 아래)/BIOS_IC(좌상단), 입력 필터 캡 3(Cap_F, 전원 커넥터 옆)/출력 필터 캡 3(Cap_O, VRM 아래), 팬 헤더 2(FanHeader + 금핀), DP_IC + SMD_DP + Crystal(좌하단 블록), 쿨러 마운팅 홀 4(PCB 관통 + Hole_Rings 도금 링). 전부 배선 구역·콜드플레이트/브릿지 높이와 충돌 없음(렌더로 확인)
```

랜딩 밖(그대로 둠, AGENTS.md 의 프로젝트 골격): `src/features/jobs`(스키마·목업), `src/mocks`·`src/instrumentation.ts`·`app/providers.tsx`(MSW), `src/lib/fetcher.ts`, `app/globals.css`(디자인 토큰), `app/layout.tsx`.

역할 경계 (레이어 기준):
- **연출(타이밍·순서·ease)을 바꾼다** → `motion/chapters.ts` (+ 새 진행도 필드는 `progress.ts`)
- **카메라가 P 를 어떻게 해석하는지** → `three/cameraRig.tsx`, **조명** → `three/lights.tsx`
- **부품이 P 를 어떻게 해석하는지 / 거리·진폭 상수** → `three/gpuModel.tsx`
- **연기 모양·밀도** → `three/smoke.tsx`
- **섹션 높이·스크롤 범위·스킵 규칙** → `landingScene.tsx`

데이터 흐름: 스크롤/시간 → anime.js 타임라인이 `P` 숫자를 움직임 → r3f `useFrame` 이 매 프레임 `P` 를 읽어 카메라·조명·부품 위치를 조립 위치 기준으로 다시 계산 (React 리렌더 0, 되감기 가능).

정리 내역:
- 1차: `a100Rig/a100Scene`(CSS 3D 구버전) + `--color-a100-gold`, `partLabels` + 라벨 투영 코드, create-next-app 기본 svg 5개 삭제. `gpu/` → `landing/`, `gpuScene` → `landingScene`
- 3차: `plan.md` → 코드 옆 `landing.md`. 관심사별 폴더 `motion/`(chapters) · `three/`(Canvas·Rig·Lights·Model·Smoke·LEDs). `gpuCanvas` 에 섞여 있던 Rig/Lights 를 `cameraRig.tsx`/`lights.tsx` 로 분리(한 파일 = 한 컴포넌트). `SHROUD_LIFT` 를 chapters → gpuModel 로 옮겨 motion→three 역참조 제거. 기능 변화 없음
- 2차: 구 분해(explode) 시스템 삭제 — `P.explode`, `STAGGER`/`easeOut`/`order`/`on`/`wobble`/`bob`/비행 기울기. 움직이는 챕터가 없어 항상 0 이었음. GLB 의 `explode` 프로퍼티는 "부품 표식"으로만 사용. `Part` 타입은 위치 계산에 실제로 쓰는 필드만

### 다른 정리 방식 후보 (지금 구조가 불편해지면)
| 기준 | 구조 | 언제 유리 |
| --- | --- | --- |
| **챕터 중심** | `chapters/hero.ts, reveal.ts, fansOut.ts …` 챕터당 파일 하나(상수·트윈·그 챕터가 쓰는 P 필드 주석) | 챕터가 6개 이상으로 늘어 `chapters.ts` 한 파일이 길어질 때 |
| **관심사(라이브러리) 중심** ✅ 채택(3차) | `three/`(r3f) / `motion/`(anime.js) / `progress.ts`(P) | 3D 쪽과 모션 쪽을 다른 사람이 맡을 때 |
| **feature 폴더** | `src/features/landing/` 로 이동(`features/jobs` 와 같은 레벨), 라우트에는 `page.tsx` 만 | 랜딩 컴포넌트를 다른 라우트(예: /about)에서도 쓰거나 테스트를 붙일 때 |
| **P 분할** | `P` 를 `camera` / `lights` / `parts` 객체로 나눔 | 필드가 15개 넘어 어느 챕터가 무엇을 만지는지 헷갈릴 때 |

## 1. 방식

- **챕터 0–2 는 시간 기반**(로드 시 자동 재생 1회, 실제 ms, 총 5.0s). **챕터 3 부터 스크롤 = 타임라인**(animejs.com 식, `onScroll({ sync: true })`).
- 인트로 재생 중에는 스크롤을 잠근다(건너뛰기 없음). 이미 챕터 구간까지 스크롤된 채 로드면 인트로는 즉시 끝 상태(reduced-motion 은 생략이 아니라 REDUCED_RATE 배속 재생, 2026-09-14) → 스크롤 tl 이 이어받음.
- 마우스 위치에 따른 카메라 시차(패럴랙스) **없음** — 카드는 고정.
- 로고는 지나가는 빛에 실버 상감이 반사되어 보이는 정도로만 (emissive 없음).

## 2. 챕터

| # | 챕터 | 구간(ms) | 카메라 | 조명 / 모델 | 카피 |
| --- | --- | --- | --- | --- | --- |
| 0 | **INTRO TOP** | 시간 0–2000ms | +X 끝 위 낮은 각(18°)에서 카드 길이 방향 컷 고정(`yaw π/2, tilt 0.315, dist 3.3, lookY −0.12` — 타깃을 낮춰 카드가 화면 위쪽에, 0–1400) → **빔 꺼지고 완전 암전(1400–1650)** → 오버레이가 다 덮인 뒤에야 끝 캡 정면으로 틸트다운(1650–2000, 안 보임) | 암전. **로고와 같은 스윕 빔**이 카메라 쪽에서 비스듬히 들어와 카드 폭 방향 **화면 왼→오(+Z→−Z)** 로 지나감 — 빔 자국이 길이 방향으로 길게 늘어져 팬 3개를 한 번에 훑음(`sweep 0→1`), 스윕 끝(1400)에 빔 꺼지며 **검은 오버레이(`#blackout`)가 250ms 에 완전히 덮고**, 카메라 이동은 그 뒤(1650)부터 → 암전 전에 모션이 보이지 않음 → HERO 시작(2000)에 오버레이 250ms 로 걷히며 빔 켜짐 | (미정) |
| 1 | **HERO** | 시간 2000–3400ms | +X 끝 캡 정면 고정 — G 원이 화면 정중앙(`yaw=π/2, tilt=0, dist=2.35, lookY=0.0225, shift=−0.05`, 상수 트윈) | 암전. 같은 빔이 캡 앞에서 **화면 왼→오(+Z→−Z)** 로 로고를 훑음(`sweep 2→3`) | (미정) |
| 2 | **REVEAL** | 시간 3400–5000ms | 줌 아웃하며 카드 전체가 한눈에. 끝 캡(정면)이 **화면 우측 하단**을 향하는 입체 뷰로 회전 — 카메라는 위·앞·오른쪽 (`yaw≈1.0, tilt≈0.55, dist≈6.5, lookY≈0, shift=0`, 스크린샷으로 튠) | **위에서 비추는 빛** 페이드인: `env 0→1` (키 라이트 (3,6,4) 가 위쪽이라 기존 조명 그대로 사용). 스윕 스팟은 3400–3900ms 에서 `beam 1→0` 으로 빠짐 | (미정) |
| 3 | **FANS + SHROUD OFF** | 스크롤 1000–3000 (**0–1000 은 정지 구간** — 인트로 직후 조립 상태 그대로, 스크롤이 조금 움직여도 부품이 반응하지 않음) | 챕터 2 뷰 유지 | 팬 3개와 쉬라우드가 **같은 구간에 함께** 위로 가속 상승해 화면 밖으로(`fans 0→1`, `shroud 0→1`, 모두 ease in(2)). 그 사이(500–2000) 백플레이트 가장자리에서 연기 퍼프가 밀려 나와 흩어짐(`smoke 0→1`). 이전 FANS OUT / SMOKE / SHROUD OFF 세 챕터를 하나로 합침 | (미정) · (미정) · (미정) |
| 4 | **ORBIT** | 스크롤 3500–4500 (3000–3500 은 정지 구간) | `cameraTrack`: CAM0 → CAM_ORBIT (`yaw 1.0 → π−1.0`, inOut(2)) — 정면(끝 캡)을 스쳐 반대편으로 돌아 **끝 캡이 화면 왼쪽 하단**을 향함. tilt/dist 유지 | 유지 | (미정) |
| 5 | **HS LIFT** | 스크롤 5000–5500 (4500–5000 은 정지, 챕터 진입 시 커서가 5000 으로 바로 맞춰짐) | 유지 | 히트싱크(핀 66장 + 히트파이프)는 **+0.5**, 콜드플레이트·플레이트 브릿지는 **+0.2** 만 들려 두 층이 구분됨(`hsLift 0→1`, inOut(2)). 이때부터 초크 펌핑 시작 | (미정) |
| 6 | **HS OFF** | 스크롤 5500–6100 (들림 직후 바로, 사이 공백 없음 — 사이드바에선 5+6 이 '히트싱크 분리' 한 챕터, 1초·선형 커서·이탈 in(3)) | 유지 | 들린 히트싱크·플레이트류가 **함께 위로 상승해 화면 밖(+7)으로 나간 뒤 숨김**(`hsOff 0→1`, in(1.2)) | (미정) |
| 7 | **TOP VIEW** | 스크롤 7500–9000 (6100–7500 은 정지) | `cameraTrack`: CAM_ORBIT → CAM_TOP (`yaw → 0`, `tilt → 0.785`, `dist → 5.6`). **탑뷰 동안만 위에서 내려 비추는 조명** `topLight 0→1`(STACK 시작 시 꺼짐) — 남은 PCB·백플레이트·브라켓을 **위(+Z)에서 −Y(PCIe 엣지) 쪽으로 45° 기울여 내려보는 뷰**. 화면 오른쪽 = +X, 아래 = PCIe 엣지, 비스듬한 회전 없음 | 유지 | (미정) |
| 8 | **STACK** | 스크롤 9500–12000 (9000–9500 은 정지) | `cameraTrack`: CAM_TOP → CAM_STACK — 낮은 각으로 내려와 **한 바퀴 회전**(`yaw 0 → 2π−0.9`, `tilt → 0.42`, `dist → 8.0`) | **햄버거 레시피 구조도** — 나갔던 팬·쉬라우드·핀·히트파이프·플레이트까지 전부 돌아와 **냉각 스택만** Blender `explode` 배치 ×1.0 으로 층층이 떠 있음(층 3개: 쉬라우드+팬(조립 상태, 0.95) / 핀+히트파이프(조립 상태, 0.55) / 콜드플레이트·브릿지(explode 배치 0.32). 핀·파이프·팬은 explode 벡터를 쓰지 않아 벌어지거나 높이가 달라지지 않음). 브라켓·PCB·백플레이트와 그 위 부속(VRAM·초크·캡 등)은 조립 상태로 붙어 있음(`stack 0→1`, 1200 동안 현재 위치에서 보간, 이후 유지). 초크 펌핑 유지 | (미정) |
| 9 | **ASSEMBLE** | 스크롤 12500–14500 (12000–12500 은 정지) | `cameraTrack`: CAM_STACK → CAM_END — +X 끝 캡(로고) 정면 쪽, 살짝 위(`yaw → 2.5π`, `tilt → 0.22`, `dist → 4.5`) | 구조도의 층 간격이 0 으로 줄어 **조립 완료**(`assemble 0→1`, stack 유지 상태에서 간격만 축소) — 로고가 앞에 보임 | (미정) |
| 10 | **END** | 스크롤 15000 (14500–15000 은 조립 완료 상태로 정지) | CAM_END 고정 | 정지 구간에 `logoSweep 0→1` 로 빔이 켜져 로고 앞을 좌우(±z 1.0, 0.22Hz, 세기 26)로 천천히 왕복하며 상감 로고를 비춘다(`lights.tsx`, 시간 기반 — 스크롤이 멈춰도 움직임). 스크롤 sync 는 끝나도 그대로(위로 올리면 역재생). 네비가 돌아오고 Footer 가 보이며, 푸터 위 우측에 떠 있는 **맨 위로** 고정 버튼(`backToTop.tsx`, `nav-btn` 모양, `body[data-nav=shown][data-nav-theme=dark]` 일 때만 표시, smooth scrollTo 0)으로 Hero 복귀 | `page.tsx` 최상위 `<BackToTop/>` + `globals.css .back-to-top` |

> 챕터 2 이후는 이어서 받아 적는다.

### 챕터 1 메모
- 스크롤 0 = 빔이 왼쪽 가장자리 → 로고 거의 안 보임. 첫 화면이 너무 비어 보이면 빔 시작 위치를 캡 왼쪽 끝(z≈+0.64)으로 두어 로고 왼쪽 끝이 살짝 걸리게.
- 빔 세기 `beam`: 0–250ms 켜짐 → 팬 훑기 → 1400 꺼짐 + `#blackout` 오버레이(완전 암전, 1400–2000) → 2000–2250 오버레이 걷히며 빔 켜짐 → 로고 훑기 → 리빌 시작 후 500ms 페이드아웃.

### 챕터 2 메모
- 카메라 좌표계: `yaw=π/2` 가 +X 캡 정면. yaw 를 1.0 쪽으로 줄이면 카메라가 +Z(앞) 쪽으로 돌아 캡이 화면 오른쪽으로, tilt 를 올리면 캡이 가까운 쪽(아래)으로 → "우측 하단을 바라봄".
- 줌 아웃과 회전은 같은 ease(`inOut(3)`) 한 트윈으로. 카드 전체가 들어오는 dist 는 기존 분해 뷰(7.6)보다 작게, 조립 상태 기준 6.5 근처.

### 챕터 3 메모
- 팬 전용 필드 `fans`. `three/gpuModel.tsx` 에서 `spin===true`(팬) 부품에만 `y += fans × FAN_LIFT(4)` 를 더함. 세 팬 동일(시차 없음).
- 팬이 나가면 팬 우물 안 히트싱크가 그대로 보임 — 다음 챕터에서 활용 가능.

## 3. 기술 설계

### 3.1 `progress.ts` 추가 필드
```ts
env: 1,   // 전체 조명 배율 (0 = 암전)
sweep: 0, // 스윕 스팟 위치 0(왼) → 1(오)
beam: 0,  // 스윕 스팟 세기 0..1
fans: 0,  // 팬 상승 이탈 0(제자리) → 1(화면 밖)
smoke: 0, // 연기 퍼프 진행도 (smoke.tsx)
shroud: 0, // 쉬라우드 분리 상승
hsLift: 0, // 히트싱크 살짝 들림
hsOff: 0, // 히트싱크 상승 이탈
stack: 0, // 햄버거 레시피 구조도(전 부품 층층이)
assemble: 0, // 층 간격 → 0 (조립 완료)
```
(챕터가 늘면 여기 추가)

### 3.2 조명 `three/lights.tsx`
- 기존 directional/ambient 4개를 `<Lights/>` 로 묶고 매 프레임 `intensity = base × P.env`, `scene.environmentIntensity = P.env`.
- 아래쪽 보조광은 거의 중성(#d2d8cf, 0.15) — 원래 #76b900/0.3 이라 금속 핀이 ORBIT 뒤 각도에서 초록/올리브로 비쳤음(캡처로 확인).
- 탑뷰 조명: `directionalLight (0.3, 8, 1.5)`, `intensity = P.topLight × 2.2` — 챕터 11 에서만.
- 부품 재질 오버라이드(`three/gpuModel.tsx` LOOKS, GLB 불변): 백플레이트 스틸(metalness 1, rough 0.32), 초크 무광 차콜, 캡 네이비 알루미늄 캔, 모스펫 검은 에폭시, VRAM 다크 패키지, SMD* 브라운, *_IC 검은 몰딩. 배선(Traces)은 Blender 재질(마스크 아래 동박 황록) 그대로.
- 스윕 스팟: `<spotLight angle≈0.4 penumbra≈0.7>`, `intensity = P.beam × 40`. 경로는 `P.sweep` 구간별: 0..1 메인 `(2.9, 1.0, 0.9−1.8u)`→타깃 카드 끝 모서리 `(1.45, 0.22, z)`(윗면 가까운 팬 + 끝 캡 정면 윗부분만 살짝) + 보조 스팟(angle 0.13)→타깃 `(−0.9, 0.25, z)`, 메인 angle 0.2 — 두 자국이 이어져 카드 길이 방향(화면 세로)으로 긴 **얇은** 띠, 세기 30/40(`lights.tsx` BEAM_TOP_MAIN/AUX, 처음 16/22 에서 올림) 으로 카드가 또렷히 드러나게, 화면 왼→오로 이동. 로고 스윕(2..3)은 angle 0.4 / 세기 40 / 1..2 쉬라우드 끝 → 캡 면 / 2..3 캡 앞 `(2.6, 0.15, 1.1 − 2.2·u)`, 타깃 캡 중심(카메라 축 근처 = metallic 반사각).

### 3.3 타임라인 `landingScene.tsx`
- 인트로 tl(`I` 상수, 시간) + 스크롤 tl(`T` 상수). `P` 기본값 = 챕터 1 시작 상태, 트윈은 `[from, to]` 명시.
- **anime.js 주의 ①** — 기본 `composition: 'replace'`: 같은 대상·속성을 만지는 트윈이 새로 생기면 기존 트윈을 **취소**한다. 인트로 tl 이 카메라를 만지면서 스크롤 tl 의 카메라 트윈이 통째로 죽어 구도가 고정됐던 원인. → 두 타임라인 모두 `defaults.composition = 'none'`.
- **anime.js 주의 ②** — 어떤 속성의 첫 트윈이 시작되기 전엔 값을 안 건드리지만, 되감기 뒤에는 가장 이른 트윈의 from 값을 남긴다. 그래서
  - **카메라는 `cameraTrack()` 한 곳**에서 스크롤 전 구간을 빈틈없이 정의(정지 구간 = 상수 트윈, 이동 구간 = `CAM0 → CAM_ORBIT → CAM_TOP → CAM_STACK → CAM_END`). 챕터 함수(topView/stack/assemble)는 카메라를 만지지 않는다. 트랙은 **1ms 부터** 시작 — 스크롤 0 의 sync 렌더가 카메라를 쓰면 인트로와 싸운다.
  - HERO 는 끝 캡 정면 값을 **상수 트윈으로 매 프레임 고정**(인트로가 카메라를 소유하는 동안 스크롤 tl 초기 렌더에 밀리지 않게).
  - node 로 `seek` 시뮬레이션해 앞/뒤 어느 순서로 가도 같은 값이 나오는지 확인함.

### 3.4 모델 `three/gpuModel.tsx`
- 매 프레임 `position = base + 챕터별 오프셋` 으로 다시 계산(누적 없음 → 스크롤 되감기 가능). 화면 밖으로 다 나간 부품(팬·쉬라우드·히트파이프류)은 진행도 1 에서 `visible=false` — 뒤 챕터 탑뷰에 걸리지 않음. 팬/쉬라우드 상승, 히트싱크·플레이트 들림·이탈, 스택/조립 모두 이 한 루프 안. 튜닝 상수(`FAN_LIFT`, `HS_LIFT/PLATE_LIFT/HS_OFF`, `CHOKE_*`, `STACK_*`)는 파일 상단.

## 4. 검증
- **이미 켜져 있는 dev 서버(:3000)만 사용**, 새로 띄우지 않음.
- 헤드리스 크롬 `--headless=new --use-angle=swiftshader --virtual-time-budget=12000 --screenshot` 으로 캡처. 특정 챕터를 보려면 `landingScene` 에서 임시로 `tl.seek(ms)` 를 넣고 찍는다(커밋하지 않음).

## 5. 결정 필요 / 다음 작업
- [x] Hero·Footer 구현 후 `page.tsx` 조립 (2026-09-07)
- [x] 인트로 자동 재생 시작점을 스테이지 고정 시점으로 변경 (2026-09-07)
- [x] **챕터 간 스크롤 거리 균등화(2026-09-11)** — 진행률을 균등 분할(카드 + 챕터 수, 지금 7)해 구간 번호 → 챕터. 한 챕터당 `SCROLL_PER_CHAPTER_VH`(150vh, 처음 100 에서 반 화면 늘림), 섹션 높이 = 100vh + 7×150vh = 1150vh(이전 1600vh, 타임라인 시각 비례). `CHAPTERS[].at` 은 더 이상 스크롤 위치에 쓰이지 않는다(타임라인 안 챕터 순서·`to` 만 사용)
- [x] **오른쪽 챕터 사이드바(2026-09-10)** — `chapterSidebar.tsx`(React Bits LineSidebar 의 TS 포팅, 좌우 반전, `chapterSidebar.module.css`)가 `chapter-store.ts` 로 현재 챕터를 읽어 상시 강조, 커서가 사이드바 근처로 오면 글자 전체가 한 번에 오른쪽→왼쪽으로 등장(2026-09-11, 순차 지연 제거), hover 항목·현재 챕터는 초록으로 더 밀림, 클릭하면 그 챕터 트리거 위치로 점프(`yFor` → `step`). 항목 7개: 카드·팬 분리·오빗·히트싱크 분리(들림+이탈 합침 2026-09-11)·탑뷰·구조도·조립 = `motion/chapters.ts` 의 `CHAPTER_LABELS`. 스크롤 입력은 가로채지 않는다(휠 한 번 = 한 챕터 방식은 사용자 결정으로 뺐다)
- [x] **챕터별 배경(2026-09-11, 우주 배경 대체)** — 별(`three/starfield.tsx`, `P.stars`) 삭제. **1–2 = 캔버스 안 금속판** `three/backdrop.tsx`: GLB `Shroud` 재질 clone 을 120×120 평면에 입혀 매 프레임 카메라 앞 24 유닛에 카메라 방향으로 세움(오빗에도 정면 유지, 환경맵 반사가 판 위로 흐름). 밝기는 `P.plate` — **01 카드 구간(`PLATE_BAND` 0) 스크롤 진행률을 `landingScene` 이 직접 기록하는 스크럽**(2026-09-12 사용자 요청: 02 도착 시 600ms 페이드인 모션이었는데, 내려간 정도에 따라 밝아지게 바꿨다. 되올리면 어두워진다 → `chapters.ts plate()` 삭제). 숨김은 검정판이 다 덮인 뒤 backdrop 이 `P.plate2W` 로 판단. **부조 도안**(2026-09-12 최종): 빽빽한 문구 각인(`drawLines`·`makeEngraving`)은 "텍스처가 너무 많다"고 전부 걷어냈다. 지금은 **판마다 문양 하나가 화면 한가운데를 지나간다** — 차콜판(01–03)은 가로 패널 이음매 한 줄 + G 마크, 스틸판(04–07)은 G 마크만(동심 홈·눈금 계측 마크를 그렸다가 "마지막 양각도 로고로" 요청으로 교체). 둘 다 **음각**(파냄). 로고는 `/logo-mark.png` 를 1회 로드(`loadMark`, 모듈 캐시·판 두 장 공유)해 알파 실루엣만 색을 바꿔 겹쳐 그린다(`tintMark`) — 늦게 오면 텍스처만 다시 그려 갱신하고(`redraw`), 실패하면 이음매만 남는다. 가로 위치는 `LOGO_DX`(-0.05)로 왼쪽 보정 — 마크 왼쪽 속도선 때문에 이미지 중심과 원의 중심이 어긋나 그냥 두면 오른쪽으로 치우쳐 보인다. 차콜판은 **진짜 요철**(`makeBumpPlate` → `bumpMap`, `BUMP_SCALE` 2.6: 기준 회색 위 이음매·로고를 어둡게 파고 색은 안 바꿔 조명이 스칠 때만 드러남), 스틸판은 조명을 안 쓰므로 **구운 음영**(`makeSteelPlate`: 파인 바닥을 옅게 어둡게 + 왼위 안쪽 벽 그늘 / 오른아래 안쪽 벽 빛, `RELIEF` .16 / `EMBOSS_PX` = 타일의 0.3%). **음영은 반드시 실루엣 안쪽으로만 자른다**(`band()`: 실루엣에서 민 사본을 `destination-out` 으로 빼 안쪽 테두리 띠만 남긴다) — 밖으로 번지면 드롭섀도로 읽혀 로고가 떠 보였고, 겹쳐 칠한 알파가 안쪽을 바탕보다 밝게 만들어도 떠 보였다(2026-09-12 두 번 수정). 타일 크기는 `TILE_SCREENS`(1.7)로 **화면 높이 기준**이라 거리가 다른 두 판이 같은 크기로 보이고, 어느 스크롤 위치에서도 문양 하나가 시야에 남는다. 해상도는 일부러 낮다(`TILE_PX` 640, 이방성 필터 끔) — 확대되며 부드러워져 배경이 멀리 있는 느낌이 난다(2026-09-12 요청). 음각 폭·이음매 두께는 `TILE_PX` 비율로 따라간다. **03 후반 이후 = 캔버스 안 검정 단색판**(`Backdrop kind="black"`, `MeshBasicMaterial` #000, dist 23 = 차콜판 앞, 고정·스크롤에 흐르지 않음. 2026-09-12 최종 — 스틸 재질·각인·어두운 띠·조명 시도는 모두 롤백). 등장은 **그라디언트 와이프 + 스크롤 스크럽**: `landingScene` 이 03 오빗 구간(`WIPE_BAND` = data-chapter 2) 안 스크롤 진행률을 `P.plate2W`(0→1)·`P.plate2` 에 매 틱 직접 기록 → 챕터 스텝 규칙의 유일한 예외, 멈추면 멈추고 되돌리면 내려간다. `backdrop.tsx` 는 이 값으로 `alphaMap` 램프(투명→검정, 폭 `RAMP_HALF`×2 = 0.4 화면)의 `offset.y` 를 놓아 아래에서 위로 올린다 — 04 에 도착하기 전에 다 덮인다(2026-09-12). 다 덮인 뒤(`P.plate2W` = 1) 판 1 은 backdrop 이 스크럽 값으로 바로 숨긴다(타임라인 되감기에 묶으면 빠른 백스크롤에서 바닥이 비쳤다). **04 히트싱크부터 섹션 끝까지 = 조명이 조금씩 켜지며 맨들한 스틸판이 드러남**(`Backdrop kind="lit"`, 2026-09-12 사용자 결정 — 04 의 위→아래 스틸 와이프와 05 의 검정 재와이프는 마음에 안 들어 뺐다): `MeshBasicMaterial` + 구운 텍스처 `makeSteelPlain`. **텍스처가 너무 많다는 지적(2026-09-12)으로 브러시 줄무늬·각인·카본 트윌을 전부 제거**(차콜판 각인도 같은 이유로 삭제) — 바탕 `STEEL_TONE` #3d4045(차콜판과 같은 계열이되 한 단계 어둡게, 2026-09-12) + 타일마다 지나가는 세로 광택 띠(`STEEL_SHEEN` .13, 타일 위아래 끝 값이 같아 이어 붙여도 경계가 없다) + 위의 부조 도안. 판이 흐르면 띠와 도안이 함께 지나간다. **조명·환경맵 없음** — 실제 조명은 카메라가 돌면 반사가 같이 돌아 카드 오빗에 배경이 끌려갔고 점광원은 켜지는 순간 전체가 밝아져 롤백. "조명 켜짐"은 알파·밝기 스크럽: 화면 중앙 원형 스팟 `alphaMap`(`makeSpot`, 중심 `SPOT_CORE` 까지 1 → 가장자리 0)을 씌우고, `landingScene` 이 04 부터 네 구간(`LIGHT_BAND` 3, `LIGHT_BANDS` 4 → 섹션 끝에 1) 진행률을 `LIGHT_EASE`(0.5, 제곱근) 곡선에 태워 `P.light` 에 기록(선형은 04~05 가 너무 어두웠다, 2026-09-12) → 밝기(opacity) = `P.light`, 스팟 지름 = 화면 높이 × (`SPOT_MIN` .9 → `SPOT_MAX` 3.5). 끝에서 모서리는 알파 ≈ .7 로 살짝 어둡다(비네트). 되돌리면 어두워진다. DOM 배경 레이어(b·c·d, INTRO TOP 전용 `#deep` 바닥)와 `--f` CSS 변수는 전부 삭제, 배경은 캔버스 판 3장(차콜·검정·lit)만. `P.plate2` 는 `plate2W > 0` 과 같아 제거(2026-09-12 정리). 챕터 0·인트로는 검정 바닥 그대로. **스크롤과 함께 흐름**: `landingScene` 이 매 틱 지나온 구간 수(진행률 × BANDS, 0..7)를 `P.scroll` 에 기록 → 텍스처가 있는 판(차콜·lit, `flow: true`)은 카메라 up 방향으로 `P.scroll × 0.5 × 화면높이`(`SCROLL_PER_CHAPTER`, 2026-09-12: 챕터당 반 화면). 와이프 램프·스팟은 흐른 만큼 되돌려 화면에 고정. 검정판은 단색이라 고정. 판 크기 120 = 최대 오프셋 3.5 화면을 덮는 크기. 카드는 고정. 스틸 브러시 줄무늬는 타일 양쪽 경계를 이어 그린다(오른쪽만 빠져 세로 이음선이 생겼던 것 수정, 2026-09-12). 번호 주의: data-chapter 0 = 사이드바 01
- [ ] ~~**우주 배경(2026-09-10)**~~ (대체됨) — 섹션·sticky 바닥 `bg-black`, 캔버스 안 `three/starfield.tsx`(drei Stars 래핑, `P.stars` 가 0 이면 숨김 → 인트로는 순수 검정, REVEAL 에서 켜짐) `<Stars radius 30 depth 40 count 440(5000 → 1250 → 440, 2026-09-10) factor 8 fade speed .5>`(점 크기 = factor×30/거리라 멀면 사라짐, 카메라 far 50→200 으로 늘려 잘리지 않게). 별은 조명(P.env)과 무관해 인트로 암전 중에도 떠 있고, 카메라 오빗 때 시차로 함께 돈다
- [x] **스크롤 스크럽 → 챕터 스텝(2026-09-10)** — `sync: true` 스크럽을 버리고, 스크롤 위치→시각(ms) 매핑은 유지한 채 `CHAPTERS`(at→to) 트리거를 넘으면 `animate(cursor, { t: to, 1300~2800ms(거리×0.9), out(3) })`(2026-09-11 inOut→out: 즉시 시작·끝에서 감속). 챕터별 `duration` 으로 덮어쓸 수 있음(히트싱크 이탈은 3600ms) 로 `tl.seek`. 되돌아가면 이전 챕터 끝으로 되감기. `section[data-chapter]` 로 어느 챕터까지 재생됐는지 노출(검증용). reduced-motion 은 즉시 seek 이 아니라 지속 시간 ×REDUCED_RATE(0.35) 로 빠르게 재생(2026-09-14 — 윈도우 "애니메이션 효과" 끄기가 이 미디어쿼리로 와서 PC방에선 전혀 안 움직였다)
- [x] **배경판 고정(2026-09-14)** — 배경 스크롤 연출을 전부 제거. `gpuCanvas` 는 `<Backdrop kind="shroud">` 한 장만 마운트하고, 그 판은 `flow:false` + `P.plate` 고정 1 이라 스크롤·챕터와 무관하게 로고가 화면 정중앙에 그대로 있다(밝기는 씬 조명 `P.env` 만 따라가 인트로 암전 구간은 검정). `landingScene` 의 `P.scroll/plate/plate2W/light` 기록과 `PLATE_BAND/WIPE_BAND/LIGHT_BAND/LIGHT_BANDS/LIGHT_EASE` 삭제 — GPU 카드의 인트로·7 챕터 스크롤 스토리는 그대로. 되돌리려면 `gpuCanvas` 에 black·lit 두 줄과 스크럽 기록을 복구
- [x] **reduced-motion = 배속 재생(2026-09-14)** — 윈도우 "애니메이션 효과 끄기"(PC방 기본값)가 `prefers-reduced-motion: reduce` 로 와서 인트로 스킵 + 즉시 seek 이 걸려 "애니메이션이 안 나온다"는 제보. 이제 `REDUCED_RATE`(0.35) 배속으로 **재생**한다(인트로는 `playbackRate`, 챕터 스텝은 duration×rate). 같은 제보의 다른 축으로 Draco 디코더를 `public/draco/` 로 자가 호스팅(`useGLTF(MODEL, DRACO)`) — drei 기본값인 gstatic CDN 이 막힌 망에선 GLB 가 영영 안 풀렸다

- [x] **Hero 삭제(2026-09-10)** — 캔버스가 첫 화면. 인트로는 로드 직후 스크롤을 잠그고 `GPU_READY_EVENT`(gpuModel 마운트 = GLB 로드 완료)를 받으면 재생, 8초 안에 안 오면 그냥 진행. 네비는 dark 고정, 인트로 뒤 맨 위와 Footer 구간에서만 보임. Hero 의 CTA(지금 시작하기 / 내 GPU로 수익 내기)가 사라졌으므로 랜딩에 진입 버튼을 다시 둘 자리(마지막 챕터 뒤 등) 결정 필요
- [ ] Nav 메뉴 앵커(`#about` `#how` `#gpus`)의 실제 대상 섹션이 없음 — 랜딩이 세 구간으로 끝나므로 메뉴를 줄이거나 챕터 위치로 스크롤시킬지 결정
- [ ] 각 챕터 카피(피그마 칩의 설명 문구를 그대로 쓸지, 캔버스 위 오버레이로 노출할지)
- [ ] 스팟 반사만으로 로고 가시성이 충분한지 (부족하면 스팟 2개/각도 조정, 재질은 그대로)
