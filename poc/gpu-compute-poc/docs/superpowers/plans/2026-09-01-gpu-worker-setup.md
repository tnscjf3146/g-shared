# GPU Worker Setup Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one `GPUWorkerSetup.exe` that securely acquires credentials, prepares Windows/WSL2/Docker, installs or updates the existing Worker onedir payload, verifies Benchmark and REGISTER/ONLINE/IDLE, and supports safe resume, AutoStart, rollback, and future signed updates.

**Architecture:** A non-elevated Tkinter Setup orchestrator owns UI, state, remediation, installation, and verification. Existing Worker modules remain authoritative for NVML, Docker GPU, WorkloadCatalog, BenchmarkCache, benchmark execution, and WebSocket REGISTER; only protected credential reading, single-instance, secure typed local control, and a read-only probe are added. The installer consumes an `UpdateSource`, validates a versioned SHA-256 manifest through embedded/staging/installed phases, and commits only after Backend and local benchmark evidence pass.

**Tech Stack:** Python 3.11+, Tkinter, `ctypes` Windows APIs, `websockets==17.1`, standard-library `urllib`, `unittest`, PyInstaller 6.22.2, PowerShell release scripts, existing Spring Boot Backend contracts.

**Spec:** `docs/superpowers/specs/2026-09-01-gpu-worker-setup-design.md`

## Global Constraints

- Target Windows 10/11 x64; supplier machines require no Python, venv, or source checkout.
- Setup is PyInstaller onefile named `GPUWorkerSetup.exe`; Worker remains PyInstaller onedir.
- Install binary/runtime only under `%LOCALAPPDATA%\GPUCompute\Worker` and never run Worker from PyInstaller `_MEI*` extraction.
- UI exposes only Backend URL, Worker ID, and masked Worker Token.
- Defaults are `DEFAULT_HOURLY_PRICE = 600`, `DEFAULT_GPU_INDEX = 0`, and existing `agent_config.DEFAULT_SUPPORTED_WORKLOADS`.
- New Setup writes only DPAPI Current User protected Token; legacy plaintext `workerToken` is read fallback only when no protected field exists.
- Token must never enter CLI, environment, elevated helper, state, Run/RunOnce, logs, or Backend response diagnostics.
- Existing `/ws/workers`, REGISTER protocol, `/api/workers/{workerId}`, workload allowlist, Docker hardening, and Benchmark rules do not change.
- `SETUP_COMPLETED` requires Backend Worker exists, ONLINE, IDLE, current positive Benchmark, plus local BenchmarkCache GPU UUID/version validity.
- Worker payload manifest covers every binary/runtime file with SHA-256; mutable allowlist is exactly `worker-config.json` and `benchmark-result.json`.
- Reject absolute, UNC, device, drive-changing, parent-traversal, root-escaping, symlink, junction, and reparse payload paths.
- BUSY Worker update is forbidden; locked update stops with no partial overwrite; backup remains until new REGISTER/IDLE succeeds.
- Arbitrary downgrade is blocked; journal-authenticated rollback to the exact previous manifest is allowed.
- Elevated helper accepts fixed enum actions only and never handles credentials, Worker execution, Benchmark, or arbitrary PowerShell.
- Docker install requires an initially unchecked explicit license checkbox; only then pass `--accept-license` with official `--user --backend=wsl-2` options.
- Production has no TLS verification disable option.
- Preserve the user's existing dirty `worker/worker-config.example.json`; do not edit, stage, print, or commit it.
- Existing Worker `88 tests + 22 subtests` and ffi packaging tests must continue to pass; new tests are additive.

---

## File Map

### Create

- `setup/pyproject.toml` — Setup package and build dependencies.
- `setup/GPUWorkerSetup.spec` — onefile Setup with embedded verified Worker payload.
- `setup/gpu_worker_setup/__init__.py` — Setup version.
- `setup/gpu_worker_setup/app.py` — mode dispatch for UI, resume, AutoStart, helper, and verify-package.
- `setup/gpu_worker_setup/defaults.py` — price, GPU, timeout, registry-name defaults.
- `setup/gpu_worker_setup/issues.py` — `SetupIssue`, severity, retryability, stable error codes.
- `setup/gpu_worker_setup/endpoint_config.py` — structured base URL to explicit WSS/REST endpoints.
- `setup/gpu_worker_setup/version_policy.py` — strict version parsing, policy decisions, downgrade and rollback rules.
- `setup/gpu_worker_setup/layout.py` — all LocalAppData binary/mutable paths.
- `setup/gpu_worker_setup/state_store.py` — atomic setup state and update journal persistence.
- `setup/gpu_worker_setup/secure_logging.py` — allowlisted rotating structured logs and secret redaction.
- `setup/gpu_worker_setup/credentials.py` — provider protocol, manual provider model, protected config writer.
- `setup/gpu_worker_setup/backend.py` — WSS preflight, REST status client, registration verifier.
- `setup/gpu_worker_setup/platform_api.py` — typed command, mutex, registry, process, signature, and filesystem protocols.
- `setup/gpu_worker_setup/windows_platform.py` — real Windows implementations.
- `setup/gpu_worker_setup/system_setup.py` — WSL/system probe and remediation decisions.
- `setup/gpu_worker_setup/docker_setup.py` — consent-gated per-user Docker install and startup wait.
- `setup/gpu_worker_setup/elevated_helper.py` — fixed administrator action allowlist.
- `setup/gpu_worker_setup/payload.py` — manifest models, canonical path/reparse/hash validation, `UpdateSource`.
- `setup/gpu_worker_setup/transaction.py` — install/update journal, staging swap, lock handling, rollback.
- `setup/gpu_worker_setup/worker_control.py` — installed Worker probe, process launch, secure local IPC client.
- `setup/gpu_worker_setup/autostart.py` — owned HKCU Run/RunOnce and update-aware AutoStart.
- `setup/gpu_worker_setup/orchestrator.py` — idempotent state machine.
- `setup/gpu_worker_setup/ui.py` — Tkinter wizard and completion settings.
- `setup/tests/` test modules matching each Setup responsibility.
- `worker/gpu_worker/protected_credentials.py` — shared DPAPI Current User protect/unprotect functions.
- `worker/gpu_worker/instance_guard.py` — Worker current-user/worker/GPU single-instance mutex.
- `worker/gpu_worker/local_control.py` — Current User ACL Named Pipe `STATUS` and `GRACEFUL_SHUTDOWN`.
- `worker/gpu_worker/setup_probe.py` — read-only authoritative environment/cache probe.
- `worker/gpu_worker/exit_codes.py` — stable process results consumed by Setup.
- `worker/tests/test_protected_credentials.py`
- `worker/tests/test_instance_guard.py`
- `worker/tests/test_local_control.py`
- `worker/tests/test_setup_probe.py`
- `scripts/build-release.ps1`
- `scripts/verify-worker.ps1`
- `scripts/generate-worker-manifest.ps1`
- `scripts/verify-setup.ps1`
- `scripts/packaged-smoke.ps1`
- `docs/gpu-worker-setup-validation.md`

### Modify

- `worker/gpu_worker/agent_config.py` — accept/decrypt `workerTokenProtected` without unsafe fallback.
- `worker/gpu_worker/client.py` — instance guard, probe mode, local control lifecycle, mismatch exit mapping.
- `worker/gpu_worker/transport.py` — graceful shutdown awaitable and typed REGISTER mismatch propagation.
- `worker/gpu_worker/errors.py` — protected credential, duplicate instance, and credential mismatch errors.
- `worker/gpu_worker/job_manager.py` — expose lifecycle-safe STARTING/IDLE/BUSY status to local control.
- `worker/gpu-worker-agent.spec` — retain `sys.base_prefix` ffi collection and include new Windows modules.
- `worker/scripts/build-agent.ps1` — callable deterministic worker build used by root release script.
- `worker/tests/test_agent_config.py`
- `worker/tests/test_client.py`
- `worker/tests/test_transport.py`
- `worker/tests/test_packaging.py` — retain present/missing ffi tests and assert packaged path policy.
- `worker/README.md`
- `.gitignore` — generated Setup/build/release payload directories only.
- `README.md` — supplier Setup entrypoint and validation document link.

### Do not modify

- Backend Java sources and configuration.
- Existing Docker workload catalog, benchmark algorithm, cache validity, REGISTER schema, and security flags.
- `worker/worker-config.example.json` because it contains pre-existing user changes.

---

### Task 1: Setup foundations, endpoint configuration, and version policy

**Files:**
- Create: `setup/pyproject.toml`
- Create: `setup/gpu_worker_setup/__init__.py`
- Create: `setup/gpu_worker_setup/defaults.py`
- Create: `setup/gpu_worker_setup/issues.py`
- Create: `setup/gpu_worker_setup/endpoint_config.py`
- Create: `setup/gpu_worker_setup/version_policy.py`
- Test: `setup/tests/test_endpoint_config.py`
- Test: `setup/tests/test_version_policy.py`
- Test: `setup/tests/test_issues.py`

**Interfaces:**
- Produces: `SetupIssue(stage, code, severity, retryability, user_message, diagnostic_id)`.
- Produces: `BackendEndpointConfig.from_base_url(value, profile)`, `.worker_websocket_url`, `.worker_status_url(worker_id)`.
- Produces: `WorkerVersion.parse(value)` and `LocalVersionPolicy.evaluate(current, candidate, metadata, rollback_context=None)`.

- [ ] **Step 1: Write failing foundation tests**

```python
def test_production_endpoint_builds_explicit_wss_and_rest_urls():
    config = BackendEndpointConfig.from_base_url("https://api.example.com/gpu")
    assert config.worker_websocket_url == "wss://api.example.com/gpu/ws/workers"
    assert config.worker_status_url("worker a") == "https://api.example.com/gpu/api/workers/worker%20a"

def test_arbitrary_downgrade_is_blocked_but_exact_journal_rollback_is_allowed():
    policy = LocalVersionPolicy()
    assert policy.evaluate(v("1.2.0"), v("1.1.0"), VersionMetadata()).reason_code == "WORKER_DOWNGRADE_BLOCKED"
    rollback = RollbackContext(previous_version=v("1.1.0"), previous_manifest_sha256="a" * 64)
    assert policy.evaluate(v("1.2.0"), v("1.1.0"), VersionMetadata(), rollback).allowed
```

- [ ] **Step 2: Run tests and confirm import failures**

Run:

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_issues tests.test_endpoint_config tests.test_version_policy -v
Pop-Location
```

Expected: FAIL because `gpu_worker_setup` foundation modules do not exist.

- [ ] **Step 3: Implement exact foundation models**

Use `Enum`/frozen dataclasses. `WorkerVersion` accepts only `MAJOR.MINOR.PATCH` with optional prerelease/build metadata and compares numeric core plus SemVer prerelease ordering. `VersionMetadata` fields are optional; absent Backend policy must not invent minimum/recommended/latest. `RollbackContext` must include the exact previous version and 64-hex manifest hash. Endpoint parsing rejects credentials, query, fragment, non-production schemes, and path traversal; the development profile is a separate constructor used only by tests. `setup/pyproject.toml` declares Python `>=3.11`, runtime dependency `websockets==17.1`, and build dependency `pyinstaller==6.22.2`; Tkinter and REST remain standard-library dependencies.

- [ ] **Step 4: Run foundation tests**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/pyproject.toml setup/gpu_worker_setup setup/tests/test_issues.py setup/tests/test_endpoint_config.py setup/tests/test_version_policy.py
git commit -m "feat: add Setup domain and version policy"
```

### Task 2: Layout, atomic state, secure logs, and Setup single-instance

**Files:**
- Create: `setup/gpu_worker_setup/layout.py`
- Create: `setup/gpu_worker_setup/state_store.py`
- Create: `setup/gpu_worker_setup/secure_logging.py`
- Create: `setup/gpu_worker_setup/platform_api.py`
- Create: `setup/gpu_worker_setup/windows_platform.py`
- Test: `setup/tests/test_layout.py`
- Test: `setup/tests/test_state_store.py`
- Test: `setup/tests/test_secure_logging.py`
- Test: `setup/tests/test_setup_lock.py`

**Interfaces:**
- Produces: `InstallLayout.for_local_app_data(root)` with `worker_dir`, `stable_setup_exe`, `state_dir`, `logs_dir`.
- Produces: `SetupStateStore.load/save`, `UpdateJournalStore.load/save/clear`, all temp-plus-replace.
- Produces: `SecureEventLogger.event(name, **allowlisted_fields)`.
- Produces: `SetupInstanceLock.acquire() -> bool` combining SID-scoped named mutex and share-none file handle.

- [ ] **Step 1: Write failing state/security tests**

```python
def test_second_setup_lock_fails_without_touching_state():
    first = SetupInstanceLock(fake_windows, layout.setup_lock_path)
    second = SetupInstanceLock(fake_windows, layout.setup_lock_path)
    assert first.acquire()
    assert not second.acquire()

def test_logger_never_writes_token_or_unapproved_fields():
    logger.event("credential_check", status="FAILED", errorCode="WORKER_TOKEN_REJECTED",
                 token="secret-value", responseBody="Bearer secret-value")
    text = log_path.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert "responseBody" not in text
```

- [ ] **Step 2: Run the focused tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_layout tests.test_state_store tests.test_secure_logging tests.test_setup_lock -v
Pop-Location
```

Expected: FAIL for missing stores and locks.

- [ ] **Step 3: Implement atomic persistence and Windows locks**

Persist only schema version, completed stages, versions, last safe error, warnings, consent boolean/timestamp, and AutoStart preference. Use `CreateMutexW` with the opaque SID hash plus a share-none `CreateFileW` lock under state. Detect `ERROR_ALREADY_EXISTS` and return `SETUP_ALREADY_RUNNING` before any orchestrator mutation. Logger field allowlist is fixed in code and rotates by configured byte size.

- [ ] **Step 4: Run focused and complete Setup tests**

Run Step 2, then:

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/layout.py setup/gpu_worker_setup/state_store.py setup/gpu_worker_setup/secure_logging.py setup/gpu_worker_setup/platform_api.py setup/gpu_worker_setup/windows_platform.py setup/tests/test_layout.py setup/tests/test_state_store.py setup/tests/test_secure_logging.py setup/tests/test_setup_lock.py
git commit -m "feat: add atomic Setup state and instance lock"
```

### Task 3: DPAPI credentials and Worker config compatibility

**Files:**
- Create: `worker/gpu_worker/protected_credentials.py`
- Create: `worker/tests/test_protected_credentials.py`
- Create: `setup/gpu_worker_setup/credentials.py`
- Create: `setup/tests/test_credentials.py`
- Modify: `worker/gpu_worker/agent_config.py`
- Modify: `worker/tests/test_agent_config.py`
- Modify: `worker/tests/test_client.py`

**Interfaces:**
- Produces: `protect_current_user(secret: str) -> str` and `unprotect_current_user(encoded: str) -> str` using `CryptProtectData`/`CryptUnprotectData`.
- Produces: `CredentialProvider` protocol and `ManualCredentialProvider`.
- Produces: `WorkerConfigWriter.write(credentials, endpoints, defaults)` with atomic replacement and no plaintext token.

- [ ] **Step 1: Add failing Worker and Setup credential tests**

```python
def test_new_config_contains_only_protected_token():
    writer.write(credentials)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert "workerToken" not in payload
    assert payload["workerTokenProtected"] != credentials.worker_token
    assert protector.unprotect(payload["workerTokenProtected"]) == credentials.worker_token

def test_broken_protected_token_never_falls_back_to_legacy_plaintext():
    write_config({"workerTokenProtected": "broken", "workerToken": "legacy-secret", **required})
    with self.assertRaisesRegex(WorkerConfigurationError, "protected credential"):
        resolve_settings(args(), env={}, directory=directory, token_unprotector=failing_unprotector)
```

- [ ] **Step 2: Verify credential tests fail**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest tests.test_protected_credentials tests.test_agent_config tests.test_client -v
Pop-Location
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_credentials -v
Pop-Location
```

Expected: FAIL because protected credentials are unsupported.

- [ ] **Step 3: Implement DPAPI and precedence**

Use zeroed `DATA_BLOB` buffers and `CRYPTPROTECT_UI_FORBIDDEN`; base64 is storage encoding only. Token precedence remains existing environment override, then protected config, then legacy plaintext only if the protected key is absent. A protected-key decryption error raises `CREDENTIAL_DECRYPTION_FAILED`; it never creates plaintext fallback. Config writer obtains workloads from `DEFAULT_SUPPORTED_WORKLOADS`, not a Setup-owned list.

- [ ] **Step 4: Run credential tests and full Worker regression**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
Pop-Location
```

Expected: all existing 88 tests and 22 subtests plus new tests PASS.

- [ ] **Step 5: Commit without the dirty example config**

```powershell
git add worker/gpu_worker/protected_credentials.py worker/gpu_worker/agent_config.py worker/tests/test_protected_credentials.py worker/tests/test_agent_config.py worker/tests/test_client.py setup/gpu_worker_setup/credentials.py setup/tests/test_credentials.py
git commit -m "feat: protect Worker credentials with DPAPI"
```

### Task 4: Explicit Backend clients and REGISTER verification

**Files:**
- Create: `setup/gpu_worker_setup/backend.py`
- Create: `setup/tests/test_backend_preflight.py`
- Create: `setup/tests/test_registration_verifier.py`
- Create: `setup/tests/tls_server.py`

**Interfaces:**
- Consumes: `BackendEndpointConfig`, in-memory `CredentialInput`, `Clock`, `BackoffPolicy`.
- Produces: `BackendPreflightClient.validate() -> PreflightResult`.
- Produces: `WorkerStatusSnapshot.from_payload()` with exact safe fields only, including `lastHeartbeat`.
- Produces: `RegistrationVerifier.wait_until_verified(worker_id, benchmark_evidence, process) -> RegistrationEvidence`.

- [ ] **Step 1: Write failing fake transport and local TLS tests**

```python
async def test_verified_requires_online_idle_current_positive_benchmark_and_local_uuid():
    status = WorkerStatusSnapshot(worker_id="w1", connection_status="ONLINE", worker_status="IDLE",
                                  benchmark_version=BENCHMARK_VERSION, benchmark_score=1.0,
                                  last_heartbeat=launch_time + timedelta(seconds=1))
    local = BenchmarkEvidence(gpu_uuid="GPU-1", benchmark_version=BENCHMARK_VERSION, benchmark_score=1.0)
    assert verifier.accepts(status, local)  # verifier built with RegistrationBaseline

async def test_null_benchmark_never_completes_setup():
    assert not verifier.accepts(replace(status, benchmark_version=None, benchmark_score=None), local)
```

Also assert handshake 401/403 stops preflight, certificate failure is not bypassed, status auth `NONE` sends no Token, `BEARER` sends it only in the header, and logger output contains neither header nor response body.

- [ ] **Step 2: Run Backend client tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_backend_preflight tests.test_registration_verifier -v
Pop-Location
```

Expected: FAIL for missing clients.

- [ ] **Step 3: Implement transport boundaries**

Use `websockets.asyncio.client.connect` for WSS and `urllib.request` inside `asyncio.to_thread` for REST. Parse only workerId, connectionStatus, workerStatus, benchmark.version, numeric benchmark.score, and lastHeartbeat. Keep raw response local to the parser and never pass it to logger/errors. Decide heartbeat freshness entirely inside the Backend's clock domain: capture the Worker's existing `lastHeartbeat` as a baseline before launch, then require the polled `lastHeartbeat` to advance past it, so a stale ONLINE record cannot complete Setup. A Worker absent at baseline is a new registration and compares no timestamps; an unknown baseline adopts the first observed heartbeat and still requires a later one. Never compare `lastHeartbeat` against the Setup PC's wall clock, whose skew would otherwise block a genuine registration forever. The verifier checks the local cache evidence because the current Backend contract has no GPU UUID or session/version identity.

- [ ] **Step 4: Run Backend tests**

Run Step 2. Expected: PASS, including local trusted test-CA TLS server and untrusted certificate rejection.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/backend.py setup/tests/test_backend_preflight.py setup/tests/test_registration_verifier.py setup/tests/tls_server.py
git commit -m "feat: verify Worker registration through explicit endpoints"
```

### Task 5: Worker single-instance and secure local control

**Files:**
- Create: `worker/gpu_worker/exit_codes.py`
- Create: `worker/gpu_worker/instance_guard.py`
- Create: `worker/gpu_worker/local_control.py`
- Create: `worker/tests/test_instance_guard.py`
- Create: `worker/tests/test_local_control.py`
- Modify: `worker/gpu_worker/errors.py`
- Modify: `worker/gpu_worker/job_manager.py`
- Modify: `worker/tests/test_job_manager.py`

**Interfaces:**
- Produces: `WorkerExitCode.OK=0`, `EXPECTED_FAILURE=2`, `TRANSPORT_FAILURE=3`, `CREDENTIAL_MISMATCH=4`, `ALREADY_RUNNING=5`.
- Produces: `WorkerInstanceGuard.acquire(worker_id, gpu_index) -> bool` using SID-scoped opaque named mutex.
- Produces: `LocalControlServer(status_provider, shutdown_event)` and `LocalControlClient.status()/graceful_shutdown()`.

- [ ] **Step 1: Write failing concurrency and IPC tests**

```python
def test_second_worker_with_same_user_id_and_gpu_is_rejected():
    with WorkerInstanceGuard("worker-1", 0, api=fake_api) as first:
        assert first.acquired
        with WorkerInstanceGuard("worker-1", 0, api=fake_api) as second:
            assert not second.acquired

def test_pipe_rejects_unknown_command_and_other_sid():
    assert server.handle(Request(command="RUN", peer_sid=current_sid)).code == "COMMAND_NOT_ALLOWED"
    assert server.handle(Request(command="STATUS", peer_sid=other_sid)).code == "ACCESS_DENIED"
```

Cover exact `STATUS`/`GRACEFUL_SHUTDOWN`, 4 KiB request limit, no path/shell fields, current-user DACL SDDL, remote pipe clients disabled, and no raw Worker ID/Token in pipe/mutex names.

- [ ] **Step 2: Run Worker control tests**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest tests.test_instance_guard tests.test_local_control tests.test_job_manager -v
Pop-Location
```

Expected: FAIL for missing guards and server.

- [ ] **Step 3: Implement Windows Named Pipe and lifecycle status**

Create pipe security descriptor granting `GENERIC_READ|GENERIC_WRITE` only to the current SID, verify the connecting client's SID using impersonation/token APIs, reject remote clients, decode a fixed JSON object containing only `command`, and cap reads. Extend local lifecycle status to `STARTING/IDLE/BUSY/OFFLINE`; REGISTER still receives only IDLE/BUSY.

- [ ] **Step 4: Run control tests and Worker regression**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add worker/gpu_worker/exit_codes.py worker/gpu_worker/instance_guard.py worker/gpu_worker/local_control.py worker/gpu_worker/errors.py worker/gpu_worker/job_manager.py worker/tests/test_instance_guard.py worker/tests/test_local_control.py worker/tests/test_job_manager.py
git commit -m "feat: prevent duplicate Worker instances"
```

### Task 6: Worker read-only probe and graceful shutdown integration

**Files:**
- Create: `worker/gpu_worker/setup_probe.py`
- Create: `worker/tests/test_setup_probe.py`
- Modify: `worker/gpu_worker/client.py`
- Modify: `worker/gpu_worker/transport.py`
- Modify: `worker/tests/test_client.py`
- Modify: `worker/tests/test_transport.py`

**Interfaces:**
- Produces: `run_setup_probe(directory, gpu_index) -> SetupProbeReport` with environment code, GPU UUID, benchmark version/score/cache-valid fields.
- Consumes: `LocalControlServer.shutdown_event` in `run_connection(..., shutdown_waiter=...)`.
- Produces: dedicated credential mismatch exit code when existing Backend closes REGISTER with policy reason `authenticated workerId mismatch`.

- [ ] **Step 1: Write failing probe/shutdown tests**

```python
async def test_shutdown_waiter_closes_connection_without_reconnect():
    stop = asyncio.Event()
    task = asyncio.create_task(run_connection(config, source, connector=connector,
                                               shutdown_waiter=stop.wait))
    stop.set()
    await task
    assert connector.calls == 1

def test_setup_probe_uses_existing_environment_and_cache_rules():
    report = run_setup_probe(...)
    assert report.environment_status == "READY"
    assert report.cache_valid is True
    assert report.gpu_uuid == "GPU-test"
```

- [ ] **Step 2: Run focused Worker tests**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest tests.test_setup_probe tests.test_transport tests.test_client -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Integrate without duplicating authoritative logic**

`--setup-probe` calls existing `NvmlGpuStatusProvider`, `EnvironmentManager`, `DockerManager`, and `BenchmarkCache.load`; it never installs, benchmarks, registers, or accepts commands. Normal `main()` acquires the instance guard before Docker/WSS, starts the control server, sets IDLE before REGISTER, and uses the same existing run path. Graceful shutdown closes the WebSocket context and returns cleanly.

- [ ] **Step 4: Run full Worker suite and subtest count check**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
Pop-Location
```

Expected: original 88 tests and 22 subtests plus all additive tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add worker/gpu_worker/setup_probe.py worker/gpu_worker/client.py worker/gpu_worker/transport.py worker/tests/test_setup_probe.py worker/tests/test_client.py worker/tests/test_transport.py
git commit -m "feat: expose safe Worker setup probe and shutdown"
```

### Task 7: Windows system probe, elevated helper, and Docker preparation

**Files:**
- Create: `setup/gpu_worker_setup/system_setup.py`
- Create: `setup/gpu_worker_setup/elevated_helper.py`
- Create: `setup/gpu_worker_setup/docker_setup.py`
- Create: `setup/tests/test_system_setup.py`
- Create: `setup/tests/test_elevated_helper.py`
- Create: `setup/tests/test_docker_setup.py`
- Modify: `setup/gpu_worker_setup/platform_api.py`
- Modify: `setup/gpu_worker_setup/windows_platform.py`

**Interfaces:**
- Produces: typed `SystemSnapshot`, `SystemAction`, `ElevatedAction` and `DockerSnapshot`.
- Produces: `ElevatedHelper.run(action, correlation_id)` with three exact actions.
- Produces: `DockerDesktopInstaller.install(consent, release)` and `DockerStartupWaiter.wait_ready()`.

- [ ] **Step 1: Write failing remediation tests**

```python
def test_no_license_consent_invokes_nothing():
    result = installer.install(consent=False, release=release)
    assert result.issue.code == "DOCKER_LICENSE_NOT_ACCEPTED"
    assert fake_runner.calls == []

def test_consented_install_uses_exact_official_arguments():
    installer.install(consent=True, release=release)
    assert fake_runner.calls[-1].args == [installer_path, "install", "--user", "--accept-license", "--backend=wsl-2"]
```

Also test Authenticode invalid blocks execution, UAC denied preserves state, only allowlisted helper actions map to fixed executable/arguments, reboot is never automatic, Docker absent is permanent for AutoStart, and starting engine follows fake-clock backoff then READY/timeout.

- [ ] **Step 2: Run remediation tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_system_setup tests.test_elevated_helper tests.test_docker_setup -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement typed Windows operations**

Use fixed executable arrays for `wsl.exe`/feature operations. The helper parser accepts exactly `--elevated-helper <enum> --correlation-id <uuid>` and rejects all other arguments. Docker download uses official HTTPS, size/output bounds, atomic destination, `WinVerifyTrust` plus expected publisher validation, then non-elevated argument-list execution. `docker desktop start --detach --timeout` is capability-checked; fallback starts only the verified installed Docker Desktop executable and polls `docker version`.

- [ ] **Step 4: Run remediation and all Setup tests**

Run Step 2, then Setup discovery. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/system_setup.py setup/gpu_worker_setup/elevated_helper.py setup/gpu_worker_setup/docker_setup.py setup/gpu_worker_setup/platform_api.py setup/gpu_worker_setup/windows_platform.py setup/tests/test_system_setup.py setup/tests/test_elevated_helper.py setup/tests/test_docker_setup.py
git commit -m "feat: prepare WSL and Docker through safe remediation"
```

### Task 8: Manifest, UpdateSource, and three-stage payload verification

**Files:**
- Create: `setup/gpu_worker_setup/payload.py`
- Create: `setup/tests/test_payload.py`
- Create: `setup/tests/test_payload_attacks.py`

**Interfaces:**
- Produces: `WorkerManifest.load(path)`, `PayloadVerifier.verify(root, manifest)`, `EmbeddedPayloadUpdateSource.open_release()`.
- Produces: `UpdateSource` protocol returning `WorkerRelease(version, manifest, payload_root, release_metadata)`.

- [ ] **Step 1: Write failing manifest and attack tests**

Generate temporary trees for valid payload, missing/hash mismatch, normalized duplicate, `../`, rooted path, drive change, UNC, device path, symlink, junction, and reparse ancestors. Assert each invalid release returns the exact manifest/path/hash issue and never calls the activation callback.

- [ ] **Step 2: Run payload tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_payload tests.test_payload_attacks -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement source-independent verification**

Normalize with `PureWindowsPath`, reject anchor/drive/UNC/device/parent components before joining, compute canonical case-insensitive containment, inspect every ancestor with Windows reparse attributes, then verify exact file set, size, and SHA-256. `EmbeddedPayloadUpdateSource` identifies PyInstaller `_MEIPASS` only as a read source; it never returns it as an executable location. Keep remote download absent; the protocol is the approved extension point.

- [ ] **Step 4: Run payload tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/payload.py setup/tests/test_payload.py setup/tests/test_payload_attacks.py
git commit -m "feat: verify embedded Worker payloads"
```

### Task 9: Transactional install, update locks, crash recovery, and rollback

**Files:**
- Create: `setup/gpu_worker_setup/transaction.py`
- Create: `setup/gpu_worker_setup/worker_control.py`
- Create: `setup/tests/test_transaction_install.py`
- Create: `setup/tests/test_transaction_update.py`
- Create: `setup/tests/test_transaction_recovery.py`
- Create: `setup/tests/test_update_autostart_race.py`
- Create: `setup/tests/test_worker_control.py`
- Modify: `setup/gpu_worker_setup/state_store.py`

**Interfaces:**
- Produces: `WorkerInstaller.install_or_update(release, mutable_files, verifier)`.
- Produces: `InstallTransaction.recover()` using exact journal phases.
- Consumes: `VersionPolicy`, exact PID/path inspector, local control shutdown, operation lock, registration verification callback.

- [ ] **Step 1: Write failing transaction tests**

Cover fresh install, same-version valid SKIP, same-version corrupt repair, arbitrary downgrade block, BUSY refusal, exact PID/path, PID reuse, graceful stop, locked DLL `WORKER_UPDATE_LOCKED`, exact mutable allowlist, no partial overwrite, and failure rollback. Inject a crash after every journal write and assert deterministic recovery from hashes plus journal rather than timestamps. `test_worker_control.py` proves that an exact-path existing Worker is reused and a same-name different-path process is never stopped or treated as installed.

- [ ] **Step 2: Run transaction tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_worker_control tests.test_transaction_install tests.test_transaction_update tests.test_transaction_recovery tests.test_update_autostart_race -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement the journaled directory swap**

Hold `install-operation.lock`; copy to a private staging root; verify; preserve only exact mutable names; request shutdown only after Backend IDLE and canonical PID/path match; wait for exit; rename-probe the directory for residual locks; swap whole directories; reverify installed hashes; keep backup through the supplied REGISTER verifier. Rollback permission contains the journal's transaction ID, exact previous version, manifest hash, and backup path. Never recursively delete an unresolved path.

- [ ] **Step 4: Run transaction tests and Setup suite**

Run Step 2 and Setup discovery. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/transaction.py setup/gpu_worker_setup/state_store.py setup/gpu_worker_setup/worker_control.py setup/tests/test_worker_control.py setup/tests/test_transaction_install.py setup/tests/test_transaction_update.py setup/tests/test_transaction_recovery.py setup/tests/test_update_autostart_race.py
git commit -m "feat: install Worker with transactional rollback"
```

### Task 10: Worker launcher, AutoStart, and reboot resume

**Files:**
- Modify: `setup/gpu_worker_setup/worker_control.py`
- Create: `setup/gpu_worker_setup/autostart.py`
- Create: `setup/tests/test_autostart.py`
- Create: `setup/tests/test_resume.py`

**Interfaces:**
- Produces: `WorkerLauncher.ensure_running()` returning existing or newly started exact-path process.
- Produces: `AutoStartManager.apply(enabled)` and `run_autostart_worker()`.
- Produces: `ResumeManager.register_resume()` with only quoted stable Setup path and `--resume`.

- [ ] **Step 1: Write failing launcher/registry tests**

Assert duplicate process races collapse through Worker mutex, active update journal yields `AUTO_START_DEFERRED_UPDATE`, only owned HKCU Run value is removed, RunOnce quoting survives spaces, and no registry value contains credentials.

- [ ] **Step 2: Run launcher tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_autostart tests.test_resume -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement non-elevated launch modes**

AutoStart acquires a bounded operation start lease, exits when journal remains active, starts/waits for Docker only when installed, and launches the installed Worker with no secret arguments. Resume copies the stable Setup before RunOnce registration and uses Windows command-line quoting. AutoStart failure becomes a warning only.

- [ ] **Step 4: Run launcher tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/worker_control.py setup/gpu_worker_setup/autostart.py setup/tests/test_autostart.py setup/tests/test_resume.py
git commit -m "feat: resume and autostart installed Worker safely"
```

### Task 11: Idempotent Setup orchestrator

**Files:**
- Create: `setup/gpu_worker_setup/orchestrator.py`
- Create: `setup/tests/test_orchestrator.py`
- Create: `setup/tests/test_orchestrator_reentry.py`
- Modify: `setup/gpu_worker_setup/issues.py`

**Interfaces:**
- Consumes all prior typed interfaces.
- Produces: `SetupOrchestrator.run(credentials=None, docker_consent=False, resume=False) -> SetupOutcome`.
- Produces state changes only after each observed success; `SETUP_COMPLETED` only after `RegistrationEvidence`.

- [ ] **Step 1: Write failing table-driven state tests**

Cover happy path, handshake failure before remediation, WSL ready SKIP after re-probe, Docker ready SKIP, reboot required, license refusal, benchmark cache hit/miss, process exit, REGISTER timeout, Backend unavailable, benchmark registration failure, mismatch token clearing, re-entry after corrected credential, and AutoStart warning after successful registration.

- [ ] **Step 2: Run orchestrator tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_orchestrator tests.test_orchestrator_reentry -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement the approved state sequence**

Sequence: lock, load hint, actual probes, credentials/preflight, system/WSL/Docker preparation, embedded release install/update, installed Worker probe, launch, local cache evidence, Backend poll, state commit, then AutoStart preference. A mismatch atomically clears protected Token and completion states while preserving URL/ID. A saved READY never bypasses a real probe.

- [ ] **Step 4: Run all Setup tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/orchestrator.py setup/gpu_worker_setup/issues.py setup/tests/test_orchestrator.py setup/tests/test_orchestrator_reentry.py
git commit -m "feat: orchestrate resumable GPU Worker setup"
```

### Task 12: Tkinter UI and application mode dispatch

**Files:**
- Create: `setup/gpu_worker_setup/ui.py`
- Create: `setup/gpu_worker_setup/app.py`
- Create: `setup/tests/test_ui_model.py`
- Create: `setup/tests/test_app_modes.py`
- Modify: `setup/gpu_worker_setup/__init__.py`

**Interfaces:**
- Produces: `main(argv=None) -> int` with normal UI, `--resume`, `--autostart-worker`, `--elevated-helper`, and read-only `--verify-package` modes.
- Produces: UI view-model events; tests do not require an interactive desktop.

- [ ] **Step 1: Write failing UI/model tests**

Assert only three credential fields, Token `show` masking, Docker license checkbox hidden when installed and unchecked when shown, mismatch returns with blank Token, retry stays on failed stage, completion AutoStart checkbox defaults true, and helper/AutoStart/verify modes do not create the normal Tk wizard.

- [ ] **Step 2: Run UI tests**

```powershell
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_ui_model tests.test_app_modes -v
Pop-Location
```

Expected: FAIL.

- [ ] **Step 3: Implement UI without putting secrets in callbacks/logs**

Copy Token from `StringVar` only into the credential value object, immediately clear it after submission, and never include it in event payloads. Keep long operations off the Tk event loop through a bounded worker thread and marshal typed progress events to the UI queue. Provide license link, friendly issue messages/codes, retry, resume, completion AutoStart toggle, and log-folder button.

- [ ] **Step 4: Run all Setup tests**

Run Setup discovery. Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add setup/gpu_worker_setup/ui.py setup/gpu_worker_setup/app.py setup/gpu_worker_setup/__init__.py setup/tests/test_ui_model.py setup/tests/test_app_modes.py
git commit -m "feat: add GPU Worker Setup wizard"
```

### Task 13: Worker and Setup packaging pipeline

**Files:**
- Create: `setup/GPUWorkerSetup.spec`
- Create: `scripts/build-release.ps1`
- Create: `scripts/verify-worker.ps1`
- Create: `scripts/generate-worker-manifest.ps1`
- Create: `scripts/verify-setup.ps1`
- Create: `scripts/packaged-smoke.ps1`
- Create: `setup/tests/test_setup_spec.py`
- Create: `setup/tests/test_build_pipeline.py`
- Modify: `worker/gpu-worker-agent.spec`
- Modify: `worker/scripts/build-agent.ps1`
- Modify: `worker/tests/test_packaging.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: root `scripts/build-release.ps1` as the single release entrypoint.
- Produces: `release/GPUWorkerSetup.exe`, generated Worker manifest, and machine-readable verification report.

- [ ] **Step 1: Write failing packaging tests**

Assert Worker spec uses `Path(sys.base_prefix) / "Library" / "bin" / "ffi-8.dll"`, Setup spec is onefile, embeds the complete onedir payload plus manifest, never executes `_MEIPASS` Worker, and root pipeline order is exactly test-worker, test-setup, build-worker, verify-worker, manifest, build-setup, verify-setup, packaged-smoke, release-report.

- [ ] **Step 2: Run packaging unit tests**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest tests.test_packaging -v
Pop-Location
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest tests.test_setup_spec tests.test_build_pipeline -v
Pop-Location
```

Expected: FAIL for missing Setup pipeline while existing ffi tests remain PASS.

- [ ] **Step 3: Implement deterministic release scripts and specs**

Scripts accept resolved explicit roots, stop on nonzero exit, never consume `worker-config.example.json` as mutable user config, generate sorted manifest entries, and validate embedded/staging/installed hashes. Production release mode fails unless Setup and Worker signatures validate; development mode records `MANUAL_FALLBACK_REQUIRED` without claiming production release.

- [ ] **Step 4: Run the complete build**

```powershell
.\scripts\build-release.ps1 -Python .\worker\.venv\Scripts\python.exe -Configuration Development
```

Expected:

- Worker tests PASS, including original 88 tests/22 subtests and additive tests.
- Setup tests PASS.
- Worker onedir exists with `_internal\ffi-8.dll`.
- `GPUWorkerSetup.exe --verify-package` exits 0.
- embedded and staging SHA-256 verification PASS.
- Development report marks missing production signatures `MANUAL_FALLBACK_REQUIRED`.

- [ ] **Step 5: Commit**

```powershell
git add setup/GPUWorkerSetup.spec setup/tests/test_setup_spec.py setup/tests/test_build_pipeline.py scripts worker/gpu-worker-agent.spec worker/scripts/build-agent.ps1 worker/tests/test_packaging.py .gitignore
git commit -m "build: package GPU Worker Setup release"
```

### Task 14: Documentation, full verification, and hardware evidence

**Files:**
- Create: `docs/gpu-worker-setup-validation.md`
- Modify: `worker/README.md`
- Modify: `README.md`

**Interfaces:**
- Produces: release status table using only `IMPLEMENTED`, `TESTED`, `HARDWARE_VERIFIED`, `MANUAL_FALLBACK_REQUIRED` definitions from the spec.

- [ ] **Step 1: Document supplier and operator procedures**

Document the three-field UI, Docker consent, UAC/reboot resume, LocalAppData layout, AutoStart setting, update/rollback, error codes, no-manual-JSON flow, and production signing gate. Document the current Backend GPU UUID limitation and future device-binding extension point.

- [ ] **Step 2: Run all automated tests**

```powershell
Push-Location worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
Pop-Location
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
Push-Location backend
.\gradlew.bat test
Pop-Location
```

Expected: all Worker, Setup, and unchanged Backend tests PASS.

- [ ] **Step 3: Run packaged verification**

```powershell
.\scripts\build-release.ps1 -Python .\worker\.venv\Scripts\python.exe -Configuration Development
.\release\GPUWorkerSetup.exe --verify-package
```

Expected: exit 0, Worker payload and ffi checks PASS, no system mutation in verify mode.

- [ ] **Step 4: Run approved Windows hardware checklist**

On a supplier-like current-user Windows account, verify no Python dependency, explicit Docker consent behavior, UAC denial/retry, reboot resume, installed-path-only execution, real NVML/Docker GPU probe, Benchmark cache miss/hit, REGISTER, Backend ONLINE/IDLE/current Benchmark, Setup/Worker mutexes, AutoStart delay, update lock, normal update, downgrade block, and rollback. Record commands, timestamps, GPU model, non-secret versions, and safe outcomes in `docs/gpu-worker-setup-validation.md`.

- [ ] **Step 5: Assign truthful production statuses**

Use `HARDWARE_VERIFIED` only for flows actually observed on Windows + NVIDIA + Docker through Benchmark and Backend ONLINE/IDLE. Use `MANUAL_FALLBACK_REQUIRED` for unavailable signing certificate, disposable VM-only destructive scenarios, BIOS changes, or external organizational policy.

- [ ] **Step 6: Commit documentation**

```powershell
git add docs/gpu-worker-setup-validation.md worker/README.md README.md
git commit -m "docs: record GPU Worker Setup validation"
```

---

## Stage-by-Stage Command Summary

```powershell
# Worker regression
Push-Location worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
Pop-Location

# Setup unit/integration
Push-Location setup
..\worker\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location

# Backend unchanged regression
Push-Location backend
.\gradlew.bat test
Pop-Location

# Full development release
.\scripts\build-release.ps1 -Python .\worker\.venv\Scripts\python.exe -Configuration Development

# Read-only packaged smoke
.\release\GPUWorkerSetup.exe --verify-package

# Production gate after operator signing
.\scripts\build-release.ps1 -Python .\worker\.venv\Scripts\python.exe -Configuration Production
```

## Manual Windows Verification Checklist

- Clean supplier-like user: no Python/venv available outside the EXE.
- NVIDIA driver/NVML and actual selected GPU index 0.
- Docker absent consent unchecked; decline causes no install/change.
- Docker official per-user installer signature and exact CLI options.
- WSL feature UAC approve/deny, reboot prompt, RunOnce resume with spaces in path.
- Existing Docker install bypasses license screen and is probed.
- Login-time Docker starting delay reaches Worker REGISTER without one-shot exit.
- `%LOCALAPPDATA%\GPUCompute\Worker` is the only executed Worker payload.
- `_internal\ffi-8.dll` present and `_ctypes`/pynvml load.
- Benchmark cache miss then valid UUID/version hit.
- Backend Worker exists, ONLINE, IDLE, current positive Benchmark.
- Current Backend UUID limitation is recorded; local cache UUID is the approved companion evidence.
- Setup duplicate launch returns `SETUP_ALREADY_RUNNING`.
- Worker duplicate launch does not send a second REGISTER.
- AutoStart during active update defers.
- BUSY update is refused; exact PID/path is enforced.
- Residual file lock returns `WORKER_UPDATE_LOCKED` without partial overwrite.
- Same-version valid install skips; corruption repairs; arbitrary downgrade blocks.
- Failed new REGISTER rolls back to the journal-authenticated backup.
- AutoStart disable removes only the owned HKCU value and preserves mutable data.
- Setup and Worker production Authenticode signatures verify, or status remains `MANUAL_FALLBACK_REQUIRED`.
