-- qlora-sft-v1 Runtime 이미지를 0.3.0 으로 — 입력 LoRA Adapter 이어 학습(--adapter-path)이 되는 첫 이미지.
--
-- 왜 지금인가: V17/V18 로 Backend 가 어댑터를 받아 execution-context 에 실어 보내기 시작했는데, V14 가 박아 둔
-- 0.2.0 은 어댑터 인자를 모른다. Worker 는 0.2.0 을 "전환기 허용"으로 두되 어댑터가 실린 Job 은 이미지 확인 단계에서
-- ADAPTER_UNAVAILABLE("the runtime image this Job names cannot continue training from an adapter")로 멈춘다
-- (system/worker/gshared_worker/runtime_pipeline.py · runtime_image.py supports_adapter). 실제로 그렇게 죽은 Job 이 나와서 옮긴다.
--
-- 아래 값은 V14 와 같은 규칙으로, system/worker/gshared_worker/runtime_image.py 의 CANONICAL_RUNTIME_DIGEST 를 그대로 옮겼다
-- (tag 0.3.0 은 사람용, pull 은 digest 로만). 두 곳이 어긋나면 Worker 가 받은 imageRef 와 허용 이미지가 달라지므로 함께 고친다.
-- 0.2.0 은 Worker 의 PREVIOUS_RUNTIME_DIGEST 로 남아 있어, 이 마이그레이션 전에 배정된 Job 도 계속 돈다.
UPDATE workloads
   SET image_digest = 'sha256:e32bd1c1b8e7689c33100eaf1718f3ceadea80ff1d13de4da970173762e8e170'
 WHERE slug = 'qlora-sft-v1'
   AND image_repository = 'le3560/gshared-qlora-sft-runtime';
