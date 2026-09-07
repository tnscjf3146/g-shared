# 랜딩 페이지 연출 재설계 계획 (animejs.com 식 스크롤 스토리)

대상: `src/app/(marketing)/_components/landing/` (이 폴더, 구조는 §0). 모델: `public/models/gpu.glb` (+X 끝 캡에 G 로고 실버 상감). 재질/모델은 건드리지 않는다.

## 0. 파일 구조 (현재)

관심사별로 나눈다: **motion/** = anime.js(연출), **three/** = r3f(3D), 최상위 = 진입점과 둘이 공유하는 상태. 의존 방향은 `three/ → progress.ts ← motion/` 한 방향(three 와 motion 은 서로 import 하지 않음).

```
src/app/(marketing)/
  page.tsx                        라우트. <LandingScene/> 만 렌더 (서버 컴포넌트)
  _components/landing/            랜딩 연출 전부
    landing.md                    이 문서
    landingScene.tsx    71줄      섹션 DOM(sticky 캔버스, 높이 2400vh) + 타임라인 2개 생성(인트로=시간, 스크롤=onScroll sync) + 스킵 규칙. 챕터 함수 호출만
    progress.ts         22줄      공유 상태 P(카메라 yaw/tilt/dist/lookY/shift, 조명 env/sweep/beam/topLight, 부품 fans/smoke/shroud/hsLift/hsOff/stack/assemble, spin). 타임라인이 쓰고 r3f 가 매 프레임 읽음
    motion/
      chapters.ts       ~110줄    챕터 정의: 상수 I(인트로 ms)/T(스크롤 챕터 시작·끝 ms) + 챕터별 함수 cameraTrack(카메라 전 구간) + introTop·hero·reveal·fansOut·smoke·shroudOff·hsLift·hsOff·topView·stack·assemble ← **연출 수정은 여기**
    three/
      gpuCanvas.tsx     ~30줄     r3f Canvas 조립만(아래 컴포넌트를 얹는다)
      cameraRig.tsx     ~20줄     P → 구면좌표 카메라(마우스 시차 없음)
      lights.tsx        ~90줄     Lights(스튜디오 조명 × P.env, 스윕 스팟 경로, 탑뷰 조명) + Studio(Lightformer 환경맵)
      gpuModel.tsx      ~105줄    GLB 로드, 부품 목록(Blender tag 프로퍼티 있는 오브젝트) + 재질 오버라이드(LOOKS), 매 프레임 위치(팬·쉬라우드 상승, 히트싱크 들림·이탈, 초크 펌핑, 스택/조립, 팬 회전). 튜닝 상수(FAN_LIFT·SHROUD_LIFT·HS_*·CHOKE_*·STACK_*)는 파일 상단
      statusLeds.tsx    ~90줄     PCB 위 붉은 상태 LED 6개 + VRAM 초록 활동 LED 64개(InstancedMesh 2개), 패턴별 점멸
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
- 스크롤이 시작되면(또는 이미 스크롤된 채 로드 / reduced-motion) 인트로는 즉시 끝 상태로 점프 → 스크롤 tl 이 이어받음.
- 마우스 위치에 따른 카메라 시차(패럴랙스) **없음** — 카드는 고정.
- 로고는 지나가는 빛에 실버 상감이 반사되어 보이는 정도로만 (emissive 없음).

## 2. 챕터

| # | 챕터 | 구간(ms) | 카메라 | 조명 / 모델 | 카피 |
| --- | --- | --- | --- | --- | --- |
| 0 | **INTRO TOP** | 시간 0–2000ms | +X 끝 위 낮은 각(18°)에서 카드 길이 방향 컷 고정(`yaw π/2, tilt 0.315, dist 3.3, lookY −0.12` — 타깃을 낮춰 카드가 화면 위쪽에, 0–1400) → **빔 꺼지고 완전 암전(1400–1650)** → 오버레이가 다 덮인 뒤에야 끝 캡 정면으로 틸트다운(1650–2000, 안 보임) | 암전. **로고와 같은 스윕 빔**이 카메라 쪽에서 비스듬히 들어와 카드 폭 방향 **화면 왼→오(+Z→−Z)** 로 지나감 — 빔 자국이 길이 방향으로 길게 늘어져 팬 3개를 한 번에 훑음(`sweep 0→1`), 스윕 끝(1400)에 빔 꺼지며 **검은 오버레이(`#blackout`)가 250ms 에 완전히 덮고**, 카메라 이동은 그 뒤(1650)부터 → 암전 전에 모션이 보이지 않음 → HERO 시작(2000)에 오버레이 250ms 로 걷히며 빔 켜짐 | (미정) |
| 1 | **HERO** | 시간 2000–3400ms | +X 끝 캡 정면 고정 — G 원이 화면 정중앙(`yaw=π/2, tilt=0, dist=2.35, lookY=0.0225, shift=−0.05`, 상수 트윈) | 암전. 같은 빔이 캡 앞에서 **화면 왼→오(+Z→−Z)** 로 로고를 훑음(`sweep 2→3`) | (미정) |
| 2 | **REVEAL** | 시간 3400–5000ms | 줌 아웃하며 카드 전체가 한눈에. 끝 캡(정면)이 **화면 우측 하단**을 향하는 입체 뷰로 회전 — 카메라는 위·앞·오른쪽 (`yaw≈1.0, tilt≈0.55, dist≈6.5, lookY≈0, shift=0`, 스크린샷으로 튠) | **위에서 비추는 빛** 페이드인: `env 0→1` (키 라이트 (3,6,4) 가 위쪽이라 기존 조명 그대로 사용). 스윕 스팟은 3400–3900ms 에서 `beam 1→0` 으로 빠짐 | (미정) |
| 3 | **FANS OUT** | 스크롤 0–1500 | 챕터 2 뷰 유지 (살짝만 계속 회전해도 됨) | 스크롤을 내리면 **팬 3개가 위로 상승해 화면 밖으로 나감**. `fans 0→1` — 팬 위치 y += fans × 4 (화면 밖까지). 팬끼리 약간 시차(왼→오 순, 각 80ms), ease `in(2)` 로 가속하며 이탈. 팬은 올라가는 동안 계속 회전 | (미정) |
| 4 | **SMOKE** | 스크롤 2550–4000 (1500–2550 은 정지) | 유지 | 백플레이트 가장자리(카드 양 옆 긴 변)에서 연기 퍼프가 바깥으로 밀려 나와 그 자리에서 흩어짐(쉬라우드를 따라가지 않음). `smoke 0→1`(linear). **회색 연기 퍼프 220개** — fbm 노이즈로 뜯어진 가장자리·얼룩, 회전·난류·부력, 일반 블렌딩. 각자 (t0, life) 를 갖고 `P.smoke` 의 **함수**로 위치·크기·알파 결정 → 스크롤 되감기 가능, 시간 스폰 없음, 외부 툴 없음. 챕터 5 내내 뿜고 2000 에 전부 사라진다. (시도 후 보류: 흰 스트릭, 화면 셰이더 연기 장막, 구멍 6개 기둥 플룸) | (미정) |
| 5 | **SHROUD OFF** | 스크롤 3000–5000 | 유지 | 연기가 흩어지는 사이 쉬라우드가 분리되어 위로 가속 상승(`shroud 0→1`, ease in(2), y += 4.5) → 화면 밖. 히트싱크·히트파이프·PCB 노출 | (미정) |
| 6 | **ORBIT** | 스크롤 8000–9000 (5000–8000 은 정지 구간) | `cameraTrack`: CAM0 → CAM_ORBIT (`yaw 1.0 → π−1.0`, inOut(2)) — 정면(끝 캡)을 스쳐 반대편으로 돌아 **끝 캡이 화면 왼쪽 하단**을 향함. tilt/dist 유지 | 유지 | (미정) |
| 7 | **HS LIFT** | 스크롤 10000–10500 (9000–10000 은 정지) | 유지 | 히트싱크(핀 66장 + 히트파이프)는 **+0.5**, 콜드플레이트·플레이트 브릿지는 **+0.2** 만 들려 두 층이 구분됨(`hsLift 0→1`, inOut(2)). 이때부터 초크 펌핑 시작 | (미정) |
| 8 | **HS OFF** | 스크롤 12000–13000 (10500–12000 은 정지) | 유지 | 들린 히트싱크·플레이트류가 **함께 위로 상승해 화면 밖(+7)으로 나간 뒤 숨김**(`hsOff 0→1`, in(1.2)) | (미정) |
| 9 | **TOP VIEW** | 스크롤 14500–16000 (13000–14500 은 정지) | `cameraTrack`: CAM_ORBIT → CAM_TOP (`yaw → 0`, `tilt → 0.785`, `dist → 5.6`). **탑뷰 동안만 위에서 내려 비추는 조명** `topLight 0→1`(STACK 시작 시 꺼짐) — 남은 PCB·백플레이트·브라켓을 **위(+Z)에서 −Y(PCIe 엣지) 쪽으로 45° 기울여 내려보는 뷰**. 화면 오른쪽 = +X, 아래 = PCIe 엣지, 비스듬한 회전 없음 | 유지 | (미정) |
| 10 | **STACK** | 스크롤 18000–20500 (16000–18000 은 정지) | `cameraTrack`: CAM_TOP → CAM_STACK — 낮은 각으로 내려와 **한 바퀴 회전**(`yaw 0 → 2π−0.9`, `tilt → 0.42`, `dist → 8.0`) | **햄버거 레시피 구조도** — 나갔던 팬·쉬라우드·핀·히트파이프·플레이트까지 전부 돌아와 **냉각 스택만** Blender `explode` 배치 ×1.0 으로 층층이 떠 있음(층 3개: 쉬라우드+팬(조립 상태, 0.95) / 핀+히트파이프(조립 상태, 0.55) / 콜드플레이트·브릿지(explode 배치 0.32). 핀·파이프·팬은 explode 벡터를 쓰지 않아 벌어지거나 높이가 달라지지 않음). 브라켓·PCB·백플레이트와 그 위 부속(VRAM·초크·캡 등)은 조립 상태로 붙어 있음(`stack 0→1`, 1200 동안 현재 위치에서 보간, 이후 유지). 초크 펌핑 유지 | (미정) |
| 11 | **ASSEMBLE** | 스크롤 21000–23000 (20500–21000 은 정지) | `cameraTrack`: CAM_STACK → CAM_END — +X 끝 캡(로고) 정면 쪽, 살짝 위(`yaw → 2.5π`, `tilt → 0.22`, `dist → 4.5`) | 구조도의 층 간격이 0 으로 줄어 **조립 완료**(`assemble 0→1`, stack 유지 상태에서 간격만 축소) — 로고가 앞에 보임 | (미정) |
| 12 | | 스크롤 23000– | | | |

> 챕터 2 이후는 이어서 받아 적는다.

### 챕터 1 메모
- 스크롤 0 = 빔이 왼쪽 가장자리 → 로고 거의 안 보임. 첫 화면이 너무 비어 보이면 빔 시작 위치를 캡 왼쪽 끝(z≈+0.64)으로 두어 로고 왼쪽 끝이 살짝 걸리게.
- 빔 세기 `beam`: 0–250ms 켜짐 → 팬 훑기 → 1400 꺼짐 + `#blackout` 오버레이(완전 암전, 1400–2000) → 2000–2250 오버레이 걷히며 빔 켜짐 → 로고 훑기 → 리빌 시작 후 500ms 페이드아웃.

### 챕터 2 메모
- 카메라 좌표계: `yaw=π/2` 가 +X 캡 정면. yaw 를 1.0 쪽으로 줄이면 카메라가 +Z(앞) 쪽으로 돌아 캡이 화면 오른쪽으로, tilt 를 올리면 캡이 가까운 쪽(아래)으로 → "우측 하단을 바라봄".
- 줌 아웃과 회전은 같은 ease(`inOut(3)`) 한 트윈으로. 카드 전체가 들어오는 dist 는 기존 분해 뷰(7.6)보다 작게, 조립 상태 기준 6.5 근처.

### 챕터 3 메모
- 팬 전용 필드 `fans`. `three/gpuModel.tsx` 에서 `spin===true`(팬) 부품에만 `y += fans × FAN_LIFT(4)` 를 더함.
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
- 스윕 스팟: `<spotLight angle≈0.4 penumbra≈0.7>`, `intensity = P.beam × 40`. 경로는 `P.sweep` 구간별: 0..1 메인 `(2.9, 1.0, 0.9−1.8u)`→타깃 카드 끝 모서리 `(1.45, 0.22, z)`(윗면 가까운 팬 + 끝 캡 정면 윗부분만 살짝) + 보조 스팟(angle 0.13)→타깃 `(−0.9, 0.25, z)`, 메인 angle 0.2 — 두 자국이 이어져 카드 길이 방향(화면 세로)으로 긴 **얇은** 띠, 세기 16/22 로 살짝만 보임, 화면 왼→오로 이동. 로고 스윕(2..3)은 angle 0.4 / 세기 40 / 1..2 쉬라우드 끝 → 캡 면 / 2..3 캡 앞 `(2.6, 0.15, 1.1 − 2.2·u)`, 타깃 캡 중심(카메라 축 근처 = metallic 반사각).

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

## 5. 결정 필요
- [ ] 챕터 2 이후 구성
- [ ] 각 챕터 카피
- [ ] 스팟 반사만으로 로고 가시성이 충분한지 (부족하면 스팟 2개/각도 조정, 재질은 그대로)
