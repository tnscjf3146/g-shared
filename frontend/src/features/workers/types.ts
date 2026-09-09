import type * as z from 'zod/mini'
import type { workerSchema, workerStatusSchema } from './schema'

export type Worker = z.infer<typeof workerSchema>
export type WorkerStatus = z.infer<typeof workerStatusSchema>
