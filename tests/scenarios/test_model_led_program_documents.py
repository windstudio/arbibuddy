from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from tests.documents.model_led_program_documents_fixtures import (
    DOCUMENTS,
    create_case,
    request_for,
    visible_text,
)


class _NaturalLanguageProgramDocumentModel:
    """Scenario adapter: natural language selects a public document request."""

    def prepare(self, user_message: str, case_id: str, revision: int) -> dict[str, object]:
        if "上海" in user_message and "注销" in user_message:
            document_type = "company_deregistration_restriction_request_shanghai"
        elif "保全" in user_message:
            document_type = "property_preservation_application"
        elif "执行" in user_message:
            document_type = "enforcement_application"
        else:
            raise AssertionError(f"scenario message was not routed: {user_message}")
        return request_for(case_id, revision, document_type, mode="candidate")


class ModelLedProgramDocumentScenarioTests(unittest.TestCase):
    def test_natural_language_routes_each_program_document_to_candidate_docx(self):
        messages = (
            "上海仲裁已经受理，但公司可能注销，我想先准备一份说明注销风险和受理情况的专项材料。",
            "仲裁还在进行，我知道对方的银行账户线索，也愿意说明担保和错误保全风险，先准备保全申请。",
            "仲裁裁决已经生效、履行期限也到了，对方仍未付款，我想整理执行依据、履行情况和管辖材料。",
        )
        model = _NaturalLanguageProgramDocumentModel()
        for message, (document_type, meta) in zip(messages, DOCUMENTS.items()):
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision, _ = create_case(workspace)
                recorded = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "记录用户自然语言程序文书目标",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": f"用户自然语言目标：{message}",
                            }
                        ],
                    }
                )
                self.assertTrue(recorded["ok"], recorded)
                current_revision = int(recorded["result"]["revision"])
                request = model.prepare(message, case_id, current_revision)

                result = DocumentRenderer(workspace).render(request)

                self.assertTrue(result["ok"], result)
                self.assertEqual(result["result"]["document_type"], document_type)
                self.assertEqual(result["result"]["delivery_state"], "candidate_ready")
                self.assertIn(meta["title"], visible_text(Path(result["result"]["canonical_docx"])))


if __name__ == "__main__":
    unittest.main()
