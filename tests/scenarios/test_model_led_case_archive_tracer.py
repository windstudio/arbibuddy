from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.case_archive import CaseArchive


ROOT = Path(__file__).resolve().parents[2]


class _NaturalLanguageScenarioModel:
    """Scenario-only model adapter; production code does not choose questions."""

    def __init__(self, workspace_root: Path, case_id: str | None = None):
        self.archive = CaseArchive(workspace_root)
        self.case_id = case_id

    def respond(self, user_message: str) -> dict[str, str]:
        if self.case_id is None:
            created = self.archive.create({"initial_goal": "梳理工资争议"})
            self.case_id = created["result"]["case_id"]
            return {
                "assistant": "我先为你建立一份可恢复的案情档案。工资是全部未付，还是只欠部分月份？",
                "case_id": self.case_id,
            }

        current = self.archive.read(self.case_id)
        revision = current["result"]["revision"]
        if "更正" in user_message:
            result = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": revision,
                    "change_summary": "用户纠正工资月份",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户纠正为 2026 年 2 月工资尚未支付。"
                            ),
                            "reason": "用户明确修正原先的月份表述",
                        }
                    ],
                }
            )
        else:
            result = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": revision,
                    "change_summary": "记录用户回答的工资月份",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                f"{user_message}"
                            ),
                        }
                    ],
                }
            )
        return {
            "assistant": "已记录这条事实；如果还有重要情况，可以直接补充。",
            "case_id": self.case_id,
            "ok": str(result["ok"]),
        }

    def resume(self) -> str:
        archive = self.archive.read(self.case_id or "")
        self.case_id = archive["result"]["case_id"]
        return "我已从现有案情继续，不会重复询问已经记录的事实。"


class ModelLedCaseArchiveTracerScenarioTests(unittest.TestCase):
    def test_skill_exposes_a_model_led_single_question_archive_path(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        contract = (ROOT / "references" / "model-led-case-archive.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("纯通用法律知识问答可以直接回答，不强制创建案件", skill)
        self.assertEqual(skill.count("每轮只提出一个需要用户回答的问题"), 1)
        self.assertIn("公开操作只有 `create`、`read`、`commit`", skill)
        self.assertIn("用户明确说刚才中断、要读取已保存案情", skill)
        self.assertIn("恢复-only 回合", skill)
        self.assertIn("create`、`read`、`commit` 三个档案操作", contract)
        self.assertNotIn("PublicTurn", skill)
        self.assertNotIn("agent_task_ready", skill)
        self.assertNotIn("advance/status", skill)

    def test_natural_language_case_correction_and_interruption_resume_from_one_archive(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            first_session = CaseArchive(Path(temporary_root))
            created = first_session.create({"initial_goal": "了解欠薪处理路径"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]

            collected = first_session.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户描述的工资事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户表示最近一个月工资尚未支付。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(collected["ok"], collected)

            corrected = first_session.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "用户自然语言纠正工资月份",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户纠正为 2026 年 2 月工资尚未支付。"
                            ),
                            "reason": "用户明确修正原先的月份表述",
                        }
                    ],
                }
            )
            self.assertTrue(corrected["ok"], corrected)

            resumed = CaseArchive(Path(temporary_root)).read(case_id)
            self.assertTrue(resumed["ok"], resumed)
            self.assertEqual(resumed["result"]["revision"], 2)
            self.assertIn("2026 年 2 月工资尚未支付", resumed["result"]["markdown"])
            self.assertNotIn("最近一个月工资尚未支付", resumed["result"]["markdown"])
            self.assertEqual(resumed["result"]["markdown"].count("[F-001]"), 1)
            self.assertNotIn("next-turn", resumed["result"]["markdown"])

    def test_user_visible_natural_language_tracer_has_one_question_and_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            model = _NaturalLanguageScenarioModel(workspace)

            first = model.respond("公司拖欠我工资，我想知道怎么办。")
            second = model.respond("是 2026 年 2 月那个月没有发工资。")
            correction = model.respond("更正一下，刚才说的月份是 2026 年 2 月。")
            resumed_model = _NaturalLanguageScenarioModel(workspace, model.case_id)
            resumed_message = resumed_model.resume()

            self.assertEqual(first["assistant"].count("？"), 1)
            self.assertEqual(second["assistant"].count("？"), 0)
            self.assertEqual(correction["assistant"].count("？"), 0)
            self.assertEqual(resumed_message.count("？"), 0)
            for message in (first["assistant"], second["assistant"], correction["assistant"], resumed_message):
                self.assertNotIn("JSON", message)
                self.assertNotIn("revision", message)
                self.assertNotIn("F-001", message)
                self.assertNotIn("commit", message)

            archive = resumed_model.archive.read(model.case_id or "")
            self.assertTrue(archive["ok"], archive)
            self.assertEqual(archive["result"]["revision"], 2)
            self.assertIn("2026 年 2 月工资尚未支付", archive["result"]["markdown"])
            self.assertNotIn("最近一个月工资尚未支付", archive["result"]["markdown"])


if __name__ == "__main__":
    unittest.main()
