'use client'

import { useEffect, useState } from 'react'

const MOCKING_ENABLED = process.env.NEXT_PUBLIC_API_MOCKING === 'enabled'

export function MswProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(!MOCKING_ENABLED)

  useEffect(() => {
    if (!MOCKING_ENABLED) return
    import('./browser')
      .then(({ worker }) => worker.start({ onUnhandledRequest: 'bypass' }))
      .then(() => setReady(true))
  }, [])

  // worker 준비 전 요청이 실제 서버로 새는 것을 막기 위해 렌더 보류
  if (!ready) return null
  return children
}
