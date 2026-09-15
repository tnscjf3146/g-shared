-- 운영 Normal Job E2E 준비 — 카탈로그를 System 이 검증한 값에 맞춘다.
--
-- ① qlora-sft-v1 Runtime 이미지를 공식값으로 교체.
--    V6 의 image_digest 는 registry 에 push 된 적이 없던 시절의 placeholder(sha256:000…0)이고
--    V10 의 image_repository 'gshared/qlora-sft-runtime' 도 실제 저장소가 아니다. Worker 는
--    승인된 canonical digest 만 실행하므로 이 값으로는 IMAGE_REFUSED 로 끝난다.
--
--    아래 값은 손으로 적은 것이 아니라 system/worker/gshared_worker/runtime_image.py 의
--    CANONICAL_RUNTIME_REPOSITORY / CANONICAL_RUNTIME_DIGEST 를 그대로 옮긴 것이다.
--    (tag 0.2.0 은 사람용이고 pull 에는 쓰지 않으므로 저장하지 않는다.)
--    두 곳이 어긋나면 Worker 가 받은 imageRef 와 Worker 가 허용하는 이미지가 달라지므로,
--    한쪽을 고칠 때 다른 쪽도 고쳐야 한다.
UPDATE workloads
   SET image_repository = 'le3560/gshared-qlora-sft-runtime',
       image_digest     = 'sha256:acda63bc4ad4d7b300c25f28725fed514b045119ac27697cd36f95faae68126d'
 WHERE slug = 'qlora-sft-v1';

-- ② qwen2.5-7b 를 선택 대상에서 내린다.
--    model_revision 이 NULL 이라 Worker 의 immutable model 준비 단계를 통과하지 못하고,
--    설령 revision 을 채워도 Runtime 은 qwen3 계열만 학습한다
--    (system/runtime/qlora-sft/qlora_sft/config.py 의 SUPPORTED_MODEL_TYPE). 임의 revision 을
--    박는 대신 비활성화한다 — 행은 남겨 두어 이 모델을 가리키는 과거 Job 조회는 그대로 된다.
UPDATE base_models
   SET enabled = FALSE
 WHERE slug = 'qwen2.5-7b';
