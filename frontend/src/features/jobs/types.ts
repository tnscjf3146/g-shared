import type * as z from 'zod/mini'
import type {
  autoModeSchema,
  baseModelSchema,
  catalogSchema,
  createJobRequestSchema,
  datasetMetadataSchema,
  datasetSchema,
  estimateRequestSchema,
  estimateSchema,
  gpuFitSchema,
  gpuOptionSchema,
  gpuSchema,
  jobDetailSchema,
  jobPageSchema,
  jobStatusSchema,
  jobSpecSchema,
  jobSummarySchema,
  trainingConfigSchema,
  trainingModeSchema,
  uploadResponseSchema,
  workloadKindSchema,
  workloadSchema,
} from './schema'

export type JobStatus = z.infer<typeof jobStatusSchema>
export type TrainingMode = z.infer<typeof trainingModeSchema>
export type AutoMode = z.infer<typeof autoModeSchema>
export type TrainingConfig = z.infer<typeof trainingConfigSchema>

export type WorkloadKind = z.infer<typeof workloadKindSchema>
export type Workload = z.infer<typeof workloadSchema>
export type BaseModel = z.infer<typeof baseModelSchema>
export type DatasetMetadata = z.infer<typeof datasetMetadataSchema>
export type Dataset = z.infer<typeof datasetSchema>
export type Gpu = z.infer<typeof gpuSchema>
export type GpuFit = z.infer<typeof gpuFitSchema>
export type GpuOption = z.infer<typeof gpuOptionSchema>
export type Catalog = z.infer<typeof catalogSchema>

export type JobSpec = z.infer<typeof jobSpecSchema>
export type EstimateRequest = z.infer<typeof estimateRequestSchema>
export type UploadResponse = z.infer<typeof uploadResponseSchema>
export type Estimate = z.infer<typeof estimateSchema>
export type CreateJobRequest = z.infer<typeof createJobRequestSchema>

export type JobSummary = z.infer<typeof jobSummarySchema>
export type JobPage = z.infer<typeof jobPageSchema>
export type JobDetail = z.infer<typeof jobDetailSchema>
