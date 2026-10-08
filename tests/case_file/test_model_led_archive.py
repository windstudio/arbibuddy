from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from scripts.case_archive import CaseArchive
from scripts.case_archive.cli import main as archive_cli_main
from scripts.case_archive.service import _process_is_alive


class ModelLedArchiveContractTests(unittest.TestCase):
    def test_create_keeps_managed_runtime_input_directory_available(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            created = CaseArchive(workspace).create({"initial_goal": "准备受管提交"})

            self.assertTrue(created["ok"], created)
            self.assertTrue(
                (workspace / ".arbibuddy" / "runtime-input").is_dir()
            )

    def test_commit_input_is_single_use_and_consumed_before_parsing(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            created = CaseArchive(workspace).create({"initial_goal": "使用受管输入"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            input_path = workspace / ".arbibuddy" / "runtime-input" / "commit.json"
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "记录一条受管输入事实",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": "用户陈述：通过受管输入提交。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                code = archive_cli_main(
                    ["--root", str(workspace), "commit", "--input", str(input_path)]
                )

            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(input_path.exists())
            self.assertEqual(CaseArchive(workspace).read(case_id)["result"]["revision"], 1)

            retry_output = StringIO()
            with redirect_stdout(retry_output):
                retry_code = archive_cli_main(
                    ["--root", str(workspace), "commit", "--input", str(input_path)]
                )

            self.assertEqual(retry_code, 2, retry_output.getvalue())
            self.assertEqual(
                json.loads(retry_output.getvalue())["errors"][0]["code"],
                "invalid_runtime_input_path",
            )

    def test_commit_input_rejects_external_and_nested_paths_without_consuming_them(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            created = CaseArchive(workspace).create({"initial_goal": "拒绝非受管输入"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            request = {
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "不应执行",
                "changes": [],
            }
            outside = workspace / "outside.json"
            outside.write_text(json.dumps(request), encoding="utf-8")
            nested = workspace / ".arbibuddy" / "runtime-input" / "nested" / "request.json"
            nested.parent.mkdir(parents=True)
            nested.write_text(json.dumps(request), encoding="utf-8")

            for input_path in (outside, nested):
                output = StringIO()
                with redirect_stdout(output):
                    code = archive_cli_main(
                        ["--root", str(workspace), "commit", "--input", str(input_path)]
                    )

                self.assertEqual(code, 2, output.getvalue())
                self.assertTrue(input_path.exists())
                self.assertEqual(
                    json.loads(output.getvalue())["errors"][0]["code"],
                    "invalid_runtime_input_path",
                )

            self.assertEqual(CaseArchive(workspace).read(case_id)["result"]["revision"], 0)

    def test_read_current_resolves_the_only_case_without_directory_discovery(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            created = CaseArchive(workspace).create({"initial_goal": "恢复当前案件"})
            self.assertTrue(created["ok"], created)
            output = StringIO()

            with redirect_stdout(output):
                code = archive_cli_main(
                    ["--root", str(workspace), "read-current"]
                )

            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["operation"], "read")
            self.assertEqual(result["result"]["case_id"], created["result"]["case_id"])

    def test_read_current_fails_closed_when_more_than_one_case_exists(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            self.assertTrue(archive.create({"initial_goal": "案件一"})["ok"])
            self.assertTrue(archive.create({"initial_goal": "案件二"})["ok"])
            output = StringIO()

            with redirect_stdout(output):
                code = archive_cli_main(
                    ["--root", str(workspace), "read-current"]
                )

            self.assertEqual(code, 2)
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "current_case_ambiguous")

    def test_installed_skill_integrity_failure_stops_archive_write_before_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            with patch(
                "scripts.runtime_identity.RuntimeIdentityModule.preflight",
                return_value={
                    "verified": False,
                    "failures": ["runtime_identity_marker_mismatch"],
                },
            ):
                response = CaseArchive(workspace).create(
                    {"initial_goal": "完整性失败不得写入案件"}
                )

            self.assertFalse(response["ok"], response)
            self.assertEqual(response["errors"][0]["code"], "installed_skill_modified")
            self.assertFalse((workspace / ".arbibuddy").exists())

    def test_successful_archive_operations_expose_only_fixed_user_visible_summary(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))

            created = tool.create({"initial_goal": "梳理欠薪事实"})
            self.assertEqual(created["result"]["user_visible_summary"], "案情档案已创建")
            case_id = created["result"]["case_id"]

            read = tool.read(case_id)
            self.assertEqual(read["result"]["user_visible_summary"], "案情档案已读取")

            committed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录一条用户陈述",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户陈述：存在工资差额。",
                        }
                    ],
                }
            )
            self.assertEqual(committed["result"]["user_visible_summary"], "案情档案已更新")
            for response in (created, read, committed):
                summary = response["result"]["user_visible_summary"]
                self.assertNotRegex(summary, r"(?:case-|[A-Z]{1,8}-\\d{3,}|revision|路径|JSON|CLI)")

    def test_create_and_read_return_the_same_authoritative_markdown_archive(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))

            created = tool.create(
                {
                    "case_label": "工资争议",
                    "jurisdiction": "上海",
                    "initial_goal": "梳理事实并准备下一步",
                }
            )

            self.assertTrue(created["ok"], created)
            self.assertEqual(created["operation"], "create")
            self.assertEqual(created["result"]["revision"], 0)
            case_id = created["result"]["case_id"]
            markdown = created["result"]["markdown"]
            self.assertTrue(case_id)
            self.assertNotIn("工资争议", case_id)
            self.assertIn("# 案情档案", markdown)
            self.assertIn("## 案件元数据", markdown)
            self.assertIn("## 事实", markdown)
            self.assertNotIn("姓名", markdown)
            self.assertNotIn("身份证号", markdown)

            read = tool.read(case_id)

            self.assertTrue(read["ok"], read)
            self.assertEqual(read["operation"], "read")
            self.assertEqual(read["result"]["case_id"], case_id)
            self.assertEqual(read["result"]["revision"], 0)
            self.assertEqual(read["result"]["markdown"], markdown)
            self.assertTrue(
                Path(read["result"]["canonical_location"]).is_file()
            )

    def test_commit_appends_a_user_statement_and_increments_the_archive_revision(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create({"initial_goal": "梳理欠薪事实"})
            case_id = created["result"]["case_id"]

            committed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户刚刚补充的欠薪事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户表示 2026 年 2 月工资尚未支付。"
                            ),
                        }
                    ],
                }
            )

            self.assertTrue(committed["ok"], committed)
            self.assertEqual(committed["operation"], "commit")
            self.assertEqual(committed["result"]["previous_revision"], 0)
            self.assertEqual(committed["result"]["revision"], 1)
            self.assertEqual(committed["result"]["generated_record_ids"], ["F-001"])
            self.assertEqual(
                committed["result"]["applied_changes"],
                [{"operation": "append", "record_type": "fact", "record_id": "F-001"}],
            )

            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertIn("[F-001]", read["result"]["markdown"])
            self.assertIn("用户表示 2026 年 2 月工资尚未支付。", read["result"]["markdown"])

    def test_commit_keeps_user_statement_evidence_and_analysis_in_distinct_archive_sections(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]

            committed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "分别记录事实、材料和待验证假设",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n用户表示已工作两年。",
                        },
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "工资流水可用于支持工资支付事实。",
                        },
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "**事实状态：分析假设**\n尚待核对劳动合同期限。",
                        },
                    ],
                }
            )

            self.assertTrue(committed["ok"], committed)
            self.assertEqual(
                committed["result"]["generated_record_ids"], ["F-001", "E-001", "A-001"]
            )
            markdown = tool.read(case_id)["result"]["markdown"]
            self.assertLess(markdown.index("## 事实"), markdown.index("[F-001]"))
            self.assertLess(markdown.index("## 证据材料"), markdown.index("[E-001]"))
            self.assertLess(markdown.index("## 分析与假设"), markdown.index("[A-001]"))
            self.assertIn("事实状态：用户陈述", markdown)
            self.assertIn("事实状态：分析假设", markdown)

    def test_commit_rejects_a_stale_revision_without_changing_the_archive(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            first = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录第一条事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n第一条事实。",
                        }
                    ],
                }
            )
            self.assertTrue(first["ok"], first)
            before = tool.read(case_id)

            stale = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "尝试覆盖并发更新",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n不应写入。",
                        }
                    ],
                }
            )

            self.assertFalse(stale["ok"], stale)
            self.assertEqual(stale["errors"][0]["code"], "revision_conflict")
            self.assertEqual(stale["errors"][0]["current_revision"], 1)
            after = tool.read(case_id)
            self.assertTrue(after["ok"], after)
            self.assertEqual(after["result"]["markdown"], before["result"]["markdown"])
            self.assertEqual(after["result"]["revision"], 1)

    def test_invalid_batch_aggregates_errors_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            before = tool.read(case_id)

            failed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "这批更新包含多个错误",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                        },
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-999",
                            "content_markdown": "不允许出现在 remove 中。",
                        },
                    ],
                }
            )

            self.assertFalse(failed["ok"], failed)
            codes = {error["code"] for error in failed["errors"]}
            self.assertIn("invalid_change", codes)
            self.assertGreaterEqual(len(failed["errors"]), 2)
            after = tool.read(case_id)
            self.assertTrue(after["ok"], after)
            self.assertEqual(after["result"]["revision"], 0)
            self.assertEqual(after["result"]["markdown"], before["result"]["markdown"])

    def test_replace_updates_the_current_fact_and_remove_deletes_it(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            appended = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录待纠正事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n原始陈述。",
                        }
                    ],
                }
            )
            self.assertTrue(appended["ok"], appended)

            corrected = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "用户纠正了原始陈述",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "content_markdown": "**事实状态：用户陈述**\n修正后的陈述。",
                            "reason": "用户明确表示上一条陈述有误",
                        }
                    ],
                }
            )
            self.assertTrue(corrected["ok"], corrected)
            archive = tool.read(case_id)
            self.assertTrue(archive["ok"], archive)
            self.assertIn("修正后的陈述。", archive["result"]["markdown"])
            self.assertNotIn("原始陈述。", archive["result"]["markdown"])
            self.assertEqual(archive["result"]["markdown"].count("[F-001]"), 1)

            removed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 2,
                    "change_summary": "移除已撤回事实",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "reason": "用户撤回该陈述",
                        }
                    ],
                }
            )
            self.assertTrue(removed["ok"], removed)
            archive = tool.read(case_id)
            self.assertTrue(archive["ok"], archive)
            self.assertNotIn("[F-001]", archive["result"]["markdown"])

    def test_remove_rejects_referenced_records_and_never_reuses_deleted_record_ids(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            first = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录可被材料引用的事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n被引用的事实。",
                        },
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "该材料支持 F-001。",
                        },
                    ],
                }
            )
            self.assertTrue(first["ok"], first)

            blocked = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "尝试删除仍被引用的事实",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "reason": "用户撤回事实",
                        }
                    ],
                }
            )
            self.assertFalse(blocked["ok"], blocked)
            self.assertEqual(blocked["errors"][0]["code"], "dangling_reference")
            self.assertEqual(tool.read(case_id)["result"]["revision"], 1)

            remove_evidence = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "移除引用材料",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "evidence",
                            "record_id": "E-001",
                            "reason": "材料不再纳入当前案件",
                        }
                    ],
                }
            )
            self.assertTrue(remove_evidence["ok"], remove_evidence)
            remove_fact = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 2,
                    "change_summary": "移除已撤回事实",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "reason": "用户撤回事实",
                        }
                    ],
                }
            )
            self.assertTrue(remove_fact["ok"], remove_fact)

            next_fact = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 3,
                    "change_summary": "记录新的用户陈述",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n新的事实不复用旧编号。",
                        }
                    ],
                }
            )
            self.assertTrue(next_fact["ok"], next_fact)
            self.assertEqual(next_fact["result"]["generated_record_ids"], ["F-002"])

    def test_replaying_the_same_commit_is_idempotent_and_returns_the_original_result(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            request = {
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录可安全重放的事实",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "**事实状态：用户陈述**\n工资被拖欠。",
                    }
                ],
            }

            first = tool.commit(request)
            replay = tool.commit(request)

            self.assertTrue(first["ok"], first)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["result"], first["result"])
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertEqual(read["result"]["markdown"].count("[F-001]"), 1)

    def test_a_new_request_with_duplicate_record_content_is_rejected_without_a_write(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            content = "**事实状态：用户陈述**\n同一事实只应记录一次。"
            first = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "首次记录事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": content,
                        }
                    ],
                }
            )
            self.assertTrue(first["ok"], first)

            duplicate = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "重复记录相同事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": content,
                        }
                    ],
                }
            )

            self.assertFalse(duplicate["ok"], duplicate)
            self.assertEqual(duplicate["errors"][0]["code"], "duplicate_record")
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertEqual(read["result"]["markdown"].count("[F-001]"), 1)

    def test_commit_rejects_dangling_record_references_and_accepts_existing_references(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            fact = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "先记录被引用的事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n有一条基础事实。",
                        }
                    ],
                }
            )
            self.assertTrue(fact["ok"], fact)

            valid_reference = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "记录引用事实的证据",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "材料支持 F-001 所述事实。",
                        }
                    ],
                }
            )
            self.assertTrue(valid_reference["ok"], valid_reference)

            dangling = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 2,
                    "change_summary": "引用不存在的事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "分析仍依赖 F-999。",
                        }
                    ],
                }
            )
            self.assertFalse(dangling["ok"], dangling)
            self.assertEqual(dangling["errors"][0]["code"], "dangling_reference")
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 2)

    def test_commit_allows_analysis_to_reference_calculations_appended_later_in_the_same_batch(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]

            committed = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录计算结果并保存分析引用",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "分析依赖同批次生成的 CAL-001 与 CAL-002。",
                        },
                        {
                            "operation": "append",
                            "record_type": "calculation",
                            "content_markdown": "第一种情景的计算结果。",
                        },
                        {
                            "operation": "append",
                            "record_type": "calculation",
                            "content_markdown": "第二种情景的计算结果。",
                        },
                    ],
                }
            )

            self.assertTrue(committed["ok"], committed)
            self.assertEqual(
                committed["result"]["generated_record_ids"],
                ["A-001", "CAL-001", "CAL-002"],
            )
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertIn("CAL-001", read["result"]["markdown"])
            self.assertIn("CAL-002", read["result"]["markdown"])

    def test_commit_rejects_an_append_reference_to_a_record_removed_earlier_in_the_same_batch(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            fact = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录待撤回事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n待撤回事实。",
                        }
                    ],
                }
            )
            self.assertTrue(fact["ok"], fact)

            rejected = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "撤回事实并记录不应继续存在的引用",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "reason": "用户撤回事实",
                        },
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "分析仍错误引用已撤回的 F-001。",
                        },
                    ],
                }
            )

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["errors"][0]["code"], "dangling_reference")
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertIn("[F-001]", read["result"]["markdown"])

    def test_commit_rejects_a_replace_reference_to_a_record_removed_earlier_in_the_same_batch(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            initial = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录事实和独立分析",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n待撤回事实。",
                        },
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "当前分析尚未引用该事实。",
                        },
                    ],
                }
            )
            self.assertTrue(initial["ok"], initial)

            rejected = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "撤回事实并更新不应继续存在的引用",
                    "changes": [
                        {
                            "operation": "remove",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "reason": "用户撤回事实",
                        },
                        {
                            "operation": "replace",
                            "record_type": "analysis",
                            "record_id": "A-001",
                            "content_markdown": "更新后的分析仍错误引用已撤回的 F-001。",
                            "reason": "同步修正分析内容",
                        },
                    ],
                }
            )

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["errors"][0]["code"], "dangling_reference")
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertIn("[F-001]", read["result"]["markdown"])

    def test_read_distinguishes_missing_case_from_corrupt_archive_with_recoverable_errors(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))

            missing = tool.read("case-000000000000000000000000")

            self.assertFalse(missing["ok"], missing)
            self.assertEqual(missing["errors"][0]["code"], "case_not_found")
            self.assertTrue(
                {"code", "path", "message", "expected", "received", "recoverable"}
                <= set(missing["errors"][0])
            )

            created = tool.create()
            case_id = created["result"]["case_id"]
            archive_path = Path(created["result"]["canonical_location"])
            archive_path.write_text(
                created["result"]["markdown"].replace("## 事实\n", "", 1),
                encoding="utf-8",
            )

            corrupt = tool.read(case_id)

            self.assertFalse(corrupt["ok"], corrupt)
            self.assertEqual(corrupt["errors"][0]["code"], "archive_corrupt")
            self.assertTrue(
                all(
                    {"code", "path", "message", "expected", "received", "recoverable"}
                    <= set(error)
                    for error in corrupt["errors"]
                )
            )
            self.assertTrue(all(error["recoverable"] for error in corrupt["errors"]))

    def test_read_reports_permission_denied_as_a_distinct_recoverable_boundary_error(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]

            def deny_archive_read(*args, **kwargs):
                raise PermissionError("denied")

            with patch(
                "scripts.case_archive.service._RUNTIME_IDENTITY.preflight",
                return_value={"verified": True},
            ), patch.object(
                Path,
                "read_text",
                autospec=True,
                side_effect=deny_archive_read,
            ):
                denied = tool.read(case_id)

            self.assertFalse(denied["ok"], denied)
            self.assertEqual(denied["errors"][0]["code"], "permission_denied")

    def test_interrupted_atomic_replace_keeps_the_previous_archive_intact(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            archive_path = Path(created["result"]["canonical_location"])
            before = archive_path.read_bytes()

            with patch(
                "scripts.case_archive.service.os.replace",
                side_effect=OSError("simulated process interruption"),
            ):
                failed = tool.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "模拟中断",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": "**事实状态：用户陈述**\n不应部分写入。",
                            }
                        ],
                    }
                )

            self.assertFalse(failed["ok"], failed)
            self.assertEqual(failed["errors"][0]["code"], "io_failure")
            self.assertEqual(archive_path.read_bytes(), before)
            self.assertEqual(list(archive_path.parent.glob("*.tmp")), [])

    def test_next_commit_recovers_a_lock_left_by_an_interrupted_process(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            archive_path = Path(created["result"]["canonical_location"])
            stale_lock = archive_path.parent / ".archive.lock"
            stale_lock.mkdir()
            (stale_lock / "owner").write_text("99999999", encoding="ascii")

            recovered = tool.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "从中断锁恢复并提交",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n恢复后的事实。",
                        }
                    ],
                }
            )

            self.assertTrue(recovered["ok"], recovered)
            self.assertFalse(stale_lock.exists())

    @unittest.skipUnless(os.name == "nt", "Windows process liveness uses kernel32")
    def test_windows_process_liveness_uses_waitable_handles_not_os_kill(self):
        kernel = SimpleNamespace(
            OpenProcess=Mock(side_effect=[101, 102]),
            WaitForSingleObject=Mock(side_effect=[258, 0]),
            CloseHandle=Mock(return_value=True),
        )
        with (
            patch("ctypes.WinDLL", return_value=kernel),
            patch("scripts.case_archive.service.os.kill") as process_signal,
        ):
            self.assertTrue(_process_is_alive(1010))
            self.assertFalse(_process_is_alive(1020))

        self.assertEqual(
            kernel.OpenProcess.call_args_list,
            [
                call(0x00100000, False, 1010),
                call(0x00100000, False, 1020),
            ],
        )
        self.assertEqual(kernel.WaitForSingleObject.call_args_list, [call(101, 0), call(102, 0)])
        self.assertEqual(kernel.CloseHandle.call_args_list, [call(101), call(102)])
        process_signal.assert_not_called()

        for error_code, expected in ((5, True), (87, False)):
            with self.subTest(error_code=error_code):
                failed_open = SimpleNamespace(
                    OpenProcess=Mock(return_value=None),
                    WaitForSingleObject=Mock(),
                    CloseHandle=Mock(),
                )
                with (
                    patch("ctypes.WinDLL", return_value=failed_open),
                    patch("ctypes.get_last_error", return_value=error_code),
                    patch("scripts.case_archive.service.os.kill") as process_signal,
                ):
                    self.assertIs(_process_is_alive(1030), expected)
                process_signal.assert_not_called()

    def test_successful_commit_reports_cleanup_warning_without_rolling_back(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]
            original_unlink = Path.unlink

            def fail_temp_cleanup(path: Path, *args, **kwargs):
                if path.name.endswith(".tmp"):
                    raise OSError("simulated cleanup permission failure")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=fail_temp_cleanup):
                committed = tool.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "记录事实并模拟清理告警",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": "**事实状态：用户陈述**\n已提交事实。",
                            }
                        ],
                    }
                )

            self.assertTrue(committed["ok"], committed)
            self.assertEqual(
                [warning["code"] for warning in committed["warnings"]],
                ["temporary_cleanup_failed"],
            )
            read = tool.read(case_id)
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["result"]["revision"], 1)
            self.assertIn("已提交事实。", read["result"]["markdown"])

    def test_two_cases_can_commit_concurrently_without_cross_case_updates(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            first = tool.create({"initial_goal": "案件甲"})
            second = tool.create({"initial_goal": "案件乙"})
            first_id = first["result"]["case_id"]
            second_id = second["result"]["case_id"]

            def commit(case_id: str, text: str):
                return tool.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "并发记录一条事实",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": f"**事实状态：用户陈述**\n{text}",
                            }
                        ],
                    }
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda item: commit(*item),
                        ((first_id, "案件甲的事实。"), (second_id, "案件乙的事实。")),
                    )
                )

            self.assertTrue(all(result["ok"] for result in results), results)
            first_read = tool.read(first_id)
            second_read = tool.read(second_id)
            self.assertTrue(first_read["ok"], first_read)
            self.assertTrue(second_read["ok"], second_read)
            self.assertEqual(first_read["result"]["revision"], 1)
            self.assertEqual(second_read["result"]["revision"], 1)
            self.assertIn("案件甲的事实。", first_read["result"]["markdown"])
            self.assertNotIn("案件乙的事实。", first_read["result"]["markdown"])
            self.assertIn("案件乙的事实。", second_read["result"]["markdown"])
            self.assertNotIn("案件甲的事实。", second_read["result"]["markdown"])

    def test_create_rejects_non_object_input_with_a_stable_error(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))

            result = tool.create(["not", "an", "object"])

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["errors"][0]["code"], "invalid_change")
            self.assertEqual(result["errors"][0]["path"], "create")

    def test_cli_input_failure_keeps_the_public_operation_name(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = archive_cli_main(
                    ["--root", temporary_root, "commit", "--json", "{"]
                )

            self.assertEqual(exit_code, 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["operation"], "commit")
            self.assertFalse(result["ok"])

    def test_commit_of_a_missing_case_returns_case_not_found_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))

            result = tool.commit(
                {
                    "case_id": "case-000000000000000000000000",
                    "expected_revision": 0,
                    "change_summary": "不存在的案件",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n不会写入。",
                        }
                    ],
                }
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["errors"][0]["code"], "case_not_found")

    def test_same_case_concurrent_commits_converge_on_one_revision(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            tool = CaseArchive(Path(temporary_root))
            created = tool.create()
            case_id = created["result"]["case_id"]

            def commit(text: str):
                return tool.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": 0,
                        "change_summary": "两个模型同时提交事实",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": f"**事实状态：用户陈述**\n{text}",
                            }
                        ],
                    }
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(commit, ("并发事实甲。", "并发事实乙。")))

            self.assertEqual(sum(result["ok"] for result in results), 1, results)
            self.assertEqual(
                sum(
                    result["errors"][0]["code"] == "revision_conflict"
                    for result in results
                    if not result["ok"]
                ),
                1,
            )
            archive = tool.read(case_id)
            self.assertTrue(archive["ok"], archive)
            self.assertEqual(archive["result"]["revision"], 1)
            self.assertEqual(
                sum(
                    marker in archive["result"]["markdown"]
                    for marker in ("并发事实甲。", "并发事实乙。")
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
