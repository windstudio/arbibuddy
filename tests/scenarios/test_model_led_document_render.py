from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from scripts.case_archive import CaseArchive
from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


class _NaturalLanguageDocumentModel:
    """Small scenario adapter: natural-language goal -> model-authored sections."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.archive = CaseArchive(workspace)
        self.renderer = DocumentRenderer(workspace)

    def draft_application(self, user_message: str) -> dict[str, object]:
        created = self.archive.create({"initial_goal": "准备劳动仲裁申请书"})
        assert created["ok"], created
        case_id = created["result"]["case_id"]
        committed = self.archive.commit(
            {
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录自然语言文书 Scenario 的脱敏事实和计算结果",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "申请人：张三；身份证号：310000199001010000；联系地址：上海市示例路1号。",
                    },
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "被申请人：示例公司；住所：上海市示例路2号；法定代表人：李四。",
                    },
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "申请日期：2026年9月8日；签名：张三。",
                    },
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": "文书标题：劳动人事争议仲裁申请书。",
                    },
                    {
                        "operation": "append",
                        "record_type": "claim",
                        "content_markdown": "仲裁请求：支付工资差额。",
                    },
                    {
                        "operation": "append",
                        "record_type": "calculation",
                        "content_markdown": "确定性计算结果：6000.00元。",
                    },
                    {
                        "operation": "append",
                        "record_type": "confirmation",
                        "content_markdown": (
                            f"用户自然语言确认：我确认按“{user_message}”的目标，"
                            "基于当前事实、请求范围和6000.00元计算结果继续准备仲裁申请书。\n"
                            "绑定档案修订版本：1"
                        ),
                    },
                ],
            }
        )
        assert committed["ok"], committed
        revision = committed["result"]["revision"]

        # The model chooses the user-facing prose. The tool receives no old
        # template payload, no Markdown document and no generated legal text.
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
                    "section_id": "facts_and_reasons",
                    "heading": "事实与理由",
                    "full_text": f"用户自然语言目标：{user_message}双方存在劳动关系，被申请人未支付工资差额。",
                },
                {"section_id": "closing", "heading": "落款", "full_text": "此致\n上海市示例劳动人事争议仲裁委员会\n申请人：张三\n2026年9月8日"},
            ],
            "locked_bindings": [
                {"kind": "document_heading", "archive_record_ref": "F-004", "rendered_value": "劳动人事争议仲裁申请书"},
                {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
                {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "被申请人：示例公司"},
                {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "支付工资差额"},
                {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "6000.00元"},
                {"kind": "date", "archive_record_ref": "F-003", "rendered_value": "2026年9月8日"},
                {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
            ],
            "confirmation_refs": ["CONF-001"],
        }
        return self.renderer.render(with_occurrence_bindings(request))


class ModelLedDocumentScenarioTests(unittest.TestCase):
    def test_natural_language_goal_reaches_real_external_final_docx(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            message = "请根据我描述的拖欠工资情况准备仲裁申请书"
            result = _NaturalLanguageDocumentModel(workspace).draft_application(message)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "final_ready")
            docx = Path(result["result"]["canonical_docx"])
            self.assertEqual(docx.suffix, ".docx")
            self.assertTrue(docx.is_file())
            self.assertFalse((docx.with_suffix(".md")).exists())
            with ZipFile(docx) as package:
                self.assertIn("word/document.xml", package.namelist())
                document_xml = package.read("word/document.xml").decode("utf-8")
            self.assertIn(message, document_xml)
            self.assertEqual(len(list(docx.parent.glob("*.docx"))), 1)
            self.assertEqual(len(list(docx.parent.glob("*仅供核对，请勿外发*.txt"))), 1)


if __name__ == "__main__":
    unittest.main()
