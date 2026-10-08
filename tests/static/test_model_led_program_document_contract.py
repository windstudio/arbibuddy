from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedProgramDocumentContractTests(unittest.TestCase):
    def test_skill_and_contract_route_three_program_documents(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        render_reference = (ROOT / "references" / "document-render.md").read_text(
            encoding="utf-8"
        )
        model_reference = (
            ROOT
            / "references"
            / "model-led"
            / "deregistration-preservation-enforcement-documents.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "references/model-led/deregistration-preservation-enforcement-documents.md",
            skill,
        )
        for document_type in (
            "company_deregistration_restriction_request_shanghai",
            "property_preservation_application",
            "enforcement_application",
        ):
            self.assertIn(document_type, render_reference)
        for phrase in (
            "仲裁受理信息",
            "申请事项",
            "保全请求",
            "财产线索",
            "担保",
            "执行请求",
            "有效执行依据",
            "错误保全",
            "上海专项",
            "external_final",
            "candidate",
            "不自动提交、冻结财产或启动执行",
        ):
            self.assertIn(phrase, model_reference + render_reference, phrase)
        for forbidden in (
            "PublicTurn",
            "advance/status",
            "scripts.documents.cli",
            "固定确认次数",
        ):
            self.assertNotIn(forbidden, model_reference + render_reference, forbidden)


if __name__ == "__main__":
    unittest.main()
