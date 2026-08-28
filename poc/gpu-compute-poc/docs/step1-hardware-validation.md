# STEP 1 CUDA Hardware Validation

## 최종 판정

- 테스트 날짜: 2026-08-27 (Asia/Seoul)
- 판정: **STEP 1 PASS**
- STEP 2 상태: 시작하지 않음

## 검증 환경

| 항목 | 결과 |
|---|---|
| PC | Samsung 960XGL, x64 |
| OS | Microsoft Windows 11 Enterprise 25H2, build 26200.9168 |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU (compute capability 8.9) |
| VRAM | 8188 MiB |
| NVIDIA Driver | 596.36 |
| NVIDIA driver CUDA support | 13.2 |
| Python | 3.12.12, `worker/.venv` |
| PyTorch | 2.12.1+cu126 |
| PyTorch CUDA | 12.6 |
| `torch.cuda.is_available()` | `True` |
| CUDA device name | NVIDIA GeForce RTX 4070 Laptop GPU |
| sentence-transformers | 6.0.0 |
| nvidia-ml-py | 13.610.43 |
| Docker | 설치되지 않음; NVIDIA Container Runtime 사용 불가 (STEP 1 호스트 실행에는 비필수) |
| CUDA Toolkit / `nvcc` | 설치되지 않음 (사전 빌드 PyTorch CUDA wheel 실행에는 비필수) |

공식 CUDA 12.6 PyTorch wheel을 사용했다. Driver 596.36은 CUDA 13.2까지 지원하므로
PyTorch CUDA 12.6 런타임과 호환된다. CUDA Toolkit 전체는 설치하지 않았다.

## 자동 테스트

```text
python -m unittest discover -s tests -v
Ran 9 tests in 0.002s
OK
```

기존 테스트를 삭제하거나 완화하지 않았으며 9개 모두 통과했다.

## 기본 CUDA 실행 결과

실행 명령:

```powershell
worker\.venv\Scripts\python.exe -m gpu_worker --repeat 256 --batch-size 64
```

| 항목 | 결과 |
|---|---|
| Model | `BAAI/bge-small-en-v1.5` |
| Device | `cuda` (실제 모델 장치 `cuda:0`) |
| Batch Size | 64 |
| Repeat | 256 |
| Input count | 768 |
| Embedding Shape | `(768, 384)` |
| Model load | 35049.04 ms (최초 모델 다운로드 포함) |
| Embedding runtime | 536.61 ms |
| Worker 내부 peak GPU utilization | 37% |
| 외부 `nvidia-smi` peak GPU utilization | 84% |
| 외부 `nvidia-smi` GPU memory | baseline 0 MiB, peak 305 MiB, delta 305 MiB |
| Worker 종료 코드 | 0 |
| CPU fallback | 미사용 |

Worker 로그에서 `CUDA: AVAILABLE`, `device=cuda`, 정상 embedding shape,
`Embedding test completed`, `POC SUCCESS: YES`를 확인했다.

## 외부 GPU 사용 증거

기본 실행과 동시에 약 200 ms 간격으로 `nvidia-smi`의 GPU utilization과 used memory를
수집했다. 작업 시간이 짧아 PID 상관관계를 더 명확히 확인하기 위해 같은 승인 모델로
`--repeat 2048 --batch-size 64` 실행도 수행했다.

확장 실행 결과:

| 항목 | 결과 |
|---|---|
| Input count | 6144 |
| Embedding Shape | `(6144, 384)` |
| Embedding runtime | 1972.47 ms |
| Worker 내부 peak GPU utilization | 80% |
| 외부 `nvidia-smi` peak GPU utilization | 80% |
| 외부 `nvidia-smi` GPU memory | baseline 0 MiB, peak 305 MiB, delta 305 MiB |
| 새 CUDA compute process | PID 24844, `C:\Users\SSAFY\miniforge3\python.exe`, type `C` |
| Worker 종료 코드 | 0 |

Windows 가상환경의 Python redirector가 base interpreter를 실행하므로 시작한 wrapper PID와
`nvidia-smi`에 표시된 CUDA compute PID가 다르다. 실행 전 Python PID 집합과 비교하여 Worker
실행 중 새로 생성된 compute PID임을 확인했다. WDDM은 PID별 GPU memory를 `N/A`로 표시하지만,
해당 compute PID만 새로 나타난 구간에 GPU 전체 memory가 0 MiB에서 305 MiB로 증가했다. Worker의
NVML 측정도 free VRAM이 7949 MiB에서 7644 MiB로 305 MiB 감소해 같은 값을 확인했다.

peak 시점의 외부 `nvidia-smi` 핵심 값:

```text
GPU-Util: 80%
Memory-Usage: 305 MiB / 8188 MiB
Process: PID 24844, Type C, python.exe
```

## STEP 1 성공 조건

| 조건 | 판정 | 근거 |
|---|---|---|
| NVIDIA GPU 정상 감지 | PASS | RTX 4070 Laptop GPU 감지 |
| `nvidia-smi` 정상 동작 | PASS | Driver/GPU/실시간 지표 정상 출력 |
| `torch.cuda.is_available() == True` | PASS | `.venv` Python에서 `True` |
| CUDA 지원 PyTorch 사용 | PASS | `torch 2.12.1+cu126`, build CUDA 12.6 |
| `gpu_worker` 종료 코드 0 | PASS | 기본/확장 실행 모두 0 |
| 로그에 `CUDA: AVAILABLE` | PASS | 기본/확장 실행 로그 확인 |
| 실제 연산 `device=cuda` | PASS | 모델 및 encode에 CUDA 사용, 실제 장치 `cuda:0` |
| 승인 모델 정상 load | PASS | `BAAI/bge-small-en-v1.5` load 완료 |
| 정상 Embedding 결과 생성 | PASS | 768개 및 6144개 embedding 생성 |
| Embedding shape 정상 | PASS | `(768, 384)`, `(6144, 384)` |
| `Embedding test completed` | PASS | Worker 로그 확인 |
| CPU fallback 미사용 | PASS | `--allow-cpu` 미사용 |
| 외부 `nvidia-smi`에서 Worker GPU 사용 확인 | PASS | 새 compute PID와 utilization 80% 확인 |
| 실제 GPU memory 사용 확인 | PASS | baseline 대비 305 MiB 증가, NVML free VRAM 305 MiB 감소 |

모든 필수 조건이 충족되어 STEP 1을 PASS로 판정한다.
