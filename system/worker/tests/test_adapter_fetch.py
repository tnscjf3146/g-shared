"""Downloading an initial adapter and unpacking it without trusting the archive."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from gshared_worker import adapter_fetch
from gshared_worker.adapter_fetch import (
    AdapterRefused,
    fetch_adapter,
    unpack_adapter,
)
from gshared_worker.job_workspace import JobWorkspace

URL = "https://storage.example/jobs/123/input/adapter.tar?X-Signature=secret-sig"
CONFIG = json.dumps({"peft_type": "LORA", "r": 16,
                     "target_modules": ["q_proj"]}).encode("utf-8")
WEIGHTS = b"safetensors-bytes" * 64


def tar_bytes(entries, *, mode="w") -> bytes:
    """entries: (name, bytes) for files, or (name, TarInfo-customiser)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            if callable(content):
                content(info)
                archive.addfile(info)
                continue
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def result_tar() -> bytes:
    """The shape a finished Job's result.tar really has."""
    return tar_bytes([("adapter_config.json", CONFIG),
                      ("adapter_model.safetensors", WEIGHTS),
                      ("training_metrics.json", b"{}")])


class Response:
    def __init__(self, data: bytes, *, fail_after: int | None = None):
        self._stream = io.BytesIO(data)
        self._fail_after = fail_after
        self._read = 0

    def read(self, size):
        if self._fail_after is not None and self._read >= self._fail_after:
            raise ConnectionResetError("reset by " + URL)
        chunk = self._stream.read(size)
        self._read += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(*responses):
    queue = list(responses)
    calls = []

    def open_it(request, timeout=None):
        calls.append(request.full_url)
        return queue.pop(0)

    open_it.calls = calls
    return open_it


class Workdir(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.workspace = JobWorkspace.under(Path(self._temporary.name), "123").prepare()

    def fetch(self, data: bytes, **overrides):
        options = dict(expected_sha256=hashlib.sha256(data).hexdigest(),
                       expected_size=len(data), opener=opener_for(Response(data)))
        options.update(overrides)
        return fetch_adapter(URL, self.workspace.adapter_archive_path,
                             self.workspace.adapter_dir, **options)

    def refused(self, data: bytes, **overrides) -> AdapterRefused:
        with self.assertRaises(AdapterRefused) as caught:
            self.fetch(data, **overrides)
        self.assertFalse(self.workspace.adapter_dir.exists(),
                         "a refused adapter must leave no directory behind")
        self.assertNotIn("secret-sig", caught.exception.detail)
        return caught.exception


class TestAGoodAdapter(Workdir):
    def test_a_finished_jobs_result_tar_is_accepted_as_it_is(self):
        data = result_tar()
        fetched = self.fetch(data)

        self.assertEqual(fetched.files, ("adapter_config.json",
                                         "adapter_model.safetensors",
                                         "training_metrics.json"))
        self.assertEqual((self.workspace.adapter_dir / "adapter_model.safetensors")
                         .read_bytes(), WEIGHTS)
        self.assertEqual(fetched.sha256, hashlib.sha256(data).hexdigest())

    def test_the_two_required_files_alone_are_enough(self):
        self.fetch(tar_bytes([("adapter_config.json", CONFIG),
                              ("adapter_model.safetensors", WEIGHTS)]))

    def test_it_lands_inside_the_mounted_input_and_the_archive_is_gone(self):
        self.fetch(result_tar())

        self.assertEqual(self.workspace.adapter_dir.parent, self.workspace.input_dir)
        self.assertFalse(self.workspace.adapter_archive_path.exists())
        self.assertFalse(Path(str(self.workspace.adapter_archive_path) + ".part").exists())
        self.assertEqual(sorted(p.name for p in self.workspace.input_dir.iterdir()),
                         ["adapter"])

    def test_a_second_fetch_replaces_the_first(self):
        self.fetch(result_tar())
        (self.workspace.adapter_dir / "stale.txt").write_text("old", encoding="utf-8")

        self.fetch(result_tar())

        self.assertFalse((self.workspace.adapter_dir / "stale.txt").exists())


class TestTheDownloadIsVerified(Workdir):
    def test_a_different_hash(self):
        self.assertTrue(self.refused(result_tar(), expected_sha256="00" * 32).fatal)

    def test_a_different_size(self):
        data = result_tar()
        self.refused(data, expected_size=len(data) + 1)

    def test_an_unusable_hash_from_the_service(self):
        for value in ("", "0x" + "ab" * 32, "zz" * 32, "ab" * 31):
            with self.subTest(sha256=value):
                self.refused(result_tar(), expected_sha256=value)

    def test_a_size_outside_the_contract(self):
        for size in (0, -1, adapter_fetch.MAX_ADAPTER_BYTES + 1):
            with self.subTest(size=size):
                self.refused(result_tar(), expected_size=size)

    def test_only_http_urls(self):
        for url in ("file:///C:/Windows/win.ini", "ftp://h/a.tar", "https:///a.tar"):
            with self.subTest(url=url.split("?")[0]):
                with self.assertRaises(AdapterRefused):
                    fetch_adapter(url, self.workspace.adapter_archive_path,
                                  self.workspace.adapter_dir,
                                  expected_sha256="ab" * 32, expected_size=10,
                                  opener=opener_for())

    def test_a_broken_transfer_is_retried_then_given_up_without_the_url(self):
        data = result_tar()
        opener = opener_for(Response(data, fail_after=0), Response(data, fail_after=0),
                            Response(data, fail_after=0))
        error = self.refused(data, opener=opener, sleep=lambda seconds: True)

        self.assertFalse(error.fatal)
        self.assertEqual(len(opener.calls), 3)

    def test_a_transfer_that_recovers(self):
        data = result_tar()
        opener = opener_for(Response(data, fail_after=0), Response(data))

        self.fetch(data, opener=opener, sleep=lambda seconds: True)

    def test_a_worker_that_is_stopping_does_not_wait_to_retry(self):
        data = result_tar()
        opener = opener_for(Response(data, fail_after=0), Response(data))
        error = self.refused(data, opener=opener, sleep=lambda seconds: False)

        self.assertFalse(error.fatal)
        self.assertEqual(len(opener.calls), 1)


class TestTheArchiveIsNotTrusted(Workdir):
    """Each of these is a tar that ``extractall`` would have written from."""

    def test_compressed_archives(self):
        self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                ("adapter_model.safetensors", WEIGHTS)], mode="w:gz"))

    def test_not_an_archive_at_all(self):
        self.refused(WEIGHTS)

    def test_names_outside_the_list(self):
        for name in ("../adapter_config.json", "/etc/passwd", "adapter/adapter_config.json",
                     "./adapter_config.json", "notes.txt", "adapter_model.bin",
                     "..\\adapter_config.json"):
            with self.subTest(name=name):
                self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                        ("adapter_model.safetensors", WEIGHTS),
                                        (name, b"x")]))

    def test_a_directory_entry(self):
        def directory(info):
            info.type = tarfile.DIRTYPE
        self.refused(tar_bytes([("adapter_config.json", directory),
                                ("adapter_model.safetensors", WEIGHTS)]))

    def test_links_under_an_allowed_name(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            def link(info, kind=kind):
                info.type = kind
                info.linkname = "../../../../Windows/System32/drivers/etc/hosts"
            with self.subTest(kind=kind):
                self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                        ("adapter_model.safetensors", link)]))

    def test_the_same_name_twice(self):
        self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                ("adapter_model.safetensors", WEIGHTS),
                                ("adapter_config.json", CONFIG)]))

    def test_a_required_file_missing(self):
        self.refused(tar_bytes([("adapter_model.safetensors", WEIGHTS)]))
        self.refused(tar_bytes([("adapter_config.json", CONFIG)]))

    def test_empty_weights(self):
        self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                ("adapter_model.safetensors", b"")]))

    def test_a_config_that_is_not_a_json_object(self):
        for config in (b"{not json", b"[1, 2]"):
            with self.subTest(config=config):
                self.refused(tar_bytes([("adapter_config.json", config),
                                        ("adapter_model.safetensors", WEIGHTS)]))

    def test_nothing_is_written_outside_the_adapter_directory(self):
        root = Path(self._temporary.name)
        before = {p for p in root.rglob("*")}
        self.refused(tar_bytes([("adapter_config.json", CONFIG),
                                ("adapter_model.safetensors", WEIGHTS),
                                ("../escaped.txt", b"x")]))
        after = {p for p in root.rglob("*")}

        self.assertEqual({p.name for p in after - before}, set())


class TestUnpackingNeverExtracts(unittest.TestCase):
    def test_the_module_does_not_call_extract_or_extractall(self):
        from tests.test_job_progress import code_only

        code = code_only(adapter_fetch)
        self.assertNotIn(".extractall(", code)
        self.assertNotIn(".extract(", code)

    def test_unpack_can_be_used_on_its_own(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "a.tar"
            archive.write_bytes(result_tar())
            files = unpack_adapter(archive, Path(folder) / "out")

        self.assertIn("adapter_model.safetensors", files)


if __name__ == "__main__":
    unittest.main()
