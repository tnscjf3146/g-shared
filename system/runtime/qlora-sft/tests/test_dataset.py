"""What the runtime will and will not accept as a training dataset.

The rejections matter more than the acceptances here. A malformed line that
slips through does not crash -- it trains, for an hour, on something that was
not what the customer meant, and the failure is discovered by whoever tries to
use the adapter.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qlora_sft.dataset import (COMPLETION_TEMPLATE, PROMPT_TEMPLATE,
                               Sample, read_dataset, read_sample)
from qlora_sft.failures import ExitCode, TrainingFailure


def a_line(**overrides) -> str:
    record = {"instruction": "결과는 어디서 보나요?", "input": "",
              "output": "결과 페이지에서 확인하실 수 있습니다."}
    record.update(overrides)
    return json.dumps(record, ensure_ascii=False)


def written(*lines: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8")
    handle.write("\n".join(lines))
    handle.close()
    return Path(handle.name)


class TestReadingGoodData(unittest.TestCase):
    def test_one_line_is_one_sample(self):
        samples = read_dataset(written(a_line()))

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].instruction, "결과는 어디서 보나요?")

    def test_every_line_is_read(self):
        self.assertEqual(len(read_dataset(written(a_line(), a_line(), a_line()))), 3)

    def test_a_blank_line_is_not_a_record(self):
        # A trailing newline is not a malformed sample, and neither is the
        # blank line an editor leaves at the end of a file.
        self.assertEqual(len(read_dataset(written(a_line(), "", a_line(), ""))), 2)

    def test_an_empty_input_is_allowed(self):
        self.assertEqual(read_dataset(written(a_line(input="")))[0].input, "")

    def test_a_missing_input_is_allowed(self):
        line = json.dumps({"instruction": "a", "output": "b"})

        self.assertEqual(read_sample(line, 1).input, "")

    def test_unicode_survives(self):
        samples = read_dataset(written(a_line(output="한글 응답입니다")))

        self.assertEqual(samples[0].output, "한글 응답입니다")


class TestRefusingBadData(unittest.TestCase):
    def refusal(self, *lines: str) -> TrainingFailure:
        with self.assertRaises(TrainingFailure) as caught:
            read_dataset(written(*lines))
        return caught.exception

    def test_not_json_at_all(self):
        self.assertEqual(self.refusal("this is not json").code,
                         ExitCode.INVALID_INPUT)

    def test_a_json_array_is_not_a_sample(self):
        self.assertIn("not a JSON object", self.refusal('["a", "b"]').detail)

    def test_a_missing_instruction(self):
        line = json.dumps({"output": "b"})

        self.assertIn("instruction", self.refusal(line).detail)

    def test_a_missing_output(self):
        line = json.dumps({"instruction": "a"})

        self.assertIn("output", self.refusal(line).detail)

    def test_an_instruction_that_is_not_a_string(self):
        self.assertIn("not a string", self.refusal(a_line(instruction=7)).detail)

    def test_an_output_that_is_not_a_string(self):
        self.assertIn("not a string", self.refusal(a_line(output=["x"])).detail)

    def test_an_input_that_is_not_a_string(self):
        self.assertIn("not a string", self.refusal(a_line(input=3)).detail)

    def test_a_null_output(self):
        self.assertIn("not a string", self.refusal(a_line(output=None)).detail)

    def test_a_whitespace_only_instruction(self):
        # Present, a string, and no instruction at all.
        self.assertIn("empty", self.refusal(a_line(instruction="   ")).detail)

    def test_a_whitespace_only_output(self):
        self.assertIn("empty", self.refusal(a_line(output="\n\t")).detail)

    def test_an_empty_file(self):
        self.assertIn("no samples", self.refusal("").detail)

    def test_a_file_that_is_not_there(self):
        failure = None
        try:
            read_dataset(Path("no-such-dataset-file.jsonl"))
        except TrainingFailure as caught:
            failure = caught

        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, ExitCode.INVALID_INPUT)

    def test_the_bad_line_is_named(self):
        self.assertIn("line 3", self.refusal(a_line(), a_line(), "{oops").detail)

    def test_the_refusal_does_not_quote_the_data(self):
        # The dataset is a customer's. This message goes to stdout, which the
        # supplier who owns the GPU can read.
        secret = "PATIENT-RECORD-4471"
        detail = self.refusal(json.dumps({"instruction": secret})).detail

        self.assertNotIn(secret, detail)


class TestTheOnePromptTemplate(unittest.TestCase):
    def test_an_instruction_alone_is_the_whole_prompt(self):
        self.assertEqual(Sample("어디서 보나요?", "", "여기서요").prompt,
                         "어디서 보나요?")

    def test_an_input_is_appended_below_the_instruction(self):
        sample = Sample("요약해 주세요", "본문입니다", "요약본")

        self.assertEqual(sample.prompt, "요약해 주세요\n\n본문입니다")

    def test_the_prompt_hands_over_to_the_assistant(self):
        # The completion has to start where the model starts speaking, or the
        # loss mask is in the wrong place.
        text = Sample("q", "", "a").prompt_text()

        self.assertTrue(text.endswith("<|im_start|>assistant\n"))

    def test_the_completion_is_the_output_and_a_stop(self):
        self.assertEqual(Sample("q", "", "a").completion_text(), "a<|im_end|>")

    def test_the_full_text_is_the_two_halves(self):
        sample = Sample("q", "i", "a")

        self.assertEqual(sample.full_text(),
                         sample.prompt_text() + sample.completion_text())

    def test_there_is_exactly_one_template(self):
        # Not a style rule -- a runtime with several templates has several
        # behaviours to explain when an adapter comes out wrong, and G-shared
        # has not trained a real job yet.
        from qlora_sft import dataset

        templates = [name for name in vars(dataset)
                     if name.endswith("_TEMPLATE")]

        self.assertEqual(sorted(templates),
                         ["COMPLETION_TEMPLATE", "PROMPT_TEMPLATE"])
        self.assertIn("{prompt}", PROMPT_TEMPLATE)
        self.assertIn("{output}", COMPLETION_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
