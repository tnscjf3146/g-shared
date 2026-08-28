# STEP 1 — Standalone CUDA Embedding Worker

## 목표와 성공 gate

승인된 `BAAI/bge-small-en-v1.5` 모델을 `device="cuda"`로 실행한다. 아래 두 조건을 모두
실제 NVIDIA GPU PC에서 확인해야 STEP 1 성공이다.

1. Worker 로그에 `device=cuda`와 `POC SUCCESS: YES`가 출력된다.
2. 별도 터미널의 `nvidia-smi`에서 해당 프로세스와 GPU utilization 상승이 관찰된다.

CPU fallback 결과는 자동 테스트나 개발 확인에만 쓸 수 있고 성공으로 인정하지 않는다.

## 사전 조건

- Python 3.11 이상
- NVIDIA GPU와 최신 호환 드라이버
- `nvidia-smi` 정상 실행
- 최초 모델 다운로드를 위한 인터넷 연결 또는 준비된 Hugging Face cache

`nvcc`는 사전 빌드 PyTorch wheel을 실행할 때 필수 조건이 아니다. CUDA 지원 PyTorch 설치 명령은
GPU PC 드라이버에 맞춰 [PyTorch 공식 selector](https://pytorch.org/get-started/locally/)에서 다시 확인한다.

## 설치

Windows PowerShell 예시(공식 CUDA 12.6 wheel 기본값):

```powershell
cd C:\gpu-compute-poc\worker
.\scripts\install-cuda.ps1 -Python py -TorchVersion 2.12.1 -CudaWheel cu126
```

`py`가 여러 버전을 관리한다면 먼저 Python 3.11+ 가상환경을 직접 만들고 그 Python 경로를
`-Python`에 넘긴다. Linux/WSL은 다음과 같다.

```bash
cd /path/to/gpu-compute-poc/worker
CUDA_WHEEL=cu126 TORCH_VERSION=2.12.1 ./scripts/install-cuda.sh
```

설치 스크립트 마지막의 `cuda_available=True`를 확인한다. False라면 실행하지 말고 드라이버와
PyTorch wheel 조합을 먼저 수정한다.

## 실행

터미널 A에서 200ms 간격으로 GPU를 관찰한다.

```powershell
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv -lms 200
```

또는 전체 utilization을 관찰한다.

```powershell
nvidia-smi dmon -s pucm -d 1
```

터미널 B에서 Worker를 실행한다.

```powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m gpu_worker --repeat 256 --batch-size 64
```

작업이 너무 빨라 utilization을 확인하기 어렵다면 `--repeat 2048` 이상으로 높인다. 테스트 문자열은
공개된 세 문장만 반복하며 임의 사용자 코드는 실행하지 않는다.

## 자동 테스트

외부 ML 패키지나 GPU 없이도 핵심 제어 로직을 검증한다.

```powershell
cd C:\gpu-compute-poc\worker
python -m unittest discover -s tests -v
```

검증 범위:

- NVML GPU 이름/VRAM/utilization 변환과 종료
- 잘못된 GPU index 거부
- CUDA unavailable 거부
- 모델 생성과 encode에 `cuda` 명시
- 실행시간 측정 전후 CUDA synchronize
- CPU에 남은 모델 거부
- allowlist 밖 모델 거부

## 개발용 CPU fallback

```powershell
python -m gpu_worker --allow-cpu --repeat 1
```

정상 실행되어도 종료 코드 3과 `POC SUCCESS: NO`가 의도된 결과다.

