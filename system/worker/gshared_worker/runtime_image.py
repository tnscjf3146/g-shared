"""Which container this machine is willing to run, and how it is named.

Three rules, and the last is the one that does the work.

**Only the approved repository.** The Backend sends an image reference. If the
Worker ran whatever it was sent, then anyone who could write to that field --
a compromised Backend, a bad migration, a mistyped seed row -- would have
arbitrary code execution on every supplier's PC, with a GPU attached. So the
repository is configuration on this machine, and the Backend's value is
checked against it rather than obeyed.

**Only an immutable digest.** ``gshared/qlora-sft-runtime:0.1.0`` names
whatever was pushed to that tag most recently, which is not a thing that can
be reviewed. ``repository@sha256:…`` names exactly one image for ever. A tag is
refused in production even when it points at the right image today, because
the point is that nobody has to check.

**Only the approved digest.** A well-formed digest in an approved repository is
still whatever somebody pushed there. The runtime this Worker was verified
against is one exact image, named below, and nothing else is run. A new runtime
version is a new Worker release, not a new catalog row.

The all-zero digest gets its own refusal. It is not a hypothetical: the
Backend's catalog seed carries
``sha256:0000…0000`` as a placeholder, with a TODO to replace it. It is
well-formed, it would pass a regular-expression check, and pulling it fails in
a way that looks like a network problem.

**The canonical runtime** -- the one value every other place in System reads:

========== ==========================================================================
repository ``le3560/gshared-qlora-sft-runtime``
tag        ``0.4.0`` (for people; never used to pull)
digest     ``sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec``
reference  ``le3560/gshared-qlora-sft-runtime@sha256:3f054d6d…e4ec``
========== ==========================================================================

0.4.0 is 0.3.0 plus the training result verification: ``training_metrics.json``
now carries ``lossHistory``, ``finalLoss`` and a ``verification`` block (LoRA
tensor counts, NaN/Inf, per layer x module change magnitude), which the
Backend's result verification panel reads out of ``result.tar``. The digest was
read from Docker Hub after the push, and on an RTX 4070 Laptop GPU the image
trained Qwen3-0.6B both ways -- fresh (``initAdapter`` null, 15 loss points,
``verification.valid`` true) and from a finished Job's adapter (its weights
hash recorded, loss 0.91, ``valid`` true).

**Earlier runtimes stay approved while the Backend's catalog moves.** A Worker
that refused what the catalog names would refuse every Job, so the new one is
approved *before* the Backend points at it:

* :data:`PREVIOUS_RUNTIME_DIGEST` -- 0.3.0, what the catalog names since V19.
  Runs everything, adapters included; only its results lack the verification.
* :data:`LEGACY_RUNTIME_DIGEST` -- 0.2.0, what the catalog named before V19.
  Cannot continue from an adapter, which :func:`supports_adapter` says.

Remove each once no catalog in service names it.

The Backend's catalog must carry exactly the canonical repository and digest;
its seed value ``gshared/qlora-sft-runtime@sha256:0000…0000`` is an invalid
placeholder and is refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``sha256:`` and sixty-four lowercase hex digits. Nothing else is a digest.
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Where the verified runtime is published.
CANONICAL_RUNTIME_REPOSITORY = "le3560/gshared-qlora-sft-runtime"

#: The human name of that image. Documentation only: pulls are by digest.
CANONICAL_RUNTIME_TAG = "0.4.0"

#: The exact image the Worker was verified with, read from the registry.
CANONICAL_RUNTIME_DIGEST = (
    "sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec")

#: Runtime 0.3.0 -- adapter-capable, without result verification. What the
#: Backend's catalog names (V19) until its next migration. Approved, not canonical.
PREVIOUS_RUNTIME_TAG = "0.3.0"
PREVIOUS_RUNTIME_DIGEST = (
    "sha256:e32bd1c1b8e7689c33100eaf1718f3ceadea80ff1d13de4da970173762e8e170")

#: Runtime 0.2.0 -- what the catalog named before V19. Runs Jobs without an
#: adapter; kept until no deployment's catalog can still name it.
LEGACY_RUNTIME_TAG = "0.2.0"
LEGACY_RUNTIME_DIGEST = (
    "sha256:acda63bc4ad4d7b300c25f28725fed514b045119ac27697cd36f95faae68126d")

#: What the Backend's execution context should carry as ``imageRef``.
CANONICAL_RUNTIME_REFERENCE = f"{CANONICAL_RUNTIME_REPOSITORY}@{CANONICAL_RUNTIME_DIGEST}"

#: A repository this machine will run. Configuration, not a message.
#:
#: A tuple rather than a string because a second approved runtime is a
#: foreseeable change and a mirror is a likely one, and both should be a line
#: here rather than a rewrite.
#:
#: The original name stays: removing it would refuse contexts that already
#: carry it, and adding a name widens nothing else -- the digest rules below
#: apply to both exactly the same, and :data:`APPROVED_DIGESTS` pins the image.
APPROVED_REPOSITORIES: tuple[str, ...] = (
    "gshared/qlora-sft-runtime",
    CANONICAL_RUNTIME_REPOSITORY,
)

#: The images this machine will run, by content: the canonical runtime and,
#: until every catalog has moved to it, the ones before.
APPROVED_DIGESTS: tuple[str, ...] = (
    CANONICAL_RUNTIME_DIGEST, PREVIOUS_RUNTIME_DIGEST, LEGACY_RUNTIME_DIGEST)

#: The approved runtimes that understand ``--adapter-path``. 0.2.0 does not:
#: handed the flag, its argument parser exits 2 before loading anything, which
#: the Worker would only see as an unknown exit code after pulling gigabytes.
ADAPTER_CAPABLE_DIGESTS: tuple[str, ...] = (
    CANONICAL_RUNTIME_DIGEST, PREVIOUS_RUNTIME_DIGEST)

#: The placeholder in the Backend's catalog seed. Named so the refusal can say
#: what it actually is instead of "pull failed".
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


class ImageRefused(Exception):
    """This image will not be run, and the reason is not going to change."""


@dataclass(frozen=True)
class RuntimeImage:
    """An image this machine has agreed to run, by digest."""

    repository: str
    digest: str

    @property
    def reference(self) -> str:
        """What goes to ``docker pull``. Never a tag."""
        return f"{self.repository}@{self.digest}"


def check_image(
    image_ref: str,
    digest: str,
    *,
    approved: tuple[str, ...] = APPROVED_REPOSITORIES,
    approved_digests: tuple[str, ...] = APPROVED_DIGESTS,
) -> RuntimeImage:
    """The image to run, or a refusal. Both arguments come from the Backend.

    ``imageRef`` and ``digest`` are sent separately and are checked against
    each other as well as against the rules -- a context where they disagree is
    a context whose two halves were built from different things.
    """
    reference = (image_ref or "").strip()
    wanted = (digest or "").strip().lower()

    if not _DIGEST.match(wanted):
        raise ImageRefused(
            f"the runtime image digest is not sha256 and 64 hex digits: "
            f"{wanted!r}")
    if wanted == PLACEHOLDER_DIGEST:
        # The catalog seed's TODO value. Well formed and completely useless.
        raise ImageRefused(
            "the runtime image digest is the all-zero placeholder, so no "
            "image has been published for this workload yet")

    if "@" not in reference:
        # A tag, or a bare repository. Either way it names whatever was pushed
        # there last, which is not something anybody reviewed.
        raise ImageRefused(
            "the runtime image is not pinned to a digest; this Worker does "
            "not run images by tag")

    repository, _, named_digest = reference.partition("@")
    if named_digest.strip().lower() != wanted:
        raise ImageRefused(
            "the runtime image reference and its digest field disagree")

    repository = repository.strip()
    if ":" in repository.rsplit("/", 1)[-1]:
        # "repo:tag@sha256:..." -- a tag *and* a digest. The digest wins in
        # Docker, but a reference carrying both is one somebody expected the
        # tag to matter in, and that expectation is wrong here.
        raise ImageRefused(
            "the runtime image reference carries a tag as well as a digest")

    if repository not in approved:
        raise ImageRefused(
            f"{repository!r} is not an approved G-shared runtime repository")

    if wanted not in approved_digests:
        # Right repository, right shape, and still not the image this Worker
        # was verified with. Whatever else was pushed there is not reviewed.
        raise ImageRefused(
            f"{wanted} is not an approved G-shared runtime digest; this Worker "
            f"runs {CANONICAL_RUNTIME_REFERENCE}")

    return RuntimeImage(repository=repository, digest=wanted)


def supports_adapter(image: RuntimeImage) -> bool:
    """Whether this runtime can continue training from an initial adapter."""
    return image.digest in ADAPTER_CAPABLE_DIGESTS


def pull_command(image: RuntimeImage) -> list[str]:
    """Fetch exactly this image. By digest, so there is nothing to resolve."""
    return ["docker", "pull", image.reference]


def inspect_command(image: RuntimeImage) -> list[str]:
    """Ask the local daemon what it actually has under that reference.

    ``RepoDigests`` rather than ``Id``: ``Id`` is the **local image id**, which
    is a different value computed a different way and does not equal the
    registry digest. Comparing against it would fail for every correctly
    pulled image, which is the kind of check that gets deleted rather than
    fixed.
    """
    return ["docker", "image", "inspect", image.reference,
            "--format", "{{json .RepoDigests}}"]


def check_pulled(image: RuntimeImage, repo_digests_json: str) -> None:
    """What the daemon has is what was asked for, or nothing runs.

    Done even when the image was already in the local cache. A cache hit means
    some earlier run fetched *something* under this reference; it is not
    evidence about what is there now.
    """
    import json

    try:
        found = json.loads(repo_digests_json or "null")
    except ValueError:
        raise ImageRefused(
            "the local image could not be inspected") from None
    if not isinstance(found, list) or not found:
        raise ImageRefused(
            "the pulled image reports no registry digest, so it cannot be "
            "confirmed as the approved image")
    if image.reference not in [str(entry).strip() for entry in found]:
        raise ImageRefused(
            "the image in the local store is not the one that was requested")
