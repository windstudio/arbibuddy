from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from tests.documents.model_led_program_documents_fixtures import (
    DOCUMENTS,
    create_case,
    request_for,
    visible_text,
)


class ModelLedProgramDocumentsRenderTests(unittest.TestCase):
    def test_public_contract_exposes_the_three_program_documents(self):
        with TemporaryDirectory() as temporary_root:
            contract = DocumentRenderer(Path(temporary_root)).describe()

        self.assertEqual(
            set(contract["input"]["document_types"]),
            {
                "employment_obligation_demand_letter",
                "forced_termination_notice",
                "arbitration_application",
                "arbitration_defense",
                "evidence_catalog",
                *DOCUMENTS,
            },
        )
        for document_type, meta in DOCUMENTS.items():
            self.assertEqual(contract["input"]["document_type_requirements"][document_type]["title"], meta["title"])
        self.assertIn("authority_required", contract["errors"]["codes"])
        self.assertIn("confirmation_stale", contract["errors"]["codes"])

    def test_each_program_document_has_a_candidate_without_high_risk_confirmation(self):
        for document_type, meta in DOCUMENTS.items():
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, _ = create_case(workspace)
                request = request_for(case_id, revision, document_type, mode="candidate")

                result = DocumentRenderer(workspace).render(request)

                self.assertTrue(result["ok"], result)
                self.assertEqual(result["result"]["delivery_state"], "candidate_ready")
                self.assertIn(meta["title"], visible_text(Path(result["result"]["canonical_docx"])))

    def test_external_final_requires_current_confirmation_and_authority_for_each_program_document(self):
        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, _ = create_case(workspace)
                request = request_for(case_id, revision, document_type)

                result = DocumentRenderer(workspace).render(request)

                self.assertFalse(result["ok"], result)
                codes = {error["code"] for error in result["errors"]}
                self.assertIn("confirmation_missing", codes)
                self.assertIn("authority_required", codes)
                self.assertFalse(
                    (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
                )

    def test_each_program_document_external_final_is_real_idempotent_docx(self):
        for document_type, meta in DOCUMENTS.items():
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = create_case(
                    workspace, confirmation_document_type=document_type
                )
                request = request_for(
                    case_id,
                    revision,
                    document_type,
                    confirmation_id=confirmation_id,
                )
                renderer = DocumentRenderer(workspace)

                first = renderer.render(request)
                replay = renderer.render(request)

                self.assertTrue(first["ok"], first)
                self.assertEqual(first, replay)
                self.assertEqual(first["result"]["delivery_state"], "final_ready")
                docx = Path(first["result"]["canonical_docx"])
                self.assertTrue(docx.is_file())
                self.assertIn(meta["title"], visible_text(docx))
                self.assertEqual(len(list(docx.parent.glob("*.docx"))), 1)
                with ZipFile(docx) as package:
                    self.assertIn("word/document.xml", package.namelist())
                    self.assertIn("word/numbering.xml", package.namelist())
                    self.assertIn("word/footer1.xml", package.namelist())

    def test_candidate_privacy_placeholder_can_be_reviewed_but_cannot_be_promoted(self):
        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = create_case(
                    workspace, confirmation_document_type=document_type
                )
                request = request_for(
                    case_id,
                    revision,
                    document_type,
                    mode="candidate",
                    confirmation_id=confirmation_id,
                )
                request["locked_bindings"] = [
                    binding
                    for binding in request["locked_bindings"]
                    if binding["kind"] != "signature"
                ]
                request["sections"][-1]["full_text"] += " 联系方式：【待补：签名】。"
                request["placeholders"] = [
                    {"placeholder_id": "signature", "text": "【待补：签名】", "label": "签名"}
                ]

                candidate = DocumentRenderer(workspace).render(request)
                self.assertTrue(candidate["ok"], candidate)

                request["mode"] = "external_final"
                promoted = DocumentRenderer(workspace).render(request)
                self.assertFalse(promoted["ok"], promoted)
                self.assertIn("placeholder_in_final", {error["code"] for error in promoted["errors"]})

    def test_program_specific_risks_and_boundaries_are_checked(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = create_case(workspace)
            renderer = DocumentRenderer(workspace)

            shanghai = request_for(
                case_id,
                revision,
                "company_deregistration_restriction_request_shanghai",
                mode="candidate",
            )
            self.assertTrue(renderer.render(shanghai)["ok"])
            shanghai_text = "\n".join(section["full_text"] for section in shanghai["sections"])
            self.assertIn("上海专项实务定位", shanghai_text)
            self.assertIn("不替代简易注销异议或法院财产保全", shanghai_text)

            preservation = request_for(
                case_id,
                revision,
                "property_preservation_application",
                mode="candidate",
            )
            preservation["sections"][5]["full_text"] = ""
            preservation["sections"][5]["full_text"] = "【待补：担保方案】"
            preservation["placeholders"] = [
                {"placeholder_id": "guarantee", "text": "【待补：担保方案】", "label": "担保方案"}
            ]
            candidate = renderer.render(preservation)
            self.assertTrue(candidate["ok"], candidate)
            preservation_text = "\n".join(
                section["full_text"] for section in preservation["sections"]
            )
            self.assertIn("担保", preservation_text)
            self.assertIn("错误保全", preservation_text)
            self.assertIn("财产线索", preservation["sections"][4]["heading"])
            self.assertIn("担保方案", preservation["placeholders"][0]["label"])

            enforcement = request_for(
                case_id,
                revision,
                "enforcement_application",
                mode="candidate",
            )
            self.assertTrue(renderer.render(enforcement)["ok"])
            enforcement_text = "\n".join(
                section["full_text"] for section in enforcement["sections"]
            )
            for required in ("生效仲裁裁决", "履行期限", "管辖权"):
                self.assertIn(required, enforcement_text)
            self.assertEqual(enforcement["sections"][4]["heading"], "财产线索")

    def test_each_program_rejects_a_conflicted_locked_record(self):
        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision, _ = create_case(workspace)
                changed = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "记录待核实的冲突主张",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "claim",
                                "content_markdown": "事实冲突：同一主张存在互相矛盾的版本。",
                            }
                        ],
                    }
                )
                self.assertTrue(changed["ok"], changed)
                conflict_ref = str(changed["result"]["generated_record_ids"][0])
                request = request_for(
                    case_id,
                    int(changed["result"]["revision"]),
                    document_type,
                    mode="candidate",
                )
                claim = next(binding for binding in request["locked_bindings"] if binding["kind"] == "claim")
                claim["archive_record_ref"] = conflict_ref
                claim["rendered_value"] = "事实冲突：同一主张存在互相矛盾的版本。"

                result = DocumentRenderer(workspace).render(request)

                self.assertFalse(result["ok"], result)
                self.assertIn("fact_conflict", {error["code"] for error in result["errors"]})
                self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_stale_confirmation_is_a_candidate_warning_but_blocks_external_final(self):
        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision, confirmation_id = create_case(
                    workspace, confirmation_document_type=document_type
                )
                changed = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "补充确认后发生的档案变化",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": "补充事实：用户要求重新核对材料。",
                            }
                        ],
                    }
                )
                self.assertTrue(changed["ok"], changed)
                current_revision = int(changed["result"]["revision"])
                candidate = request_for(
                    case_id,
                    current_revision,
                    document_type,
                    mode="candidate",
                    confirmation_id=confirmation_id,
                )
                renderer = DocumentRenderer(workspace)

                candidate_result = renderer.render(candidate)

                self.assertTrue(candidate_result["ok"], candidate_result)
                self.assertIn(
                    "confirmation_stale",
                    {warning["code"] for warning in candidate_result["warnings"]},
                )
                candidate["mode"] = "external_final"
                final_result = renderer.render(candidate)
                self.assertFalse(final_result["ok"], final_result)
                self.assertIn(
                    "confirmation_stale",
                    {error["code"] for error in final_result["errors"]},
                )

    def test_external_final_rejects_stale_authority_and_revision_conflict_for_each_program(self):
        for document_type, meta in DOCUMENTS.items():
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision, confirmation_id = create_case(
                    workspace,
                    authority_status="基线过期",
                    confirmation_document_type=document_type,
                )
                request = request_for(
                    case_id,
                    revision,
                    document_type,
                    confirmation_id=confirmation_id,
                )
                stale_authority = DocumentRenderer(workspace).render(request)
                self.assertFalse(stale_authority["ok"], stale_authority)
                self.assertIn(
                    "authority_stale",
                    {error["code"] for error in stale_authority["errors"]},
                )

                current_archive, current_case_id, current_revision, current_confirmation_id = create_case(
                    workspace / "revision-conflict",
                    confirmation_document_type=document_type,
                )
                self.assertIsNotNone(current_archive)
                conflict_request = request_for(
                    current_case_id,
                    current_revision - 1,
                    document_type,
                    mode="candidate",
                )
                conflict = DocumentRenderer(workspace / "revision-conflict").render(conflict_request)
                self.assertFalse(conflict["ok"], conflict)
                self.assertIn(
                    "archive_revision_conflict",
                    {error["code"] for error in conflict["errors"]},
                )

                wrong_authority = request_for(
                    case_id,
                    revision,
                    document_type,
                    confirmation_id=confirmation_id,
                )
                wrong_authority["authority_refs"] = [
                    "AUTH-002" if meta["authority"] != "AUTH-002" else "AUTH-001"
                ]
                wrong_authority_result = DocumentRenderer(workspace).render(wrong_authority)
                self.assertFalse(wrong_authority_result["ok"], wrong_authority_result)
                self.assertIn(
                    "authority_stale",
                    {error["code"] for error in wrong_authority_result["errors"]},
                )

    def test_program_publication_failure_rolls_back_for_all_three_types(self):
        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = create_case(
                    workspace, confirmation_document_type=document_type
                )
                request = request_for(
                    case_id,
                    revision,
                    document_type,
                    confirmation_id=confirmation_id,
                )
                renderer = DocumentRenderer(workspace)

                with patch(
                    "scripts.documents.public.commit_generation_staging",
                    side_effect=OSError("injected program publication failure"),
                ):
                    failed = renderer.render(request)
                self.assertFalse(failed["ok"], failed)
                self.assertIn("io_failure", {error["code"] for error in failed["errors"]})
                output = workspace / ".arbibuddy" / "cases" / case_id / "output"
                self.assertFalse(output.exists())

                successful = renderer.render(request)
                self.assertTrue(successful["ok"], successful)
                document = Path(successful["result"]["canonical_docx"])
                self.assertEqual(len(list(document.parent.glob("*.docx"))), 1)
                self.assertEqual(len(list(document.parent.glob("*.txt"))), 1)
                self.assertEqual(len(list(document.parent.glob("*.json"))), 1)

    def test_wrong_region_stale_authority_conflict_and_internal_leak_fail_without_publication(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, confirmation_id = create_case(
                workspace,
                jurisdiction="北京市",
                confirmation_document_type="company_deregistration_restriction_request_shanghai",
            )
            request = request_for(
                case_id,
                revision,
                "company_deregistration_restriction_request_shanghai",
                confirmation_id=confirmation_id,
            )
            wrong_region = DocumentRenderer(workspace).render(request)
            self.assertFalse(wrong_region["ok"], wrong_region)
            self.assertIn("authority_stale", {error["code"] for error in wrong_region["errors"]})

        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = create_case(workspace, authority_status="基线过期")
            request = request_for(
                case_id,
                revision,
                "property_preservation_application",
                mode="candidate",
            )
            request["authority_refs"] = ["AUTH-002"]
            stale = DocumentRenderer(workspace).render(request)
            self.assertTrue(stale["ok"], stale)
            self.assertIn("authority_stale", {warning["code"] for warning in stale["warnings"]})

        for document_type in DOCUMENTS:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, _ = create_case(workspace)
                request = request_for(case_id, revision, document_type, mode="candidate")
                respondent = next(
                    binding
                    for binding in request["locked_bindings"]
                    if binding["kind"] == "party" and binding["archive_record_ref"] == "F-002"
                )
                respondent["rendered_value"] = (
                    "被执行人：李四"
                    if document_type == "enforcement_application"
                    else "被申请人：李四"
                )
                facts = next(section for section in request["sections"] if section["heading"] == "事实与理由")
                facts["full_text"] += " 内部引用F-001。"

                failed = DocumentRenderer(workspace).render(request)

                self.assertFalse(failed["ok"], failed)
                codes = {error["code"] for error in failed["errors"]}
                self.assertIn("binding_mismatch", codes)
                self.assertIn("internal_content_leak", codes)
                self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())


if __name__ == "__main__":
    unittest.main()
