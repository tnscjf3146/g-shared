import type * as z from 'zod/mini'
import type {
  autoModeSchema,
  baseModelSchema,
  catalogSchema,
  createDatasetRequestSchema,
  createJobRequestSchema,
  createJobResponseSchema,
  datasetMetadataSchema,
  datasetSchema,
  fundResponseSchema,
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
  lossPointSchema,
  resultVerificationSchema,
  trainingConfigSchema,
  trainingModeSchema,
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
export type CreateDatasetRequest = z.infer<typeof createDatasetRequestSchema>
export type Dataset = z.infer<typeof datasetSchema>
export type Gpu = z.infer<typeof gpuSchema>
export type GpuFit = z.infer<typeof gpuFitSchema>
export type GpuOption = z.infer<typeof gpuOptionSchema>
export type Catalog = z.infer<typeof catalogSchema>

export type JobSpec = z.infer<typeof jobSpecSchema>
export type EstimateRequest = z.infer<typeof estimateRequestSchema>
export type Estimate = z.infer<typeof estimateSchema>
export type CreateJobRequest = z.infer<typeof createJobRequestSchema>
export type CreateJobResponse = z.infer<typeof createJobResponseSchema>

export type FundResponse = z.infer<typeof fundResponseSchema>
export type JobSummary = z.infer<typeof jobSummarySchema>
export type JobPage = z.infer<typeof jobPageSchema>
export type JobDetail = z.infer<typeof jobDetailSchema>

export type LossPoint = z.infer<typeof lossPointSchema>
export type ResultVerification = z.infer<typeof resultVerificationSchema>
