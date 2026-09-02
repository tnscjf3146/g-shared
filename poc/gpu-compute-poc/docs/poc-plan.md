# PoC 구현 계획

## 현재 게이트 상태 (2026-08-28)

| 단계 | 상태 | 검증 |
|---|---|---|
| STEP 1 | **PASS** | RTX 4070 Laptop GPU에서 CUDA Embedding, 외부 `nvidia-smi`, 기존 테스트 9개 통과 |
| STEP 2 | **PASS** | 실제 Python GPU Worker outbound WebSocket 연결, REGISTER, 5초 HEARTBEAT, OFFLINE, last-connection-wins, Backend/Python 전체 테스트 통과 |
| STEP 2.5 | **PASS** | 서로 다른 네트워크의 Backend ↔ Worker를 Cloudflare WSS Tunnel로 연결, 원격 `GPU_PYTORCH_TEST` SUCCESS, RTX 4070 Docker CUDA 실행, `containerExitCode=0`, checksum 일치, cache miss/hit 검증 |
| STEP 3 | 시작하지 않음 | STEP 2 이후 별도 설계 필요 |

STEP 2의 실제 결과는 [step2-integration-validation.md](step2-integration-validation.md),
STEP 2.5의 실제 결과는 [step2-5-integration-validation.md](step2-5-integration-validation.md)에 기록한다.

## 사전 환경 점검 결과 (2026-08-26)

| 항목 | 확인 결과 | 판정 |
|---|---|---|
| OS | Windows 11 Home 64-bit, build 26200 | 사용 가능 |
| Git 저장소 | `C:\`에는 저장소 없음 | `C:\gpu-compute-poc` 신규 생성 |
| Python | Windows 3.10.9, WSL Ubuntu 3.12.3 | WSL은 요구사항 충족, Windows Python은 업그레이드 필요 |
| Java | JDK/Javac 21.0.6 | STEP 2 준비됨 |
| Docker | Desktop 4.65.0, Engine 29.2.1, Linux containers | 사용 가능 |
| Container runtime | `nvidia` runtime 등록됨 | GPU 장치 유무는 별도 검증 필요 |
| GPU | Intel Iris Xe만 탐지 | NVIDIA CUDA 검증 불가 |
| NVIDIA 도구 | 호스트 `nvidia-smi`, `nvcc` 없음 | NVIDIA GPU/드라이버 없음 |
| Python ML 패키지 | torch, sentence-transformers, pynvml 없음 | GPU PC에서 설치 필요 |

## 최소 의존성

STEP 1 런타임만 기준으로 한다.

- Python 3.11 이상
- NVIDIA GPU와 호환 드라이버 (`nvidia-smi` 정상 동작)
- CUDA 지원 PyTorch wheel
- `sentence-transformers==6.0.0`
- `nvidia-ml-py==13.610.43`
- 모델 다운로드를 위한 최초 1회 네트워크 또는 사전 채운 Hugging Face cache

CUDA Toolkit의 `nvcc`는 사전 빌드 PyTorch wheel 실행에 필수는 아니다. Docker, Java, Redis,
PostgreSQL은 STEP 1 런타임 의존성이 아니다.

STEP 2는 Java 21, Spring Boot 4.1.1, Gradle Wrapper 9.3.1, `websockets==17.1`을 추가로 사용한다.
DB, Redis, Docker Runtime은 STEP 2에 포함하지 않는다.

## 단계별 구현 게이트

1. Worker 단독 CUDA Embedding: GPU/NVML 탐지, 명시적 CUDA 실행, 시간 측정 및 사용률 관찰.
2. **Backend 연결 — PASS**: Spring Boot WebSocket 서버, outbound Worker 등록, heartbeat, liveness와 조회 API.
3. 원격 실행: 승인된 EMBEDDING 명령만 Worker registry를 통해 실행하고 결과 반환.
4. Multi GPU: Job/Chunk 상태 머신, 두 실제 GPU Worker의 동시 처리와 결과 순서 병합.
5. Scheduler V1: FastAPI, hard constraint와 benchmark 기반 FAST/CHEAP/BALANCED 동적 ranking.
6. 계측/비용: Chunk GPU 구간 실행시간과 worker 가격으로 execution record 및 집계 생성.
7. 정산: Foundry/Anvil의 ComputeEscrow로 여러 provider에 서로 다른 테스트 금액 지급.
8. P2P data plane: Backend는 peer 정보만 중개하고 입력/결과는 libp2p로 직접 전달.
9. 장애 복구: heartbeat timeout, lease, attempt 및 다른 Worker 재할당.
10. Benchmark: 단일 GPU/round robin/scheduler 비교와 재현 가능한 결과 문서화.

각 단계는 바로 전 단계의 실제 성공 기준이 충족된 뒤에만 시작한다.

## 주요 위험과 대응

| 위험 | 영향 | 대응/검증 |
|---|---|---|
| NVIDIA GPU 또는 드라이버 부재 | STEP 1 차단 | `nvidia-smi`와 `torch.cuda.is_available()`를 모두 gate로 사용 |
| PyTorch wheel/드라이버 CUDA 호환성 | CUDA 초기화 실패 | GPU PC의 드라이버에 맞는 공식 PyTorch selector 사용, 실행 시 build CUDA 출력 |
| 최초 모델 다운로드 실패/느림 | Worker 시작 지연 | cache 경로 운영 및 모델 사전 다운로드; 오류를 성공으로 숨기지 않음 |
| 작은 입력으로 사용률 샘플 누락 | GPU 사용 증거 부족 | 입력 반복 수 조절, NVML 50ms peak sampler와 외부 `nvidia-smi` 동시 관찰 |
| 서로 다른 GPU 성능 편차 | straggler/비용 왜곡 | STEP 5 benchmark throughput과 동적 chunk 배정 |
| NAT/방화벽 | 외부 Worker 연결/P2P 실패 | control plane은 outbound WebSocket, P2P는 relay 허용 |
| Worker 이탈/중복 완료 | Chunk 손실 또는 중복 과금 | STEP 9 lease/attempt와 idempotent 결과 수락 |
| 임의 코드 실행 | 공급자 PC 침해 | workload allowlist; 사용자 Python/쉘 실행 경로를 만들지 않음 |
| 결과 payload 대형화 | Backend 병목 | 핵심 검증 이후 STEP 8 P2P data plane으로 분리 |
| 비용 단위/부동소수점 | 정산 오차 | 최소 단위 정수 사용, 반올림 정책을 STEP 6/7 ADR로 고정 |
| Windows WDDM에서 장시간 유지한 NVML handle 무효화 | heartbeat snapshot 실패 및 Worker 종료 | snapshot 실패 시 NVML probe를 1회 재초기화하고, 실패가 계속되면 non-zero 종료 |

