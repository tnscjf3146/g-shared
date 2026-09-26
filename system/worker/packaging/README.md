# G-shared.exe 패키징

GPU 공급자가 자기 Windows PC에서 실행하는 Worker를 단일 실행파일로 만든다.
Python·소스·venv 없이 실행된다.

## 빌드

```powershell
powershell -ExecutionPolicy Bypass -File system\worker\packaging\build.ps1
```

| 단계 | 내용 |
|---|---|
| clean venv | `build\venv`에 `requirements-build.txt`만 설치 — 빌드 PC의 torch·pandas 등이 섞이지 않음 |
| freeze | `G-shared.spec` → `dist\G-shared.exe` + `selfcheck.exe` |
| selfcheck | 번들 안에서 검사, 하나라도 FAIL이면 빌드 실패 |
| 정리 | `selfcheck.exe` 삭제(배포 안 함), exe 크기·SHA-256 출력 |

`build\`, `dist\`는 git-ignore.

## 배포

빌드 산출물(`dist\G-shared.exe`)은 커밋하지 않는다. **검증을 통과한 exe만 `system/G-shared/G-shared.exe`로 복사해 커밋한다** — 이 파일이 배포 기준(source of truth)이다.

| 단계 | 내용 |
|---|---|
| 빌드 | 위 `build.ps1` — selfcheck PASS, 크기·SHA-256 확인 |
| 교체 | `dist\G-shared.exe` → `system/G-shared/G-shared.exe`. 다른 경로에 exe를 중복 커밋하지 않는다 |
| 커밋 | 기능 커밋과 분리해 exe만 담은 커밋으로 (`[sys-chore] … 배포 실행파일 갱신`) |
| 배포 | Frontend Docker 빌드가 이 파일을 `public/downloads/`로 복사 → `https://<도메인>/downloads/G-shared.exe` (`frontend/Dockerfile`, `docker-compose.yml`의 `worker` build context) |
| 확인 | 배포 후 내려받은 파일의 SHA-256이 커밋한 exe와 같은지 |

## 파일

| 파일 | 역할 |
|---|---|
| `G-shared.spec` | PyInstaller 레시피 (onefile). `G-shared.exe`는 콘솔 창 없음, `selfcheck.exe`만 콘솔 |
| `requirements-build.txt` | 번들에 들어갈 것 + PyInstaller, 버전 고정 |
| `selfcheck.py` | 빌드 검증 전용 — 배포 exe에는 없음 |
| `build.ps1` | 위 전 과정 |

## 번들 내용

| 포함 | 제외 |
|---|---|
| Python 3.10, `gshared_worker` 전 모듈, tkinter | torch · transformers · xgboost · pandas · numpy · sklearn |
| websockets, web3 / eth-account | pytest · tests · fixtures · setuptools · pip |
| `blockchain/abi/GPUJobEscrow.json` (원본 그대로) | `.env` · 토큰 · 키 · `worker-config.json` · `worker-wallet.json` |
| 런타임 의존성의 dist-info 메타데이터 | Docker 이미지 · 모델 가중치 · 데이터셋 · git 정보 |

- 학습은 Docker 컨테이너가 한다. exe는 `le3560/gshared-qlora-sft-runtime@sha256:3f054d6d…e4ec`(0.4.0, 입력 Adapter + 학습 결과 검증)을 Job이 올 때 digest로 pull하고, 승인된 digest가 아닌 이미지는 실행하지 않는다. Backend 카탈로그가 0.4.0으로 옮겨질 때까지 0.3.0(`e32bd1c1…e170`)과 0.2.0(`acda63bc…126d`)도 허용한다. 기준값은 `gshared_worker/runtime_image.py`의 `CANONICAL_RUNTIME_*` 상수이며 selfcheck의 `runtime image` 항목이 번들 안에서 확인한다.
- selfcheck의 `chain config` 항목은 번들된 기본 체인 설정(chainId 31221, 에스크로 주소 형식, https RPC)을 확인한다.
- dist-info가 필요한 이유: web3·py_ecc·toolz가 import 중 자기 버전을 `importlib.metadata`로 읽는다. 없으면 `import eth_account`가 번들 안에서 실패해 signing key를 못 만든다.

## 외부 전제 (exe에 없음)

WSL2 · NVIDIA Driver · Docker Engine · NVIDIA Container Toolkit.
없으면 기존 환경 진단이 무엇이 없는지와 해결 방법을 출력하고 종료한다(crash 아님).

## 파일 저장 위치

| 실행 방식 | 설정·토큰·signing key·로그 |
|---|---|
| 소스 실행 | `system/worker/` (기존과 동일) |
| exe, 옆에 `worker-config.json` 또는 `worker-wallet.json` 있음 | exe 폴더 (portable — 기존 identity 유지) |
| exe, 그 외 | `%LOCALAPPDATA%\G-shared\` |

- 단일 실행 lock: `%LOCALAPPDATA%\G-shared\state\` + 사용자별 named mutex.
- 토큰·private key는 DPAPI(Current User)로 봉인. 다른 계정·다른 PC에서는 못 읽는다.
- portable과 LocalAppData 양쪽에 wallet이 있으면 공개 Operator Address가 같을 때만 portable을 사용한다. 다르거나 안전하게 비교할 수 없으면 둘 다 변경하지 않고 저장소 충돌로 중단한다.
- `Program Files`처럼 쓰기 금지 폴더에 설치해도 exe 폴더에 쓰지 않는다.

## Backend 주소

우선순위: `GSHARED_WORKER_BACKEND_URL` → `worker-config.json`의 `backendUrl` → 기본값.

기본값: `https://j15e204.p.ssafy.io` (`config.py`의 `DEFAULT_BACKEND_URL`). 로그인·등록은 `https://j15e204.p.ssafy.io/api/...`, WebSocket은 `wss://j15e204.p.ssafy.io/ws/workers`로 자동 생성된다. `https://`·`wss://`·끝의 `/` 어느 형태로 지정해도 같은 주소가 된다. localhost는 하드코딩되어 있지 않다.

## 최초 실행 (first-run enrollment)

설정 파일도 `GSHARED_WORKER_ID`도 없는 첫 실행:

환경 진단 → 런타임 준비 → 로그인 창 → 로그인 → GPU benchmark → operator key 생성(DPAPI) → `POST /api/workers/register` → workerId·workerToken을 `%LOCALAPPDATA%\G-shared\worker-config.json`에 DPAPI로 저장 → authenticate → WebSocket(REGISTER / REGISTER_ACK / heartbeat)

- 두 번째 실행부터는 저장된 토큰으로 바로 authenticate — 로그인·등록 없음.
- User Access Token은 등록에만 쓰고 저장하지 않는다.
- 등록 전 실행끼리는 `unregistered` identity로 단일 실행을 공유 — 동시에 두 번 등록되지 않는다.
- 로그인 버튼 중복 클릭은 요청 1회로 처리된다.

## 화면과 로그

- 콘솔 창이 없다. 실행하면 **상태 창**이 먼저 뜬다 (`gui/status_window.py`).

| 영역 | 내용 |
|---|---|
| 상태 | 시작 · 환경 확인 · 런타임 준비 · 인증 · 등록 필요 · 연결 중 · 온라인 · 작업 준비/실행 · 종료 중 · 오류 |
| 정보 | GPU(모델·VRAM), Worker(연결 상태, 축약 ID), 현재 작업, 진행률(JOB_PROGRESS가 있을 때) |
| 작업 단계 | 수락 후 학습 전까지 **작업 준비 중** — 실행 정보 확인 → 모델 준비(처음이면 "처음 내려받는 중, 몇 분") → 데이터셋 → 어댑터(있을 때) → 실행 이미지 확인. 이어서 **학습 진행 중**(진행률) → **결과를 올리고 정산하는 중** |
| 로그 | 기존 진행 문구 최근 200줄. 토큰·JWT·키·서명 URL 쿼리는 가림 |
| [종료] / X | 같은 처리. 작업이 없으면 바로 종료, 작업 중이면 "작업을 마치고 결과를 보낸 뒤 종료"를 묻고 새 작업은 받지 않음. 기다리는 중 한 번 더 누르면 "지금 종료"를 다시 확인 |

- Worker는 별도 스레드에서 돈다. Tk는 창 스레드에서만 다루고, Worker는 `status_events` 큐에 값만 넣는다. 로그인 창도 창 스레드에서 연다.
- 종료는 기존 stop 신호(`InterruptibleStop`)를 쓴다. 단계 사이·연결 루프에서 멈추며 강제 kill은 없다. 설치·벤치마크 같은 단계 도중에는 그 단계가 끝난 뒤 멈춘다.
- 실패하면 기존과 같은 오류 문구를 창 위 오류 대화상자로 보여주고, 창은 "오류" 상태로 남는다.
- 터미널에서 `python -m gshared_worker`로 실행하면 창 없이 기존처럼 콘솔에 출력한다.
- `gshared-debug.log` 기본 수준은 WARNING. 정상 진행은 기록하지 않고, 실패·거절·연결 오류·예외와 실패한 실행의 종료 코드·빌드·설정 요약만 남긴다.
- 토큰·비밀번호·private key·서명 URL은 기록하지 않는다.

## 알려진 제약

- 작업 중 "지금 종료"를 고르면 학습 컨테이너(`docker run --rm`, 이 프로세스에 붙어 실행)가 정리되지 않고 남을 수 있다. Worker가 스스로 작업을 반환하는 계약은 없으므로 기본 종료는 작업 완료 후 종료다.

- pynvml은 선언된 의존성이 아니라 번들되지 않는다. GPU 조회는 드라이버에 포함된 `nvidia-smi`로 한다.
