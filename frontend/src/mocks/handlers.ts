import { authHandlers } from '@/features/auth/mocks/handlers'
import { jobHandlers } from '@/features/jobs/mocks/handlers'

export const handlers = [...jobHandlers, ...authHandlers]
