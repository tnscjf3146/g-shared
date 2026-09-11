"""G-shared's approved QLoRA supervised fine-tuning runtime.

One runner, two supported base models, and no network. What a job is given is
a directory of weights, a JSONL dataset and a set of numbers; what it produces
is a LoRA adapter. See ``README.md`` beside this package for the mount and
stdout contracts that task 7's Worker runtime will hold it to.
"""

from __future__ import annotations

#: Bumped when the container's contract changes -- the CLI, the mounts, the
#: stdout events or the exit codes. This is not the image tag: an image can be
#: rebuilt for a dependency bump without any of that moving.
RUNTIME_CONTRACT_VERSION = "qlora-sft-runtime-v1"
