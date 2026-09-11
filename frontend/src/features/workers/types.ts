import type * as z from 'zod/mini'
import type { networkStatsSchema, workerSchema, workerStatusSchema } from './schema'

export type Worker = z.infer<typeof workerSchema>
export type WorkerStatus = z.infer<typeof workerStatusSchema>
export type NetworkStats = z.infer<typeof networkStatsSchema>
