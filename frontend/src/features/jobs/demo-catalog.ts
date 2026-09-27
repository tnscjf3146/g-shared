// ponytail: 발표용 목업 — 서버 목록 뒤에 붙여 보여 주기만 한다. 발표 후 이 파일 삭제 + api.ts 원복.
// 목업 항목은 서버에 없으므로 고르고 진행하면 다음 단계에서 에러가 난다(의도).
import type { BaseModel, GpuOption, Workload } from './types'

const W = { image: 9001, speech: 9002, multimodal: 9003, code: 9004 }

export const DEMO_WORKLOADS: Workload[] = [
  { id: W.image, kind: 'TRAIN_LORA', name: '이미지 학습', description: '이미지 생성·분류 모델을 내 이미지 데이터로 미세조정합니다.' },
  { id: W.speech, kind: 'TRAIN_LORA', name: '음성 학습', description: '음성 인식(받아쓰기) 모델을 내 음성 데이터로 미세조정합니다.' },
  { id: W.multimodal, kind: 'TRAIN_LORA', name: '멀티모달 학습', description: '이미지와 텍스트를 함께 이해하는 모델을 학습합니다.' },
  { id: W.code, kind: 'TRAIN_LORA', name: '코드 학습', description: '내 코드 저장소로 코드 생성 모델을 미세조정합니다.' },
]

// TEXT 자리는 서버의 실제 텍스트 학습 워크로드 id 로 채운다(demoBaseModels)
const TEXT = -1
const MODELS: BaseModel[] = [
  { id: 9101, name: 'Llama 3.2 3B', params: '3B', workloadIds: [TEXT] },
  { id: 9102, name: 'Gemma 2 9B', params: '9B', workloadIds: [TEXT] },
  { id: 9103, name: 'EXAONE 3.5 7.8B', params: '7.8B', workloadIds: [TEXT] },
  { id: 9111, name: 'Stable Diffusion XL', params: '3.5B', workloadIds: [W.image] },
  { id: 9112, name: 'FLUX.1 schnell', params: '12B', workloadIds: [W.image] },
  { id: 9113, name: 'ViT-Base', params: '86M', workloadIds: [W.image] },
  { id: 9121, name: 'Whisper Large v3', params: '1.5B', workloadIds: [W.speech] },
  { id: 9122, name: 'Whisper Small', params: '244M', workloadIds: [W.speech] },
  { id: 9131, name: 'Qwen2.5-VL 3B', params: '3B', workloadIds: [W.multimodal] },
  { id: 9132, name: 'LLaVA 1.6 7B', params: '7B', workloadIds: [W.multimodal] },
  { id: 9141, name: 'Qwen2.5-Coder 7B', params: '7B', workloadIds: [W.code] },
  { id: 9142, name: 'CodeLlama 7B', params: '7B', workloadIds: [W.code] },
]

export function demoBaseModels(realWorkloads: Workload[]): BaseModel[] {
  const textIds = realWorkloads.filter((w) => w.kind === 'TRAIN_LORA').map((w) => w.id)
  return MODELS.map((m) => ({ ...m, workloadIds: m.workloadIds.flatMap((id) => (id === TEXT ? textIds : [id])) }))
}

const GPUS: [string, number, number][] = [
  ['NVIDIA RTX A6000', 49152, 1],
  ['NVIDIA GeForce RTX 4090', 24576, 3],
  ['NVIDIA GeForce RTX 3090', 24576, 2],
  ['NVIDIA GeForce RTX 4080 SUPER', 16384, 2],
  ['NVIDIA GeForce RTX 4070 Ti', 12288, 4],
  ['NVIDIA GeForce RTX 3060', 12288, 5],
]

/** 실제 등급 뒤에 붙일 목업 GPU. 실제 워커와 같은 모델명은 뺀다. 추천 배지 없이 전부 OK */
export function demoGpuOptions(real: GpuOption[]): GpuOption[] {
  const taken = new Set(real.map((g) => g.model))
  const requiredVramMb = real[0]?.requiredVramMb ?? 8192
  return GPUS.filter(([model]) => !taken.has(model)).map(([model, vramMb, availableWorkers], i) => ({
    id: 9201 + i,
    model,
    vramMb,
    hourlyPrice: 0,
    fit: 'OK',
    requiredVramMb,
    reason: '',
    availableWorkers,
  }))
}

const DEMO_IDS = new Set([...DEMO_WORKLOADS.map((w) => w.id), ...MODELS.map((m) => m.id)])
export const isDemoId = (id: number) => DEMO_IDS.has(id)
