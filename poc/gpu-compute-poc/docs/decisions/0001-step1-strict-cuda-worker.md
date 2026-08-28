# ADR-0001: STEP 1은 CUDA를 기본 강제한다

- 상태: Accepted
- 날짜: 2026-08-26

## Context

PoC의 첫 번째 gate는 sentence-transformers Embedding이 실제 NVIDIA GPU에서 실행되는지 확인하는 것이다.
CPU fallback을 자동 적용하면 코드 동작과 핵심 기술 검증 성공을 혼동할 수 있다.

## Decision

- 기본 CLI는 CUDA가 없거나 NVML로 GPU를 읽을 수 없으면 non-zero로 종료한다.
- 모델 생성과 `encode` 양쪽에 명시적으로 `device="cuda"`를 전달한다.
- CUDA 비동기 실행 때문에 실행시간 경계 전후에 `torch.cuda.synchronize()`를 호출한다.
- 개발 편의를 위한 CPU 실행은 명시적 `--allow-cpu`에서만 허용하고 `POC SUCCESS: NO`를 출력한다.
- 모델은 사전 승인 allowlist의 `BAAI/bge-small-en-v1.5`만 허용한다.
- STEP 1 실제 성공에는 Worker 로그의 `device=cuda`와 외부 `nvidia-smi` 사용률 상승 증거가 모두 필요하다.

## Consequences

GPU가 없는 개발 PC에서는 자동 테스트는 가능하지만 STEP 1 완료 판정을 내릴 수 없다. 따라서 이 환경에서는
STEP 2 구현을 시작하지 않는다.

