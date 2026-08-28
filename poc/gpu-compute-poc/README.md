# Idle GPU Distributed Compute PoC

유휴 NVIDIA GPU를 이용해 승인된 Embedding workload를 분산 실행하는 기술 검증 저장소입니다.

## 현재 상태

- 현재 단계: STEP 1 — **PASS**
- 구현 상태: Worker 코드, 자동 테스트, 실제 CUDA 하드웨어 검증 완료
- 실제 CUDA 검증: **PASS** — NVIDIA GeForce RTX 4070 Laptop GPU에서 검증 (2026-08-27)
- STEP 2: 시작하지 않음

단계별 진행 원칙과 전체 계획은 [docs/poc-plan.md](docs/poc-plan.md), STEP 1 실행법은
[worker/README.md](worker/README.md), 실제 검증 결과는
[docs/step1-hardware-validation.md](docs/step1-hardware-validation.md)를 참고하세요.

## 저장소 구조

현재 단계에 필요한 디렉터리만 생성합니다. 이후 각 단계가 실제 테스트를 통과할 때 다음 디렉터리를 추가합니다.

```text
gpu-compute-poc/
├── worker/                 # STEP 1
├── docs/
│   └── decisions/
├── backend/                # STEP 2 이후
├── frontend/               # STEP 3 이후 최소 GUI
├── scheduler/              # STEP 5
├── blockchain/             # STEP 7
├── p2p/                    # STEP 8
└── infra/                  # 필요한 단계에서만 추가
```

