"""Which container this machine will run.

The threat this file is about: the image reference arrives from the Backend. A
Worker that ran what it was sent would give anyone who could write that field
-- a compromised service, a bad migration, a mistyped seed row -- code
execution on every supplier's PC, with a GPU attached and a customer's dataset
mounted.

So three rules, and every test here is one of them seen from a different
angle: the repository is configuration on this machine, the image is named by
a digest that cannot move, and that digest is the one runtime this Worker was
verified with.
"""

from __future__ import annotations

import unittest

from gshared_worker.runtime_image import (
    APPROVED_DIGESTS,
    APPROVED_REPOSITORIES,
    CANONICAL_RUNTIME_DIGEST,
    CANONICAL_RUNTIME_REFERENCE,
    CANONICAL_RUNTIME_REPOSITORY,
    CANONICAL_RUNTIME_TAG,
    PLACEHOLDER_DIGEST,
    ImageRefused,
    check_image,
    check_pulled,
    inspect_command,
    pull_command,
)

REPO = "gshared/qlora-sft-runtime"
#: The approved image, by content. Read from the module, never retyped, so a
#: runtime bump cannot leave a test asserting the old value.
DIGEST = CANONICAL_RUNTIME_DIGEST
REF = f"{REPO}@{DIGEST}"

#: A digest of the right shape that is not the approved runtime.
UNKNOWN_DIGEST = "sha256:" + "ab" * 32

#: The runtime as published on Docker Hub -- the real 0.2.0 digest, read from
#: the registry after the push, not a made-up value. Written out here once on
#: purpose: if the module constant changes, this test is the one that says so.
HUB_REPO = "le3560/gshared-qlora-sft-runtime"
HUB_DIGEST = "sha256:acda63bc4ad4d7b300c25f28725fed514b045119ac27697cd36f95faae68126d"
HUB_REF = f"{HUB_REPO}@{HUB_DIGEST}"


class TestTheCanonicalRuntime(unittest.TestCase):
    """One value for the whole of System, and the value is the published one."""

    def test_it_is_the_published_docker_hub_image(self):
        self.assertEqual(CANONICAL_RUNTIME_REPOSITORY, HUB_REPO)
        self.assertEqual(CANONICAL_RUNTIME_DIGEST, HUB_DIGEST)
        self.assertEqual(CANONICAL_RUNTIME_TAG, "0.2.0")

    def test_the_reference_is_repository_at_digest(self):
        self.assertEqual(CANONICAL_RUNTIME_REFERENCE, HUB_REF)
        self.assertNotIn(":0.2.0", CANONICAL_RUNTIME_REFERENCE)

    def test_it_is_accepted(self):
        image = check_image(CANONICAL_RUNTIME_REFERENCE, CANONICAL_RUNTIME_DIGEST)

        self.assertEqual(image.reference, CANONICAL_RUNTIME_REFERENCE)

    def test_it_is_the_only_approved_digest(self):
        self.assertEqual(APPROVED_DIGESTS, (CANONICAL_RUNTIME_DIGEST,))

    def test_its_repository_is_approved(self):
        self.assertIn(CANONICAL_RUNTIME_REPOSITORY, APPROVED_REPOSITORIES)

    def test_the_backend_seed_value_is_refused(self):
        # What production's workloads row carries today.
        seed = f"gshared/qlora-sft-runtime@{PLACEHOLDER_DIGEST}"

        with self.assertRaises(ImageRefused) as caught:
            check_image(seed, PLACEHOLDER_DIGEST)

        self.assertIn("placeholder", str(caught.exception))


class TestTheApprovedDigest(unittest.TestCase):
    """A well-formed digest in an approved repository is still not enough."""

    def test_an_unknown_digest_in_the_canonical_repository_is_refused(self):
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{HUB_REPO}@{UNKNOWN_DIGEST}", UNKNOWN_DIGEST)

        self.assertIn("not an approved G-shared runtime digest", str(caught.exception))

    def test_an_unknown_digest_in_the_original_repository_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_image(f"{REPO}@{UNKNOWN_DIGEST}", UNKNOWN_DIGEST)

    def test_the_refusal_names_the_image_this_worker_runs(self):
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{HUB_REPO}@{UNKNOWN_DIGEST}", UNKNOWN_DIGEST)

        self.assertIn(CANONICAL_RUNTIME_REFERENCE, str(caught.exception))

    def test_a_missing_digest_is_refused(self):
        for missing in ("", None, "   "):
            with self.subTest(digest=missing), self.assertRaises(ImageRefused):
                check_image(HUB_REF, missing)

    def test_a_missing_reference_is_refused(self):
        for missing in ("", None):
            with self.subTest(reference=missing), self.assertRaises(ImageRefused):
                check_image(missing, HUB_DIGEST)

    def test_the_right_digest_under_the_wrong_repository_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_image(f"attacker/gshared-qlora-sft-runtime@{HUB_DIGEST}", HUB_DIGEST)

    def test_an_explicitly_narrowed_digest_list_is_honoured(self):
        with self.assertRaises(ImageRefused):
            check_image(HUB_REF, HUB_DIGEST, approved_digests=())


class TestTheApprovedRepository(unittest.TestCase):
    def test_the_approved_runtime_is_accepted(self):
        image = check_image(REF, DIGEST)

        self.assertEqual(image.repository, REPO)
        self.assertEqual(image.digest, DIGEST)

    def test_somebody_elses_repository_is_refused(self):
        other = f"attacker/backdoor@{DIGEST}"

        with self.assertRaises(ImageRefused) as caught:
            check_image(other, DIGEST)

        self.assertIn("approved", str(caught.exception))

    def test_a_lookalike_repository_is_refused(self):
        # "gshared/qlora-sft-runtime-evil" shares a prefix and is not the same
        # thing. Membership, not startswith.
        lookalike = f"{REPO}-evil@{DIGEST}"

        with self.assertRaises(ImageRefused):
            check_image(lookalike, DIGEST)

    def test_a_registry_prefix_is_not_silently_accepted(self):
        # A different registry serving the same path is a different image.
        with self.assertRaises(ImageRefused):
            check_image(f"evil.example.com/{REPO}@{DIGEST}", DIGEST)

    def test_the_approved_list_is_configuration_not_a_message(self):
        # Nothing in the call can extend it; it is a module constant passed by
        # keyword only so tests can narrow it, never widen it from a payload.
        self.assertIn(REPO, APPROVED_REPOSITORIES)

    def test_the_published_docker_hub_runtime_is_accepted(self):
        image = check_image(HUB_REF, HUB_DIGEST)

        self.assertEqual(image.repository, HUB_REPO)
        self.assertEqual(image.reference, HUB_REF)

    def test_the_original_repository_is_still_approved(self):
        # Adding the published name must not drop the old one.
        self.assertIn(REPO, APPROVED_REPOSITORIES)
        self.assertIn(HUB_REPO, APPROVED_REPOSITORIES)

    def test_the_docker_hub_repository_still_needs_a_digest(self):
        with self.assertRaises(ImageRefused):
            check_image(f"{HUB_REPO}:0.2.0", HUB_DIGEST)

    def test_the_docker_hub_repository_still_refuses_the_placeholder(self):
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{HUB_REPO}@{PLACEHOLDER_DIGEST}", PLACEHOLDER_DIGEST)

        self.assertIn("placeholder", str(caught.exception))

    def test_a_lookalike_of_the_docker_hub_repository_is_refused(self):
        for other in (f"{HUB_REPO}-evil", "le3560/gpu-pytorch-poc",
                      "attacker/gshared-qlora-sft-runtime"):
            with self.subTest(other=other), self.assertRaises(ImageRefused):
                check_image(f"{other}@{HUB_DIGEST}", HUB_DIGEST)

    def test_the_docker_hub_image_passes_the_pull_check(self):
        image = check_image(HUB_REF, HUB_DIGEST)

        check_pulled(image, f'["{HUB_REF}"]')

    def test_an_explicitly_narrowed_list_is_honoured(self):
        with self.assertRaises(ImageRefused):
            check_image(REF, DIGEST, approved=("something/else",))


class TestTheDigestMustBeImmutable(unittest.TestCase):
    def test_a_tag_is_refused(self):
        """The rule that matters most.

        A tag names whatever was pushed to it last. Refused even when it
        points at the right image today, because the point is that nobody has
        to check.
        """
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{REPO}:0.1.0", DIGEST)

        self.assertIn("digest", str(caught.exception))

    def test_a_bare_repository_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_image(REPO, DIGEST)

    def test_a_reference_carrying_both_a_tag_and_a_digest_is_refused(self):
        # Docker resolves the digest and ignores the tag, so this works -- but
        # somebody wrote it expecting the tag to matter, and it does not.
        with self.assertRaises(ImageRefused):
            check_image(f"{REPO}:0.1.0@{DIGEST}", DIGEST)

    def test_the_reference_and_the_digest_field_must_agree(self):
        other = "sha256:" + "cd" * 32

        with self.assertRaises(ImageRefused) as caught:
            check_image(REF, other)

        self.assertIn("disagree", str(caught.exception))

    def test_a_short_digest_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_image(f"{REPO}@sha256:abc123", "sha256:abc123")

    def test_a_digest_with_the_wrong_algorithm_is_refused(self):
        bad = "sha512:" + "ab" * 32

        with self.assertRaises(ImageRefused):
            check_image(f"{REPO}@{bad}", bad)

    def test_a_digest_with_non_hex_characters_is_refused(self):
        bad = "sha256:" + "zz" * 32

        with self.assertRaises(ImageRefused):
            check_image(f"{REPO}@{bad}", bad)

    def test_uppercase_hex_is_normalised_rather_than_refused(self):
        upper = "sha256:" + DIGEST.removeprefix("sha256:").upper()

        self.assertEqual(check_image(f"{REPO}@{upper}", upper).digest, DIGEST)


class TestThePlaceholderDigest(unittest.TestCase):
    """The all-zero digest in the Backend's catalog seed.

    It is well formed, it passes a regular-expression check, and pulling it
    fails in a way that reads like a network problem. It gets its own refusal
    so the message says what is actually wrong.
    """

    def test_it_is_refused(self):
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{REPO}@{PLACEHOLDER_DIGEST}", PLACEHOLDER_DIGEST)

        self.assertIn("placeholder", str(caught.exception))

    def test_the_refusal_says_no_image_has_been_published(self):
        with self.assertRaises(ImageRefused) as caught:
            check_image(f"{REPO}@{PLACEHOLDER_DIGEST}", PLACEHOLDER_DIGEST)

        self.assertIn("published", str(caught.exception))

    def test_it_is_all_zeroes_and_the_right_shape(self):
        # If the seed value ever changes shape this test fails rather than the
        # refusal silently stopping working.
        self.assertEqual(PLACEHOLDER_DIGEST, "sha256:" + "0" * 64)

    def test_it_is_never_an_approved_digest(self):
        self.assertNotIn(PLACEHOLDER_DIGEST, APPROVED_DIGESTS)


class TestTheCommands(unittest.TestCase):
    def test_the_pull_is_by_digest(self):
        command = pull_command(check_image(REF, DIGEST))

        self.assertEqual(command, ["docker", "pull", REF])
        self.assertNotIn(":0.1.0", " ".join(command))

    def test_the_commands_are_argument_lists(self):
        for command in (pull_command(check_image(REF, DIGEST)),
                        inspect_command(check_image(REF, DIGEST))):
            with self.subTest(command=command[1]):
                self.assertIsInstance(command, list)
                self.assertTrue(all(isinstance(a, str) for a in command))

    def test_inspect_asks_for_the_registry_digest_not_the_local_id(self):
        """``.Id`` is a different value computed a different way.

        Comparing against it would fail for every correctly pulled image,
        which is the kind of check somebody deletes rather than fixes.
        """
        command = inspect_command(check_image(REF, DIGEST))

        self.assertIn("{{json .RepoDigests}}", command)
        self.assertNotIn("{{json .Id}}", command)


class TestWhatActuallyLanded(unittest.TestCase):
    """Checked even on a cache hit: a hit proves an earlier pull, not this one."""

    def test_a_matching_digest_passes(self):
        check_pulled(check_image(REF, DIGEST), f'["{REF}"]')

    def test_it_passes_when_several_are_listed(self):
        other = f"{REPO}@sha256:{'cd' * 32}"

        check_pulled(check_image(REF, DIGEST), f'["{other}", "{REF}"]')

    def test_a_different_digest_is_refused(self):
        other = f"{REPO}@sha256:{'cd' * 32}"

        with self.assertRaises(ImageRefused):
            check_pulled(check_image(REF, DIGEST), f'["{other}"]')

    def test_an_image_with_no_registry_digest_is_refused(self):
        # A locally built image has none. It cannot be confirmed as the
        # approved one, so it is not run.
        with self.assertRaises(ImageRefused) as caught:
            check_pulled(check_image(REF, DIGEST), "[]")

        self.assertIn("no registry digest", str(caught.exception))

    def test_a_null_answer_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_pulled(check_image(REF, DIGEST), "null")

    def test_unreadable_output_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_pulled(check_image(REF, DIGEST), "not json at all")

    def test_an_empty_answer_is_refused(self):
        with self.assertRaises(ImageRefused):
            check_pulled(check_image(REF, DIGEST), "")


if __name__ == "__main__":
    unittest.main()
