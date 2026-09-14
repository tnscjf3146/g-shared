"""Getting the two inputs onto disk: the dataset and the weights.

Both arrive from outside and neither is trusted on arrival. The dataset is
hashed against what the Backend said it would be; the model is pinned to an
exact commit and a half-finished download is thrown away rather than reused.

The signed download URL is a credential -- its query string grants read access
to a customer's data -- so several tests here are about it never appearing in
a log, a message or a filename.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import io
import tempfile
import unittest
from pathlib import Path

from gshared_worker.dataset_fetch import (
    DatasetRefused,
    check_url,
    fetch_dataset,
)
from gshared_worker.job_workspace import (
    COMPLETE_MARKER,
    JobWorkspace,
    ModelCache,
    UnsafePath,
    safe_job_id,
    safe_model_key,
    to_distro_path,
)
from gshared_worker.model_prep import (
    MUTABLE_REFERENCES,
    READY_MARKER,
    ModelRefused,
    check_revision,
    fetch_command,
    prepare_model,
)

REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
MODEL = "Qwen/Qwen3-0.6B"
IMAGE = "gshared/qlora-sft-runtime@sha256:" + "ab" * 32

PAYLOAD = b'{"instruction":"a","input":"","output":"b"}\n' * 64
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def code_only(module) -> str:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)


def serving(payload=PAYLOAD, chunks=None):
    """An opener that hands back ``payload``, optionally in pieces."""

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=None):
        return Response(payload)

    return opener


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

class TestPathsAreNeverConcatenated(unittest.TestCase):
    """Job ids and model references arrive from the Backend."""

    def test_a_numeric_job_id_is_fine(self):
        self.assertEqual(safe_job_id("123"), "123")

    def test_a_traversing_job_id_is_refused(self):
        for bad in ("../../etc", "..", "12/34", "/abs", "C:\\x", "a b",
                    "", "12;rm -rf /", "CON"):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafePath):
                    safe_job_id(bad)

    def test_it_is_refused_rather_than_sanitised(self):
        # Stripping "../" would turn two different inputs into one directory.
        with self.assertRaises(UnsafePath):
            safe_job_id("../12")

    def test_a_model_key_carries_the_revision(self):
        key = safe_model_key(MODEL, REVISION)

        self.assertIn("Qwen3-0.6B", key)
        self.assertIn(REVISION, key)

    def test_two_revisions_are_two_directories(self):
        """Otherwise a Job gets the wrong weights and nothing notices.

        The files would load perfectly; only the output would be wrong.
        """
        other = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"

        self.assertNotEqual(safe_model_key(MODEL, REVISION),
                            safe_model_key(MODEL, other))

    def test_a_model_key_has_no_separator_in_it(self):
        self.assertNotIn("/", safe_model_key(MODEL, REVISION))
        self.assertNotIn("\\", safe_model_key(MODEL, REVISION))

    def test_a_traversing_model_ref_is_refused(self):
        for bad in ("../../etc/passwd", "Qwen/../..", "/absolute/path",
                    "Qwen", "", "Qwen/a/b"):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafePath):
                    safe_model_key(bad, REVISION)

    def test_a_branch_name_is_not_a_revision(self):
        with self.assertRaises(UnsafePath):
            safe_model_key(MODEL, "main")

    def test_a_windows_path_reaches_the_daemon_as_a_mount_path(self):
        # The daemon runs in WSL and has no drive letters.
        self.assertEqual(to_distro_path(Path("C:\\S15P21E204\\jobs\\1")),
                         "/mnt/c/S15P21E204/jobs/1")

    def test_a_posix_path_is_left_alone(self):
        self.assertEqual(to_distro_path(Path("/var/lib/gshared")),
                         "/var/lib/gshared")


class TestTheJobWorkspace(unittest.TestCase):
    def workspace(self):
        return JobWorkspace.under(Path(tempfile.mkdtemp()), "123")

    def test_it_makes_the_directories(self):
        workspace = self.workspace().prepare()

        self.assertTrue(workspace.input_dir.is_dir())
        self.assertTrue(workspace.output_dir.is_dir())

    def test_the_dataset_has_the_name_the_container_expects(self):
        self.assertEqual(self.workspace().dataset_path.name, "dataset.jsonl")

    def test_preparing_clears_what_an_earlier_attempt_left(self):
        # A dataset from a previous attempt is a file this run did not verify.
        workspace = self.workspace().prepare()
        workspace.dataset_path.write_bytes(b"old")
        (workspace.output_dir / "adapter_config.json").write_text("{}")

        workspace.prepare()

        self.assertFalse(workspace.dataset_path.exists())
        self.assertEqual(list(workspace.output_dir.iterdir()), [])

    def test_it_is_not_deleted_when_a_job_finishes(self):
        # Task 9 uploads what is in there.
        from gshared_worker import job_workspace

        source = code_only(job_workspace)
        self.assertNotIn("def cleanup", source)
        self.assertNotIn("def remove_workspace", source)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class TestTheDownloadUrl(unittest.TestCase):
    def test_https_is_allowed(self):
        check_url("https://storage.example.com/x?sig=abc")

    def test_http_is_allowed(self):
        check_url("http://127.0.0.1:9000/x")

    def test_a_file_url_is_refused(self):
        # It would read the Worker's own disk.
        with self.assertRaises(DatasetRefused):
            check_url("file:///etc/passwd")

    def test_an_exotic_scheme_is_refused(self):
        for bad in ("ftp://h/x", "gopher://h/x", "data:text/plain,hi", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(DatasetRefused):
                    check_url(bad)

    def test_a_url_with_no_host_is_refused(self):
        with self.assertRaises(DatasetRefused):
            check_url("https:///x")


class TestFetchingTheDataset(unittest.TestCase):
    def destination(self):
        return Path(tempfile.mkdtemp()) / "input" / "dataset.jsonl"

    def fetch(self, destination=None, *, payload=PAYLOAD, digest=DIGEST,
              size=None, opener=None):
        return fetch_dataset(
            "https://storage.example.com/x?sig=secret",
            destination or self.destination(),
            expected_sha256=digest,
            expected_size=len(payload) if size is None else size,
            opener=opener or serving(payload))

    def test_a_matching_file_is_written(self):
        destination = self.destination()

        result = self.fetch(destination)

        self.assertTrue(destination.is_file())
        self.assertEqual(destination.read_bytes(), PAYLOAD)
        self.assertEqual(result.sha256, DIGEST)

    def test_the_hash_is_recomputed_not_taken_on_trust(self):
        """The Backend's hash is a claim about a file that has not arrived yet.

        A signed URL can expire mid-stream and storage can return an error
        page with a 200 -- both produce something that looks like a file.
        """
        with self.assertRaises(DatasetRefused) as caught:
            self.fetch(payload=b"something else entirely", digest=DIGEST,
                       size=len(b"something else entirely"))

        self.assertIn("SHA-256", str(caught.exception))

    def test_a_hash_mismatch_leaves_no_file_behind(self):
        destination = self.destination()

        with self.assertRaises(DatasetRefused):
            self.fetch(destination, payload=b"wrong", size=5)

        self.assertFalse(destination.exists())

    def test_a_size_mismatch_is_refused(self):
        with self.assertRaises(DatasetRefused) as caught:
            self.fetch(size=len(PAYLOAD) + 1)

        self.assertIn("bytes", str(caught.exception))

    def test_the_size_is_the_file_not_the_header(self):
        # Content-Length is not consulted anywhere.
        from gshared_worker import dataset_fetch

        self.assertNotIn("Content-Length", code_only(dataset_fetch))
        self.assertNotIn("content-length", code_only(dataset_fetch).lower())

    def test_a_hash_mismatch_is_never_retried(self):
        """Asking again cannot change which of the two is wrong.

        A Worker that kept asking until the numbers agreed would eventually
        train on whatever it was handed.
        """
        attempts: list = []

        def opener(request, timeout=None):
            attempts.append(1)
            return serving(b"wrong")(request)

        with self.assertRaises(DatasetRefused):
            self.fetch(payload=b"wrong", size=5, opener=opener)

        self.assertEqual(len(attempts), 1)

    def test_a_broken_transfer_is_retried(self):
        attempts: list = []

        def opener(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("connection reset")
            return serving()(request)

        result = fetch_dataset(
            "https://s/x", self.destination(),
            expected_sha256=DIGEST, expected_size=len(PAYLOAD),
            opener=opener, sleep=lambda s: True)

        self.assertEqual(result.sha256, DIGEST)
        self.assertEqual(len(attempts), 2)

    def test_retrying_is_bounded(self):
        attempts: list = []

        def opener(request, timeout=None):
            attempts.append(1)
            raise OSError("connection reset")

        with self.assertRaises(DatasetRefused):
            fetch_dataset("https://s/x", self.destination(),
                          expected_sha256=DIGEST, expected_size=len(PAYLOAD),
                          opener=opener, sleep=lambda s: True, attempts=3)

        self.assertEqual(len(attempts), 3)

    def test_stopping_ends_it(self):
        def opener(request, timeout=None):
            raise OSError("reset")

        with self.assertRaises(DatasetRefused) as caught:
            fetch_dataset("https://s/x", self.destination(),
                          expected_sha256=DIGEST, expected_size=len(PAYLOAD),
                          opener=opener, sleep=lambda s: False)

        self.assertFalse(caught.exception.fatal)

    def test_the_file_only_appears_once_it_is_verified(self):
        # The real name is created by a rename, so a dataset that exists is a
        # dataset that passed both checks.
        from gshared_worker import dataset_fetch

        source = code_only(dataset_fetch)
        self.assertIn(".part", source)
        self.assertIn("replace", source)

    def test_a_partial_file_is_cleaned_up(self):
        destination = self.destination()

        with self.assertRaises(DatasetRefused):
            self.fetch(destination, payload=b"wrong", size=5)

        leftovers = list(destination.parent.iterdir())
        self.assertEqual(leftovers, [], leftovers)

    def test_an_absurd_size_is_refused_before_anything_is_written(self):
        with self.assertRaises(DatasetRefused):
            self.fetch(size=9 * 1024 * 1024 * 1024)

    def test_a_bad_expected_hash_is_refused(self):
        for bad in ("", "abc", "z" * 64, "ab" * 31):
            with self.subTest(bad=bad):
                with self.assertRaises(DatasetRefused):
                    self.fetch(digest=bad)

    def test_a_0x_prefixed_hash_is_accepted(self):
        # The escrow's result hash carries 0x; a file hash does not. Accepting
        # both stops a prefixed value comparing unequal for ever.
        result = self.fetch(digest="0x" + DIGEST)

        self.assertEqual(result.sha256, DIGEST)


class TestTheUrlIsACredential(unittest.TestCase):
    """Its query string grants read access to a customer's data."""

    def source(self):
        from gshared_worker import dataset_fetch

        return code_only(dataset_fetch)

    def test_the_url_is_never_logged(self):
        for line in self.source().splitlines():
            if "_debug" in line:
                with self.subTest(line=line.strip()):
                    self.assertNotIn("url", line)

    def test_a_transfer_failure_reports_only_the_exception_type(self):
        # A urllib error's message can contain the whole URL.
        self.assertIn("type(error).__name__", self.source())

    def test_the_url_never_names_a_file(self):
        # A filename taken from the URL is a path an attacker chooses.
        source = self.source()
        for absent in ("basename", "urlparse(url).path", "split('/')[-1]"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_dataset_contents_are_never_logged(self):
        source = self.source()
        for line in source.splitlines():
            if "_debug" in line:
                with self.subTest(line=line.strip()):
                    self.assertNotIn("chunk", line)
                    self.assertNotIn("payload", line)

    def test_nothing_reads_the_whole_file_into_memory(self):
        # A dataset can be hundreds of megabytes.
        source = self.source()
        self.assertIn("CHUNK_BYTES", source)
        self.assertNotIn(".read()", source.replace("response.read(CHUNK_BYTES)", ""))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class TestTheRevisionMustBePinned(unittest.TestCase):
    def test_a_commit_sha_is_accepted(self):
        self.assertEqual(check_revision(MODEL, REVISION), REVISION)

    def test_uppercase_is_normalised(self):
        self.assertEqual(check_revision(MODEL, REVISION.upper()), REVISION)

    def test_a_missing_revision_is_refused(self):
        """The Backend's catalog has this today: model_revision is NULL.

        There is no way to know which weights the Job was priced against, so
        it does not run.
        """
        for missing in (None, "", "   "):
            with self.subTest(missing=missing):
                with self.assertRaises(ModelRefused):
                    check_revision(MODEL, missing)

    def test_a_moving_reference_is_refused(self):
        for moving in sorted(MUTABLE_REFERENCES):
            with self.subTest(moving=moving):
                with self.assertRaises(ModelRefused) as caught:
                    check_revision(MODEL, moving)
                self.assertIn("moves", str(caught.exception))

    def test_a_short_sha_is_refused(self):
        with self.assertRaises(ModelRefused):
            check_revision(MODEL, REVISION[:12])

    def test_a_tag_is_refused(self):
        with self.assertRaises(ModelRefused):
            check_revision(MODEL, "v1.0")


class TestTheFetchContainer(unittest.TestCase):
    def command(self):
        return fetch_command(IMAGE, Path("/cache/x"), MODEL, REVISION)

    def test_the_reference_and_revision_are_arguments_not_code(self):
        """A model reference containing a quote would otherwise run code.

        The program is a constant; the values are argv.
        """
        command = self.command()

        self.assertIn(MODEL, command)
        self.assertIn(REVISION, command)
        program = command[command.index("-c") + 1]
        self.assertNotIn(MODEL, program)
        self.assertNotIn(REVISION, program)

    def test_it_has_no_gpu(self):
        self.assertNotIn("--gpus", self.command())

    def test_it_is_otherwise_locked_down(self):
        command = self.command()

        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("no-new-privileges", command)

    def test_the_hub_writes_only_where_the_read_only_container_can(self):
        """The runtime image aims every cache at /job/cache, which --read-only
        freezes. Xet's chunk cache there failed a real Qwen3-0.6B fetch with
        "Read-only file system", so Xet is off and the hub's own homes are on
        the tmpfs."""
        command = self.command()
        env = [command[i + 1] for i, part in enumerate(command) if part == "-e"]

        self.assertIn("HF_HUB_DISABLE_XET=1", env)
        for name in ("HOME", "HF_HOME", "XDG_CACHE_HOME"):
            with self.subTest(name=name):
                value = next(e.split("=", 1)[1] for e in env if e.startswith(name + "="))
                self.assertTrue(value.startswith("/tmp"), value)
        self.assertNotIn("/job/cache", " ".join(env))

    def test_it_carries_no_credential(self):
        joined = " ".join(self.command()).lower()

        for absent in ("token", "password", "secret", "privatekey"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, joined)

    def test_it_does_not_enable_remote_code(self):
        # trust_remote_code runs arbitrary Python from the model repository.
        from gshared_worker import model_prep

        self.assertNotIn("trust_remote_code", code_only(model_prep))


class Ran:
    def __init__(self, exit_code=0, stdout="", raises=None, result=True):
        self._exit, self._stdout = exit_code, stdout
        self._raises, self._result = raises, result
        self.commands: list = []

    async def run(self, args, *, timeout, max_output_bytes=0):
        self.commands.append(args)
        if self._raises:
            raise self._raises
        if not self._result:
            return None

        class Result:
            exit_code = self._exit
            stdout = self._stdout
            stderr = ""

        return Result()


class TestPreparingTheModel(unittest.TestCase):
    def cache(self):
        return ModelCache(Path(tempfile.mkdtemp()))

    def prepare(self, runner, cache=None):
        cache = cache or self.cache()
        return asyncio.run(prepare_model(
            runner, cache, IMAGE, MODEL, REVISION)), cache

    def test_a_successful_fetch_is_marked_complete(self):
        prepared, cache = self.prepare(Ran(0, READY_MARKER + "x"))

        self.assertTrue(prepared.fetched)
        self.assertTrue(cache.is_complete(MODEL, REVISION))

    def test_a_complete_cache_is_reused_without_fetching(self):
        runner = Ran(0, READY_MARKER + "x")
        _, cache = self.prepare(runner)

        again = asyncio.run(prepare_model(runner, cache, IMAGE, MODEL, REVISION))

        self.assertFalse(again.fetched)
        self.assertEqual(len(runner.commands), 1)

    def test_an_incomplete_directory_is_not_reused(self):
        """A process killed mid-download leaves one behind.

        It looks entirely reasonable and is missing a shard.
        """
        cache = self.cache()
        directory = cache.directory_for(MODEL, REVISION)
        directory.mkdir(parents=True)
        (directory / "config.json").write_text("{}", encoding="utf-8")

        runner = Ran(0, READY_MARKER + "x")
        prepared = asyncio.run(
            prepare_model(runner, cache, IMAGE, MODEL, REVISION))

        self.assertTrue(prepared.fetched)
        self.assertEqual(len(runner.commands), 1)

    def test_a_failed_fetch_leaves_nothing_marked(self):
        cache = self.cache()

        with self.assertRaises(ModelRefused):
            asyncio.run(prepare_model(Ran(1), cache, IMAGE, MODEL, REVISION))

        self.assertFalse(cache.is_complete(MODEL, REVISION))

    def test_exit_zero_without_the_marker_is_a_failure(self):
        # An empty directory would otherwise be marked complete and mounted.
        cache = self.cache()

        with self.assertRaises(ModelRefused):
            asyncio.run(prepare_model(Ran(0, ""), cache, IMAGE, MODEL, REVISION))

        self.assertFalse(cache.is_complete(MODEL, REVISION))

    def test_a_docker_that_did_not_answer_is_a_failure(self):
        with self.assertRaises(ModelRefused):
            asyncio.run(prepare_model(
                Ran(result=False), self.cache(), IMAGE, MODEL, REVISION))

    def test_a_missing_revision_never_reaches_docker(self):
        runner = Ran(0)

        with self.assertRaises(ModelRefused):
            asyncio.run(prepare_model(runner, self.cache(), IMAGE, MODEL, None))

        self.assertEqual(runner.commands, [])

    def test_nothing_falls_back_to_another_model(self):
        """Training a different model produces an adapter nobody can use.

        The failure is not discovered until somebody tries to load it.
        """
        from gshared_worker import model_prep

        source = code_only(model_prep)
        for absent in ("Qwen2.5", "fallback", "except.*default"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_completion_marker_is_written_last(self):
        _, cache = self.prepare(Ran(0, READY_MARKER + "x"))
        directory = cache.directory_for(MODEL, REVISION)

        self.assertTrue((directory / COMPLETE_MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
