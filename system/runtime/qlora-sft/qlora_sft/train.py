"""The one QLoRA runner. Both supported models go through this file.

Everything heavy is imported inside a function. That is not tidiness: the
modules beside this one are unit-tested on the host's Python, which has no
torch and never will, and a top-level ``import torch`` would take the dataset
parser and the event contract down with it.

The order below is deliberate, and each step is cheap before it is expensive:

1. the GPU, because there is no CPU fallback and finding that out after
   tokenising a customer's dataset helps nobody;
2. the config and the dataset, which are pure parsing;
3. the model directory's own ``config.json``;
4. only then, weights.

**No CPU fallback.** A container that quietly trained on a CPU would take a
day to do what it promised in an hour, and the supplier's GPU -- the thing
being paid for -- would sit idle. Missing CUDA is CUDA_UNAVAILABLE.

**No config rescue.** An out-of-memory failure exits CUDA_OOM. It does not
halve the batch size and try again. The numbers may be a customer's own, and
choosing them is the AI Scheduler's job.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .adapter import check_adapter_directory
from .cli import steps_for
from .config import check_model_directory
from .dataset import read_dataset
from .events import EventWriter
from .failures import ExitCode, TrainingFailure
from .verification import (compare_lora_parameters, loss_history,
                           snapshot_lora_parameters, verification_report)

#: 4-bit NF4 with double quantisation -- the configuration QLoRA describes.
QUANT_TYPE = "nf4"
DOUBLE_QUANT = True

#: LoRA. Rank 16 with alpha 32 is the common QLoRA starting point, and it is
#: fixed here rather than exposed, because the Backend's trainingConfig has no
#: field for it and inventing one would be a contract change.
LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05

#: PEFT finds every linear layer in the loaded model, including the 4-bit
#: ones, and leaves ``lm_head`` alone. What it found is checked against the
#: model actually loaded (see :func:`_attach_adapters`) rather than trusted --
#: a hard-coded list of projection names copied from another architecture is
#: the classic way to train nothing at all and still finish successfully.
#:
#: On Qwen3 with PEFT 0.20 this resolves to the seven projections the
#: architecture actually has -- q_proj, k_proj, v_proj, o_proj, gate_proj,
#: up_proj, down_proj -- which was read back out of a written
#: ``adapter_config.json`` rather than assumed.
LORA_TARGETS = "all-linear"

#: PEFT writes a Hugging Face model card next to the adapter. It is removed
#: from a job's output: it is not part of the result a customer asked for, and
#: it records ``base_model: /job/model``, which is this container's own mount
#: path and says nothing true about which model was trained.
MODEL_CARD_FILE = "README.md"

#: Files that would mean the whole base model had been copied into the job's
#: output. The result of a job is an adapter; a customer paying for one should
#: not be handed gigabytes of weights they already had.
BASE_WEIGHT_FILES = (
    "model.safetensors", "pytorch_model.bin", "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
)

METRICS_FILE = "training_metrics.json"


def run(request, events=None) -> dict:
    """Train one adapter. Returns the metrics it wrote."""
    events = events or EventWriter()
    torch = _torch()
    _require_gpu(torch)

    samples = read_dataset(request.dataset_path)
    model_config = check_model_directory(request.model_path)
    # Checked with the rest of the inputs, before any weights: a job that
    # brought a broken adapter should not spend a model load finding out.
    adapter_path = getattr(request, "adapter_path", None)
    init_adapter = (check_adapter_directory(adapter_path)
                    if adapter_path is not None else None)
    total_steps = steps_for(len(samples), request.config, request.max_steps)

    events.training_started(
        total_steps=total_steps,
        sampleCount=len(samples),
        epochs=request.config.epochs,
        batchSize=request.config.batch_size,
        maxLength=request.config.max_length,
        learningRate=request.config.learning_rate,
        modelType=model_config.get("model_type"),
        gpuName=torch.cuda.get_device_name(0),
        computeDtype=_compute_dtype_name(torch),
        initAdapter=init_adapter is not None,
    )

    tokenizer = _tokenizer(request.model_path)
    encoded = [_encode(tokenizer, sample, request.config.max_length)
               for sample in samples]

    started = time.monotonic()
    model = _load_quantized(torch, request.model_path)
    model = _attach_adapters(model, request.config, init_adapter)
    # The LoRA weights as they are before the first optimizer step, so the
    # result can be compared with them. CPU copies of the adapters only.
    baseline = snapshot_lora_parameters(model)
    metrics = _train(torch, model, tokenizer, encoded, request,
                     total_steps, events)
    metrics["verification"] = verification_report(
        compare_lora_parameters(baseline, model))
    del baseline
    _save(model, request.output_dir)

    # The model is dropped before the adapter is loaded back, so the check
    # happens with the GPU in the state a fresh process would find it in
    # rather than with everything still resident.
    del model
    torch.cuda.empty_cache()
    _check_output(torch, request)

    metrics["runtimeSec"] = round(time.monotonic() - started, 3)
    metrics["sampleCount"] = len(samples)
    metrics["totalSteps"] = total_steps
    metrics["gpuName"] = torch.cuda.get_device_name(0)
    metrics["trainingConfig"] = asdict(request.config)
    # The files that were loaded, by hash. Null means a fresh adapter -- the
    # result then proves it did not start from anything.
    metrics["initAdapter"] = (init_adapter.describe()
                              if init_adapter is not None else None)
    _write_metrics(request.output_dir, metrics)
    events.training_completed(
        runtime_sec=metrics["runtimeSec"],
        trainLoss=metrics.get("trainLoss"),
        totalSteps=total_steps,
        peakVramMb=metrics.get("peakVramMb"),
    )
    return metrics


def _torch():
    try:
        import torch
    except ImportError:
        raise TrainingFailure(
            ExitCode.CUDA_UNAVAILABLE,
            "this runtime image has no working torch installation") from None
    return torch


def _require_gpu(torch) -> None:
    """An NVIDIA GPU, or stop. There is no second-best answer here."""
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise TrainingFailure(
            ExitCode.CUDA_UNAVAILABLE,
            "no CUDA device is available to this container; this runtime "
            "does not train on a CPU")


def _compute_dtype(torch):
    """bfloat16 where the card supports it, float16 otherwise.

    Ada and Ampere do bf16 in hardware, and it has the exponent range that
    makes a loss spike survivable. Older cards get fp16, which trains the same
    model to the same shape with a narrower range.
    """
    return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16


def _compute_dtype_name(torch) -> str:
    return "bfloat16" if torch.cuda.is_bf16_supported() else "float16"


def _tokenizer(model_path):
    from transformers import AutoTokenizer

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(model_path), local_files_only=True)
    except Exception:
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the tokenizer in the model directory could not be loaded") from None
    if tokenizer.pad_token_id is None:
        # Qwen3 has a pad token; this is for a directory that does not, and
        # padding with EOS is the usual answer. Padded positions are masked
        # out of the loss either way.
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def _encode(tokenizer, sample, max_length: int) -> dict:
    """One sample as ids and labels, with the prompt masked out of the loss.

    Truncation is from the right and applies to the whole sequence. A sample
    whose prompt alone fills ``max_length`` ends up with no supervised tokens,
    which is a property of that sample rather than an error -- it is kept, and
    its contribution to the loss is nothing.
    """
    prompt_ids = tokenizer(
        sample.prompt_text(), add_special_tokens=False)["input_ids"]
    completion_ids = tokenizer(
        sample.completion_text(), add_special_tokens=False)["input_ids"]

    input_ids = (prompt_ids + completion_ids)[:max_length]
    labels = ([-100] * len(prompt_ids) + completion_ids)[:max_length]
    return {"input_ids": input_ids, "labels": labels}


def _collate(pad_id: int):
    """Pad a batch to its own longest sequence, not to ``max_length``.

    Padding every batch out to the cap would put the same number of tokens
    through the GPU whatever the data looked like -- simpler to reason about,
    and several times slower on a dataset of short answers.
    """
    import torch

    def collate(batch: list) -> dict:
        width = max(len(item["input_ids"]) for item in batch)
        input_ids, labels, attention = [], [], []
        for item in batch:
            padding = width - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [pad_id] * padding)
            labels.append(item["labels"] + [-100] * padding)
            attention.append([1] * len(item["input_ids"]) + [0] * padding)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }

    return collate


def _load_quantized(torch, model_path):
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type=QUANT_TYPE,
        bnb_4bit_use_double_quant=DOUBLE_QUANT,
        bnb_4bit_compute_dtype=_compute_dtype(torch),
    )
    try:
        model = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            quantization_config=quantization,
            dtype=_compute_dtype(torch),
            local_files_only=True,
            device_map={"": 0},
        )
    except torch.cuda.OutOfMemoryError:
        raise TrainingFailure(
            ExitCode.CUDA_OOM,
            "the GPU ran out of memory while loading the model") from None
    except Exception:
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the model weights could not be loaded") from None
    model.config.use_cache = False
    return model


def _attach_adapters(model, config, init_adapter=None):
    """4-bit base, trainable low-rank adapters on every linear layer.

    With ``init_adapter`` the adapters are the job's own, loaded trainable,
    and keep that adapter's rank and targets -- continuing training means
    continuing *that* adapter, not a new one shaped like it. Without it they
    are freshly initialised, exactly as before.
    """
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=config.gradient_checkpointing)
    if init_adapter is not None:
        model = _load_init_adapter(model, init_adapter)
    else:
        model = get_peft_model(model, LoraConfig(
            r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=LORA_DROPOUT,
            bias="none", task_type="CAUSAL_LM", target_modules=LORA_TARGETS))

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if trainable == 0:
        # An adapter attached to nothing trains successfully and produces a
        # file that changes no output. Better to stop.
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the LoRA adapters attached to no layer of this model")
    return model


def _load_init_adapter(model, init_adapter):
    """The job's adapter, attached trainable, or INVALID_INPUT.

    PEFT refuses an adapter whose shapes or target modules do not exist in
    this base model -- which is how an adapter trained on a different model
    shows up. That is the job's input being wrong, not training failing.
    """
    import torch
    from peft import PeftModel

    try:
        return PeftModel.from_pretrained(
            model, str(init_adapter.path), is_trainable=True)
    except torch.cuda.OutOfMemoryError:
        raise TrainingFailure(
            ExitCode.CUDA_OOM,
            "the GPU ran out of memory while loading the initial adapter") from None
    except Exception as error:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            "the initial adapter does not fit this base model ("
            + type(error).__name__ + ")") from None


def _train(torch, model, tokenizer, encoded, request, total_steps, events) -> dict:
    from transformers import Trainer, TrainingArguments

    config = request.config
    # The Trainer wants a directory of its own for checkpoints. It is pointed
    # at scratch space rather than at the job's output, because `save_strategy
    # ="no"` means nothing is ever written there and an empty `_trainer/`
    # folder in a customer's result is a thing they would have to ask about.
    arguments = TrainingArguments(
        output_dir=tempfile.mkdtemp(prefix="qlora-trainer-"),
        num_train_epochs=config.epochs,
        max_steps=request.max_steps if request.max_steps else -1,
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        seed=config.seed,
        data_seed=config.seed,
        gradient_checkpointing=config.gradient_checkpointing,
        bf16=_compute_dtype(torch) is torch.bfloat16,
        fp16=_compute_dtype(torch) is torch.float16,
        optim="paged_adamw_8bit",
        logging_steps=1,
        save_strategy="no",
        report_to=[],
        disable_tqdm=True,
        dataloader_pin_memory=False,
    )
    trainer = Trainer(
        model=model,
        args=arguments,
        train_dataset=encoded,
        data_collator=_collate(tokenizer.pad_token_id),
        callbacks=[ProgressEvents(events, total_steps)],
    )
    torch.cuda.reset_peak_memory_stats()
    try:
        result = trainer.train()
    except torch.cuda.OutOfMemoryError:
        raise TrainingFailure(
            ExitCode.CUDA_OOM,
            "the GPU ran out of memory during training; the training config "
            "was not changed and nothing was retried") from None
    except TrainingFailure:
        raise
    except Exception as error:
        # The type name only. A message from deep inside a training stack can
        # carry a path, and this stream is read by whoever owns the GPU.
        raise TrainingFailure(
            ExitCode.TRAINING_FAILED,
            "training failed (" + type(error).__name__ + ")") from None

    elapsed = result.metrics.get("train_runtime") or 0.0
    return {
        "trainLoss": round(float(result.training_loss), 6),
        "trainingRuntimeSec": round(float(elapsed), 3),
        "stepsPerSecond": round(float(
            result.metrics.get("train_steps_per_second") or 0.0), 4),
        "samplesPerSecond": round(float(
            result.metrics.get("train_samples_per_second") or 0.0), 4),
        "peakVramMb": int(torch.cuda.max_memory_allocated() // (1024 * 1024)),
        "peakVramReservedMb": int(
            torch.cuda.max_memory_reserved() // (1024 * 1024)),
        **_loss_curve(trainer),
    }


def _loss_curve(trainer) -> dict:
    """``lossHistory`` and ``finalLoss`` from the Trainer's own log history.

    A chart is not worth a failed job: if the history cannot be read, the run
    still succeeds and the curve is simply empty. ``trainLoss`` keeps its
    meaning -- the Trainer's average over the run -- and ``finalLoss`` is the
    loss at the last logged optimizer step, which is a different number.
    """
    try:
        history = loss_history(getattr(trainer.state, "log_history", None))
    except Exception:
        history = []
    curve = {"lossHistory": history}
    if history:
        curve["finalLoss"] = history[-1]["loss"]
    return curve


class ProgressEvents:
    """Turns the Trainer's step counter into the stdout event contract.

    A plain object with the hooks on it rather than a ``TrainerCallback``
    subclass, because that base class cannot be imported until transformers is
    present -- which, in the unit tests, it is not. The Trainer calls hooks by
    name and does not check the type, so this works and stays testable.

    **Why the loss comes from ``on_log`` and not ``on_step_end``.** The Trainer
    runs ``on_step_end`` first and logs afterwards, so a callback that emitted
    on ``on_step_end`` and read whatever loss it had seen would attach step
    N-1's loss to step N. The first smoke run did exactly that: step 2 carried
    step 1's number, and step 1 carried none. A progress event with somebody
    else's loss in it is worse than one with no loss, because it looks right.

    So the log hook is the emitter whenever it has a loss, and ``on_step_end``
    only covers the steps it did not -- which, with ``logging_steps=1``, is
    none of them, but the run is not allowed to silently stop reporting
    progress if that argument ever changes.
    """

    def __init__(self, events, total_steps: int) -> None:
        self._events = events
        self._total = total_steps
        self._reported_through = 0
        #: Monotonic, never the wall clock: a training run measured with
        #: ``time.time()`` changes length when the host adjusts its clock.
        self._started = time.monotonic()

    def __getattr__(self, name):
        # Every hook the Trainer calls that this class does not implement.
        if name.startswith("on_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def on_train_begin(self, args, state, control, **kwargs):
        # The clock the elapsed time is measured from. Set again here because
        # this object is built a moment before ``trainer.train()`` and the
        # setup in between -- dataloaders, the optimiser -- is not training.
        self._started = time.monotonic()

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs and "loss" in logs:
            self._emit(args, state, float(logs["loss"]))

    def on_step_end(self, args, state, control, **kwargs):
        # Only when logging will not cover this step. The hooks run in the
        # order on_step_end, on_log -- so emitting here unconditionally would
        # publish a null-loss event and then a second one for the same step.
        if getattr(args, "logging_steps", 0) != 1:
            self._emit(args, state, None)

    def _emit(self, args, state, loss) -> None:
        step = int(state.global_step)
        if step <= self._reported_through:
            return
        # ``state.max_steps`` is what the Trainer will actually do; ours is
        # what the reader was told before the run. They agree, and the
        # Trainer's is the one that cannot drift.
        total = int(state.max_steps or self._total)
        whole, of_many = _epoch_numbers(args, state)
        self._events.step(step=step, total_steps=total,
                          epoch=float(state.epoch or 0.0), loss=loss,
                          current_epoch=whole, total_epochs=of_many,
                          elapsed_sec=round(time.monotonic() - self._started, 3))
        self._reported_through = step


def _epoch_numbers(args, state) -> tuple[int | None, int | None]:
    """Which epoch this is, out of how many, from the Trainer's own state.

    ``state.epoch`` is fractional -- 0.0 through ``num_train_epochs`` -- and
    the Backend's column is an integer, so one has to become the other
    somewhere. It is done here, with the Trainer's number in hand, rather
    than on the Worker, which would have to divide the step count by a guess
    at how many steps an epoch holds. That guess is wrong whenever gradient
    accumulation or a short final batch changes the count, and it is wrong
    silently.

    The count is 1-based: during the first pass over the data this reports
    epoch 1, not 0. At the very end ``state.epoch`` reaches the total exactly,
    which would name an epoch that does not exist, so the last one is held.

    Returns ``(None, None)`` when the Trainer did not say -- ``max_steps``
    runs have no meaningful epoch total, and a fabricated one is worse than
    an absent one.
    """
    total = getattr(args, "num_train_epochs", None)
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return None, None
    total = int(total)
    if total <= 0:
        return None, None
    position = float(state.epoch or 0.0)
    if position < 0:
        return None, None
    return min(int(position) + 1, total), total


def _save(model, output_dir) -> None:
    """Write the adapter, and only the adapter.

    ``save_pretrained`` also leaves a model card behind. The job's output
    directory is handed to a customer, so what is in it should be what they
    paid for: two adapter files and the metrics. The card is neither, and it
    quotes a path that only exists inside this container.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output), safe_serialization=True)
    card = output / MODEL_CARD_FILE
    if card.is_file():
        card.unlink()


def _check_output(torch, request) -> None:
    """The result is an adapter, and it loads. Both are checked here, once.

    Loading it back costs a model load. It is worth it: the alternative is a
    job that reports success and hands a customer a file that turns out to be
    unusable, which is discovered by the customer.
    """
    output = Path(request.output_dir)
    for required in ("adapter_config.json", "adapter_model.safetensors"):
        if not (output / required).is_file():
            raise TrainingFailure(
                ExitCode.OUTPUT_VALIDATION_FAILED,
                "training finished without writing " + required)
    for forbidden in BASE_WEIGHT_FILES:
        if (output / forbidden).is_file():
            raise TrainingFailure(
                ExitCode.OUTPUT_VALIDATION_FAILED,
                "the output holds " + forbidden + ", which is the base model "
                "rather than an adapter")

    from peft import PeftModel

    try:
        base = _load_quantized(torch, request.model_path)
        reloaded = PeftModel.from_pretrained(base, str(output))
    except TrainingFailure:
        raise
    except Exception:
        raise TrainingFailure(
            ExitCode.OUTPUT_VALIDATION_FAILED,
            "the adapter that was written could not be loaded back onto the "
            "base model") from None
    del reloaded, base
    torch.cuda.empty_cache()


def _write_metrics(output_dir, metrics: dict) -> None:
    path = Path(output_dir) / METRICS_FILE
    path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False,
                   ensure_ascii=False),
        encoding="utf-8")
