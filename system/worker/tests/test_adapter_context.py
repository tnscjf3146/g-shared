"""The execution context's optional ``adapter``: a LoRA adapter to continue from.

Contract with the Backend: ``adapter: { downloadUrl, sha256, sizeBytes }`` or
``null``. Absent is the same as null, so a Backend that predates the field
still runs every Job exactly as before.
"""

from __future__ import annotations

import unittest

from gshared_worker.execution_context import (
    AdapterContext,
    ContextRefused,
    read_context,
)

from tests.test_execution_context import body

ADAPTER = {"downloadUrl": "https://s/jobs/123/input/adapter.tar?sig=abc",
           "sha256": "ef" * 32, "sizeBytes": 40_427_520}


class TestNoAdapter(unittest.TestCase):
    def test_a_backend_that_does_not_send_the_field(self):
        self.assertIsNone(read_context(body()).adapter)

    def test_an_explicit_null(self):
        self.assertIsNone(read_context(body(adapter=None)).adapter)


class TestAnAdapter(unittest.TestCase):
    def test_every_field_is_read(self):
        adapter = read_context(body(adapter=dict(ADAPTER))).adapter

        self.assertEqual(adapter, AdapterContext(
            download_url=ADAPTER["downloadUrl"], sha256="ef" * 32,
            size_bytes=40_427_520))

    def test_the_rest_of_the_context_is_unchanged(self):
        with_adapter = read_context(body(adapter=dict(ADAPTER)))
        without = read_context(body())

        self.assertEqual(with_adapter.dataset, without.dataset)
        self.assertEqual(with_adapter.training, without.training)


class TestAHalfDescribedAdapterIsRefused(unittest.TestCase):
    """Not read as "no adapter": that would train from scratch without saying so."""

    def refused(self, adapter):
        with self.assertRaises(ContextRefused) as caught:
            read_context(body(adapter=adapter))
        return caught.exception.detail

    def test_not_an_object(self):
        for value in ("https://s/adapter.tar", [ADAPTER], 1, True):
            with self.subTest(adapter=value):
                self.refused(value)

    def test_a_missing_or_empty_field(self):
        for field in ("downloadUrl", "sha256", "sizeBytes"):
            for broken in (None, ""):
                with self.subTest(field=field, value=broken):
                    self.refused({**ADAPTER, field: broken})

    def test_a_size_that_is_not_a_real_size(self):
        for size in (0, -1, 1.5, "40427520", True):
            with self.subTest(sizeBytes=size):
                self.refused({**ADAPTER, "sizeBytes": size})

    def test_the_refusal_never_carries_the_signed_url(self):
        detail = self.refused({**ADAPTER, "sha256": ""})

        self.assertNotIn("sig=abc", detail)


if __name__ == "__main__":
    unittest.main()
