// 로그인이 필요한 경로 — proxy.ts(서버: 쿠키 존재만 봄)와 authProvider(클라이언트: 실제 세션 확인)가 같은 목록을 쓴다
export const PROTECTED_PATHS = ['/dashboard', '/wallet', '/workers', '/jobs', '/account']
export const isProtectedPath = (pathname: string) => PROTECTED_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'))
