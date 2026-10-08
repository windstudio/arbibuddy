from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import create_delivery_set, render
from scripts.documents.view import managed_delivery_view
from scripts.workbuddy_acceptance.service import audit_workspace
from tests.documents.test_model_led_document_render import (
    _application_request,
    _create_archive,
    _evidence_request,
)


class DeliverySetTests(unittest.TestCase):
    def test_arbitration_bundle_rejects_missing_unified_confirmation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create({"initial_goal": "准备仲裁申请书"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            revision = created["result"]["revision"]
            result = create_delivery_set(
                {
                    "case_id": case_id,
                    "archive_revision": revision,
                    "mode": "candidate",
                    "requested_templates": ["labor-arbitration-application"],
                    "confirmation_refs": [],
                },
                workspace,
            )
            self.assertFalse(result["ok"], result)
            self.assertIn("unified_confirmation_required", {item["code"] for item in result["errors"]})
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "delivery-set-v1.json").exists())

    def test_arbitration_bundle_rejects_confirmation_without_revision_binding(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "准备仲裁申请书"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit({
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录不完整的测试确认",
                "changes": [{
                    "operation": "append",
                    "record_type": "confirmation",
                    "content_markdown": "用户说可以继续。",
                }],
            })
            self.assertTrue(committed["ok"], committed)
            result = create_delivery_set({
                "case_id": case_id,
                "archive_revision": 1,
                "mode": "candidate",
                "requested_templates": ["labor-arbitration-application"],
                "confirmation_refs": [],
            }, workspace)
            self.assertFalse(result["ok"], result)
            self.assertIn(
                "confirmation_revision_unreadable",
                {item["code"] for item in result["errors"]},
            )
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "delivery-set-v1.json").exists()
            )

    def test_missing_confirmation_and_unreadable_confirmation_revision_are_distinct(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "准备仲裁申请书"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit({
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录不可解析的历史确认字段",
                "changes": [{
                    "operation": "append",
                    "record_type": "confirmation",
                    "content_markdown": (
                        "完整摘要自然语言确认（用户已确认）\n"
                        "- 档案修订版本：1\n"
                        "- 本确认绑定档案修订版本 1\n"
                        "- 用户自然语言确认：确认事实与处理范围，请准备候选稿。"
                    ),
                }],
            })
            self.assertTrue(committed["ok"], committed)
            result = create_delivery_set({
                "case_id": case_id,
                "archive_revision": 1,
                "mode": "candidate",
                "requested_templates": ["labor-arbitration-application"],
                "confirmation_refs": [],
            }, workspace)

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "confirmation_revision_unreadable",
                {item["code"] for item in result["errors"]},
            )
            self.assertNotIn(
                "unified_confirmation_required",
                {item["code"] for item in result["errors"]},
            )

    def test_arbitration_bundle_accepts_bulleted_natural_confirmation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "准备仲裁申请书"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit({
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录用户确认当前完整摘要",
                "changes": [{
                    "operation": "append",
                    "record_type": "confirmation",
                    "content_markdown": (
                        "**完整摘要自然语言确认（用户已确认）**\n"
                        "- 绑定档案修订版本：1\n"
                        "- 用户自然语言确认：确认已知事实、待核项和本次处理范围都对，请准备候选稿。\n"
                        "- 关键事实：工资、奖金和加班事实按当前摘要。\n"
                        "- 权益范围：本次处理三项主张。\n"
                        "- 主要口径：金额待核。\n"
                        "- 重要假设：未核事项不作确定结论。\n"
                        "- 拟交付内容：仲裁申请候选稿和证据目录。"
                    ),
                }],
            })
            self.assertTrue(committed["ok"], committed)
            result = create_delivery_set({
                "case_id": case_id,
                "archive_revision": 1,
                "mode": "candidate",
                "requested_templates": ["labor-arbitration-application"],
                "confirmation_refs": [],
            }, workspace)
            self.assertTrue(result["ok"], result)

    def test_application_bundle_stays_closed_until_all_members_are_rendered(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            delivery = create_delivery_set(
                {
                    "case_id": case_id,
                    "archive_revision": revision,
                    "mode": "external_final",
                    "requested_templates": ["labor-arbitration-application"],
                    "confirmation_refs": [],
                },
                workspace,
            )
            self.assertTrue(delivery["ok"], delivery)
            delivery_id = delivery["result"]["delivery_set_id"]
            self.assertEqual(
                delivery["result"]["expected_document_types"],
                ["arbitration_application", "evidence_catalog"],
            )

            application = _application_request(case_id, revision)
            application["delivery_set_id"] = delivery_id
            self.assertTrue(render(application, workspace)["ok"])

            incomplete = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertTrue(incomplete["stopped"])
            self.assertEqual(incomplete["error_code"], "delivery_set_incomplete")
            self.assertEqual(incomplete["presentation_files"], [])
            self.assertEqual(incomplete["present_files_arguments"]["files"], [])

            evidence = _evidence_request(case_id, revision)
            evidence["delivery_set_id"] = delivery_id
            self.assertTrue(render(evidence, workspace)["ok"])

            complete = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(complete["stopped"])
            self.assertEqual(
                [item["delivery_label"] for item in complete["presentation_files"]],
                [
                    "arbitration_application",
                    "arbitration_application",
                    "evidence_catalog",
                    "evidence_catalog",
                ],
            )

    def test_single_render_result_is_artifact_diagnostic_not_presentation_authorization(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            result = render(_application_request(case_id, revision), workspace)
            self.assertTrue(result["ok"], result)
            payload = result["result"]
            self.assertFalse(payload["presentation_authorized"])
            self.assertEqual(
                [item["path"] for item in payload["artifact_files"]],
                [item["path"] for item in payload["presentation_files"]],
            )

    def test_direct_render_artifacts_cannot_authorize_present_files(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = render(_application_request(case_id, revision), workspace)
            self.assertTrue(rendered["ok"], rendered)
            files = [item["path"] for item in rendered["result"]["presentation_files"]]
            transcript = workspace / "direct-render.jsonl"
            records = [
                {
                    "sessionId": "direct-render",
                    "type": "function_call",
                    "id": "skill",
                    "name": "Skill",
                    "arguments": {"skill": "arbibuddy"},
                },
                {
                    "sessionId": "direct-render",
                    "type": "function_call_result",
                    "call_id": "skill",
                    "name": "Skill",
                    "status": "success",
                },
                {
                    "sessionId": "direct-render",
                    "type": "function_call",
                    "id": "render",
                    "name": "document.render-v1",
                    "arguments": {"case_id": case_id},
                },
                {
                    "sessionId": "direct-render",
                    "type": "function_call_result",
                    "call_id": "render",
                    "name": "document.render-v1",
                    "status": "success",
                    "result": rendered,
                },
                {
                    "sessionId": "direct-render",
                    "type": "function_call",
                    "name": "present_files",
                    "arguments": {"cwd": str(workspace), "files": files},
                },
            ]
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="direct-render",
            )
            self.assertIn(
                "incomplete_managed_presentation",
                {item["code"] for item in report["violations"]},
            )


if __name__ == "__main__":
    unittest.main()
