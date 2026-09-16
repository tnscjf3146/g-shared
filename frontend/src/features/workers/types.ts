import type * as z from 'zod/mini'
import type { networkStatsSchema, operatorStatusSchema, workerSchema, workerStatusSchema } from './schema'

export type Worker = z.infer<typeof workerSchema>
export type WorkerStatus = z.infer<typeof workerStatusSchema>
export type NetworkStats = z.infer<typeof networkStatsSchema>
export type OperatorStatus = z.infer<typeof operatorStatusSchema>
