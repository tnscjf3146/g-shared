"""Fetches a pinned base model to a local directory. Development only.

This is the helper that makes the container's no-network rule workable while
developing: somebody has to put weights under ``/job/model``, and it is not
the container.

**This is not the Worker's asset preparation.** Task 7 owns that: the Worker
will need a cache keyed by revision, a disk budget, a partial-download policy,
integrity checking against what the Backend declared, and a story for two jobs
wanting the same model at once. None of that is here, and none of it should be
copied from here. What is here is enough to get weights onto a developer's
machine so the runtime can be tested.

    python tools/prepare_model.py qwen3-0.6b --dest /opt/gshared-dev/models/qwen3-0.6b

The revision is not an argument. It comes from ``qlora_sft.config.CATALOG``,
which is the same table the runtime and the documentation use, so a model
prepared by this script is the model the runtime was tested against. Passing
``--ref``/``--revision`` by hand is possible for trying something new; it
prints that it is off the catalogue when you do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qlora_sft.config import CATALOG  # noqa: E402

#: Weights, config and tokenizer. Not the .gguf conversions, not the ONNX
#: exports, not the READMEs -- a few gigabytes of files this runtime will
#: never open.
WANTED = ["*.json", "*.safetensors", "*.txt", "*.py"]


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", help="a catalogue key, e.g. qwen3-0.6b")
    parser.add_argument("--dest", required=True)
    parser.add_argument("--ref", default=None, help="off-catalogue repo id")
    parser.add_argument("--revision", default=None, help="off-catalogue commit SHA")
    args = parser.parse_args(argv)

    if args.ref or args.revision:
        if not (args.ref and args.revision):
            parser.error("--ref and --revision go together, or neither")
        ref, revision = args.ref, args.revision
        print("off-catalogue: this is not a model the runtime is tested "
              "against", file=sys.stderr)
    else:
        entry = CATALOG.get(args.model)
        if entry is None:
            parser.error(
                "unknown model %r; the catalogue holds %s"
                % (args.model, ", ".join(sorted(CATALOG))))
        ref, revision = entry.ref, entry.revision

    from huggingface_hub import snapshot_download

    destination = Path(args.dest)
    path = snapshot_download(
        repo_id=ref, revision=revision, local_dir=str(destination),
        allow_patterns=WANTED)

    # A note beside the weights saying which commit they are, because a
    # directory of safetensors otherwise says nothing about its own version
    # and the runtime is deliberately not told.
    (destination / "gshared-model-source.json").write_text(
        json.dumps({"modelRef": ref, "modelRevision": revision},
                   indent=2, sort_keys=True),
        encoding="utf-8")
    print(f"{ref}@{revision} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
