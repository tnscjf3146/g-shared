import type { z } from 'zod'
import type { jobSchema, jobStatusSchema } from './schema'

export type Job = z.infer<typeof jobSchema>
export type JobStatus = z.infer<typeof jobStatusSchema>
