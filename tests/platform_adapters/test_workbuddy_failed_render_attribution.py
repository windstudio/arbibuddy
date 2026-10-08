from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.workbuddy_acceptance.service import _audit_transcript_jsonl


class WorkBuddyFailedRenderAttributionTests(unittest.TestCase):
    def test_failed_render_does_not_require_a_success_receipt(self):
        with TemporaryDirectory() as temporary_root:
            violations: list[dict[str, str]] = []
            _audit_transcript_jsonl(
                Path("<workbuddy-native-trace>"),
                "session-1",
                violations,
                workspace=Path(temporary_root),
                case_id="case-123",
                records_override=[
                    {
                        "type": "function_call_result",
                        "sessionId": "session-1",
                        "name": "document.render-v1",
                        "output": {
                            "contract_version": "document.render-v1",
                            "operation": "document.render",
                            "ok": False,
                            "errors": [{"code": "io_failure"}],
                        },
                    }
                ],
            )

            self.assertNotIn(
                "render_evidence_missing",
                {item["code"] for item in violations},
            )
