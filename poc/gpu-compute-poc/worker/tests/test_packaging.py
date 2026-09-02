from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def run_spec(base_prefix):
    spec_path = Path(__file__).parents[1] / "gpu-worker-agent.spec"
    captured = {}

    def analysis(*args, **kwargs):
        captured["binaries"] = kwargs["binaries"]
        return SimpleNamespace(
            pure=[],
            scripts=[],
            binaries=kwargs["binaries"],
            datas=kwargs["datas"],
        )

    spec_globals = {
        "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    }

    with patch.object(sys, "base_prefix", str(base_prefix)):
        runpy.run_path(str(spec_path), init_globals=spec_globals)

    return captured["binaries"]


class WorkerAgentSpecTest(unittest.TestCase):
    def test_existing_base_prefix_ffi_dll_is_collected_as_a_binary(self):

        with TemporaryDirectory() as directory:
            base_prefix = Path(directory)
            ffi_dll = base_prefix / "Library" / "bin" / "ffi-8.dll"
            ffi_dll.parent.mkdir(parents=True)
            ffi_dll.write_bytes(b"test ffi binary")

            binaries = run_spec(base_prefix)

            self.assertEqual(binaries, [(str(ffi_dll), ".")])

    def test_missing_base_prefix_ffi_dll_is_not_collected(self):
        with TemporaryDirectory() as directory:
            binaries = run_spec(Path(directory))

        self.assertEqual(binaries, [])


if __name__ == "__main__":
    unittest.main()
