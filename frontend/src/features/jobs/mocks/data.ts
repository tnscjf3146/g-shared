import { catalogSchema, datasetSchema, gpuSchema, jobDetailSchema } from '../schema'
import type { Catalog, Dataset, Gpu, JobDetail } from '../types'
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
})

/** GET /gpus 응답. 카탈로그에서 떼어 냈다 — 이름표(workloads·baseModels)와 달리 살아 있는 워커에 따라 변한다(FE-03) */
export const MOCK_GPUS: Gpu[] = z.array(gpuSchema).parse([
  { id: 1, model: 'NVIDIA GeForce RTX 3060', vramMb: 12288, hourlyPrice: 400 },
  { id: 2, model: 'NVIDIA GeForce RTX 4070', vramMb: 12282, hourlyPrice: 600 },
  { id: 3, model: 'NVIDIA GeForce RTX 4090', vramMb: 24564, hourlyPrice: 1200 },
])

/** 등록된 데이터셋(back DatasetResponse). 새로 등록한 것은 id 5000 부터 */
export const MOCK_DATASETS: Dataset[] = z.array(datasetSchema).parse([
  { datasetId: 101, sizeBytes: 61_217, sampleCount: 300, totalTokens: 10_522, effectiveTokens: 10_522, avgSeqLength: 35, maxSeqLength: 40, dataHash: '0x' + '3c'.repeat(32), storageUri: null, createdAt: '2026-09-09T09:00:00' },
  { datasetId: 102, sizeBytes: 9_161, sampleCount: 100, totalTokens: 1_073, effectiveTokens: 1_073, avgSeqLength: 11, maxSeqLength: 14, dataHash: '0x' + '7e'.repeat(32), storageUri: null, createdAt: '2026-09-09T08:40:00' },
])

const base = {
  workloadId: 1,
  baseModelId: 11,
  datasetId: 101,
  datasetUploaded: true,
  trainingMode: 'AUTO',
  epochs: 3,
  batchSize: 16,
  learningRate: 3e-5,
  maxLength: 40,
  assignedWorkerId: null,
  failReason: null,
  predictedRuntimeSec: 4800,
  actualRuntimeSec: null,
  estimatedCost: 1200,
  actualCost: null,
  progressPercent: null,
  progressEpoch: null,
  progressTotalEpochs: null,
  resultHash: null,
  resultUrl: null,
  adapterName: null,
  adapterVersion: null,
  parentJobId: null,
  startedAt: null,
  finishedAt: null,
}

/** 상태별 1건씩. 새로 만든 작업은 id 1000 부터 */
export const MOCK_JOBS: JobDetail[] = z.array(jobDetailSchema).parse([
  { ...base, jobId: 7, status: 'RUNNING', assignedWorkerId: 'worker-11111111-1111-4111-8111-111111111111', progressPercent: 42, progressEpoch: 2, progressTotalEpochs: 3, createdAt: '2026-09-09T09:10:00', startedAt: '2026-09-09T09:20:00' },
  { ...base, jobId: 6, status: 'ASSIGNED', assignedWorkerId: 'worker-11111111-1111-4111-8111-111111111111', createdAt: '2026-09-09T09:05:00' },
  { ...base, jobId: 5, status: 'FUNDED', workloadId: 2, baseModelId: 21, predictedRuntimeSec: 9000, estimatedCost: 3600, datasetId: 102, maxLength: 14, createdAt: '2026-09-09T08:50:00' },
  { ...base, jobId: 4, status: 'CREATED', trainingMode: 'CUSTOM', epochs: 5, batchSize: 8, learningRate: 2e-5, maxLength: 40, createdAt: '2026-09-09T08:30:00' },
  {
    ...base, jobId: 3, status: 'COMPLETED', assignedWorkerId: 'worker-11111111-1111-4111-8111-111111111111', actualRuntimeSec: 4410, actualCost: 1200, progressPercent: 100, progressEpoch: 3, progressTotalEpochs: 3,
    resultHash: '0x9f2c1e7ab4d3c6f0e1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7a8', resultUrl: 'https://example.com/results/job-3.tar',
    adapterName: 'cs-상담봇', adapterVersion: 2, parentJobId: 8, // 작업 #8(v1)에서 이어서 학습한 v2 — 계보 표시·다운로드 파일 이름 확인용
    createdAt: '2026-09-08T15:00:00', startedAt: '2026-09-08T15:12:00', finishedAt: '2026-09-08T16:25:30',
  },
  {
    ...base, jobId: 8, status: 'COMPLETED', assignedWorkerId: 'worker-11111111-1111-4111-8111-111111111111', actualRuntimeSec: 3900, actualCost: 1200, progressPercent: 100, progressEpoch: 3, progressTotalEpochs: 3,
    resultHash: '0x1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7a89f2c1e7ab4d3c6f0e', resultUrl: 'https://example.com/results/job-8.tar',
    adapterName: 'cs-상담봇', adapterVersion: 1,
    createdAt: '2026-09-07T13:00:00', startedAt: '2026-09-07T13:10:00', finishedAt: '2026-09-07T14:15:00',
  },
  { ...base, jobId: 2, status: 'FAILED', assignedWorkerId: 'worker-22222222-2222-4222-8222-222222222222', failReason: 'CUDA_OOM: 학습 도중 GPU 메모리가 부족했습니다(batch 16 × max length 40).', progressPercent: 18, progressEpoch: 1, progressTotalEpochs: 3, actualRuntimeSec: 900, createdAt: '2026-09-08T11:00:00', startedAt: '2026-09-08T11:05:00', finishedAt: '2026-09-08T11:20:00' },
  { ...base, jobId: 1, status: 'CANCELLED', createdAt: '2026-09-07T10:00:00' },
  // 예치 보류 — 트랜잭션은 보냈는데 서버가 아직 영수증을 못 읽은 상태(CREATED + fundTxHash + escrowJobId 없음).
  // 목업에 이 모양이 없어서 FE-01(새로고침 후 보류 복원 · 보류 중 취소 차단)을 화면으로 확인할 길이 없었다.
  // 자동 전이(MOCK_ADVANCE_MS)에 걸리지 않게 createdAt 을 과거로 둔다
  {
    ...base, jobId: 9, status: 'CREATED',
    fundTxHash: '0x' + 'ab'.repeat(32), escrowJobId: null,
    createdAt: '2026-09-06T09:00:00',
  },
])

/** 목업 전용 자동 전이 간격(ms): 생성 → FUNDED → ASSIGNED → RUNNING → 진행률 +10%/틱 → COMPLETED */
export const MOCK_ADVANCE_MS = { toFunded: 5_000, toAssigned: 5_000, toRunning: 3_000, progressTick: 3_000 }
