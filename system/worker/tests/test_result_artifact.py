"""The archive whose hash goes on chain, and what must not be in it.

Two things are being pinned here, and they pull in opposite directions.

**Determinism.** The same three files must produce the same archive, byte for
byte, always. A customer verifying their result recomputes this hash and
compares it with the one the contract holds; an archive that carried the
moment it was built would give them a different number every time and there
would be no way to tell that from a tampered file.

**Exclusion.** Everything else in a workspace stays out -- the dataset, the
model cache, a stray log, and in particular anything reached through a link.
This is a customer's data on a stranger's PC and the archive is the one thing
that leaves it.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from gshared_worker.result_artifact import (
    ARTIFACT_NAME,
    MAX_ARTIFACT_BYTES,
    RESULT_FILES,
    ArtifactRefused,
    check_output,
    hash_file,
    pack_result,
)

ADAPTER = b'{"peft_type":"LORA","r":16,"lora_alpha":32}'
WEIGHTS = b"\x00safetensors\x01" * 64
METRICS = b'{"trainLoss":0.41,"runtimeSec":982.4,"totalSteps":120}'


def a_workspace(**extra: bytes) -> Path:
    """An output directory as a finished container leaves it."""
    root = Path(tempfile.mkdtemp())
    output = root / "output"
    output.mkdir()
    (output / "adapter_config.json").write_bytes(ADAPTER)
    (output / "adapter_model.safetensors").write_bytes(WEIGHTS)
    (output / "training_metrics.json").write_bytes(METRICS)
    for name, content in extra.items():
        (output / name.replace("__", ".")).write_bytes(content)
    return output


def can_symlink() -> bool:
    """Windows needs Developer Mode or an admin for this."""
    root = Path(tempfile.mkdtemp())
    try:
        (root / "target").write_bytes(b"x")
        os.symlink(root / "target", root / "link")
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


SYMLINKS = can_symlink()


class TestOnlyTheThreeFiles(unittest.TestCase):
    def names_in(self, archive: Path) -> list[str]:
        with tarfile.open(archive) as tar:
            return tar.getnames()

    def test_the_archive_holds_exactly_the_three(self):
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertEqual(self.names_in(packaged.path), list(RESULT_FILES))

    def test_an_extra_file_in_the_output_is_left_out(self):
        output = a_workspace(**{"trainer_log__txt": b"loading shards 1/4..."})

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertEqual(self.names_in(packaged.path), list(RESULT_FILES))

    def test_a_dataset_left_in_the_output_is_left_out(self):
        # The thing that must never travel. It is not in the output directory
        # in the real pipeline -- it is mounted read-only elsewhere -- but a
        # whitelist that only works when nothing goes wrong is not a whitelist.
        secret = b'{"instruction":"a customer secret","output":"b"}'
        output = a_workspace(**{"dataset__jsonl": secret})

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertNotIn(secret, packaged.path.read_bytes())
        self.assertEqual(self.names_in(packaged.path), list(RESULT_FILES))

    def test_a_subdirectory_is_not_walked(self):
        output = a_workspace()
        cache = output / "cache"
        cache.mkdir()
        (cache / "blobs").write_bytes(b"model weights nobody asked for")

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertEqual(self.names_in(packaged.path), list(RESULT_FILES))

    def test_the_entries_carry_no_directory(self):
        # A path would put the supplier's folder layout in the customer's file.
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        for name in self.names_in(packaged.path):
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)


class TestTheSameFilesMakeTheSameArchive(unittest.TestCase):
    def test_twice_in_a_row_is_the_same_hash(self):
        output = a_workspace()

        first = pack_result(output, output.parent / "a.tar")
        second = pack_result(output, output.parent / "b.tar")

        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.path.read_bytes(), second.path.read_bytes())

    def test_two_machines_with_the_same_output_agree(self):
        one = pack_result(a_workspace(), Path(tempfile.mkdtemp()) / "a.tar")
        other = pack_result(a_workspace(), Path(tempfile.mkdtemp()) / "b.tar")

        self.assertEqual(one.sha256, other.sha256)

    def test_a_later_modification_time_changes_nothing(self):
        output = a_workspace()
        first = pack_result(output, output.parent / "a.tar")

        os.utime(output / "adapter_config.json", (1_700_000_000, 1_700_000_000))
        second = pack_result(output, output.parent / "b.tar")

        self.assertEqual(first.sha256, second.sha256)

    def test_the_order_is_fixed_not_whatever_the_directory_says(self):
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        with tarfile.open(packaged.path) as tar:
            self.assertEqual([m.name for m in tar.getmembers()],
                             list(RESULT_FILES))

    def test_nothing_of_this_machine_is_in_the_entries(self):
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        with tarfile.open(packaged.path) as tar:
            for member in tar.getmembers():
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)
                self.assertEqual(member.uname, "")
                self.assertEqual(member.gname, "")
                self.assertEqual(member.mode, 0o644)

    def test_different_content_is_a_different_hash(self):
        output = a_workspace()
        first = pack_result(output, output.parent / "a.tar")

        (output / "adapter_model.safetensors").write_bytes(WEIGHTS + b"!")
        second = pack_result(output, output.parent / "b.tar")

        self.assertNotEqual(first.sha256, second.sha256)


class TestTheHashIsOfTheArchive(unittest.TestCase):
    def test_it_is_the_sha256_of_the_file_that_gets_uploaded(self):
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertEqual(
            packaged.sha256,
            hashlib.sha256(packaged.path.read_bytes()).hexdigest())

    def test_it_is_not_the_adapter_on_its_own(self):
        output = a_workspace()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertNotEqual(packaged.sha256,
                            hashlib.sha256(WEIGHTS).hexdigest())

    def test_it_is_not_a_hash_of_the_three_hashes(self):
        output = a_workspace()
        of_hashes = hashlib.sha256(b"".join(
            hashlib.sha256(part).digest() for part in
            (ADAPTER, WEIGHTS, METRICS))).hexdigest()

        packaged = pack_result(output, output.parent / ARTIFACT_NAME)

        self.assertNotEqual(packaged.sha256, of_hashes)

    def test_it_is_sixty_four_lowercase_hex_with_no_prefix(self):
        # The form ResultUploadCompleteRequest validates: ^[0-9a-fA-F]{64}$,
        # "0x 없이".
        packaged = pack_result(a_workspace(),
                               Path(tempfile.mkdtemp()) / ARTIFACT_NAME)

        self.assertEqual(len(packaged.sha256), 64)
        self.assertEqual(packaged.sha256, packaged.sha256.lower())
        self.assertFalse(packaged.sha256.startswith("0x"))
        int(packaged.sha256, 16)

    def test_the_chain_form_adds_the_prefix(self):
        packaged = pack_result(a_workspace(),
                               Path(tempfile.mkdtemp()) / ARTIFACT_NAME)

        self.assertEqual(packaged.result_hash, "0x" + packaged.sha256)
        self.assertEqual(len(packaged.result_hash), 66)

    def test_the_size_is_the_size_of_that_file(self):
        packaged = pack_result(a_workspace(),
                               Path(tempfile.mkdtemp()) / ARTIFACT_NAME)

        self.assertEqual(packaged.size_bytes, packaged.path.stat().st_size)

    def test_hashing_reads_in_pieces(self):
        # A hundred megabytes of adapter must not become a hundred megabytes
        # of process. Checked by size rather than by mocking read().
        big = Path(tempfile.mkdtemp()) / "big.bin"
        big.write_bytes(b"x" * (3 * 1024 * 1024))

        digest, size = hash_file(big)

        self.assertEqual(size, 3 * 1024 * 1024)
        self.assertEqual(digest, hashlib.sha256(b"x" * size).hexdigest())


class TestAnOutputThatIsNotOne(unittest.TestCase):
    def refuses(self, output: Path, because: str):
        with self.assertRaises(ArtifactRefused) as raised:
            check_output(output)
        self.assertIn(because, str(raised.exception).lower())

    def test_a_missing_adapter_is_refused(self):
        output = a_workspace()
        (output / "adapter_model.safetensors").unlink()

        self.refuses(output, "adapter_model.safetensors")

    def test_a_missing_metrics_file_is_refused(self):
        output = a_workspace()
        (output / "training_metrics.json").unlink()

        self.refuses(output, "training_metrics.json")

    def test_an_empty_file_is_refused(self):
        output = a_workspace()
        (output / "adapter_config.json").write_bytes(b"")

        self.refuses(output, "empty")

    def test_a_directory_in_place_of_a_file_is_refused(self):
        output = a_workspace()
        (output / "training_metrics.json").unlink()
        (output / "training_metrics.json").mkdir()

        self.refuses(output, "training_metrics.json")

    def test_no_output_directory_at_all_is_refused(self):
        self.refuses(Path(tempfile.mkdtemp()) / "never-written", "output")

    def test_nothing_is_written_when_the_output_is_refused(self):
        output = a_workspace()
        (output / "adapter_config.json").unlink()
        destination = output.parent / ARTIFACT_NAME

        with self.assertRaises(ArtifactRefused):
            pack_result(output, destination)

        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_a_file_past_the_ceiling_is_refused(self):
        # The ceiling is lowered rather than eight gigabytes being written.
        from gshared_worker import result_artifact

        output = a_workspace()
        real = result_artifact.MAX_ARTIFACT_BYTES
        try:
            result_artifact.MAX_ARTIFACT_BYTES = 4
            self.refuses(output, "larger")
        finally:
            result_artifact.MAX_ARTIFACT_BYTES = real

    def test_the_ceiling_is_a_real_limit(self):
        self.assertGreater(MAX_ARTIFACT_BYTES, 0)


@unittest.skipUnless(SYMLINKS, "symlinks need Developer Mode on Windows")
class TestLinksAreNotFollowed(unittest.TestCase):
    """The attack this whitelist is actually for.

    A link named ``adapter_config.json`` pointing at the supplier's ``.env``
    passes "is a regular file", "is not empty" and "is readable". It would be
    packaged, uploaded to the Backend and handed to a customer.
    """

    def test_a_link_standing_in_for_a_result_file_is_refused(self):
        output = a_workspace()
        secret = output.parent / ".env"
        secret.write_bytes(b"MYSQL_PASSWORD=hunter2\nWORKER_TOKEN=abcd")
        (output / "adapter_config.json").unlink()
        os.symlink(secret, output / "adapter_config.json")

        with self.assertRaises(ArtifactRefused) as raised:
            check_output(output)

        self.assertIn("link", str(raised.exception))

    def test_that_secret_never_reaches_an_archive(self):
        output = a_workspace()
        secret = output.parent / ".env"
        secret.write_bytes(b"WORKER_TOKEN=abcd")
        (output / "training_metrics.json").unlink()
        os.symlink(secret, output / "training_metrics.json")
        destination = output.parent / ARTIFACT_NAME

        with self.assertRaises(ArtifactRefused):
            pack_result(output, destination)

        self.assertFalse(destination.exists())

    def test_an_output_directory_reached_through_a_link_still_works(self):
        """Both sides are resolved, so a linked workspace is not a failure.

        The rule is "the file must live in the output directory", not "no
        link may appear anywhere in the path". The pipeline passes its own
        workspace path, and a supplier whose drive is mounted through a
        junction should not have every Job refused.
        """
        real = a_workspace()
        elsewhere = Path(tempfile.mkdtemp()) / "output"
        os.symlink(real, elsewhere, target_is_directory=True)

        found = check_output(elsewhere)

        self.assertEqual([p.name for p in found], list(RESULT_FILES))


if __name__ == "__main__":
    unittest.main()
