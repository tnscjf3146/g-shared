// Hero 우측 일러스트 (피그마 HeroArt 520×420): 민트 링 2개 + 배선 + 노드 점 + 가운데 GPU 카드. 인라인 SVG 하나
const NODES: [number, number, boolean][] = [
  // [x, y, 활성(민트) 여부] — 비활성 노드 하나는 회색
  [260, 70, true],
  [90, 130, true],
  [430, 150, true],
  [70, 350, false],
  [450, 330, true],
]

export function HeroArt() {
  return (
    <svg viewBox="0 0 520 420" width="520" height="420" role="img" aria-label="유휴 GPU가 네트워크로 연결된 모습" className="max-w-full">
      <defs>
        <linearGradient id="gpuBody" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8fd400" />
          <stop offset="1" stopColor="#2f5a00" />
        </linearGradient>
        <filter id="glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">
          <feDropShadow dx="0" dy="14" stdDeviation="14" floodColor="#0d0f0b" floodOpacity="0.25" />
        </filter>
      </defs>
      {/* 링 */}
      <circle cx="260" cy="210" r="180" fill="none" stroke="#30e2ad" strokeOpacity="0.35" />
      <circle cx="260" cy="210" r="240" fill="none" stroke="#30e2ad" strokeOpacity="0.18" />
      {/* 배선 */}
      {NODES.map(([x, y]) => (
        <line key={`l${x}${y}`} x1="260" y1="210" x2={x} y2={y} stroke="#333333" strokeOpacity="0.45" />
      ))}
      {/* GPU 카드 */}
      <g filter="url(#shadow)">
        <rect x="117" y="128" width="286" height="163" rx="20" fill="url(#gpuBody)" />
        <rect x="117" y="128" width="286" height="163" rx="20" fill="none" stroke="#ffffff" strokeOpacity="0.15" />
        <rect x="190" y="142" width="140" height="6" rx="3" fill="#30e2ad" opacity="0.9" />
        {[190, 312].map((cx) => (
          <g key={cx}>
            <circle cx={cx} cy="214" r="38" fill="#0d0f0b" fillOpacity="0.55" stroke="#30e2ad" strokeWidth="2" />
            <circle cx={cx} cy="214" r="12" fill="#30e2ad" />
          </g>
        ))}
      </g>
      {/* 노드 */}
      {NODES.map(([x, y, on]) => (
        <circle key={`n${x}${y}`} cx={x} cy={y} r="7" fill={on ? '#30e2ad' : '#5e6e77'} filter={on ? 'url(#glow)' : undefined} />
      ))}
    </svg>
  )
}
