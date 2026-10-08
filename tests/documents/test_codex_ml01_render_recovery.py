from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.case_archive import CaseArchive
from scripts.documents import public as public_documents
from scripts.documents.public import render


def _archive_without_calculation(workspace: Path) -> tuple[str, int]:
    archive = CaseArchive(workspace)
    created = archive.create({"initial_goal": "隔离重放候选仲裁申请书"})
    assert created["ok"], created
    case_id = created["result"]["case_id"]
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": 0,
            "change_summary": "记录候选文书所需的脱敏事实、主张和证据",
            "changes": [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "合同约定月薪；具体数额和工资构成待根据合同与工资凭证核实。",
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": "仲裁请求：支付工资差额。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": "证据名称：劳动合同与工资条；材料内容待核。",
                },
            ],
        }
    )
    assert committed["ok"], committed
    assert not any(
        record_id.startswith("CAL-")
        for record_id in committed["result"]["generated_record_ids"]
    )
    revision = committed["result"]["revision"]
    for summary in ("确认已记录", "分析摘要已记录"):
        committed = archive.commit(
            {
                "case_id": case_id,
                "expected_revision": revision,
                "change_summary": summary,
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": f"{summary}。",
                    }
                ],
            }
        )
        assert committed["ok"], committed
        revision = committed["result"]["revision"]
    return case_id, revision


def _candidate_request(case_id: str, revision: int) -> dict:
    return {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "arbitration_application",
        "template_version": "1.0.0",
        "mode": "candidate",
        "title": "劳动人事争议仲裁申请书",
        "sections": [
            {
                "section_id": "applicant",
                "heading": "申请人",
                "full_text": "姓名：【待补：申请人姓名】",
            },
            {
                "section_id": "respondent",
                "heading": "被申请人",
                "full_text": "名称：【待补：用人单位名称】",
            },
            {
                "section_id": "requests",
                "heading": "仲裁请求",
                "full_text": "请求支付工资差额【待核：工资差额金额】。",
            },
            {
                "section_id": "facts",
                "heading": "事实与理由",
                "full_text": (
                    "合同载明月薪12,000元；具体工资构成需结合劳动合同和工资凭证核实。"
                    "现有证据包括劳动合同与工资条。"
                ),
            },
            {
                "section_id": "closing",
                "heading": "落款",
                "full_text": (
                    "此致\n【待补：仲裁委员会名称】\n"
                    "申请人（签名）：【待补：申请人签名】\n【待补：申请日期】"
                ),
            },
        ],
        "locked_bindings": [
            {
                "kind": "claim",
                "archive_record_ref": "CL-001",
                "rendered_value": "支付工资差额",
            },
            {
                "kind": "evidence",
                "archive_record_ref": "E-001",
                "rendered_value": "劳动合同与工资条",
            },
        ],
        "placeholders": [
            {"placeholder_id": "PH-APPLICANT", "text": "【待补：申请人姓名】"},
            {"placeholder_id": "PH-RESPONDENT", "text": "【待补：用人单位名称】"},
            {"placeholder_id": "PH-WAGE-CLAIM", "text": "【待核：工资差额金额】"},
            {"placeholder_id": "PH-ARBITRATION-ORG", "text": "【待补：仲裁委员会名称】"},
            {"placeholder_id": "PH-SIGNATURE", "text": "【待补：申请人签名】"},
            {"placeholder_id": "PH-DATE", "text": "【待补：申请日期】"},
        ],
    }


class CodexMl01RenderRecoveryTests(unittest.TestCase):
    def _candidate_with_fact_salary(self, workspace):
        case_id, revision = _archive_without_calculation(workspace)
        saved = CaseArchive(workspace).commit({
            "case_id": case_id, "expected_revision": revision,
            "change_summary": "保留用户陈述的月薪，不生成计算结果",
            "changes": [{"operation": "append", "record_type": "fact",
                         "content_markdown": "用户陈述：约定月薪12000元；合同原文及工资构成待核。"}],
        })
        self.assertTrue(saved["ok"], saved)
        request = _candidate_request(case_id, saved["result"]["revision"])
        request["sections"][2]["full_text"] = "请求支付工资差额【待补：工资差额金额】。"
        request["placeholders"][2]["text"] = "【待补：工资差额金额】"
        request["sections"][3]["full_text"] = (
            "据申请人陈述，约定月薪12,000元；合同原文及工资构成待核。"
            "现有证据包括劳动合同与工资条。"
        )
        request["locked_bindings"].append({
            "kind": "fact_amount", "archive_record_ref": saved["result"]["generated_record_ids"][0],
            "rendered_value": "12000元",
            "occurrences": [{"section_id": "facts", "text": "约定月薪12,000元"}],
        })
        return request

    def test_candidate_preserves_user_salary_without_fabricating_calculation(self):
        from zipfile import ZipFile
        from xml.etree import ElementTree

        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            request = self._candidate_with_fact_salary(workspace)
            response = render(request, workspace)
            self.assertTrue(response["ok"], response)
            with ZipFile(response["result"]["canonical_docx"]) as docx:
                text = "".join(ElementTree.fromstring(docx.read("word/document.xml")).itertext())
            self.assertIn("约定月薪12,000元", text)
            self.assertIn("据申请人陈述", text)
            self.assertIn("待补：工资差额金额", text)
            archive = CaseArchive(workspace).read(request["case_id"])
            self.assertTrue(archive["ok"], archive)
            self.assertNotIn("### [CAL-", archive["result"]["markdown"])

    def test_fact_salary_cannot_replace_claim_calculation_or_enter_final(self):
        for change in ("wrong_value", "wrong_source", "requests", "external_final", "missing_occurrence"):
            with self.subTest(change=change), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                request = self._candidate_with_fact_salary(workspace)
                binding = request["locked_bindings"][-1]
                if change == "wrong_value":
                    binding["rendered_value"] = "15000元"
                elif change == "wrong_source":
                    binding["archive_record_ref"] = "CL-001"
                elif change == "requests":
                    request["sections"][2]["full_text"] = "请求支付工资差额12000元。"
                    binding["occurrences"].append({"section_id": "requests", "text": "差额12000元"})
                elif change == "external_final":
                    request["mode"] = "external_final"
                else:
                    binding["occurrences"] = []
                response = render(request, workspace)
                self.assertFalse(response["ok"], response)
                self.assertTrue(any(error["path"].startswith("locked_bindings") for error in response["errors"]), response)
                self.assertFalse((workspace / ".arbibuddy" / "cases" / request["case_id"] / "output").exists())

    def test_candidate_render_does_not_depend_on_tempfile_staging_acl(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            case_id, revision = _archive_without_calculation(workspace)
            request = _candidate_request(case_id, revision)
            request["sections"][2]["full_text"] = "请求支付工资差额【待补：工资差额金额】。"
            request["placeholders"][2]["text"] = "【待补：工资差额金额】"
            request["sections"][3]["full_text"] = (
                "合同约定月薪；具体工资构成需结合劳动合同和工资凭证核实。"
                "现有证据包括劳动合同与工资条。"
            )

            with patch.object(
                public_documents,
                "mkdtemp",
                side_effect=PermissionError("temporary staging ACL denied"),
                create=True,
            ):
                response = render(request, workspace)

            self.assertTrue(response["ok"], response)
            self.assertTrue(Path(response["result"]["canonical_docx"]).is_file())

    def test_candidate_binds_same_calendar_date_with_different_spacing(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            case_id, revision = _archive_without_calculation(workspace)
            saved = CaseArchive(workspace).commit({
                "case_id": case_id, "expected_revision": revision,
                "change_summary": "记录已声明的入职日期",
                "changes": [{"operation": "append", "record_type": "fact",
                             "content_markdown": "用户陈述：2024 年 3 月 1 日入职。"}],
            })
            self.assertTrue(saved["ok"], saved)
            request = _candidate_request(case_id, saved["result"]["revision"])
            request["sections"][2]["full_text"] = "请求支付工资差额【待补：工资差额金额】。"
            request["placeholders"][2]["text"] = "【待补：工资差额金额】"
            request["sections"][3]["full_text"] = "申请人于2024 年 3 月 1 日入职；现有证据包括劳动合同与工资条。"
            request["locked_bindings"].append({
                "kind": "date", "archive_record_ref": saved["result"]["generated_record_ids"][0],
                "rendered_value": "2024年3月1日",
                "occurrences": [{"section_id": "facts", "text": "2024 年 3 月 1 日"}],
            })
            response = render(request, workspace)
            self.assertTrue(response["ok"], response)
            self.assertTrue(Path(response["result"]["canonical_docx"]).is_file())

    def test_reference_separates_analysis_status_from_docx_placeholder_syntax(self):
        reference = (
            Path(__file__).resolve().parents[2] / "references" / "document-render.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`【待核：...】` 不是有效的 DOCX 占位符", reference)
        self.assertIn("合同工资标准", reference)
        self.assertIn("不得为了通过渲染创建伪计算绑定", reference)

    def test_public_renderer_rejects_unapproved_placeholder_and_unbound_salary(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            case_id, revision = _archive_without_calculation(workspace)
            request = _candidate_request(case_id, revision)

            response = render(request, workspace)

            self.assertFalse(response["ok"], response)
            errors = response["errors"]
            self.assertEqual(
                Counter(error["code"] for error in errors),
                Counter(
                    {
                        "invalid_placeholder": 2,
                        "binding_mismatch": 1,
                        "missing_input": 1,
                    }
                ),
            )
            self.assertEqual(
                {error["path"] for error in errors if error["code"] == "binding_mismatch"},
                {"sections[3].full_text"},
            )
            self.assertEqual(
                {error["path"] for error in errors if error["code"] == "missing_input"},
                {"locked_bindings"},
            )

    def test_corrected_candidate_uses_waiting_placeholder_and_omits_unbound_amount(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            case_id, revision = _archive_without_calculation(workspace)
            request = _candidate_request(case_id, revision)
            request["sections"][2]["full_text"] = "请求支付工资差额【待补：工资差额金额】。"
            request["placeholders"][2]["text"] = "【待补：工资差额金额】"
            request["sections"][3]["full_text"] = (
                "合同载明月薪约定；具体工资标准和构成需结合劳动合同与工资凭证核实。"
                "现有证据包括劳动合同与工资条。"
            )

            response = render(request, workspace)

            self.assertTrue(response["ok"], response)
            self.assertEqual(response["result"]["delivery_state"], "candidate_ready")
            self.assertTrue(Path(response["result"]["canonical_docx"]).is_file())
            self.assertTrue(Path(response["result"]["verification_checklist"]).is_file())


if __name__ == "__main__":
    unittest.main()
