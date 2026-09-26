-- qlora-sft-v1 Runtime 이미지를 0.4.0 으로 — 학습 결과 검증 데이터를 쓰는 첫 이미지.
--
-- 왜 지금인가: Job 상세의 '학습 결과 검증' 패널이 Loss Curve·LoRA Adapter 검증·Layer Heatmap 세 칸을 모두
-- "데이터가 제공되지 않습니다" 로 그리고 있었다. Backend(JobResultVerificationService)와 화면은 완성돼 있었고,
-- 그 값을 만드는 쪽이 없었다 — training_metrics.json 에 lossHistory·finalLoss·verification 이 들어간 것은
-- Runtime 0.4.0 부터다. 카탈로그가 0.3.0 을 가리키는 한 Worker 는 그 이미지를 받으므로 패널은 계속 비어 있다.
--
-- 아래 값은 V14·V19 와 같은 규칙으로, system/worker/gshared_worker/runtime_image.py 의
-- CANONICAL_RUNTIME_DIGEST 를 그대로 옮겼다 (tag 0.4.0 은 사람용, pull 은 digest 로만).
-- 두 곳이 어긋나면 Worker 가 받은 imageRef 와 허용 이미지가 달라져 Job 이 이미지 확인에서 멈춘다.
--
-- 순서: Worker 가 0.4.0 을 승인한 뒤에 이 마이그레이션이 온다. 반대로 카탈로그가 먼저 옮겨 가면
-- Worker 의 APPROVED_DIGESTS 에 없는 digest 라 모든 Job 이 거부된다. 0.4.0 승인은
-- sys-chore/runtime-0.4.0-verification-release (MR !154) 에서 이미 배포됐다.
--
-- 0.3.0 은 Worker 의 PREVIOUS_RUNTIME_DIGEST 로 남아 있어, 이 마이그레이션 전에 배정된 Job 도 계속 돈다.
-- 이미 완료된 Job 은 result.tar 안에 검증 데이터가 없어 소급되지 않는다 — 패널이 채워지는 것은 새 Job 부터다.
UPDATE workloads
   SET image_digest = 'sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec'
 WHERE slug = 'qlora-sft-v1'
   AND image_repository = 'le3560/gshared-qlora-sft-runtime';
