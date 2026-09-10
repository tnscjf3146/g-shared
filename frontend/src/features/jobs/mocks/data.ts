import { catalogSchema, jobDetailSchema } from '../schema'
import type { Catalog, JobDetail } from '../types'
import * as z from 'zod/mini'

// 고정 fixture(schema 통과값만). 카탈로그는 백엔드 제안 스펙(job.md §1-2)의 모양
export const MOCK_CATALOG: Catalog = catalogSchema.parse({
  workloads: [
    { id: 1, kind: 'TRAIN_LORA', name: '학습 (LoRA)', description: '내 데이터셋으로 제공 모델을 LoRA 방식으로 미세조정합니다. 결과물은 어댑터 파일입니다.' },
    { id: 2, kind: 'INFERENCE', name: '추론', description: '모델(또는 모델+어댑터)에 입력 데이터를 넣어 결과를 뽑습니다.' },
    { id: 3, kind: 'EVALUATION', name: '평가', description: '평가 데이터셋으로 모델(또는 모델+어댑터)의 성능 지표를 계산합니다.' },
  ],
  baseModels: [
    { id: 11, name: 'bert-base-multilingual', params: '178M', workloadIds: [1, 3] },
    { id: 12, name: 'klue-roberta-base', params: '110M', workloadIds: [1, 3] },
    { id: 21, name: 'Llama-3.2-1B', params: '1.2B', workloadIds: [1, 2, 3] },
    { id: 22, name: 'Qwen2.5-1.5B-Instruct', params: '1.5B', workloadIds: [1, 2, 3] },
  ],
  gpus: [
    { id: 1, model: 'NVIDIA GeForce RTX 3060', vramMb: 12288, hourlyPrice: 400 },
    { id: 2, model: 'NVIDIA GeForce RTX 4070', vramMb: 12282, hourlyPrice: 600 },
    { id: 3, model: 'NVIDIA GeForce RTX 4090', vramMb: 24564, hourlyPrice: 1200 },
  ],
})

const base = {
  workloadId: 1,
  baseModelId: 11,
  gpuId: 2,
  trainingMode: 'AUTO',
  predictedRuntimeSec: 4800,
  actualRuntimeSec: null,
  estimatedCost: 1200,
  actualCost: null,
  progressPercent: null,
  progressEpoch: null,
  progressTotalEpochs: null,
  resultHash: null,
  resultUrl: null,
  startedAt: null,
  finishedAt: null,
  datasetName: 'instruct-300.jsonl',
  datasetMetadata: { sizeBytes: 61_217, sampleCount: 300, totalTokens: 10_522, avgSeqLength: 35, maxSeqLength: 40 },
}

/** 상태별 1건씩. 새로 만든 작업은 id 1000 부터 */
export const MOCK_JOBS: JobDetail[] = z.array(jobDetailSchema).parse([
  { ...base, jobId: 7, status: 'RUNNING', progressPercent: 42, progressEpoch: 2, progressTotalEpochs: 3, createdAt: '2026-09-09T09:10:00', startedAt: '2026-09-09T09:20:00' },
  { ...base, jobId: 6, status: 'ASSIGNED', createdAt: '2026-09-09T09:05:00' },
  { ...base, jobId: 5, status: 'FUNDED', workloadId: 2, baseModelId: 21, gpuId: 3, predictedRuntimeSec: 9000, estimatedCost: 3600, createdAt: '2026-09-09T08:50:00', datasetName: 'inference-inputs-100.json', datasetMetadata: { sizeBytes: 9_161, sampleCount: 100, totalTokens: 1_073, avgSeqLength: 11, maxSeqLength: 14 } },
  { ...base, jobId: 4, status: 'CREATED', trainingMode: 'CUSTOM', createdAt: '2026-09-09T08:30:00' },
  {
    ...base, jobId: 3, status: 'COMPLETED', actualRuntimeSec: 4410, actualCost: 1200, progressPercent: 100, progressEpoch: 3, progressTotalEpochs: 3,
    resultHash: '0x9f2c1e7ab4d3c6f0e1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7a8', resultUrl: 'https://example.com/results/job-3.tar.gz',
    createdAt: '2026-09-08T15:00:00', startedAt: '2026-09-08T15:12:00', finishedAt: '2026-09-08T16:25:30',
  },
  { ...base, jobId: 2, status: 'FAILED', progressPercent: 18, progressEpoch: 1, progressTotalEpochs: 3, actualRuntimeSec: 900, createdAt: '2026-09-08T11:00:00', startedAt: '2026-09-08T11:05:00', finishedAt: '2026-09-08T11:20:00' },
  { ...base, jobId: 1, status: 'CANCELLED', createdAt: '2026-09-07T10:00:00' },
])

/** 목업 전용 자동 전이 간격(ms): 생성 → FUNDED → ASSIGNED → RUNNING → 진행률 +10%/틱 → COMPLETED */
export const MOCK_ADVANCE_MS = { toFunded: 5_000, toAssigned: 5_000, toRunning: 3_000, progressTick: 3_000 }
