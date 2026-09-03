const g = globalThis as typeof globalThis & { __mswServerStarted?: boolean }

export async function register() {
  if (process.env.NEXT_RUNTIME !== 'nodejs' || process.env.NEXT_PUBLIC_API_MOCKING !== 'enabled') return
  if (g.__mswServerStarted) return // HMR로 register()가 재실행돼도 listen()은 한 번만
  g.__mswServerStarted = true
  const { server } = await import('@/mocks/server')
  server.listen({ onUnhandledRequest: 'bypass' })
}
