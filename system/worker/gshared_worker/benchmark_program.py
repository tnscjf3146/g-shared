"""The fixed training workload every Worker is measured by.

This program is **Worker-owned and constant**. It is handed to the pinned image
with ``python -c`` because the image is a general PyTorch runtime rather than a
benchmark image of our own. No part of it comes from the Backend, from a
supplier, or from anything on the machine: two Workers run byte-identical code,
which is the only reason their numbers can be compared.

**What it measures.** G-shared's first jobs are text fine-tuning -- LoRA/QLoRA
SFT -- so the workload is a fine-tune, not a matrix multiplication. A fixed
decoder-only Transformer is built from code, its base weights are frozen, and
low-rank adapters are trained through a real step: forward, cross-entropy loss,
backward, AdamW. That is the shape of the work this machine is being hired for,
and a GPU that is fast at it is fast at the job.

Building the model in code rather than downloading one is deliberate. There is
no Hugging Face fetch, no dataset, and no network at all: the container runs
with ``--network none``. Weights come from a fixed seed, and the tokens are
synthetic. Nothing about the number depends on how fast this machine's internet
is.

**What is not measured.** Docker start-up, Python import, model construction and
warm-up all happen before the clock starts. Timing is taken with CUDA events
around the measured steps only, so it is GPU time rather than wall-clock time
that happens to include Python.

Every constant below is fixed for all Workers on purpose. Adapting the batch
size or the model to the GPU in front of us would produce numbers that cannot
be compared, which would make the whole measurement useless to the scheduler. A
GPU that cannot run this workload is a benchmark failure, not a smaller run.
"""

from __future__ import annotations

#: What the program prints its result behind. One line, so it survives whatever
#: else the container writes to stdout.
RESULT_MARKER = "GSHARED_BENCHMARK_RESULT="

#: Bumped whenever the workload changes in a way that makes old numbers
#: incomparable to new ones. A result carrying any other version is refused.
BENCHMARK_VERSION = "gshared-benchmark-v1"

#: The workload, fixed. These are duplicated inside the program text below --
#: it runs in another interpreter and cannot import this module -- and the
#: result is checked against them, so the two cannot drift apart unnoticed.
BATCH_SIZE = 4
SEQUENCE_LENGTH = 512
WARMUP_STEPS = 8
MEASURED_STEPS = 60
RUNS = 3

#: Tokens put through the GPU in one measured run. Batch x sequence x steps:
#: exactly the tensor the model was given, with nothing subtracted for padding
#: (there is none) because the GPU did the work either way.
PROCESSED_TOKENS_PER_RUN = BATCH_SIZE * SEQUENCE_LENGTH * MEASURED_STEPS


BENCHMARK_PROGRAM = r'''import json
import math
import statistics
import time

BENCHMARK_VERSION = "gshared-benchmark-v1"
RESULT_MARKER = "GSHARED_BENCHMARK_RESULT="

# The model. A small decoder-only Transformer, GPT-2-small in shape.
VOCAB_SIZE = 32000
D_MODEL = 768
N_LAYERS = 12
N_HEADS = 12
D_FF = 3072
LORA_RANK = 16

# The training step.
BATCH_SIZE = 4
SEQ_LEN = 512
WARMUP_STEPS = 8
MEASURED_STEPS = 60
RUNS = 3
LEARNING_RATE = 1e-4

# Weights and tokens come from here, so every Worker trains the same thing.
SEED = 20250907


class Failed(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def build_model(torch, nn):
    """A fixed Transformer, frozen except for LoRA adapters and norms.

    This is the QLoRA/SFT shape: the base weights do not move, gradients still
    flow the whole way back to reach the adapters, and the optimiser carries
    state for a small fraction of the parameters. Four-bit quantisation is the
    one piece missing -- bitsandbytes is not in the runtime -- so the arithmetic
    is bf16 rather than NF4.
    """
    import torch.nn.functional as F

    class LoRALinear(nn.Module):
        def __init__(self, d_in, d_out):
            super().__init__()
            self.base = nn.Linear(d_in, d_out, bias=False)
            self.base.weight.requires_grad_(False)
            self.a = nn.Parameter(torch.zeros(LORA_RANK, d_in))
            self.b = nn.Parameter(torch.zeros(d_out, LORA_RANK))
            nn.init.normal_(self.a, std=0.02)

        def forward(self, x):
            return self.base(x) + (x @ self.a.T) @ self.b.T

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.n1 = nn.LayerNorm(D_MODEL)
            self.q = LoRALinear(D_MODEL, D_MODEL)
            self.k = nn.Linear(D_MODEL, D_MODEL, bias=False)
            self.v = LoRALinear(D_MODEL, D_MODEL)
            self.o = nn.Linear(D_MODEL, D_MODEL, bias=False)
            self.n2 = nn.LayerNorm(D_MODEL)
            self.up = nn.Linear(D_MODEL, D_FF, bias=False)
            self.down = nn.Linear(D_FF, D_MODEL, bias=False)
            for frozen in (self.k, self.o, self.up, self.down):
                frozen.weight.requires_grad_(False)

        def forward(self, x):
            batch, length, channels = x.shape
            head = channels // N_HEADS
            h = self.n1(x)
            q = self.q(h).view(batch, length, N_HEADS, head).transpose(1, 2)
            k = self.k(h).view(batch, length, N_HEADS, head).transpose(1, 2)
            v = self.v(h).view(batch, length, N_HEADS, head).transpose(1, 2)
            attended = F.scaled_dot_product_attention(q, k, v, is_causal=True)
            x = x + self.o(attended.transpose(1, 2).reshape(batch, length, channels))
            return x + self.down(F.gelu(self.up(self.n2(x))))

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = nn.Embedding(VOCAB_SIZE, D_MODEL)
            self.positions = nn.Embedding(SEQ_LEN, D_MODEL)
            self.blocks = nn.ModuleList([Block() for _ in range(N_LAYERS)])
            self.final_norm = nn.LayerNorm(D_MODEL)
            self.head = nn.Linear(D_MODEL, VOCAB_SIZE, bias=False)
            for frozen in (self.embedding, self.positions, self.head):
                frozen.weight.requires_grad_(False)

        def forward(self, tokens):
            length = tokens.shape[1]
            where = torch.arange(length, device=tokens.device)
            x = self.embedding(tokens) + self.positions(where)[None]
            for block in self.blocks:
                x = block(x)
            return self.head(self.final_norm(x))

    return Model()


def train_step(torch, model, optimiser, tokens, loss_fn):
    """One real fine-tuning step: forward, loss, backward, optimiser."""
    optimiser.zero_grad(set_to_none=True)
    with torch.autocast("cuda", dtype=torch.bfloat16):
        logits = model(tokens[:, :-1])
        loss = loss_fn(
            logits.reshape(-1, VOCAB_SIZE).float(), tokens[:, 1:].reshape(-1))
    loss.backward()
    optimiser.step()


def measure(torch, model, optimiser, tokens, loss_fn):
    """Warm up, then time the measured steps on the GPU's own clock.

    CUDA is asynchronous, so wall-clock around the loop would measure how fast
    Python queued work rather than how fast the GPU did it. CUDA events are
    recorded in the stream and read after a synchronise, which is GPU time.
    """
    for _ in range(WARMUP_STEPS):
        train_step(torch, model, optimiser, tokens, loss_fn)
    torch.cuda.synchronize()

    started = torch.cuda.Event(enable_timing=True)
    finished = torch.cuda.Event(enable_timing=True)
    started.record()
    for _ in range(MEASURED_STEPS):
        train_step(torch, model, optimiser, tokens, loss_fn)
    finished.record()
    torch.cuda.synchronize()

    elapsed = started.elapsed_time(finished) / 1000.0
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise Failed("BENCHMARK_FAILED", "the GPU reported no elapsed time")
    processed = BATCH_SIZE * SEQ_LEN * MEASURED_STEPS
    rate = processed / elapsed
    if not math.isfinite(rate) or rate <= 0:
        raise Failed("BENCHMARK_FAILED", "tokens per second was not a positive number")
    return {"processedTokens": processed,
            "elapsedSeconds": round(elapsed, 6),
            "tokensPerSec": round(rate, 3)}


def run(torch, nn):
    if not bool(torch.cuda.is_available()):
        raise Failed("BENCHMARK_CUDA_UNAVAILABLE",
                     "torch.cuda.is_available() returned false in the container")
    if not bool(torch.cuda.is_bf16_supported()):
        # Not a smaller benchmark: a different one, whose number could not be
        # compared with everybody else's.
        raise Failed("BENCHMARK_GPU_UNSUPPORTED",
                     "this GPU does not support bfloat16, which the workload requires")

    torch.manual_seed(SEED)
    model = build_model(torch, nn).cuda()
    trainable = [p for p in model.parameters() if p.requires_grad]
    if not trainable:
        raise Failed("BENCHMARK_FAILED", "the workload has no trainable parameters")
    optimiser = torch.optim.AdamW(trainable, lr=LEARNING_RATE)
    loss_fn = nn.CrossEntropyLoss()
    tokens = torch.randint(
        0, VOCAB_SIZE, (BATCH_SIZE, SEQ_LEN + 1), device="cuda",
        generator=torch.Generator(device="cuda").manual_seed(SEED))

    runs = [measure(torch, model, optimiser, tokens, loss_fn) for _ in range(RUNS)]
    rates = [entry["tokensPerSec"] for entry in runs]
    # The median rather than the mean: one run slowed by something else on the
    # machine should not drag the answer down with it.
    representative = statistics.median(rates)
    if not math.isfinite(representative) or representative <= 0:
        raise Failed("BENCHMARK_FAILED", "tokens per second was not a positive number")

    return {
        "benchmarkVersion": BENCHMARK_VERSION,
        "status": "OK",
        "tokensPerSec": round(representative, 3),
        "processedTokens": runs[0]["processedTokens"],
        "elapsedSeconds": runs[0]["elapsedSeconds"],
        "batchSize": BATCH_SIZE,
        "sequenceLength": SEQ_LEN,
        "warmupSteps": WARMUP_STEPS,
        "measuredSteps": MEASURED_STEPS,
        "runs": runs,
        "trainableParams": sum(p.numel() for p in trainable),
        "totalParams": sum(p.numel() for p in model.parameters()),
        "peakVramMb": round(torch.cuda.max_memory_allocated() / (1024 * 1024)),
    }


def main():
    try:
        import torch
        import torch.nn as nn
        payload = run(torch, nn)
        exit_code = 0
    except Failed as error:
        payload = {"benchmarkVersion": BENCHMARK_VERSION, "status": "FAILED",
                   "errorCode": error.code, "errorMessage": str(error)}
        exit_code = 20
    except Exception as error:
        payload = {"benchmarkVersion": BENCHMARK_VERSION, "status": "FAILED",
                   "errorCode": "BENCHMARK_FAILED",
                   "errorMessage": ("%s: %s" % (type(error).__name__, error))[:300]}
        exit_code = 21
    print(RESULT_MARKER + json.dumps(payload, separators=(",", ":"), allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
'''
