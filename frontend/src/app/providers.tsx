'use client'

import { AuthProvider } from '@/features/auth/components/authProvider'
import { MswProvider } from '@/mocks/msw-provider'

// MSW 안쪽에 AuthProvider — 세션 복구(reissue) 요청이 목업 워커 준비 뒤에 나가도록
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MswProvider>
      <AuthProvider>{children}</AuthProvider>
    </MswProvider>
  )
}
