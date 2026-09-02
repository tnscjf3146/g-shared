# Idle GPU Distributed Compute PoC

유휴 NVIDIA GPU를 외부 Worker로 연결해 GPU 상태를 등록하고 향후 연산을 분산 실행하기 위한 기술 검증 저장소입니다.

## 현재 상태

- 현재 단계: STEP 2.5 — **PASS**
- STEP 1: **PASS** — Worker 코드, 자동 테스트, 실제 CUDA 하드웨어 검증 완료
- 실제 CUDA 검증: **PASS** — NVIDIA GeForce RTX 4070 Laptop GPU에서 검증 (2026-08-27)
- STEP 2: **PASS** — Spring Boot Backend, outbound WebSocket Worker 등록, 5초 heartbeat, 15초 liveness timeout, REST 조회 및 last-connection-wins 검증 완료
- STEP 2.5: **PASS** — 서로 다른 네트워크의 Backend ↔ Worker를 Cloudflare WSS Tunnel로 연결해 실제 RTX 4070 Docker CUDA 원격 실행 검증 완료 (2026-08-28)
- 표준 GPU Benchmark 등록: 구현 완료 — Worker 최초 등록 전 고정 FP16 CUDA benchmark, UUID/version cache, REGISTER/Registry/API 연동
- STEP 3: 시작하지 않음

단계별 진행 원칙과 전체 계획은 [docs/poc-plan.md](docs/poc-plan.md), STEP 1 실행법은
[worker/README.md](worker/README.md), Backend 실행법은 [backend/README.md](backend/README.md)를 참고하세요.
실제 검증 결과는 [STEP 1](docs/step1-hardware-validation.md),
[STEP 2](docs/step2-integration-validation.md),
[STEP 2.5](docs/step2-5-integration-validation.md)에 기록되어 있습니다.
표준 Benchmark 규격과 RTX 4050/4070 검증 절차는
[GPU standard benchmark](docs/gpu-standard-benchmark.md)를 참고하세요.

GPU 공급자는 `worker-config.json`을 채우고 `gpu-worker-agent.exe`를 더블클릭하는 것만으로
Worker를 실행합니다. 자세한 설정 항목은 [worker/README.md](worker/README.md)를 참고하세요.

## 저장소 구조

현재 단계에 필요한 디렉터리만 생성합니다. 이후 각 단계가 실제 테스트를 통과할 때 다음 디렉터리를 추가합니다.

```text
gpu-compute-poc/
├── worker/                 # STEP 1 CUDA runtime + STEP 2 WebSocket client
├── backend/                # STEP 2 Spring Boot control plane
├── docs/
│   └── decisions/
├── frontend/               # STEP 3 이후 최소 GUI
├── scheduler/              # STEP 5
├── blockchain/             # STEP 7
├── p2p/                    # STEP 8
└── infra/                  # 필요한 단계에서만 추가
```

