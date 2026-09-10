-- 카탈로그 화면 표시용 필드.
-- 지금까지 workloads 는 Scheduler 에 넘길 값(image_digest·min_vram_mb)만 갖고 있었다.
-- 사용자가 메뉴판에서 무엇을 고르는지는 이름 하나로 알 수 없어, 유형과 설명을 더한다.

ALTER TABLE workloads
    ADD COLUMN kind        VARCHAR(20)  NOT NULL DEFAULT 'TRAIN_LORA' AFTER slug,
    ADD COLUMN description VARCHAR(255) NULL                          AFTER name;

-- seed 보정 — 화면에 그대로 나가는 문구다.
UPDATE workloads
   SET description = '준비된 데이터셋으로 베이스 모델에 LoRA 어댑터를 학습합니다.'
 WHERE slug = 'qlora-sft-v1';

-- 모델 규모. 화면은 "7B" 처럼 사람이 읽는 문자열로 보여주므로 원본 수치를 채워 둔다
-- (V8 seed 에서 비워 두었던 값).
UPDATE base_models
   SET param_count = 7000000000
 WHERE slug = 'qwen2.5-7b';
