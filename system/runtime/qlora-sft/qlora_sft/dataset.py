"""Reading a job's dataset, and turning it into the one text SFT sees.

The MVP supports exactly one format and exactly one prompt template. That is
a decision, not a gap. A runtime with four formats and three templates has
twelve behaviours to explain when a customer's adapter comes out wrong, and
G-shared has not trained a single real job yet.

**Format.** JSON Lines, one object per line::

    {"instruction": "...", "input": "", "output": "..."}

``instruction`` and ``output`` are required and must be non-empty strings.
``input`` is optional and may be empty. Anything else on the line -- a missing
field, a number where a string belongs, a line that is not JSON at all -- is
rejected, naming the line number and nothing from the line itself. The dataset
belongs to a customer and this container's stdout is going to be read by a
stranger who owns the GPU.

**Template.** Qwen3's chat markers, written out here rather than taken from
the tokenizer's ``chat_template``. Two reasons: the markers become part of
what the adapter learns, so they must be pinned like everything else in a
reproducible run; and a template that lives in a constant can be tested
without downloading a tokenizer.

Loss is taken on the response only. The prompt is masked out, so the adapter
learns to answer rather than to recite the question back -- which is what
makes the before/after comparison in ``tools/validate_adapter.py`` show
anything at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .failures import ExitCode, TrainingFailure

#: Everything up to and including the point where the model starts answering.
#: Kept separate from the completion because the split is where loss masking
#: happens.
PROMPT_TEMPLATE = "<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
COMPLETION_TEMPLATE = "{output}<|im_end|>"

#: A dataset with nothing in it is a mistake somewhere upstream, not a run
#: that finishes instantly with an untrained adapter.
MINIMUM_SAMPLES = 1


@dataclass(frozen=True)
class Sample:
    instruction: str
    input: str
    output: str

    @property
    def prompt(self) -> str:
        """The user turn: instruction, and the input below it when there is one."""
        if self.input:
            return f"{self.instruction}\n\n{self.input}"
        return self.instruction

    def prompt_text(self) -> str:
        return PROMPT_TEMPLATE.format(prompt=self.prompt)

    def completion_text(self) -> str:
        return COMPLETION_TEMPLATE.format(output=self.output)

    def full_text(self) -> str:
        return self.prompt_text() + self.completion_text()


def read_dataset(path: Path) -> list[Sample]:
    """Every sample in the file, or a refusal naming the line that broke it."""
    file = Path(path)
    if not file.is_file():
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the dataset file is not there")
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the dataset file could not be read") from None
    except UnicodeDecodeError:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the dataset file is not valid UTF-8") from None

    samples = list(_parse(text))
    if len(samples) < MINIMUM_SAMPLES:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the dataset has no samples in it")
    return samples


def _parse(text: str) -> Iterator[Sample]:
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            # A trailing newline is not a malformed record, and neither is a
            # blank line somebody's editor left behind.
            continue
        yield read_sample(line, number)


def read_sample(line: str, number: int) -> Sample:
    """One JSONL line as a sample, or a refusal that quotes none of it."""
    try:
        record = json.loads(line)
    except ValueError:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} is not valid JSON") from None
    if not isinstance(record, dict):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} is not a JSON object")

    instruction = _required(record, "instruction", number)
    output = _required(record, "output", number)
    supplied = record.get("input", "")
    if not isinstance(supplied, str):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} has an 'input' that is not a string")
    return Sample(instruction=instruction, input=supplied, output=output)


def _required(record: dict, field: str, number: int) -> str:
    if field not in record:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} has no '{field}'")
    value = record[field]
    if not isinstance(value, str):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} has a '{field}' that is not a string")
    if not value.strip():
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"dataset line {number} has an empty '{field}'")
    return value
