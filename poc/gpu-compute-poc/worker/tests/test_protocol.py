import json
import unittest

from gpu_worker.errors import WorkerProtocolError
from gpu_worker.protocol import decode_execute


class ExecuteProtocolTest(unittest.TestCase):
    def test_decodes_only_job_id_and_allowlisted_workload(self):
        request = decode_execute(json.dumps({
            "type": "EXECUTE",
            "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
            "workload": "GPU_PYTORCH_TEST",
        }))
        self.assertEqual(request.workload, "GPU_PYTORCH_TEST")

    def test_rejects_image_command_args_and_mount_fields(self):
        for field in ("image", "command", "args", "mount"):
            with self.subTest(field=field):
                with self.assertRaises(WorkerProtocolError):
                    decode_execute(json.dumps({
                        "type": "EXECUTE",
                        "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
                        "workload": "GPU_PYTORCH_TEST",
                        field: "attacker-controlled",
                    }))

    def test_unknown_workload_is_parsed_without_accepting_an_image_override(self):
        request = decode_execute(json.dumps({
            "type": "EXECUTE",
            "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
            "workload": "UNKNOWN",
        }))
        self.assertEqual(request.workload, "UNKNOWN")
