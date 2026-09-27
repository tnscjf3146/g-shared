// 브라우저는 항상 같은 오리진 /api 로 호출한다(next.config rewrite 가 백엔드로 넘김 → refresh 쿠키가 CORS 없이 동작).
// 서버(서버 컴포넌트·instrumentation)는 백엔드 오리진으로 직접.
// `||` 를 쓴다(`??` 아님): .env 에 키만 있고 값이 비면 undefined 가 아니라 빈 문자열이 들어오는데,
// `??` 는 그것을 기본값으로 바꿔 주지 않아 백엔드 주소가 통째로 사라진다(.env.example 을 그대로 복사한 경우).
export const API_BASE_URL =
  typeof window === 'undefined'
    ? `${process.env.BACKEND_ORIGIN || 'http://localhost:8080'}/api`
    : (process.env.NEXT_PUBLIC_API_BASE_URL || '/api')

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function fetcher<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.code ?? 'UNKNOWN', body.message ?? res.statusText)
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json() as Promise<T>
}
