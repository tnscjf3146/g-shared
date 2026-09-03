import { z } from 'zod'

export const jobStatusSchema = z.enum(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'])

export const jobSchema = z.object({
  id: z.string(),
  name: z.string(),
  status: jobStatusSchema,
  workerId: z.string().nullable(),
  createdAt: z.string(),
})
