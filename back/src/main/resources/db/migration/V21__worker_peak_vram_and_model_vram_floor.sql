-- 6 GB GPU(RTX 4050 Laptop)가 Qwen3 0.6B 작업에서도 후보에 오르지 못하는 문제의 Backend 쪽 준비.
--
-- 지금은 VRAM 하한이 workloads.min_vram_mb(8192) 하나뿐이라 모델·설정과 무관하게 모든 Job 이 8 GB 를
-- 요구하고, Worker 가 등록 때 재는 peakVramMb(벤치마크 중 실제로 쓴 VRAM)는 등록 요청에 자리가 없어 버려진다.
-- 두 칸을 만들어 두되 값은 채우지 않는다 — 근거 있는 숫자(실측)가 나오기 전까지는 NULL 이고,
-- NULL 인 동안 Backend 는 지금과 똑같이 workloads.min_vram_mb 를 쓴다(동작 변화 없음).

-- ① Worker 가 벤치마크 중 실제로 쓴 VRAM 최고치. 구버전 exe 는 보내지 않으므로 NULL 허용.
ALTER TABLE workers
    ADD COLUMN benchmark_peak_vram_mb INT NULL AFTER benchmark_tokens_per_sec;

-- ② 모델별 VRAM 하한. 값이 있으면 workloads.min_vram_mb 보다 우선한다(더 정확한 값이라서).
--    실측이 끝나기 전에는 NULL — 값 입력은 AI 쪽 AUTO 상한 조정과 같은 시점에 별도 마이그레이션으로.
ALTER TABLE base_models
    ADD COLUMN min_vram_mb INT NULL AFTER param_count;
