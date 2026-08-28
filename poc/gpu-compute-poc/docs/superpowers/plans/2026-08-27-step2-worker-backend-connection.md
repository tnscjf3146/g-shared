# STEP 2 Worker-Backend Persistent Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task-by-task. Do not dispatch sub-agents unless the user explicitly requests delegation.

**Goal:** Build and validate a persistent outbound raw-WebSocket connection from the existing CUDA Python Worker to a Java 21 Spring Boot Backend, including registration, heartbeat, liveness, in-memory status queries, and safe connection replacement.

**Architecture:** The Backend exposes /ws/workers and owns an in-memory, session-aware Worker Registry. The existing Python package gains a separate transport client that reuses the STEP 1 CUDA/NVML runtime without changing the embedding CLI. All state transitions are atomic per workerId, and connectionStatus is independent from workerStatus.

**Tech Stack:** Java 21, Spring Boot 4.1.1, Gradle Wrapper 9.3.1, Spring Web, Spring WebSocket, Jackson, JUnit 5, Python 3.12.12, PyTorch 2.12.1+cu126, nvidia-ml-py 13.610.43, websockets 17.1, unittest.

**Spec:** docs/superpowers/specs/2026-08-27-step2-worker-backend-connection-design.md

## Global Constraints

- Preserve every STEP 1 CLI behavior and keep the existing 9 tests passing.
- Java source and target compatibility are exactly Java 21.
- Use Spring Boot 4.1.1 and Gradle Wrapper 9.3.1.
- Use raw JSON WebSocket at /ws/workers; do not add STOMP or WebFlux.
- Worker heartbeat interval is 5 seconds.
- Backend liveness timeout is 15 seconds and the scan interval is 1 second.
- connectionStatus is ONLINE or OFFLINE; workerStatus is independently IDLE or BUSY.
- hourlyPrice is a non-negative minimum-currency-unit integer: Java long and Python int.
- supportedWorkloads is an extensible set of non-blank strings, not a workload enum.
- Registry persistence is in-memory only.
- Do not add Docker Runtime, EXECUTE, LoRA Training, Scheduler, P2P, Blockchain, Chunking, DB, Redis, or automatic reconnect.
- Do not mark STEP 2 PASS until the actual RTX 4070 integration flow and every automated test pass.
- Do not commit or push; the user has not authorized either action.

---

## Planned File Structure

### Backend production files

- backend/settings.gradle — Gradle project name.
- backend/build.gradle — Java 21, Spring Boot 4.1.1, Web and WebSocket dependencies.
- backend/gradlew, backend/gradlew.bat, backend/gradle/wrapper/* — reproducible Gradle 9.3.1 wrapper.
- backend/src/main/java/com/gpucompute/poc/backend/GpuComputeBackendApplication.java — application and scheduling entry point.
- backend/src/main/java/com/gpucompute/poc/backend/config/WebSocketConfig.java — /ws/workers registration.
- backend/src/main/java/com/gpucompute/poc/backend/config/TimeConfig.java — injectable Clock.
- backend/src/main/java/com/gpucompute/poc/backend/domain/ConnectionStatus.java — ONLINE/OFFLINE.
- backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerStatus.java — IDLE/BUSY.
- backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerRegistration.java — validated registration command.
- backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerHeartbeat.java — validated heartbeat command.
- backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRecord.java — immutable registry state including active session.
- backend/src/main/java/com/gpucompute/poc/backend/registry/RegistrationResult.java — current record and replaced session.
- backend/src/main/java/com/gpucompute/poc/backend/registry/HeartbeatResult.java — APPLIED or rejected update result.
- backend/src/main/java/com/gpucompute/poc/backend/registry/TimedOutSession.java — session returned for close after timeout transition.
- backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java — atomic in-memory transitions.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessage.java — common decoded-message contract.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageType.java — REGISTER/HEARTBEAT discriminator.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/RegisterMessage.java — wire DTO.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/HeartbeatMessage.java — wire DTO.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/ProtocolException.java — close status plus safe reason.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageDecoder.java — type-first JSON decoding.
- backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageValidator.java — boundary and cross-field validation.
- backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerSessionBindings.java — sessionId to workerId bindings.
- backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java — WebSocket lifecycle and protocol orchestration.
- backend/src/main/java/com/gpucompute/poc/backend/liveness/WorkerLivenessMonitor.java — one-second stale-worker scan.
- backend/src/main/java/com/gpucompute/poc/backend/api/WorkerResponse.java — session-free REST DTO.
- backend/src/main/java/com/gpucompute/poc/backend/api/WorkerController.java — list and detail endpoints.
- backend/src/main/resources/application.yml — liveness configuration and application name.

### Backend test files

- backend/src/test/java/com/gpucompute/poc/backend/BackendApplicationTest.java
- backend/src/test/java/com/gpucompute/poc/backend/websocket/WebSocketEndpointIntegrationTest.java
- backend/src/test/java/com/gpucompute/poc/backend/registry/WorkerRegistryTest.java
- backend/src/test/java/com/gpucompute/poc/backend/protocol/RegisterMessageTest.java
- backend/src/test/java/com/gpucompute/poc/backend/protocol/HeartbeatMessageTest.java
- backend/src/test/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandlerTest.java
- backend/src/test/java/com/gpucompute/poc/backend/liveness/WorkerLivenessMonitorTest.java
- backend/src/test/java/com/gpucompute/poc/backend/api/WorkerControllerTest.java
- backend/src/test/java/com/gpucompute/poc/backend/integration/WorkerProtocolIntegrationTest.java

### Python production and test files

- worker/requirements.txt — add websockets 17.1.
- worker/pyproject.toml — declare the same pinned transport dependency.
- worker/gpu_worker/errors.py — add client configuration/runtime/transport domain errors.
- worker/gpu_worker/runtime.py — CUDA/NVML status provider.
- worker/gpu_worker/messages.py — REGISTER and HEARTBEAT payload construction.
- worker/gpu_worker/transport.py — one-connection WebSocket lifecycle and heartbeat loop.
- worker/gpu_worker/client.py — separate STEP 2 CLI entry point.
- worker/tests/test_runtime.py
- worker/tests/test_messages.py
- worker/tests/test_transport.py
- worker/tests/test_client.py

### Documentation files updated only after successful integration

- backend/README.md
- worker/README.md
- docs/step2-integration-validation.md
- docs/poc-plan.md
- README.md

---

### Task 1: Backend Base Project and WebSocket Endpoint

**Files:**

- Create: backend/settings.gradle
- Create: backend/build.gradle
- Create: backend/gradlew
- Create: backend/gradlew.bat
- Create: backend/gradle/wrapper/gradle-wrapper.jar
- Create: backend/gradle/wrapper/gradle-wrapper.properties
- Create: backend/src/main/java/com/gpucompute/poc/backend/GpuComputeBackendApplication.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/config/WebSocketConfig.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java
- Create: backend/src/main/resources/application.yml
- Create: backend/src/test/java/com/gpucompute/poc/backend/BackendApplicationTest.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/websocket/WebSocketEndpointIntegrationTest.java

**Interfaces:**

- Produces a Spring bean named WorkerWebSocketHandler.
- Produces raw WebSocket endpoint ws://host:port/ws/workers.
- Produces Gradle commands .\gradlew.bat test and .\gradlew.bat bootRun.
- Does not yet parse REGISTER or HEARTBEAT.

**Implementation content:**

Configure the Gradle project exactly as follows.

~~~groovy
plugins {
    id 'java'
    id 'org.springframework.boot' version '4.1.1'
}

group = 'com.gpucompute.poc'
version = '0.2.0'

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation platform(org.springframework.boot.gradle.plugin.SpringBootPlugin.BOM_COORDINATES)
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-websocket'
    testImplementation platform(org.springframework.boot.gradle.plugin.SpringBootPlugin.BOM_COORDINATES)
    testImplementation 'org.springframework.boot:spring-boot-starter-test'
}

tasks.named('test') {
    useJUnitPlatform()
}
~~~

Generate the Gradle Wrapper from the existing local Gradle 9.3.1 installation.

~~~powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
& 'C:\reasoning-game\.tools\java-gradle\Library\bin\gradle.bat' wrapper --gradle-version 9.3.1
~~~

Register a minimal TextWebSocketHandler at /ws/workers. Do not enable STOMP, SockJS, or wildcard origins.

**TDD steps:**

- [ ] Create only the Gradle build, settings, and wrapper files required to run tests.
- [ ] Write BackendApplicationTest with @SpringBootTest and contextLoads().
- [ ] Write WebSocketEndpointIntegrationTest using @SpringBootTest(webEnvironment = RANDOM_PORT) and StandardWebSocketClient to connect to /ws/workers.
- [ ] Run the tests and verify failure because the application/config/handler classes do not exist.
- [ ] Add GpuComputeBackendApplication, the no-op WorkerWebSocketHandler bean, and WebSocketConfig.
- [ ] Run the focused tests and verify the context loads and a real WebSocket handshake succeeds.
- [ ] Run all existing Python tests to prove STEP 1 remains intact.

**Test commands:**

~~~powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*BackendApplicationTest' --tests '*WebSocketEndpointIntegrationTest'

cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

**Completion criteria:**

- Gradle Wrapper reports 9.3.1 and compiles with Java 21.
- Spring context starts.
- A real client completes a WebSocket handshake at /ws/workers.
- No REST, registry, heartbeat, retry, or EXECUTE behavior exists yet.
- Existing STEP 1 tests remain 9/9 PASS.

---

### Task 2: In-Memory Worker Registry

**Files:**

- Create: backend/src/main/java/com/gpucompute/poc/backend/domain/ConnectionStatus.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerStatus.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerRegistration.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRecord.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/registry/RegistrationResult.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/registry/WorkerRegistryTest.java

**Interfaces:**

~~~java
RegistrationResult register(
    WorkerRegistration registration,
    WebSocketSession session,
    Instant observedAt
);

Optional<WorkerRecord> findById(String workerId);
List<WorkerRecord> findAll();
~~~

WorkerRegistration fields:

~~~java
String workerId;
String gpuModel;
long totalVramMb;
long freeVramMb;
int gpuUtilization;
boolean cudaAvailable;
Set<String> supportedWorkloads;
long hourlyPrice;
WorkerStatus workerStatus;
~~~

WorkerRecord adds ConnectionStatus connectionStatus, Instant lastHeartbeat, and WebSocketSession activeSession.

**Implementation content:**

- Store records in ConcurrentHashMap<String, WorkerRecord>.
- Make WorkerRecord immutable and defensively copy supportedWorkloads with Set.copyOf.
- register uses workers.compute(workerId, ...) and sets connectionStatus ONLINE and lastHeartbeat to observedAt.
- RegistrationResult returns the new current record and the prior active session when present. Closing that session belongs to Task 5.
- findAll returns immutable snapshots sorted by workerId.

**TDD steps:**

- [ ] Write a test registering worker-001/session-A at a fixed Instant and assert every field, ONLINE, IDLE, session-A, and lastHeartbeat.
- [ ] Write a test proving findById returns empty for an unknown ID.
- [ ] Write a test proving findAll is ordered by workerId and cannot mutate the registry.
- [ ] Run WorkerRegistryTest and verify compilation fails because the domain/registry types do not exist.
- [ ] Add the two enums, WorkerRegistration, WorkerRecord, RegistrationResult, and minimal WorkerRegistry.
- [ ] Run WorkerRegistryTest and then the full Backend suite.

**Test command:**

~~~powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*WorkerRegistryTest'
~~~

**Completion criteria:**

- Registry stores the complete required Worker state in memory.
- connectionStatus and workerStatus are separate values.
- session is retained internally.
- Lookup snapshots are deterministic.
- No database or cache dependency is introduced.

---

### Task 3: REGISTER Message Processing and Validation

**Files:**

- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessage.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageType.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/RegisterMessage.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/ProtocolException.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageDecoder.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageValidator.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerSessionBindings.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/protocol/RegisterMessageTest.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandlerTest.java

**Interfaces:**

~~~java
interface WorkerMessage {
    WorkerMessageType type();
}

WorkerMessage decode(String json);
WorkerRegistration validate(RegisterMessage message);
boolean bind(String sessionId, String workerId);
Optional<String> remove(String sessionId);
~~~

ProtocolException carries org.springframework.web.socket.CloseStatus and a log-safe reason.

**Implementation content:**

- WorkerMessageDecoder parses JsonNode first, reads type, rejects missing/unknown type, then maps REGISTER to RegisterMessage. The same decode contract remains stable when HEARTBEAT is added in Task 4.
- RegisterMessage uses nullable wrapper fields (`Long`, `Integer`, and `Boolean`) for required wire numbers/booleans so omitted JSON fields cannot silently become primitive defaults. WorkerRegistration contains only validated primitive/domain values.
- WorkerMessageValidator enforces workerId and gpuModel non-blank, totalVramMb > 0, freeVramMb in range, utilization 0..100, required cudaAvailable, non-empty workloads with no blank values, hourlyPrice >= 0, and IDLE/BUSY.
- Return supportedWorkloads as a de-duplicated Set.
- WorkerWebSocketHandler registers only unbound sessions, binds after validation, calls WorkerRegistry.register, and logs the accepted live data.
- Keep replaced-session close behavior disabled until Task 5 so the next test demonstrates the missing behavior.

**Representative failing assertion:**

~~~java
assertThatThrownBy(() -> validator.validate(messageWithFreeVram(9000, 8188)))
    .isInstanceOf(ProtocolException.class)
    .extracting("closeStatus")
    .isEqualTo(CloseStatus.BAD_DATA);
~~~

**TDD steps:**

- [ ] Write REGISTER deserialization coverage for the approved JSON contract.
- [ ] Add parameterized invalid-boundary cases for every required field.
- [ ] Write a handler test proving a valid REGISTER stores worker-001 and binds session-A.
- [ ] Write handler tests for malformed JSON, unknown type, and repeated REGISTER on one session with close codes 1007, 1003, and 1008.
- [ ] Run focused tests and confirm failure before adding protocol classes.
- [ ] Implement decoding, validation, bindings, handler orchestration, and safe logging.
- [ ] Re-run focused and full Backend tests.

**Test command:**

~~~powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*RegisterMessageTest' --tests '*WorkerWebSocketHandlerTest'
~~~

**Completion criteria:**

- Valid REGISTER JSON becomes a complete ONLINE registry record.
- Invalid data closes the connection with the specified code and never mutates the registry.
- REGISTER sets Backend receive time as lastHeartbeat.
- connectionStatus is Backend-derived and absent from the Worker message.
- session never appears in serialized application data.

---

### Task 4: HEARTBEAT Processing and 15-Second Liveness Timeout

**Files:**

- Create: backend/src/main/java/com/gpucompute/poc/backend/domain/WorkerHeartbeat.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/protocol/HeartbeatMessage.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/registry/HeartbeatResult.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/registry/TimedOutSession.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/config/TimeConfig.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/liveness/WorkerLivenessMonitor.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/GpuComputeBackendApplication.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageDecoder.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageValidator.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java
- Modify: backend/src/main/resources/application.yml
- Create: backend/src/test/java/com/gpucompute/poc/backend/protocol/HeartbeatMessageTest.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/liveness/WorkerLivenessMonitorTest.java

**Interfaces:**

~~~java
WorkerHeartbeat validate(
    HeartbeatMessage message,
    long registeredTotalVramMb
);

HeartbeatResult updateHeartbeat(
    WorkerHeartbeat heartbeat,
    String sessionId,
    Instant observedAt
);

List<TimedOutSession> expireStale(Instant now, Duration timeout);
void scan();
~~~

HeartbeatResult values are APPLIED, UNKNOWN_WORKER, STALE_SESSION, and OFFLINE. TimedOutSession contains workerId and the exact WebSocketSession invalidated by the atomic timeout transition.

**Implementation content:**

- HeartbeatMessage uses nullable wrapper fields for required wire numbers so missing values are rejected explicitly; WorkerHeartbeat contains validated primitive/domain values.
- HEARTBEAT validation checks workerId, freeVramMb against the registered worker's total VRAM, utilization 0..100, and IDLE/BUSY. The handler resolves the current registration for validation, while updateHeartbeat atomically rechecks ONLINE state and active session identity before applying it.
- updateHeartbeat uses workers.computeIfPresent and updates only free VRAM, utilization, workerStatus, and lastHeartbeat when workerId, session ID, and ONLINE all match.
- expireStale re-evaluates the current record inside compute; exactly lastHeartbeat plus 15 seconds is expired.
- Timeout preserves workerStatus and lastHeartbeat, changes connectionStatus to OFFLINE, and clears activeSession.
- WorkerLivenessMonitor uses injected Clock, scans every 1000 ms, and closes sessions returned by expireStale with code 1008 and reason heartbeat timeout.
- Enable Spring scheduling.

**Representative timeout test:**

~~~java
Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
registry.register(registration(WorkerStatus.BUSY), sessionA, registeredAt);

assertThat(registry.expireStale(registeredAt.plusSeconds(14), Duration.ofSeconds(15)))
    .isEmpty();
assertThat(registry.expireStale(registeredAt.plusSeconds(15), Duration.ofSeconds(15)))
    .extracting(TimedOutSession::session)
    .containsExactly(sessionA);

WorkerRecord offline = registry.findById("worker-001").orElseThrow();
assertThat(offline.connectionStatus()).isEqualTo(ConnectionStatus.OFFLINE);
assertThat(offline.workerStatus()).isEqualTo(WorkerStatus.BUSY);
assertThat(offline.activeSession()).isNull();
~~~

**TDD steps:**

- [ ] Write HEARTBEAT decoding and validation boundary tests.
- [ ] Write tests that active-session HEARTBEAT updates only allowed fields.
- [ ] Write tests that less than 15 seconds remains ONLINE and exactly 15 seconds becomes OFFLINE.
- [ ] Write tests that timeout preserves BUSY/lastHeartbeat and clears activeSession.
- [ ] Write tests that late heartbeat returns OFFLINE and cannot restore ONLINE.
- [ ] Write WorkerLivenessMonitorTest with a mutable Clock and verify stale session close follows the atomic transition.
- [ ] Run focused tests and verify failure.
- [ ] Implement heartbeat DTO/validation/handler, registry transitions, Clock bean, scheduling, and liveness monitor.
- [ ] Re-run focused and full Backend tests.

**Test command:**

~~~powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*HeartbeatMessageTest' --tests '*WorkerLivenessMonitorTest' --tests '*WorkerRegistryTest'
~~~

**Completion criteria:**

- Active-session heartbeat refreshes lastHeartbeat and live GPU state.
- Timeout does not occur before 15 seconds and does occur at 15 seconds.
- Timeout invalidates and closes only the stale active session.
- workerStatus remains the last reported value.
- Late heartbeat cannot restore ONLINE.
- Unit tests use no wall-clock sleep.

---

### Task 5: Last-Connection-Wins and Stale Disconnect Safety

**Files:**

- Modify: backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerSessionBindings.java
- Modify: backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java
- Modify: backend/src/test/java/com/gpucompute/poc/backend/registry/WorkerRegistryTest.java
- Modify: backend/src/test/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandlerTest.java

**Interfaces:**

~~~java
boolean disconnectIfActive(String workerId, String sessionId);
~~~

RegistrationResult.replacedSession is consumed by the handler and closed only after WorkerRegistry.register returns.

**Implementation content:**

- register atomically replaces the WorkerRecord for duplicate workerId and returns the old active session.
- Handler binds session-B, completes the registry swap, logs replacement, then closes session-A outside compute.
- afterConnectionClosed removes the session binding and calls disconnectIfActive.
- disconnectIfActive compares activeSession.getId() with the disconnecting session ID inside compute.
- Matching disconnect sets OFFLINE, preserves status/heartbeat/GPU data, and clears activeSession.
- Stale disconnect is a no-op.

**Required scenario test:**

~~~java
registry.register(registration(WorkerStatus.IDLE), sessionA, t0);
RegistrationResult replacement =
    registry.register(registration(WorkerStatus.IDLE), sessionB, t1);

assertThat(replacement.replacedSession()).contains(sessionA);
assertThat(registry.findById("worker-001").orElseThrow().activeSession())
    .isSameAs(sessionB);

assertThat(registry.disconnectIfActive("worker-001", sessionA.getId())).isFalse();
assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
    .isEqualTo(ConnectionStatus.ONLINE);

assertThat(registry.disconnectIfActive("worker-001", sessionB.getId())).isTrue();
assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
    .isEqualTo(ConnectionStatus.OFFLINE);
~~~

**TDD steps:**

- [ ] Add the exact session-A/session-B/stale-disconnect test.
- [ ] Add a handler test proving session-A closes only after session-B becomes active.
- [ ] Add a handler test proving session-A callback cannot make session-B OFFLINE.
- [ ] Add active session-B disconnect and workerStatus-preservation coverage.
- [ ] Add timeout-versus-replacement coverage that protects the new session.
- [ ] Run focused tests and verify current behavior fails these expectations.
- [ ] Implement the minimal atomic swap and identity-checked disconnect.
- [ ] Run focused and full Backend tests.

**Test command:**

~~~powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*WorkerRegistryTest' --tests '*WorkerWebSocketHandlerTest'
~~~

**Completion criteria:**

- New connection becomes active before old connection closes.
- Old disconnect never changes the new active record.
- Active disconnect transitions only that worker to OFFLINE.
- Timeout, heartbeat, replacement, and disconnect use session identity and atomic per-worker transitions.

---

### Task 6: Worker Query REST API

**Files:**

- Create: backend/src/main/java/com/gpucompute/poc/backend/api/WorkerResponse.java
- Create: backend/src/main/java/com/gpucompute/poc/backend/api/WorkerController.java
- Create: backend/src/test/java/com/gpucompute/poc/backend/api/WorkerControllerTest.java

**Interfaces:**

~~~text
GET /api/workers
GET /api/workers/{workerId}
~~~

WorkerResponse fields:

~~~java
String workerId;
String gpuModel;
long totalVramMb;
long freeVramMb;
int gpuUtilization;
boolean cudaAvailable;
Set<String> supportedWorkloads;
long hourlyPrice;
ConnectionStatus connectionStatus;
WorkerStatus workerStatus;
Instant lastHeartbeat;
~~~

**Implementation content:**

- Map WorkerRecord to WorkerResponse without activeSession.
- GET /api/workers returns all ONLINE/OFFLINE records sorted by workerId.
- GET /api/workers/{workerId} returns 404 when missing.
- Use Jackson ISO-8601 Instant serialization.

**Representative REST assertion:**

~~~java
mockMvc.perform(get("/api/workers/worker-001"))
    .andExpect(status().isOk())
    .andExpect(jsonPath("$.connectionStatus").value("ONLINE"))
    .andExpect(jsonPath("$.workerStatus").value("IDLE"))
    .andExpect(jsonPath("$.hourlyPrice").value(600))
    .andExpect(jsonPath("$.session").doesNotExist())
    .andExpect(jsonPath("$.activeSession").doesNotExist());
~~~

**TDD steps:**

- [ ] Write list test with worker-002 and worker-001 and assert order.
- [ ] Write detail test asserting every public field and no session.
- [ ] Write OFFLINE detail test proving last workerStatus remains visible.
- [ ] Write unknown worker test expecting HTTP 404.
- [ ] Run WorkerControllerTest and verify failure.
- [ ] Implement WorkerResponse and WorkerController only.
- [ ] Run focused and full Backend tests.

**Test command:**

~~~powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat test --tests '*WorkerControllerTest'
~~~

**Completion criteria:**

- Both required REST endpoints return the designed schema.
- OFFLINE workers remain queryable.
- Lists are stable and sorted.
- WebSocketSession is not exposed.
- No mutation, delete, scheduling, job, or pricing API is added.

---

### Task 7: Python WebSocket Client Transport

**Files:**

- Modify: worker/requirements.txt
- Modify: worker/pyproject.toml
- Modify: worker/gpu_worker/errors.py
- Create: worker/gpu_worker/transport.py
- Create: worker/gpu_worker/client.py
- Create: worker/tests/test_transport.py
- Create: worker/tests/test_client.py

**Interfaces:**

Pin websockets==17.1 in both dependency declarations. PyPI declares Python 3.12 support.

~~~python
@dataclass(frozen=True)
class WorkerClientConfig:
    backend_url: str
    worker_id: str
    hourly_price: int
    gpu_index: int
    supported_workloads: tuple[str, ...]
    heartbeat_interval_seconds: float = 5.0

class WorkerMessageSource(Protocol):
    def register_payload(self) -> dict[str, object]: ...
    def heartbeat_payload(self) -> dict[str, object]: ...

async def run_connection(
    config: WorkerClientConfig,
    source: WorkerMessageSource,
    *,
    connector: Callable,
    wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None: ...
~~~

The production connector default is websockets.asyncio.client.connect.

**Implementation content:**

- Keep client.py independent from gpu_worker.cli and do not modify gpu_worker.__main__.
- Open one connection, send REGISTER exactly once, then wait five seconds before each HEARTBEAT.
- Never reconnect after close.
- Encode compact UTF-8-safe JSON.
- Validate ws/wss URL, non-blank worker ID, non-negative price/GPU index, and non-empty/non-blank workloads.
- Convert configuration, runtime, and connection failures to deterministic non-zero exits.

**Representative transport test:**

~~~python
async def test_register_precedes_heartbeat(self):
    socket = FakeSocket()
    wait = AsyncMock(side_effect=[None, CancelledError()])

    with self.assertRaises(CancelledError):
        await run_connection(
            config(),
            FakeMessageSource(),
            connector=FakeConnector(socket),
            wait=wait,
        )

    self.assertEqual(json.loads(socket.sent[0])["type"], "REGISTER")
    self.assertEqual(json.loads(socket.sent[1])["type"], "HEARTBEAT")
    wait.assert_awaited_once_with(5.0)
~~~

**TDD steps:**

- [ ] Add websockets 17.1 to both dependency files and install it in worker/.venv.
- [ ] Write config-validation tests for URL, workerId, hourlyPrice, gpuIndex, and workloads.
- [ ] Write async tests proving REGISTER first and five-second heartbeat wait.
- [ ] Write server-close test proving connector call count remains one.
- [ ] Run tests and verify failure because transport/client modules do not exist.
- [ ] Implement config, message-source protocol, run_connection, CLI parser, and exit mapping.
- [ ] Run new tests and complete Python discovery.

**Test commands:**

~~~powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest tests.test_transport tests.test_client -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

**Completion criteria:**

- Generic injected source maintains one WebSocket connection.
- REGISTER always precedes HEARTBEAT.
- Heartbeat cadence is five seconds.
- Client exits on close/error without reconnect.
- Existing STEP 1 CLI and tests are unchanged.

---

### Task 8: Live NVML REGISTER and HEARTBEAT Integration

**Files:**

- Create: worker/gpu_worker/runtime.py
- Create: worker/gpu_worker/messages.py
- Modify: worker/gpu_worker/client.py
- Modify: worker/gpu_worker/errors.py
- Create: worker/tests/test_runtime.py
- Create: worker/tests/test_messages.py
- Modify: worker/tests/test_client.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class WorkerGpuState:
    gpu_model: str
    total_vram_mb: int
    free_vram_mb: int
    gpu_utilization: int
    cuda_available: bool

class CudaGpuStatusProvider:
    def __enter__(self) -> "CudaGpuStatusProvider": ...
    def snapshot(self) -> WorkerGpuState: ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...

@dataclass(frozen=True)
class WorkerMetadata:
    worker_id: str
    hourly_price: int
    supported_workloads: tuple[str, ...]
    worker_status: str = "IDLE"

class NvmlWorkerMessageSource:
    def register_payload(self) -> dict[str, object]: ...
    def heartbeat_payload(self) -> dict[str, object]: ...
~~~

**Implementation content:**

- CudaGpuStatusProvider uses gpu_worker.embedding.load_torch and gpu_worker.gpu.NvmlGpuProbe.
- Check CUDA before opening NVML.
- Keep one probe open for connection lifetime and read a fresh snapshot per message.
- REGISTER includes live model/VRAM/utilization/cuda true plus configured workload/price and IDLE.
- HEARTBEAT includes a new free VRAM/utilization snapshot and IDLE.
- Do not hardcode RTX 4070, 8188 MiB, or measured values.
- CUDA/NVML failure occurs before WebSocket connection.

**Representative message assertion:**

~~~python
self.assertEqual(
    source.register_payload(),
    {
        "type": "REGISTER",
        "workerId": "worker-001",
        "gpuModel": "NVIDIA Test GPU",
        "totalVramMb": 8192,
        "freeVramMb": 7800,
        "gpuUtilization": 4,
        "cudaAvailable": True,
        "supportedWorkloads": ["EMBEDDING"],
        "hourlyPrice": 600,
        "workerStatus": "IDLE",
    },
)
~~~

**TDD steps:**

- [ ] Write runtime tests with fake torch/probe for CUDA available/unavailable.
- [ ] Write REGISTER payload test using deterministic first snapshot.
- [ ] Write HEARTBEAT test proving a second fresh snapshot is read.
- [ ] Write client test proving CUDA/NVML failure prevents connector invocation.
- [ ] Run focused tests and verify failure.
- [ ] Implement runtime provider, message source, and CLI wiring.
- [ ] Run focused tests, full Python suite, and a real-GPU snapshot smoke command.

**Test commands:**

~~~powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest tests.test_runtime tests.test_messages tests.test_client -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -c "from gpu_worker.runtime import CudaGpuStatusProvider; p=CudaGpuStatusProvider(0); p.__enter__(); print(p.snapshot()); p.__exit__(None,None,None)"
~~~

**Completion criteria:**

- REGISTER and every HEARTBEAT use current NVML values.
- cudaAvailable comes from PyTorch and is true on the actual machine.
- No GPU model, VRAM, or utilization is hardcoded.
- Runtime/provider contains no WebSocket logic.
- Transport contains no NVML/PyTorch logic.

---

### Task 9: Complete Backend and Python Automated Tests

**Files:**

- Create: backend/src/test/java/com/gpucompute/poc/backend/integration/WorkerProtocolIntegrationTest.java
- Modify all Backend test files created in Tasks 1–6 as coverage requires.
- Modify worker/tests/test_runtime.py
- Modify worker/tests/test_messages.py
- Modify worker/tests/test_transport.py
- Modify worker/tests/test_client.py

**Interfaces:**

- Consumes all public interfaces from Tasks 1–8.
- Produces one Backend test command and one Python test command covering STEP 2 and STEP 1 regression.

**Implementation content:**

- Complete coverage for REGISTER validation, Registry registration, duplicate replacement, stale/active disconnect, heartbeat, timeout boundaries, late heartbeat rejection, re-register recovery, REST list/detail/404, and close codes.
- Add WorkerProtocolIntegrationTest with a real loopback WebSocket and random Spring port.
- For liveness integration, REGISTER and keep the socket open without heartbeat; await OFFLINE with a bounded condition loop, then prove the socket closes and only a new connection+REGISTER restores ONLINE.
- Python unit tests use injected fakes; actual GPU connection is Task 10.

**TDD steps:**

- [ ] Build a requirement-to-test matrix from spec sections 5, 7, 8, 10, and 13.
- [ ] Add missing failing boundary/race/REST tests one behavior at a time.
- [ ] Run each focused test immediately and make only the smallest correction.
- [ ] Run clean complete Backend tests.
- [ ] Run complete Python discovery and confirm original 9 tests are included.
- [ ] Run pip check.
- [ ] Search production source for excluded feature names and inspect every match.

**Test commands:**

~~~powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat clean test --no-daemon

cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check

cd C:\gpu-compute-poc
rg -n "EXECUTE|runtimeImage|LORA_TRAINING|LORA_INFERENCE|Scheduler|Blockchain|Chunk" backend worker\gpu_worker
~~~

**Completion criteria:**

- All seven originally requested Backend test categories have explicit coverage.
- Last-connection-wins has the exact stale session-A disconnect regression.
- Liveness covers exact 15-second boundary and no-heartbeat real socket.
- Python covers order, five-second cadence, no reconnect, and provider wiring.
- Original STEP 1 tests remain included and passing.
- Both suites exit zero and pip check reports no broken requirements.

---

### Task 10: RTX 4070 Actual Integration Test

**Files:**

- Create: backend/README.md
- Modify: worker/README.md
- Create only after all gates pass: docs/step2-integration-validation.md
- Modify only after all gates pass: docs/poc-plan.md
- Modify only after all gates pass: README.md

**Interfaces:**

- Consumes .\gradlew.bat bootRun, python -m gpu_worker.client, GET /api/workers, and GET /api/workers/{workerId}.
- Produces reproducible STEP 2 evidence and documentation.

**Implementation content:**

- Document startup before testing, but do not mark PASS.
- Use separate long-running terminal sessions so Ctrl+C cleanly terminates real processes.
- Capture Backend logs, two REST snapshots more than five seconds apart, and OFFLINE after Worker termination.
- Run two actual clients with one workerId for last-connection-wins.
- Use Task 9 real-socket integration as liveness evidence and actual RTX Worker as GPU/NVML evidence.

**Actual test commands:**

Terminal A:

~~~powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat bootRun
~~~

Terminal B:

~~~powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m gpu_worker.client --backend-url ws://127.0.0.1:8080/ws/workers --worker-id worker-001 --hourly-price 600 --gpu-index 0 --supported-workload EMBEDDING
~~~

Terminal C:

~~~powershell
$first=Invoke-RestMethod http://127.0.0.1:8080/api/workers/worker-001
Start-Sleep -Seconds 6
$second=Invoke-RestMethod http://127.0.0.1:8080/api/workers/worker-001
$first | ConvertTo-Json -Depth 5
$second | ConvertTo-Json -Depth 5
if([datetime]$second.lastHeartbeat -le [datetime]$first.lastHeartbeat){
    throw 'lastHeartbeat did not advance'
}
~~~

Send Ctrl+C to Terminal B, then query again:

~~~powershell
Invoke-RestMethod http://127.0.0.1:8080/api/workers/worker-001 | ConvertTo-Json -Depth 5
~~~

**Last-connection-wins actions:**

- [ ] Start session-A with workerId worker-duplicate.
- [ ] Start session-B with the same workerId.
- [ ] Confirm Backend logs replacement and closes session-A.
- [ ] Confirm REST stays ONLINE after session-A closes.
- [ ] Stop session-B and confirm REST becomes OFFLINE.

**Evidence to record:**

- Java, Spring Boot, Gradle, Python, PyTorch, and PyTorch CUDA versions.
- Actual GPU model and total/free VRAM.
- cudaAvailable true.
- Two advancing lastHeartbeat values.
- ONLINE and IDLE while connected.
- Heartbeat GPU utilization/free VRAM.
- OFFLINE with preserved workerStatus after process exit.
- Replacement log and REST result.
- 15-second liveness integration result.
- Backend and Python test counts/results.

**Final verification commands:**

~~~powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat clean test --no-daemon

cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check

cd C:\gpu-compute-poc
git diff --check
git status --short --branch
git diff
~~~

**Completion criteria:**

- Backend starts on Java 21.
- Actual RTX 4070 Worker connects outbound.
- Backend logs and REST contain live RTX 4070/NVML/CUDA values.
- REGISTER and at least one five-second HEARTBEAT are observed.
- lastHeartbeat advances and GPU state updates.
- Worker termination produces OFFLINE while preserving workerStatus.
- Actual duplicate clients prove last-connection-wins and stale disconnect safety.
- Automated liveness integration proves OFFLINE at 15 seconds and new connection+REGISTER recovery.
- All Backend and Python tests pass, including original 9 STEP 1 tests.
- Only then update README and docs to STEP 2 PASS.
- STEP 3 code remains absent.

---

## Execution Notes

- Execute tasks in order because later tasks consume interfaces fixed earlier.
- Use red-green-refactor in every task and avoid unrelated STEP 1 refactoring.
- On unexpected failures, invoke superpowers:systematic-debugging before changing implementation.
- Before any completion claim, invoke superpowers:verification-before-completion and run the full commands.
- Review git diff after every task and preserve unrelated user changes.
- Do not commit or push unless the user explicitly requests it.
