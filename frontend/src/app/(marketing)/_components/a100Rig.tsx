/**
 * NVIDIA A100 80GB PCIe — CSS 3D 리그 (animejs.com 기법, Three.js 없음)
 *
 * 카드를 눕혀 놓은 좌표계: x = 길이(L), y = 폭(W), z = 두께(T, 2슬롯).
 * 쉬라우드는 상판 + 측벽 4장으로 된 상자, 그 안에 PCB·다이·히트싱크 레이어가 들어 있다.
 * 브라켓(끝면)은 x=0, PCIe 골드 커넥터는 y=W 바깥으로 돌출된 PCB 위.
 * 카메라: rotateX(62°) rotateZ(-30°) → 브라켓이 좌하단, 긴 축이 우상향 (참고 사진과 동일 구도).
 *
 * anime.js는 #l-* 레이어의 translateZ / 측벽 opacity를 움직여 분해한다. 페이지에 한 인스턴스만 (id 고정).
 */
export const RIG = { L: 640, W: 266, T: 78 } as const

// 조립 z / 분해 z (이후 단계에서 타임라인이 사용)
export const LAYER_Z = {
  pcb: { base: 0, open: 0 },
  die: { base: 6, open: 6 },
  vapor: { base: 16, open: 120 },
  fins: { base: 24, open: 190 },
  shroud: { base: RIG.T, open: 330 },
} as const

// 참고 사진 색 — 랜딩 일러스트 전용이라 토큰화하지 않음
const C = {
  topA: '#d9d2c3', // 상판 밝은 면 (브라켓 쪽)
  topB: '#c9c1b0', // 상판 어두운 면 (대각선 너머)
  edge: '#e6e0d3', // 모서리 하이라이트
  sideFront: '#b3ab9c',
  sideEnd: '#a49c8d',
  bracket: '#c8cbcd',
  grille: '#141614',
  pcb: '#17321f',
  gold: 'var(--color-a100-gold)',
}

const { L, W, T } = RIG
const layer = 'layer absolute [transform-style:preserve-3d]'

// 카메라 프리셋: top = 상판 정면(첫 화면), iso = 등각 뷰(도착점)
export const CAMERA = {
  top: { rotateX: 0, rotateZ: 0, scale: 1.25 },
  iso: { rotateX: 62, rotateZ: -30, scale: 1 },
} as const

type Cam = keyof typeof CAMERA

export function A100Rig({ camera = 'iso' }: { camera?: Cam }) {
  const c = CAMERA[camera]
  return (
    <div id="rig-float" className="relative" style={{ width: L, height: W, transformStyle: 'preserve-3d' }}>
      <div
        id="rig"
        className="absolute inset-0"
        style={{ transformStyle: 'preserve-3d', transform: `rotateX(${c.rotateX}deg) rotateZ(${c.rotateZ}deg) scale(${c.scale})` }}
      >
        {/* 바닥 그림자 */}
        <div className="absolute rounded-3xl bg-black/80 blur-2xl" style={{ left: 10, top: 10, width: L - 20, height: W + 10, transform: 'translateZ(-40px)' }} />

        {/* ── PCB (박스보다 y로 22px 돌출, 골드 PCIe 커넥터) ── */}
        <div id="l-pcb" className={layer} style={{ left: 0, top: 0, width: L, height: W + 22, transform: `translateZ(${LAYER_Z.pcb.base}px)` }}>
          <svg viewBox={`0 0 ${L} ${W + 22}`} className="h-full w-full">
            <rect x="0" y="0" width={L} height={W + 4} rx="6" fill={C.pcb} />
            <rect x={L * 0.36} y={W + 2} width={L * 0.36} height="20" fill={C.gold} />
            <rect x={L * 0.36 + 60} y={W + 2} width="6" height="20" fill={C.pcb} />
            {[50, 80, 190, 220].map((y) => <rect key={y} x="50" y={y} width={L - 100} height="1.5" fill="rgba(255,255,255,0.06)" />)}
          </svg>
        </div>

        {/* ── GA100 다이 + HBM2e ×6 ── */}
        <div id="l-die" className={layer} style={{ left: 0, top: 0, width: L, height: W, transform: `translateZ(${LAYER_Z.die.base}px)` }}>
          <svg viewBox={`0 0 ${L} ${W}`} className="h-full w-full overflow-visible">
            {[[214, 88], [214, 118], [214, 148], [390, 88], [390, 118], [390, 148]].map(([x, y], i) => (
              <rect key={i} className="hbm" x={x} y={y} width="36" height="26" rx="2" fill="#3a3d3a" stroke="rgba(0,0,0,0.5)" />
            ))}
            <g id="die" style={{ transformBox: 'fill-box', transformOrigin: 'center' }}>
              <rect x="272" y="85" width="96" height="96" rx="3" className="fill-black" />
              <rect x="278" y="91" width="84" height="84" rx="2" fill="var(--color-accent-mint)" fillOpacity="0.28" />
              {[0, 1, 2, 3, 4].map((i) => (
                <g key={i}>
                  <rect x={278 + i * 21} y="91" width="1" height="84" fill="rgba(0,0,0,0.5)" />
                  <rect x="278" y={91 + i * 21} width="84" height="1" fill="rgba(0,0,0,0.5)" />
                </g>
              ))}
              <rect id="die-core" x="299" y="112" width="42" height="42" rx="2" fill="var(--color-accent-mint)" fillOpacity="0.85" />
            </g>
          </svg>
        </div>

        {/* ── 베이퍼 챔버 ── */}
        <div id="l-vapor" className={layer} style={{ left: 30, top: 18, width: L - 60, height: W - 36, transform: `translateZ(${LAYER_Z.vapor.base}px)`, background: 'linear-gradient(90deg,#b08d57,#c9a96e 50%,#b08d57)', borderRadius: 6 }} />

        {/* ── 히트싱크 핀 (길이 방향) ── */}
        <div id="l-fins" className={layer} style={{ left: 36, top: 22, width: L - 72, height: W - 44, transform: `translateZ(${LAYER_Z.fins.base}px)`, background: 'repeating-linear-gradient(180deg,#cfd3cc 0 5px,transparent 5px 13px)' }} />

        {/* ── 쉬라우드: 상자 (상판 + 측벽 4) ── */}
        <div id="l-shroud" className={layer} style={{ left: 0, top: 0, width: L, height: W }}>
          {/* 상판 — 대각선 면 분할 */}
          <div className="absolute inset-0 overflow-hidden rounded-[10px]" style={{ transform: `translateZ(${T}px)`, background: C.topB }}>
            <div className="absolute inset-0" style={{ background: C.topA, clipPath: `polygon(0 0, ${L * 0.6}px 0, ${L * 0.46}px 100%, 0 100%)` }} />
            {/* NVLink 커버 (먼 쪽 긴 변, 중앙 우측) */}
            <div className="absolute rounded-sm" style={{ left: L * 0.5, top: 0, width: L * 0.22, height: 12, background: 'rgba(0,0,0,0.12)' }} />
            {/* 상단 액센트 라인 */}
            <div id="a100-accent" className="absolute bg-primary" style={{ left: 18, top: 10, width: L - 36, height: 2, borderRadius: 1, opacity: 0.9 }} />
            {/* 모서리 광택 */}
            <div className="absolute inset-0 rounded-[10px]" style={{ boxShadow: `inset 0 0 0 1px ${C.edge}`, opacity: 0.6 }} />
          </div>

          {/* 앞 측벽 (y = W) */}
          <div className="absolute" style={{ left: 0, top: W, width: L, height: T, transformOrigin: 'top', transform: 'rotateX(90deg)', background: `linear-gradient(180deg, ${C.sideFront}, #9c9486)` }} />
          {/* 뒤 측벽 (y = 0) */}
          <div className="absolute" style={{ left: 0, top: 0, width: L, height: T, transformOrigin: 'top', transform: 'rotateX(90deg)', background: C.sideFront }} />
          {/* 먼 끝 측벽 (x = L) */}
          <div className="absolute" style={{ left: L - T, top: 0, width: T, height: W, transformOrigin: 'right', transform: 'rotateY(90deg)', background: C.sideEnd }} />

          {/* 브라켓 (x = 0): 두께 BT의 실버 슬랩. 바깥면(그릴) + 안쪽면 + 윗면·앞면 모서리 띠. 탭이 y 방향으로 돌출 */}
          {(() => {
            const BT = 6 // 브라켓 두께
            const BH = W + 34 // 브라켓 높이 (y 방향, 탭 포함)
            const face = { top: -6, width: T, height: BH, borderRadius: 3 } as const
            return (
              <>
                {/* 바깥면 — 통풍 그릴 */}
                <div className="absolute" style={{ ...face, left: -BT, transformOrigin: 'left', transform: 'rotateY(-90deg)', background: `linear-gradient(90deg, ${C.bracket}, #b5b8ba)` }}>
                  <div className="absolute rounded-sm" style={{ left: 8, top: 14, width: T - 16, height: W - 18, background: C.grille, backgroundImage: 'repeating-linear-gradient(180deg, rgba(255,255,255,0.18) 0 2px, transparent 2px 9px)' }} />
                  <div className="absolute rounded-full bg-black/40" style={{ left: T / 2 - 4, top: W + 14, width: 8, height: 8 }} />
                </div>
                {/* 안쪽면 — 카드 쪽 */}
                <div className="absolute" style={{ ...face, left: 0, transformOrigin: 'left', transform: 'rotateY(-90deg)', background: '#aeb1b3' }} />
                {/* 윗면 모서리 띠 (z = T) */}
                <div className="absolute" style={{ left: -BT, top: -6, width: BT, height: BH, transform: `translateZ(${T}px)`, background: '#dcdfe1' }} />
                {/* 앞면 모서리 띠 (탭 끝, y = BH - 6) */}
                <div className="absolute" style={{ left: -BT, top: BH - 6, width: BT, height: T, transformOrigin: 'top', transform: 'rotateX(90deg)', background: '#b9bcbe' }} />
                {/* 뒷면 모서리 띠 (y = -6) */}
                <div className="absolute" style={{ left: -BT, top: -6, width: BT, height: T, transformOrigin: 'top', transform: 'rotateX(90deg)', background: '#b9bcbe' }} />
              </>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
