'use client'

import { MswProvider } from '@/mocks/msw-provider'

export function Providers({ children }: { children: React.ReactNode }) {
  return <MswProvider>{children}</MswProvider>
}
