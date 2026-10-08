from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.legal_verification.runtime_cli import main


class LegalVerificationRuntimeCliTests(unittest.TestCase):
    @staticmethod
    def _observation() -> dict[str, object]:
        return {
            "scope": "当前官方来源暂不可用时的降级记录",
            "trigger": "local_rule",
            "outcome": "unavailable",
            "official_sources": [],
            "accessed_on": "2026-09-18",
            "jurisdiction": "待用户确认",
            "impact": "地方口径保持待核验，不阻断全国通用分析。",
        }

    def test_record_consumes_managed_input_and_commits_authority(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证动态核验降级"}
            )
            case_id = created["result"]["case_id"]
            input_path = workspace / ".arbibuddy" / "runtime-input" / "authority.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "记录动态核验降级",
                        "observation": self._observation(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "record",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"], result)
            read = CaseArchive(workspace).read(case_id)
            self.assertIn("核验结论：官方来源暂未完成动态核验", read["result"]["markdown"])
            self.assertIn("适用地区：待用户确认", read["result"]["markdown"])

    def test_runtime_input_outside_managed_directory_is_not_read_or_deleted(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            input_path = workspace / "authority.json"
            input_path.write_text("{}", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "assess",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertTrue(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertEqual(result["errors"][0]["code"], "invalid_runtime_input_path")

    def test_record_blocks_unbound_local_publisher_without_archive_write(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证地方来源地区门禁"}
            )
            case_id = created["result"]["case_id"]
            input_path = workspace / ".arbibuddy" / "runtime-input" / "authority.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            observation = self._observation()
            observation["official_sources"] = [
                {
                    "title": "上海地方来源",
                    "url": "https://www.shanghai.gov.cn/example/law",
                    "jurisdiction": "全国",
                    "publisher_jurisdiction": "上海市",
                }
            ]
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "observation": observation,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "record",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 2, output.getvalue())
            self.assertFalse(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertEqual(
                result["errors"][0]["code"],
                "jurisdiction_confirmation_required",
            )
            read = CaseArchive(workspace).read(case_id)
            self.assertEqual(read["result"]["revision"], 0)
            self.assertNotIn("AUTH-001", read["result"]["markdown"])


if __name__ == "__main__":
    unittest.main()
