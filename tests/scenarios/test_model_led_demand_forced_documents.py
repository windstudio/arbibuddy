from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from tests.documents.model_led_demand_forced_fixtures import (
    create_case,
    forced_request,
)
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


def _history_request(case_id: str, revision: int, history_fact_ref: str) -> dict[str, object]:
    request = {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "arbitration_application",
        "template_version": "1.0.0",
        "mode": "external_final",
        "title": "劳动人事争议仲裁申请书",
        "sections": [
            {"section_id": "applicant", "heading": "申请人", "full_text": "申请人：张三。"},
            {"section_id": "respondent", "heading": "被申请人", "full_text": "被申请人：示例公司。"},
            {"section_id": "requests", "heading": "仲裁请求", "full_text": "请求支付工资差额6000.00元。"},
            {
                "section_id": "facts",
                "heading": "事实与理由",
                "full_text": "解除已经发生，本次只分析历史索赔，不提出新的解除动作。",
            },
            {"section_id": "closing", "heading": "落款", "full_text": "此致\n仲裁委员会\n申请人：张三\n2026年9月8日"},
        ],
        "locked_bindings": [
            {
                "kind": "document_heading",
                "archive_record_ref": history_fact_ref,
                "rendered_value": "劳动人事争议仲裁申请书",
            },
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "劳动者：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "用人单位：示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "支付2026年8月工资差额"},
            {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "6000.00元"},
            {"kind": "date", "archive_record_ref": "F-003", "rendered_value": "2026年9月8日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
        ],
        "placeholders": [],
        "confirmation_refs": [],
        "authority_refs": [],
    }
    return with_occurrence_bindings(request)


class _NaturalLanguageDocumentScenarioModel:
    def __init__(self, workspace: Path, *, confirmed: bool = False):
        self.workspace = workspace
        self.archive, self.case_id, self.revision, self.confirmation_id = create_case(
            workspace, with_confirmation=False
        )
        self.renderer = DocumentRenderer(workspace)
        added = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": self.revision,
                "change_summary": "记录历史索赔材料标题",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "历史索赔材料标题：劳动人事争议仲裁申请书。",
                    }
                ],
            }
        )
        assert added["ok"], added
        self.revision = int(added["result"]["revision"])
        self.history_fact_ref = str(added["result"]["generated_record_ids"][0])
        if confirmed:
            confirmation = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": self.revision,
                    "change_summary": "记录计划被迫解除通知的实质知情确认",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "高风险确认标识：planned_termination\n"
                                "action_type：planned_termination\n"
                                "action_scope：按当前事实准备被迫解除通知书，劳动者张三向示例公司发出，理由为未及时足额支付劳动报酬，使用欠薪流水，通知日期为2026年9月8日，解除生效日期为2026年9月20日，不包含自动发送或提交。\n"
                                "risk_summary：解除理由、日期、程序、送达、时效和证据不足可能影响后果。\n"
                                "alternatives_presented：先催告履行、补强证据、暂缓解除并保留历史索赔。\n"
                                "user_choice：我明确选择按已说明范围继续准备被迫解除通知书。\n"
                                "confirmed_at：2026-09-08\n"
                                f"archive_revision：{self.revision + 1}\n"
                                "source_refs：F-001、F-002、F-003、F-004、CL-001、E-001、AUTH-001。"
                            ),
                        }
                    ],
                }
            )
            assert confirmation["ok"], confirmation
            self.revision = int(confirmation["result"]["revision"])
            self.confirmation_id = str(confirmation["result"]["generated_record_ids"][0])

    def render_for_goal(self, natural_language_goal: str) -> dict[str, object]:
        if "历史索赔" in natural_language_goal:
            return self.renderer.render(
                _history_request(self.case_id, self.revision, self.history_fact_ref)
            )
        if "送达" in natural_language_goal:
            request = forced_request(self.case_id, self.revision, mode="candidate")
            request["sections"][3]["full_text"] = "送达事实尚待核对，本次先保留候选内容，不作新的解除动作。"
            request["locked_bindings"] = [
                binding
                for binding in request["locked_bindings"]
                if binding["kind"] != "amount"
            ]
            return self.renderer.render(with_occurrence_bindings(request))
        if "明确确认" in natural_language_goal:
            assert self.confirmation_id is not None
            return self.renderer.render(
                forced_request(
                    self.case_id,
                    self.revision,
                    confirmation_refs=[self.confirmation_id],
                    authority_refs=["AUTH-001"],
                )
            )
        return self.renderer.render(
            forced_request(self.case_id, self.revision, mode="candidate")
        )


class ModelLedDemandAndForcedDocumentScenarioTests(unittest.TestCase):
    def test_natural_language_goals_keep_history_planned_and_delivery_uncertainty_separate(self):
        with TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageDocumentScenarioModel(Path(temporary_root))

            historical = model.render_for_goal("解除已经发生，我只想准备历史索赔材料")
            planned = model.render_for_goal("我计划发送被迫解除通知")
            sent_unknown = model.render_for_goal("通知已发送，但送达事实不明")

            self.assertTrue(historical["ok"], historical)
            self.assertEqual(historical["result"]["document_type"], "arbitration_application")
            self.assertEqual(historical["result"]["mode"], "external_final")
            self.assertTrue(planned["ok"], planned)
            self.assertEqual(planned["result"]["delivery_state"], "candidate_ready")
            self.assertTrue(sent_unknown["ok"], sent_unknown)
            self.assertEqual(sent_unknown["result"]["delivery_state"], "candidate_ready")
            forced_output = Path(planned["result"]["canonical_docx"]).parent
            self.assertFalse((forced_output / "《被迫解除劳动合同通知书》.docx").exists())

    def test_natural_language_explicit_confirmation_reaches_only_forced_external_final(self):
        with TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageDocumentScenarioModel(Path(temporary_root), confirmed=True)

            result = model.render_for_goal("我明确确认按已说明范围继续准备被迫解除通知")

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["document_type"], "forced_termination_notice")
            self.assertEqual(result["result"]["delivery_state"], "final_ready")
            self.assertTrue(Path(result["result"]["canonical_docx"]).is_file())


if __name__ == "__main__":
    unittest.main()
