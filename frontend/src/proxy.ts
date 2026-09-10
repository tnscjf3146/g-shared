import { type NextRequest, NextResponse } from 'next/server'

// 보호 경로 가드 (Next 16 의 middleware 대체). refresh 쿠키(`refreshToken`, 백엔드가 /api 리라이트를 통해 같은 오리진에 심음)의
// **존재 여부만** 본다 — 서명·만료 검증은 백엔드, 실제 접근 토큰은 클라이언트가 reissue 로 받는다. 쿠키가 없으면 로그인으로 보내고
// 로그인 뒤 돌아올 곳을 next 에 담는다(loginForm 이 safeNextPath 로 다시 검증). 보호 경로가 늘면 PROTECTED 와 matcher 에 같이 추가
const REFRESH_COOKIE = 'refreshToken'
const PROTECTED = ['/dashboard', '/wallet', '/workers', '/jobs']

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl
  const isProtected = PROTECTED.some((p) => pathname === p || pathname.startsWith(p + '/'))
  if (!isProtected || request.cookies.has(REFRESH_COOKIE)) return NextResponse.next()
  const login = new URL('/login', request.url)
  login.searchParams.set('next', pathname + search)
  return NextResponse.redirect(login)
}

export const config = {
  matcher: ['/dashboard/:path*', '/wallet/:path*', '/workers/:path*', '/jobs/:path*'],
}
