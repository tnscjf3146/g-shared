import { authHandlers } from '@/features/auth/mocks/handlers'
import { jobHandlers } from '@/features/jobs/mocks/handlers'
import { walletHandlers } from '@/features/wallet/mocks/handlers'
import { workerHandlers } from '@/features/workers/mocks/handlers'

export const handlers = [...jobHandlers, ...authHandlers, ...walletHandlers, ...workerHandlers]
