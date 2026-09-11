"""Same prompt, base model and base model + adapter, side by side.

This answers one question and does not pretend to answer a bigger one: **did
the adapter actually load, and did training change anything?** A job that
finishes cleanly and writes a well-formed adapter file that has no effect on
any output is a failure the exit code cannot see.

What this is not: a quality measure. There is no held-out set, no score and no
claim that the adapter is *good*. Two generations printed next to each other
are evidence that the weights are doing something, and are useful on stage for
exactly that reason. Anyone reading a number into it is reading something that
is not there.

    python tools/validate_adapter.py \\
        --model-path /job/model --adapter-path /job/output

Development only. Not in the image; a job never runs this.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qlora_sft.dataset import PROMPT_TEMPLATE  # noqa: E402

DEFAULT_PROMPTS = [
    "서비스 이용이 끝나면 결과는 어디서 확인하나요?",
    "데이터셋은 어떤 형식으로 준비하나요?",
    "학습 결과물은 어떤 형태인가요?",
]


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--adapter-path", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=96)
    parser.add_argument("--prompt", action="append", default=None)
    args = parser.parse_args(argv)

    import torch
    from peft import PeftModel
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig)

    from qlora_sft.train import DOUBLE_QUANT, QUANT_TYPE

    prompts = args.prompt or DEFAULT_PROMPTS
    dtype = (torch.bfloat16 if torch.cuda.is_bf16_supported()
             else torch.float16)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model_path, local_files_only=True)
    base = AutoModelForCausalLM.from_pretrained(
        args.model_path,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type=QUANT_TYPE,
            bnb_4bit_use_double_quant=DOUBLE_QUANT,
            bnb_4bit_compute_dtype=dtype),
        dtype=dtype, local_files_only=True, device_map={"": 0})

    # Greedy, so a difference between the two columns is the adapter rather
    # than the sampler having a different day.
    def answer(model, prompt: str) -> str:
        text = PROMPT_TEMPLATE.format(prompt=prompt)
        encoded = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **encoded, max_new_tokens=args.max_new_tokens,
                do_sample=False, temperature=None, top_p=None, top_k=None,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
        return tokenizer.decode(
            out[0][encoded["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()

    before = [answer(base, prompt) for prompt in prompts]

    tuned = PeftModel.from_pretrained(base, args.adapter_path)
    tuned.eval()
    after = [answer(tuned, prompt) for prompt in prompts]

    changed = 0
    for prompt, was, now in zip(prompts, before, after):
        print("=" * 72)
        print("PROMPT       :", prompt)
        print("BASE         :", was)
        print("BASE+ADAPTER :", now)
        if was != now:
            changed += 1
    print("=" * 72)
    print(f"adapter loaded: yes ({args.adapter_path})")
    print(f"outputs changed: {changed}/{len(prompts)}")
    # Identical output on every prompt does not prove the adapter is broken,
    # but it is the one result worth failing on: it is what an adapter
    # attached to nothing looks like.
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
