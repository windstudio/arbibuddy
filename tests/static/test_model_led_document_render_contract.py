from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedDocumentRenderContractTests(unittest.TestCase):
    def test_skill_routes_the_single_public_document_render_contract(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references" / "document-render.md").read_text(encoding="utf-8")

        self.assertIn("references/document-render.md", skill)
        for required in (
            "document.render",
            "case_id",
            "archive_revision",
            "document_type",
            "template_version",
            "mode",
            "title",
            "sections",
            "locked_bindings",
            "placeholders",
            "confirmation_refs",
            "authority_refs",
            "candidate",
            "external_final",
            "仅供核对，请勿外发",
            "archive_revision_conflict",
            "binding_mismatch",
            "internal_content_leak",
            "ooxml_invalid",
            "publication_conflict",
            "失败不会创建或替换 canonical DOCX",
        ):
            self.assertIn(required, reference, required)
        self.assertIn("arbitration_application", reference)
        self.assertIn("arbitration_defense", reference)
        self.assertIn("evidence_catalog", reference)
        self.assertIn(
            "每个 `(kind, archive_record_ref, rendered_value)` 组合只能出现一次",
            reference,
        )
        self.assertIn("完整文书正文和核验清单只放在单次 `render` 输入中", reference)
        self.assertNotIn("PublicTurn", reference)
        self.assertNotIn("advance/status", reference)

    def test_new_public_renderer_does_not_depend_on_retired_orchestration_or_composer(self):
        source = (ROOT / "scripts" / "documents" / "public.py").read_text(encoding="utf-8")
        self.assertNotIn("scripts.core_workflow", source)
        self.assertNotIn("DocumentComposer", source)
        self.assertNotIn("PublicTurn", source)
        self.assertIn("commit_generation_staging", source)
        self.assertIn("audit_docx", source)


if __name__ == "__main__":
    unittest.main()
