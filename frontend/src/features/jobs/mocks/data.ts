import { jobSchema } from '../schema'

// 고정 fixture — schema를 통과하지 못하면 여기서 바로 터진다
export const jobs = jobSchema.array().parse([
  { id: 'job-001', name: 'resnet50-train', status: 'RUNNING', workerId: 'worker-01', createdAt: '2026-09-01T09:00:00Z' },
  { id: 'job-002', name: 'llama-infer', status: 'QUEUED', workerId: null, createdAt: '2026-09-01T09:05:00Z' },
  { id: 'job-003', name: 'stable-diffusion', status: 'SUCCEEDED', workerId: 'worker-02', createdAt: '2026-08-31T18:30:00Z' },
  { id: 'job-004', name: 'whisper-batch', status: 'FAILED', workerId: 'worker-01', createdAt: '2026-08-31T12:00:00Z' },
])
