from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.model_led_agent_eval.artifact_oracle import inspect_public_artifacts


class PublicArtifactOracleTests(unittest.TestCase):
    def test_removed_record_reference_in_update_history_is_not_dangling(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            appended = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录一条材料线索",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "用户称持有劳动合同。",
                        }
                    ],
                }
            )
            self.assertTrue(appended["ok"], appended)
            removed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "移除不再采用的材料线索",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "evidence",
                            "record_id": "E-001",
                            "reason": "用户撤回该材料线索",
                        }
                    ],
                }
            )
            self.assertTrue(removed["ok"], removed)

            archive_markdown = archive.read(case_id)["result"]["markdown"]
            self.assertIn("remove|evidence|E-001", archive_markdown)
            snapshot = inspect_public_artifacts(workspace)

            self.assertEqual(len(snapshot["case_archives"]), 1, snapshot)
            self.assertNotIn("E-001", snapshot["case_archives"][0]["record_ids"])

    def test_active_record_reference_to_missing_record_is_rejected(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            appended = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录一条事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户陈述：仍在职。",
                        }
                    ],
                }
            )
            self.assertTrue(appended["ok"], appended)
            path = workspace / ".arbibuddy" / "cases" / case_id / "案情档案.md"
            markdown = path.read_text(encoding="utf-8")
            path.write_text(
                markdown.replace(
                    "用户陈述：仍在职。",
                    "用户陈述：仍在职；引用不存在的记录 F-999。",
                ),
                encoding="utf-8",
            )

            snapshot = inspect_public_artifacts(workspace)

            self.assertEqual(snapshot["case_archives"], [])
