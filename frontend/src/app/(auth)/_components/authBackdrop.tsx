// 인증 화면 배경 장식: 초록 블러 원 2개 (피그마 P2 로그인 = 좌상·우하, P3 회원가입 = 우상·좌하)
// 원들은 화면을 꽉 채우는 inset-0 레이어 안에 둔다 → 페이지의 첫 DOM 요소가 항상 뷰포트 안에 있어 Next 의 이동 후 scrollIntoView 가 아무것도 스크롤하지 않는다
const POS = {
  login: ['-top-[100px] -left-[120px]', 'right-[-100px] bottom-[-100px]'],
  signup: ['-top-[120px] right-[-100px]', 'left-[-100px] bottom-[-100px]'],
} as const

export function AuthBackdrop({ variant }: { variant: keyof typeof POS }) {
  const [a, b] = POS[variant]
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-clip">
      <div className={`absolute ${a} h-[420px] w-[420px] rounded-full bg-primary/10 blur-[80px]`} />
      <div className={`absolute ${b} h-[360px] w-[360px] rounded-full bg-primary/20 blur-[90px]`} />
    </div>
  )
}
