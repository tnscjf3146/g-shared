// 로그인 뒤 돌아갈 경로(next) 검증 — 오픈 리다이렉트 차단.
// 같은 오리진의 절대 경로('/'로 시작)만 허용하고, 프로토콜 상대 URL('//evil.com', '/\evil.com')과 절대 URL·스킴은 홈으로 떨어뜨린다
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return '/'
  try {
    const u = new URL(raw, 'http://local') // 상대 경로로 해석됐을 때만 통과(절대 URL 이면 origin 이 달라진다)
    if (u.origin !== 'http://local') return '/'
    return u.pathname + u.search + u.hash
  } catch {
    return '/'
  }
}
