from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedDemandAndForcedDocumentContractTests(unittest.TestCase):
    def test_skill_routes_08_documents_through_the_single_public_renderer(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        render_reference = (ROOT / "references" / "document-render.md").read_text(
            encoding="utf-8"
        )
        model_reference = (
            ROOT
            / "references"
            / "model-led"
            / "demand-and-forced-termination-documents.md"
        ).read_text(encoding="utf-8")

        self.assertIn("references/model-led/demand-and-forced-termination-documents.md", skill)
        for document_type in (
            "employment_obligation_demand_letter",
            "forced_termination_notice",
        ):
            self.assertIn(document_type, render_reference)
        for phrase in (
            "收件人",
            "劳动关系说明",
            "具体义务",
            "履行期限",
            "沟通与保留权利",
            "解除意思表示",
            "解除理由",
            "结算与手续",
            "confirmation_refs",
            "authority_refs",
            "candidate",
            "external_final",
            "不创建第二套文书工具",
            "不自动提交、发送或签署",
        ):
            self.assertIn(phrase, model_reference + render_reference)

    def test_08_reference_keeps_history_planned_and_delivery_uncertainty_distinct(self):
        text = (
            ROOT
            / "references"
            / "model-led"
            / "demand-and-forced-termination-documents.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "历史索赔",
            "计划解除",
            "已发送但送达事实不明",
            "先核对通知内容",
            "明确选择",
            "source_refs",
            "档案 revision",
            "动态现行规则核验失败",
            "candidate",
            "external final",
        ):
            self.assertIn(phrase, text, phrase)
        self.assertIn("scripts.documents.runtime_cli", text)
        for forbidden in (
            "PublicTurn",
            "advance/status",
            "解除分析器",
        ):
            self.assertNotIn(forbidden, text, forbidden)

    def test_public_renderer_is_the_only_new_runtime_document_surface(self):
        source = (ROOT / "scripts" / "documents" / "public.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("employment_obligation_demand_letter", source)
        self.assertIn("forced_termination_notice", source)
        self.assertIn("CaseArchive", source)
        self.assertIn("commit_generation_staging", source)
        self.assertIn("class _DocumentRenderer", source)
        self.assertNotIn("class DocumentRenderer", source)
        self.assertNotIn("DocumentComposer", source)
        self.assertNotIn("scripts.core_workflow", source)
        self.assertNotIn("PublicTurn", source)

        package = (ROOT / "scripts" / "documents" / "__init__.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from .public import describe_contract, render", package)
        self.assertIn('__all__ = ["describe_contract", "render"]', package)
        self.assertNotIn("DocumentRenderer", package)

    def test_model_route_uses_public_tool_without_a_parallel_document_entry(self):
        from scripts.runtime_resources import RUNTIME_FILES
        self.assertIn("scripts/documents/public.py", RUNTIME_FILES)
        for path in ("scripts/documents/cli.py", "scripts/documents/service.py", "scripts/documents/composer.py"):
            self.assertNotIn(path, RUNTIME_FILES)
        model_reference = (ROOT / "references/model-led/demand-and-forced-termination-documents.md").read_text(encoding="utf-8")
        self.assertNotIn("CaseFileStore", model_reference)


if __name__ == "__main__":
    unittest.main()
