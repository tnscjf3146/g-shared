# 워크로드 등록 3단계 시연용 예시 데이터셋 생성. 고정 시드라 매번 같은 파일이 나온다(스크린샷 비교 가능).
#   python tools/make_sample_datasets.py  →  tools/sample-datasets/*.jsonl|.json|.csv|.txt
# 개인정보처럼 보이는 값 없음. 문장은 템플릿 조합.
import csv, json, random
from pathlib import Path

OUT = Path(__file__).parent / 'sample-datasets'
OUT.mkdir(exist_ok=True)
rng = random.Random(20260910)

TOPICS = ['배터리 수명', '카메라 화질', '배송 속도', '고객 응대', '가격 대비 성능', '소음', '디스플레이 밝기', '앱 안정성']
POS = ['정말 만족스럽습니다', '기대 이상이었어요', '다시 구매할 생각입니다', '주변에 추천했습니다', '이 가격에 이 정도면 훌륭합니다']
NEG = ['많이 아쉬웠습니다', '기대에 못 미쳤어요', '개선이 필요합니다', '다시 사지는 않을 것 같아요', '설명과 달랐습니다']
Q = ['{t}은(는) 어떤가요?', '{t} 관련해서 궁금한 점이 있어요', '{t}에 대해 간단히 설명해 주세요', '{t}을(를) 개선하는 방법은?']
A = ['{t}은(는) 사용 환경에 따라 차이가 있지만, 일반적인 조건에서는 안정적인 편입니다.',
     '{t}을(를) 볼 때는 실제 사용 시간과 공식 수치를 함께 비교하는 것이 좋습니다.',
     '{t}은(는) 설정 메뉴에서 조정할 수 있고, 기본값은 대부분의 상황에 맞춰져 있습니다.']

# 1) 지시-응답 쌍(JSONL) — 학습(LoRA) 기본 예시. 300줄
with (OUT / 'instruct-300.jsonl').open('w', encoding='utf-8', newline='\n') as f:
    for _ in range(300):
        t = rng.choice(TOPICS)
        f.write(json.dumps({'instruction': rng.choice(Q).format(t=t), 'output': rng.choice(A).format(t=t)}, ensure_ascii=False) + '\n')

# 2) 텍스트-라벨(CSV) — 감성 분류. 헤더 1줄 + 500행
with (OUT / 'sentiment-500.csv').open('w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['text', 'label'])
    for _ in range(500):
        t = rng.choice(TOPICS)
        pos = rng.random() < 0.55
        w.writerow([f'{t}이(가) {rng.choice(POS if pos else NEG)}.', 'positive' if pos else 'negative'])

# 3) 입력 샘플 배열(JSON) — 추론용. 100개
prompts = [{'id': i + 1, 'prompt': rng.choice(Q).format(t=rng.choice(TOPICS))} for i in range(100)]
(OUT / 'inference-inputs-100.json').write_text(json.dumps(prompts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

# 4) 한 줄에 한 문장(TXT) — 200줄
lines = [f'{rng.choice(TOPICS)}이(가) {rng.choice(POS + NEG)}.' for _ in range(200)]
(OUT / 'sentences-200.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')

# 5) 정답 포함 평가셋(JSONL) — 평가용. 150줄
with (OUT / 'eval-150.jsonl').open('w', encoding='utf-8', newline='\n') as f:
    for _ in range(150):
        t = rng.choice(TOPICS)
        f.write(json.dumps({'input': rng.choice(Q).format(t=t), 'reference': rng.choice(A).format(t=t)}, ensure_ascii=False) + '\n')

for p in sorted(OUT.iterdir()):
    print(f'{p.name:28s} {p.stat().st_size:>8,d} bytes')
