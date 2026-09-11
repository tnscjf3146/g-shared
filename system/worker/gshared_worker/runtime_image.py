"""Which container this machine is willing to run, and how it is named.

Two rules, and the second is the one that does the work.

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

The all-zero digest gets its own refusal. It is not a hypothetical: the
Backend's catalog seed carries
``sha256:0000…0000`` as a placeholder, with a TODO to replace it. It is
well-formed, it would pass a regular-expression check, and pulling it fails in
a way that looks like a network problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``sha256:`` and sixty-four lowercase hex digits. Nothing else is a digest.
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

#: A repository this machine will run. Configuration, not a message.
#:
#: One entry today. It is a tuple rather than a string because a second
#: approved runtime is a foreseeable change and a mirror is a likely one, and
#: both should be a line here rather than a rewrite.
APPROVED_REPOSITORIES: tuple[str, ...] = ("gshared/qlora-sft-runtime",)

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

    return RuntimeImage(repository=repository, digest=wanted)


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
