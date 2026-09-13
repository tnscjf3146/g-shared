-- E2E 에서 실제로 돌릴 모델을 카탈로그에 넣는다.
--
-- 지금까지 base_models 에는 seed 한 줄(qwen2.5-7b)뿐이고 model_revision 이
-- NULL 이었다. Worker 의 model_prep.check_revision 은 NULL 과 "main" 을 모두
-- 거절한다 -- 태그는 움직이고, 값이 매겨져 배정된 Job 이 다음 주에 다른
-- 가중치로 학습되면 안 되기 때문이다. 그래서 NULL 인 카탈로그로는 어떤 Job 도
-- 실행 단계를 통과하지 못한다.
--
-- 아래 두 모델의 revision 은 손으로 적은 값이 아니라
-- system/runtime/qlora-sft/qlora_sft/config.py 의 CATALOG 에 있는 것을 그대로
-- 옮긴 것이다. 그 파일의 주석대로 두 SHA 는 Hugging Face API 에서 읽어 확인된
-- 값이다. 두 곳이 어긋나면 Worker 가 받은 revision 과 Runtime 이 지원한다고
-- 선언한 revision 이 달라지므로, 한쪽을 고칠 때 다른 쪽도 고쳐야 한다.
--
-- 기존 qwen2.5-7b 는 **건드리지 않는다.** 카탈로그에서 무엇을 내릴지는 서비스
-- 정책이지 이 마이그레이션이 정할 일이 아니다. 다만 알려진 사실 하나는 남긴다:
-- Runtime 은 qwen3 계열만 학습한다(config.py 의 SUPPORTED_MODEL_TYPE). Qwen2.5
-- 로 배정된 Job 은 model_revision 이 NULL 이라 모델 준비 단계에서, 설령 채워도
-- 컨테이너의 아키텍처 검사에서 거절된다. 내릴지 말지는 담당자가 정한다.
--
-- image_digest 도 건드리지 않는다. 아직 registry 에 push 된 적이 없어 진짜
-- digest 가 존재하지 않고, placeholder 를 진짜처럼 바꿔 두면 Worker 의
-- immutable digest 검증이 통과해 버린다. 그 검증은 켜 둔 채로 남긴다.

-- ── Qwen3 0.6B — Quick Demo ──────────────────────────────────────────
INSERT INTO base_models (slug, workload_id, display_name, model_ref, model_revision,
                         param_count, source_uri, enabled, created_at, updated_at)
SELECT 'qwen3-0.6b', workload_id, 'Qwen3 0.6B',
       'Qwen/Qwen3-0.6B',
       'c1899de289a04d12100db370d81485cdf75e47ca',
       600000000,
       'https://huggingface.co/Qwen/Qwen3-0.6B',
       TRUE, NOW(6), NOW(6)
FROM workloads
WHERE slug = 'qlora-sft-v1'
  AND NOT EXISTS (SELECT 1 FROM (SELECT slug FROM base_models) AS existing
                  WHERE existing.slug = 'qwen3-0.6b');

-- ── Qwen3 1.7B — Standard ────────────────────────────────────────────
INSERT INTO base_models (slug, workload_id, display_name, model_ref, model_revision,
                         param_count, source_uri, enabled, created_at, updated_at)
SELECT 'qwen3-1.7b', workload_id, 'Qwen3 1.7B',
       'Qwen/Qwen3-1.7B',
       '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
       1700000000,
       'https://huggingface.co/Qwen/Qwen3-1.7B',
       TRUE, NOW(6), NOW(6)
FROM workloads
WHERE slug = 'qlora-sft-v1'
  AND NOT EXISTS (SELECT 1 FROM (SELECT slug FROM base_models) AS existing
                  WHERE existing.slug = 'qwen3-1.7b');
