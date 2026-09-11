"""Builds the demo and standard datasets, the same way every time.

Committing five thousand JSONL lines would put a generated file under review
and make its diffs unreadable. What is committed instead is this generator and
one small fixture beside it (``fixtures/demo-sample.jsonl``), so a reviewer can
see the shape without scrolling past it, and anyone can rebuild the full file
byte-for-byte from a seed.

**The content is synthetic and ours.** Every sentence below was written for
this repository. Nothing is scraped, nothing is copied from a support desk
that exists, and no real person or company appears -- G-shared answering
questions about itself is a subject we own outright, and it has the property
the demo actually needs: an untrained Qwen3 answers these questions in
generic, hedging paragraphs, and a trained one answers in the house style, so
the before/after in ``validate_adapter.py`` is visible to an audience from the
back of a room.

Sample counts are arguments, not constants. How many samples make a two-minute
run is a fact about a particular GPU and has to be measured on it -- see
README.md for what this machine measured and what was chosen from it.

    python tools/make_dataset.py demo     --out /tmp/demo.jsonl
    python tools/make_dataset.py standard --out /tmp/standard.jsonl

This script is development-only. It is not in the runtime image, it is never
run inside a job, and the container it feeds never learns it exists.
"""

from __future__ import annotations

import argparse
import io
import json
import random
from pathlib import Path

#: Fixed, so "the demo dataset" names one file rather than a family of them.
DEFAULT_SEED = 20260910

#: Both numbers were measured on the test GPU, not guessed. They are correct
#: for **an RTX 4070 Laptop and nothing else** -- a different card moves both,
#: which is why they are defaults rather than constants in the runtime.
#:
#: DEMO_SAMPLES, Qwen3-0.6B + SPEED (1 epoch, batch 16):
#:     240  samples ->  15 steps ->  33s   (too short to show anything)
#:     1440 samples ->  90 steps -> 268s = 4.5 min   <- measured, in the 2-5 min target
#:
#: STANDARD_SAMPLES, Qwen3-1.7B + BALANCED (3 epochs, batch 8), from the
#: calibration in README.md at 3.597 s/step:
#:     2400 samples -> 300 steps/epoch -> 900 steps -> ~54 min estimated
#: The step count is round on purpose: 900 steps at the measured rate sits in
#: the middle of the 45-75 min band rather than against one of its edges.
#: This one is an **estimate**. No full run of it has been done.
DEMO_SAMPLES = 1440
STANDARD_SAMPLES = 2400

#: Things a customer asks G-shared, paired with the answer in the style the
#: adapter is being taught. Kept as (subject, question forms, answer) so a few
#: dozen hand-written facts become a few thousand distinct samples without any
#: of them being nonsense.
TOPICS = [
    (
        "결과 확인",
        ["서비스 이용이 끝나면 결과는 어디서 확인하나요?",
         "학습이 완료되면 결과물을 어떻게 받나요?",
         "작업 결과는 어디에 저장되나요?"],
        "작업 완료 후 결과 페이지에서 학습된 Adapter 파일을 확인할 수 있습니다.",
    ),
    (
        "결과물 형식",
        ["학습 결과물은 어떤 형태인가요?",
         "받게 되는 파일은 무엇인가요?",
         "모델 전체를 받는 건가요?"],
        "결과물은 LoRA Adapter입니다. 베이스 모델 전체가 아니라 학습된 어댑터만 전달되므로, "
        "베이스 모델과 함께 불러와 사용하시면 됩니다.",
    ),
    (
        "소요 시간",
        ["학습에 시간이 얼마나 걸리나요?",
         "작업이 끝나기까지 얼마나 기다려야 하나요?",
         "예상 소요 시간을 알 수 있나요?"],
        "소요 시간은 선택하신 모델과 데이터셋 크기, 학습 설정에 따라 달라집니다. "
        "작업 생성 화면의 예상 시간을 참고해 주세요.",
    ),
    (
        "데이터셋 형식",
        ["데이터셋은 어떤 형식으로 준비하나요?",
         "학습 데이터 파일 형식이 궁금합니다.",
         "어떤 파일을 올려야 하나요?"],
        "데이터셋은 JSONL 형식으로 준비해 주세요. 한 줄에 instruction, input, output "
        "세 필드를 가진 JSON 객체를 하나씩 넣으시면 됩니다.",
    ),
    (
        "빈 입력",
        ["input 필드는 비워도 되나요?",
         "추가 입력이 없는 데이터는 어떻게 하나요?",
         "input을 꼭 채워야 하나요?"],
        "input 필드는 비워 두셔도 됩니다. 지시문만으로 충분한 데이터라면 빈 문자열로 두시면 됩니다.",
    ),
    (
        "학습 모드",
        ["학습 모드는 어떻게 고르나요?",
         "SPEED와 BALANCED는 무엇이 다른가요?",
         "자동 설정과 직접 설정 중 어느 쪽이 좋나요?"],
        "빠른 확인이 필요하시면 SPEED, 품질과 시간의 균형을 원하시면 BALANCED를 권해 드립니다. "
        "직접 값을 정하고 싶으시면 CUSTOM에서 설정하실 수 있습니다.",
    ),
    (
        "요금",
        ["요금은 어떻게 계산되나요?",
         "비용은 무엇을 기준으로 정해지나요?",
         "결제 금액은 언제 확정되나요?"],
        "요금은 작업 생성 시 산정된 견적을 기준으로 하며, 작업 시작 전에 에스크로에 예치됩니다. "
        "정확한 금액은 견적 화면에서 미리 확인하실 수 있습니다.",
    ),
    (
        "GPU 선택",
        ["어떤 GPU에서 학습되나요?",
         "GPU를 직접 고를 수 있나요?",
         "GPU는 어떻게 배정되나요?"],
        "학습에 필요한 VRAM 조건을 만족하는 GPU 중에서 자동으로 배정됩니다. "
        "추천된 후보는 작업 생성 화면에서 확인하실 수 있습니다.",
    ),
    (
        "작업 실패",
        ["학습이 실패하면 어떻게 되나요?",
         "작업이 중간에 멈추면 요금은 어떻게 되나요?",
         "실패한 작업도 결제되나요?"],
        "작업이 실패하면 결과물은 생성되지 않으며, 예치된 금액은 정산 정책에 따라 처리됩니다. "
        "실패 사유는 작업 상세 화면에서 확인하실 수 있습니다.",
    ),
    (
        "지원 모델",
        ["어떤 베이스 모델을 지원하나요?",
         "선택할 수 있는 모델이 무엇인가요?",
         "모델 목록은 어디서 보나요?"],
        "현재는 Qwen3 계열의 텍스트 모델을 지원합니다. 선택 가능한 모델은 작업 생성 화면의 "
        "모델 목록에서 확인하실 수 있습니다.",
    ),
    (
        "진행 상황",
        ["학습 진행 상황을 볼 수 있나요?",
         "지금 몇 퍼센트나 진행됐는지 알 수 있나요?",
         "작업이 잘 돌아가고 있는지 확인하고 싶습니다."],
        "작업 상세 화면에서 진행률을 확인하실 수 있습니다. 진행률은 전체 학습 단계 중 "
        "완료된 단계를 기준으로 표시됩니다.",
    ),
    (
        "데이터 보안",
        ["업로드한 데이터는 안전한가요?",
         "제 데이터가 다른 곳에 쓰이나요?",
         "학습이 끝나면 데이터는 어떻게 되나요?"],
        "업로드하신 데이터는 해당 작업의 학습에만 사용됩니다. 학습 컨테이너는 외부 네트워크가 "
        "차단된 상태로 실행되며, 작업이 끝나면 작업 공간은 정리됩니다.",
    ),
    (
        "GPU 제공",
        ["제 GPU를 빌려주려면 어떻게 하나요?",
         "GPU 공급자로 참여하고 싶습니다.",
         "유휴 GPU를 등록하는 방법이 궁금합니다."],
        "Worker 프로그램을 설치하고 실행하시면 GPU가 등록됩니다. 등록 시 성능 측정이 한 번 "
        "수행되며, 이후 유휴 상태일 때 작업이 배정됩니다.",
    ),
    (
        "동시 작업",
        ["한 번에 여러 작업을 돌릴 수 있나요?",
         "작업을 두 개 동시에 넣어도 되나요?",
         "GPU 하나에 작업이 몇 개 들어가나요?"],
        "GPU 한 대에는 한 번에 한 작업만 배정됩니다. 여러 작업을 만드시면 순차적으로 처리됩니다.",
    ),
    (
        "어댑터 사용법",
        ["받은 Adapter는 어떻게 쓰나요?",
         "학습된 어댑터를 적용하는 방법이 궁금합니다.",
         "결과 파일을 어떻게 불러오나요?"],
        "내려받으신 Adapter는 학습에 사용한 베이스 모델 위에 함께 불러와 사용하시면 됩니다. "
        "베이스 모델은 작업 상세 화면에 표시된 것과 같은 버전을 사용해 주세요.",
    ),
]

#: Optional context lines. Roughly a third of the samples carry one, so the
#: adapter sees both shapes of the template rather than only the empty-input
#: one -- a dataset where ``input`` is always blank teaches the model that the
#: field does not exist.
CONTEXTS = [
    "",
    "",
    "",
    "작업 상태: 진행 중",
    "작업 상태: 완료",
    "선택한 모델: Qwen3 0.6B",
    "선택한 모델: Qwen3 1.7B",
    "학습 모드: SPEED",
    "학습 모드: BALANCED",
    "데이터셋: 1,440건",
    "결제 수단: 지갑 연결됨",
]

#: Politeness prefixes. Real questions do not arrive stripped of manners, and
#: an adapter trained only on bare questions answers oddly when one arrives
#: with a greeting attached.
OPENERS = ["", "", "안녕하세요. ", "문의드립니다. ", "궁금한 점이 있습니다. "]

#: Closings on the answer side, so the house style has some variation in it
#: rather than one memorised final sentence.
CLOSINGS = [
    "",
    "",
    " 추가로 궁금한 점이 있으시면 언제든 문의해 주세요.",
    " 도움이 필요하시면 알려 주세요.",
]


def build(count: int, seed: int = DEFAULT_SEED) -> list:
    """``count`` samples, deterministic for a given ``(count, seed)``.

    The topics are cycled rather than sampled so every subject appears the
    same number of times whatever the count; only the phrasing around them is
    drawn from the random number generator. Sampling topics randomly would
    leave a small dataset lopsided, and "the demo taught it four of the
    fifteen answers" is a bad thing to discover on stage.
    """
    rng = random.Random(seed)
    samples = []
    for index in range(count):
        subject, questions, answer = TOPICS[index % len(TOPICS)]
        question = rng.choice(questions)
        opener = rng.choice(OPENERS)
        closing = rng.choice(CLOSINGS)
        samples.append({
            "instruction": opener + question,
            "input": rng.choice(CONTEXTS),
            "output": answer + closing,
        })
    return samples


def write(path: Path, samples: list) -> None:
    lines = [json.dumps(sample, ensure_ascii=False, sort_keys=True)
             for sample in samples]
    # newline="" so the separator stays LF on every platform. Written from
    # Windows with the default, this file would carry CRLF -- and then a given
    # (count, seed) would produce two different files depending on who ran the
    # generator, which is the one promise it makes.
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", choices=("demo", "standard", "smoke"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    default = {"demo": DEMO_SAMPLES, "standard": STANDARD_SAMPLES, "smoke": 16}
    count = args.count if args.count is not None else default[args.profile]
    samples = build(count, args.seed)
    write(Path(args.out), samples)
    print(f"{len(samples)} samples -> {args.out} (seed {args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
