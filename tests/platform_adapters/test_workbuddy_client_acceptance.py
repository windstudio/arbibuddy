from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import shutil
from time import time_ns
import unittest
from zipfile import ZipFile

from scripts.documents.public import render as public_render
from scripts.documents.runtime_cli import main as document_runtime_main
from scripts.case_archive import CaseArchive
from scripts.documents.view import managed_delivery_view
from scripts.workbuddy_acceptance.service import (
    audit_workspace,
    build_no_delivery_contract,
)
from scripts.workbuddy_acceptance.cli import main as workbuddy_acceptance_main
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as create_model_case,
    demand_request as model_demand_request,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_no_delivery_contract(transcript: Path, case_id: str) -> Path:
    contract = transcript.with_name("no-delivery-contract.json")
    build_no_delivery_contract(
        journey="ML03-v2",
        case_id=case_id,
        transcript_jsonl=transcript,
        output=contract,
    )
    return contract


def _timeline_transcript(
    *,
    session_id: str,
    case_id: str,
    revision: int,
    manifest: Path,
    files: list[str],
    include_user_follow_up: bool = False,
    include_second_present: bool = False,
    include_second_render: bool = False,
    commit_before_present: bool = False,
) -> list[dict[str, object]]:
    def render_call(call_id: str, render_revision: int, render_files: list[str]):
        return [
            {
                "sessionId": session_id,
                "type": "function_call",
                "id": call_id,
                "name": "document.render-v1",
                "arguments": {
                    "case_id": case_id,
                    "archive_revision": render_revision,
                },
            },
            {
                "sessionId": session_id,
                "type": "function_call_result",
                "call_id": call_id,
                "name": "document.render-v1",
                "status": "success",
                "result": {
                    "ok": True,
                    "result": {
                        "archive_revision": render_revision,
                        "machine_manifest": str(manifest),
                        "machine_manifest_sha256": sha256(
                            manifest.read_bytes()
                        ).hexdigest(),
                        "presentation_files": [
                            {
                                "path": value,
                                "sha256": sha256(Path(value).read_bytes()).hexdigest(),
                            }
                            for value in render_files
                        ],
                    },
                },
            },
        ]

    records: list[dict[str, object]] = [
        {
            "sessionId": session_id,
            "type": "function_call",
            "id": "skill-call",
            "name": "Skill",
            "arguments": {"skill": "arbibuddy"},
        },
        {
            "sessionId": session_id,
            "type": "function_call_result",
            "call_id": "skill-call",
            "name": "Skill",
            "status": "success",
            "result": "Skill loaded",
        },
    ]
    records.extend(render_call("render-1", revision, files))
    managed_view = {
        "schema_version": 1,
        "case_id": case_id,
        "delivery_state": "final_ready",
        "stopped": False,
        "presentation_files": [
            {
                "delivery_label": "employment_obligation_demand_letter",
                "kind": "document",
                "order": 1,
                "path": files[0],
                "sha256": sha256(Path(files[0]).read_bytes()).hexdigest(),
                "state": "final_ready",
            },
            {
                "delivery_label": "employment_obligation_demand_letter",
                "kind": "checklist",
                "order": 2,
                "path": files[1],
                "sha256": sha256(Path(files[1]).read_bytes()).hexdigest(),
                "state": "final_ready",
            },
        ],
        "delivery_set": {
            "archive_revision": revision,
            "expected_document_types": ["employment_obligation_demand_letter"],
            "completed_document_types": ["employment_obligation_demand_letter"],
        },
    }
    records.extend(
        [
            {
                "sessionId": session_id,
                "type": "function_call",
                "id": "managed-view-1",
                "name": "Bash",
                "arguments": {
                    "command": "python -m scripts.documents.runtime_cli view --json request"
                },
            },
            {
                "sessionId": session_id,
                "type": "function_call_result",
                "call_id": "managed-view-1",
                "name": "Bash",
                "status": "success",
                "result": managed_view,
            },
        ]
    )
    if commit_before_present:
        records.append(
            {
                "sessionId": session_id,
                "type": "function_call",
                "name": "CaseArchive.commit",
                "arguments": {"operation": "commit", "case_id": case_id},
            }
        )
    records.append(
        {
            "sessionId": session_id,
            "type": "function_call",
            "name": "present_files",
            "arguments": {"cwd": str(Path(files[0]).parents[0]), "files": files},
        }
    )
    if include_user_follow_up:
        records.append(
            {
                "sessionId": session_id,
                "type": "user_message",
                "role": "user",
                "content": "替我发送",
            }
        )
    if not commit_before_present:
        records.append(
            {
                "sessionId": session_id,
                "type": "function_call",
                "name": "CaseArchive.commit",
                "arguments": {"operation": "commit", "case_id": case_id},
            }
        )
    if include_second_render:
        records.extend(render_call("render-2", revision + 1, files))
        refreshed_view = dict(managed_view)
        refreshed_view["delivery_set"] = {
            "archive_revision": revision + 1,
            "expected_document_types": ["employment_obligation_demand_letter"],
            "completed_document_types": ["employment_obligation_demand_letter"],
        }
        records.extend(
            [
                {
                    "sessionId": session_id,
                    "type": "function_call",
                    "id": "managed-view-2",
                    "name": "Bash",
                    "arguments": {
                        "command": "python -m scripts.documents.runtime_cli view --json request"
                    },
                },
                {
                    "sessionId": session_id,
                    "type": "function_call_result",
                    "call_id": "managed-view-2",
                    "name": "Bash",
                    "status": "success",
                    "result": refreshed_view,
                },
            ]
        )
    if include_second_present:
        records.append(
            {
                "sessionId": session_id,
                "type": "function_call",
                "name": "present_files",
                "arguments": {"cwd": str(Path(files[0]).parents[0]), "files": files},
            }
        )
    return records


class WorkBuddyClientAcceptanceTests(unittest.TestCase):
    def test_nested_workbuddy_failure_is_not_a_successful_archive_read(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "恢复工资争议档案"})
            case_id = created["result"]["case_id"]
            transcript = root / "failed-recovery.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "failed-recovery",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "failed-recovery",
                            "type": "function_call_result",
                            "callId": "skill-call",
                            "name": "Skill",
                            "status": "completed",
                            "output": {"type": "text", "text": "Skill loaded"},
                        },
                        {
                            "sessionId": "failed-recovery",
                            "type": "function_call",
                            "callId": "wrong-read",
                            "name": "Bash",
                            "arguments": {
                                "command": (
                                    "python -m scripts.case_archive.runtime_cli "
                                    "read-current"
                                )
                            },
                        },
                        {
                            "sessionId": "failed-recovery",
                            "type": "function_call_result",
                            "callId": "wrong-read",
                            "name": "Bash",
                            "status": "completed",
                            "output": {
                                "type": "text",
                                "text": "No module named scripts.case_archive.runtime_cli",
                            },
                            "providerData": {
                                "toolResult": {
                                    "rawResponse": {
                                        "exitCode": 1,
                                        "is_error": True,
                                        "error": (
                                            "No module named "
                                            "scripts.case_archive.runtime_cli"
                                        ),
                                    }
                                }
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="failed-recovery",
            )

            self.assertFalse(
                report["transcript_diagnostic"]["case_archive_action_succeeded"]
            )

    def test_recovered_read_only_transport_retries_are_diagnostic_only(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "恢复工资争议档案"})
            case_id = created["result"]["case_id"]
            skill_root = root / "installed" / "arbibuddy"
            public_cli = skill_root / "scripts" / "case_archive" / "cli.py"
            public_cli.parent.mkdir(parents=True)
            public_cli.write_text("# public transport fixture\n", encoding="utf-8")
            session_id = "recovered-retry"
            transcript = root / "recovered-retry.jsonl"

            def failed_result(call_id: str, exit_code: int, error: str):
                return {
                    "sessionId": session_id,
                    "type": "function_call_result",
                    "callId": call_id,
                    "name": "Bash",
                    "status": "completed",
                    "output": {"type": "text", "text": error},
                    "providerData": {
                        "toolResult": {
                            "rawResponse": {
                                "exitCode": exit_code,
                                "is_error": True,
                                "error": error,
                            }
                        }
                    },
                }

            correct_command = (
                f'cd "{skill_root}" && python -B -X utf8 '
                f'-m scripts.case_archive.cli --root "{workspace}" read-current'
            )
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call_result",
                            "callId": "skill-call",
                            "name": "Skill",
                            "status": "completed",
                            "output": {"type": "text", "text": "Skill loaded"},
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "callId": "wrong-module",
                            "name": "Bash",
                            "arguments": {
                                "command": (
                                    f'cd "{skill_root}" && python -m '
                                    "scripts.case_archive.runtime_cli read-current"
                                )
                            },
                        },
                        failed_result(
                            "wrong-module",
                            1,
                            "No module named scripts.case_archive.runtime_cli",
                        ),
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "callId": "wrong-script",
                            "name": "Bash",
                            "arguments": {
                                "command": (
                                    f'cd "{skill_root}" && python '
                                    "scripts/case_archive/runtime_cli.py read-current"
                                )
                            },
                        },
                        failed_result(
                            "wrong-script",
                            2,
                            "can't open file scripts/case_archive/runtime_cli.py",
                        ),
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "callId": "reference-read",
                            "name": "Read",
                            "arguments": {
                                "file_path": str(
                                    skill_root
                                    / "references"
                                    / "model-led-case-archive.md"
                                )
                            },
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call_result",
                            "callId": "reference-read",
                            "name": "Read",
                            "status": "completed",
                            "output": {"type": "text", "text": "public contract"},
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "callId": "correct-read",
                            "name": "Bash",
                            "arguments": {"command": correct_command},
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call_result",
                            "callId": "correct-read",
                            "name": "Bash",
                            "status": "completed",
                            "output": {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "ok": True,
                                        "operation": "read",
                                        "result": {"case_id": case_id},
                                    }
                                ),
                            },
                            "providerData": {
                                "toolResult": {
                                    "rawResponse": {
                                        "exitCode": 0,
                                        "is_error": False,
                                    }
                                }
                            },
                        },
                        {
                            "sessionId": session_id,
                            "type": "assistant_output",
                            "content": "已从当前档案继续：目前仍在职，拖欠三个月工资。下一步希望先比较哪种处理路径？",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id=session_id,
            )

            codes = {item["code"] for item in report["violations"]}
            diagnostics = set(
                report["transcript_diagnostic"]["diagnostic_observations"]
            )
            self.assertNotIn("prohibited_temporary_script", codes)
            self.assertNotIn("prohibited_case_archive_transport", codes)
            self.assertNotIn("skill_invoked_but_bypassed", codes)
            self.assertIn("recoverable_case_archive_transport_retry", diagnostics)
            self.assertIn(
                "recovery_reference_read_before_current_archive", diagnostics
            )
            self.assertTrue(
                report["transcript_diagnostic"]["case_archive_action_succeeded"]
            )

    def test_default_acceptance_reports_post_hoc_only_memory_policy(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create({"initial_goal": "观察宿主 memory 诊断"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]

            report = audit_workspace(workspace=workspace, case_id=case_id)

            self.assertEqual(
                report["memory_policy"],
                {
                    "mode": "post_hoc_audit_only",
                    "prewrite_enforced": False,
                    "case_data_isolated": False,
                    "verified": False,
                },
            )






    def test_managed_view_reads_model_public_final_canonical_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )

            self.assertTrue(rendered["ok"], rendered)
            result = rendered["result"]
            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertFalse(view["stopped"])
            self.assertEqual(view["delivery_state"], "final_ready")
            self.assertEqual(
                [item["kind"] for item in view["presentation_files"]],
                ["document", "checklist"],
            )
            self.assertEqual(
                [item["path"] for item in view["presentation_files"]],
                [
                    str(Path(str(result["canonical_docx"])).resolve()),
                    str(Path(str(result["verification_checklist"])).resolve()),
                ],
            )
            self.assertEqual(
                rendered["result"]["presentation_files"],
                view["presentation_files"],
            )
            self.assertEqual(
                [item["state"] for item in view["presentation_files"]],
                ["final_ready", "final_ready"],
            )
            self.assertEqual(
                [item["order"] for item in view["presentation_files"]],
                [1, 2],
            )
            summary = view["delivery_summaries"][0]
            self.assertEqual(summary["contract_version"], "document.render-v1")
            self.assertEqual(summary["operation"], "document.render")
            self.assertEqual(summary["delivery_state"], "final_ready")
            self.assertEqual(summary["validation_summary"]["status"], "passed")

    def test_managed_view_reads_model_public_candidate_canonical_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision, mode="candidate"),
                workspace,
            )

            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertFalse(view["stopped"])
            self.assertEqual(view["delivery_state"], "candidate_ready")
            self.assertEqual(
                [item["state"] for item in view["presentation_files"]],
                ["candidate_ready", "candidate_ready"],
            )
            self.assertEqual(
                view["delivery_summaries"][0]["delivery_state"],
                "candidate_ready",
            )

    def test_managed_view_stops_when_archive_revision_changes_after_render(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "补充渲染后才确认的事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "渲染后新增事实：待重新核实。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertEqual(view["error_code"], "stale_managed_delivery")
            stale_report = audit_workspace(workspace=workspace, case_id=case_id)
            self.assertIn(
                "stale_managed_delivery",
                {item["code"] for item in stale_report["violations"]},
            )

            transcript = workspace / "render-order.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "render-order",
                            "type": "function_call",
                            "name": "document.render-v1",
                        },
                        {
                            "sessionId": "render-order",
                            "type": "function_call",
                            "name": "CaseArchive.commit",
                            "arguments": {"operation": "commit"},
                        },
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="render-order",
            )
            self.assertTrue(report["transcript_diagnostic"]["render_observed"])
            self.assertTrue(
                report["transcript_diagnostic"]["archive_change_after_render_observed"]
            )
            self.assertEqual(
                report["transcript_diagnostic"]["first_archive_change_after_render_index"],
                1,
            )
            self.assertEqual(
                report["transcript_diagnostic"]["archive_change_after_render_kind"],
                "unprompted_or_delivery_status",
            )


    def test_managed_view_rejects_model_public_delivery_without_manifest_or_summary(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            manifest.unlink()

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload.pop("validation_summary")
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

    def test_managed_view_rejects_model_archive_symlink_escape(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            archive_path = Path(
                str(archive.read(case_id)["result"]["canonical_location"])
            )
            with TemporaryDirectory() as outside_temp:
                external_archive = Path(outside_temp) / "external-archive.md"
                external_archive.write_text(
                    archive_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                archive_path.unlink()
                try:
                    archive_path.symlink_to(external_archive)
                except (OSError, NotImplementedError) as error:
                    self.skipTest(
                        f"当前 Windows 环境不允许创建文件符号链接：{error}"
                    )

                view = managed_delivery_view(
                    workspace=workspace, case_id=case_id
                )

                self.assertTrue(view["stopped"])
                self.assertEqual(view["presentation_files"], [])
                self.assertIn("case-archive-v1", view["reason"])

    def test_managed_view_rejects_model_manifest_and_checklist_contract_drift(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["template_version"] = "0.0.0"
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            checklist = Path(str(rendered["result"]["verification_checklist"]))
            checklist.write_text("不完整的清单", encoding="utf-8")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["verification_checklist"]["sha256"] = sha256(
                checklist.read_bytes()
            ).hexdigest()
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

    def test_managed_view_rejects_model_public_path_escape_and_invalid_docx(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["document"]["filename"] = "..\\outside.docx"
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            document = Path(str(rendered["result"]["canonical_docx"]))
            document.write_bytes(b"not-a-docx-package")

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            document = Path(str(rendered["result"]["canonical_docx"]))
            document.write_bytes(b"not-a-docx-package")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["document"]["sha256"] = sha256(
                document.read_bytes()
            ).hexdigest()
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            document = Path(str(rendered["result"]["canonical_docx"]))
            original = document.read_bytes()
            buffer = BytesIO()
            with ZipFile(document) as source, ZipFile(buffer, "w") as target:
                for entry in source.infolist():
                    content = source.read(entry.filename)
                    if entry.filename == "word/document.xml":
                        content = content.replace(
                            "劳动用工义务催告函".encode("utf-8"),
                            "请勿外发".encode("utf-8"),
                            1,
                        )
                    target.writestr(entry, content)
            mutated = buffer.getvalue()
            self.assertNotEqual(mutated, original)
            document.write_bytes(mutated)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["document"]["sha256"] = sha256(
                document.read_bytes()
            ).hexdigest()
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"])
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("document.render-v1", view["reason"])

    def test_acceptance_audits_model_public_render_v1_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )

            self.assertTrue(rendered["ok"], rendered)
            report = audit_workspace(workspace=workspace, case_id=case_id)

            self.assertTrue(report["passed"], report)
            self.assertEqual(
                report["expected_deliveries"],
                ["employment_obligation_demand_letter"],
            )
            self.assertEqual(report["presentation_count"], 2)
            self.assertEqual(
                report["presentation_files"],
                report["managed_delivery_view"]["presentation_files"],
            )
            self.assertTrue(report["delivery_validity"]["passed"])
            self.assertFalse(report["agent_bypass"]["observed"])

    def test_harness_builds_no_delivery_contract_from_sealed_transcript(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text('{"type":"message"}\n', encoding="utf-8")
            output = workspace / "evidence" / "no-delivery-contract.json"

            contract = build_no_delivery_contract(
                journey="ML03-v2",
                case_id="case-1234567890abcdef12345678",
                transcript_jsonl=transcript,
                output=output,
            )

            self.assertTrue(output.is_file())
            self.assertEqual(contract["source"], "workbuddy-journey-harness")
            self.assertEqual(contract["journey_id"], "ML03-v2")
            self.assertEqual(contract["case_id"], "case-1234567890abcdef12345678")
            self.assertEqual(contract["expected_deliveries"], [])
            self.assertEqual(
                set(contract),
                {
                    "schema_version",
                    "source",
                    "journey_id",
                    "case_id",
                    "expected_deliveries",
                    "transcript_sha256",
                },
            )
            self.assertEqual(
                contract["transcript_sha256"],
                sha256(transcript.read_bytes()).hexdigest(),
            )

    def test_harness_no_delivery_contract_rejects_unknown_journey(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text('{"type":"message"}\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                build_no_delivery_contract(
                    journey="ML01-v2",
                    case_id="case-1234567890abcdef12345678",
                    transcript_jsonl=transcript,
                    output=workspace / "contract.json",
                )

    def test_no_delivery_contract_rejects_manual_extra_field(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text('{"type":"message"}\n', encoding="utf-8")
            contract_path = _write_no_delivery_contract(transcript, case_id)
            payload = json.loads(contract_path.read_text(encoding="utf-8"))
            payload["manual_note"] = "不应接受"
            contract_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                no_delivery_contract=contract_path,
            )

            self.assertIn(
                "invalid_no_delivery_mode",
                {item["code"] for item in report["violations"]},
            )

    def test_no_delivery_contract_rejects_repository_artifact_path(self):
        with TemporaryDirectory() as temp:
            transcript = Path(temp) / "transcript.jsonl"
            transcript.write_text('{"type":"message"}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "源码树外"):
                build_no_delivery_contract(
                    journey="ML03-v2",
                    case_id="case-1234567890abcdef12345678",
                    transcript_jsonl=transcript,
                    output=ROOT / "tests" / "runs" / "should-not-exist.json",
                )

    def test_cli_builds_no_delivery_contract_without_manual_json(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text('{"type":"message"}\n', encoding="utf-8")
            output = workspace / "contract.json"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = workbuddy_acceptance_main([
                    "build-no-delivery-contract",
                    "--journey", "ML03-v2",
                    "--case-id", "case-1234567890abcdef12345678",
                    "--transcript-jsonl", str(transcript),
                    "--output", str(output),
                ])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["contract"]["journey_id"], "ML03-v2")
            self.assertEqual(payload["contract_path"], str(output.resolve()))

    def test_claude_slash_expansion_without_case_action_is_loaded_not_executed(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "claude-loaded-only",
                            "type": "message",
                            "content": (
                                "<command-name>/arbibuddy</command-name>"
                                " .claude/skills/arbibuddy/SKILL.md"
                            ),
                        },
                        {
                            "sessionId": "claude-loaded-only",
                            "type": "system",
                            "content": (
                                "name: arbibuddy\n"
                                "description: 面向中国大陆劳动者的劳动争议辅助"
                            ),
                        },
                    )
                ),
                encoding="utf-8",
            )
            client_log = workspace / "client.log"
            client_log.write_text(
                json.dumps({"event": "skill_installed"}, ensure_ascii=False),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                client_log=client_log,
                session_id="claude-loaded-only",
            )

            lifecycle = report["skill_lifecycle"]
            self.assertEqual(lifecycle["status"], "loaded_not_executed")
            self.assertTrue(lifecycle["activation_evidence"]["slash_command"])
            self.assertTrue(lifecycle["activation_evidence"]["skill_body_injected"])

    def test_claude_installed_without_activation_is_not_invoked(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "claude-not-invoked",
                        "type": "message",
                        "content": "公司拖欠工资，我想了解下一步。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client_log = workspace / "client.log"
            client_log.write_text(
                json.dumps({"event": "skill_installed"}, ensure_ascii=False),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                client_log=client_log,
                session_id="claude-not-invoked",
            )

            self.assertEqual(report["skill_lifecycle"]["status"], "not_invoked")

    def test_structured_skill_then_case_archive_action_is_executed(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "claude-executed",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "claude-executed",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                        {
                            "sessionId": "claude-executed",
                            "type": "function_call",
                            "id": "archive-call",
                            "name": "CaseArchive.read",
                            "arguments": {"case_id": case_id},
                        },
                        {
                            "sessionId": "claude-executed",
                            "type": "function_call_result",
                            "call_id": "archive-call",
                            "name": "CaseArchive.read",
                            "status": "success",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="claude-executed",
            )

            self.assertEqual(report["skill_lifecycle"]["status"], "executed")

    def test_loaded_contract_discovery_is_reported_as_bypass(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "claude-contract-discovery",
                            "type": "message",
                            "content": (
                                "<command-name>/arbibuddy</command-name>"
                                " .claude/skills/arbibuddy/SKILL.md"
                            ),
                        },
                        {
                            "sessionId": "claude-contract-discovery",
                            "type": "system",
                            "content": (
                                "name: arbibuddy\n"
                                "description: 面向中国大陆劳动者的劳动争议辅助"
                            ),
                        },
                        {
                            "sessionId": "claude-contract-discovery",
                            "type": "tool_call",
                            "name": "Read",
                            "arguments": {
                                "file_path": "scripts/case_archive/cli.py"
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )
            client_log = workspace / "client.log"
            client_log.write_text(
                json.dumps({"event": "skill_installed"}, ensure_ascii=False),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                client_log=client_log,
                session_id="claude-contract-discovery",
            )

            self.assertEqual(
                report["skill_lifecycle"]["status"],
                "loaded_then_contract_discovery_bypass",
            )

    def test_model_journey_without_delivery_can_explicitly_skip_document_gate(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "no-delivery-journey",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "no-delivery-journey",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                        {
                            "sessionId": "no-delivery-journey",
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "我暂时无法提供更多材料，本轮不生成文书。如果当前无法取得必要的最新官方依据，请只根据当前档案说明：哪些事项仍待核实、哪些分析可以继续、哪些高风险定稿必须暂缓；最后只问一个下一步问题。",
                                }
                            ],
                        },
                    )
                ),
                encoding="utf-8",
            )
            no_delivery_contract = _write_no_delivery_contract(transcript, case_id)

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="no-delivery-journey",
                allow_no_delivery=True,
                no_delivery_contract=no_delivery_contract,
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("invalid_model_delivery", codes)
            self.assertNotIn("missing_expected_deliveries", codes)
            self.assertTrue(report["passed"], report)

    def test_no_delivery_mode_rejects_document_activity(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "invalid-no-delivery",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "invalid-no-delivery",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                        {
                            "sessionId": "invalid-no-delivery",
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "我暂时无法提供更多材料，本轮不生成文书。如果当前无法取得必要的最新官方依据，请只根据当前档案说明：哪些事项仍待核实、哪些分析可以继续、哪些高风险定稿必须暂缓；最后只问一个下一步问题。",
                                }
                            ],
                        },
                        {
                            "sessionId": "invalid-no-delivery",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"files": ["candidate.docx"]},
                        },
                    )
                ),
                encoding="utf-8",
            )
            no_delivery_contract = _write_no_delivery_contract(transcript, case_id)

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="invalid-no-delivery",
                allow_no_delivery=True,
                no_delivery_contract=no_delivery_contract,
            )

            self.assertIn(
                "invalid_no_delivery_mode",
                {item["code"] for item in report["violations"]},
            )
            self.assertFalse(report["passed"], report)

    def test_no_delivery_mode_requires_explicit_user_goal(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "missing-no-delivery-goal",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "missing-no-delivery-goal",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                    )
                ),
                encoding="utf-8",
            )
            no_delivery_contract = _write_no_delivery_contract(transcript, case_id)

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="missing-no-delivery-goal",
                allow_no_delivery=True,
                no_delivery_contract=no_delivery_contract,
            )

            self.assertIn(
                "invalid_no_delivery_mode",
                {item["code"] for item in report["violations"]},
            )
            self.assertFalse(report["passed"], report)


    def test_trace_stops_after_public_failure_and_rejects_bottom_level_fallback(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-WORKBUDDY-STOP"
            case_root = workspace / ".arbibuddy" / "cases"
            case_id = self._create_delivery_case(case_root)
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text("\n".join(
                json.dumps(item, ensure_ascii=False)
                for item in (
                    {
                        "sessionId": "stop-session",
                        "timestamp": 100,
                        "commandPreview": "python -B -X utf8 -m scripts.core_workflow.cli run-package --request failed.json",
                        "returncode": 2,
                    },
                    {
                        "sessionId": "stop-session",
                        "timestamp": 200,
                        "commandPreview": "python -m scripts.documents.cli generate CASE --input x --output-dir output || true",
                        "returncode": 0,
                    },
                    {
                        "sessionId": "stop-session",
                        "timestamp": 300,
                        "commandPreview": "present_files output/旁路.docx",
                        "returncode": 0,
                    },
                )
            ), encoding="utf-8")

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="stop-session",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("continued_after_controlled_failure", codes)
            self.assertIn("prohibited_bottom_level_entry", codes)
            self.assertIn("masked_exit_status", codes)


    def test_rc9_native_skill_result_fixture_is_invoked_but_bypassed(self):
        fixture = (
            ROOT / "tests" / "fixtures" / "workbuddy"
            / "rc9-skill-called-bypassed-2026-08-21"
        )
        workspace = Path(__import__("tempfile").gettempdir()) / f"rc9-native-fixture-{time_ns()}"
        workspace.mkdir(parents=True)
        try:
            report = audit_workspace(
                workspace=workspace,
                case_id="SH-2026-RC9-MINIMAL-FIXTURE",
                audit_log=fixture / "audit.jsonl",
                session_id="fixture-rc9-skill-bypassed",
                transcript_jsonl=fixture / "transcript.jsonl",
                client_log=fixture / "client.log",
            )

            diagnostic = report["transcript_diagnostic"]
            codes = {item["code"] for item in report["violations"]}
            self.assertTrue(diagnostic["skill_invocation_observed"])
            self.assertTrue(diagnostic["skill_result_observed"])
            self.assertFalse(diagnostic["first_barrier_obeyed"])
            self.assertNotIn("skill_invocation_missing", codes)
            self.assertIn("skill_invoked_but_bypassed", codes)
            self.assertNotIn("skill_not_in_execution_chain", codes)
            self.assertEqual(
                report["skill_lifecycle"]["host_noise"],
                [
                    "builtin_tencent_docx_yaml_warning",
                    "file_version_store_enoent",
                    "session_run_state_machine_invalid_transition",
                ],
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_native_transcript_links_call_id_ignores_read_prep_and_blocks_same_round_resolution(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            transcript = workspace / "native.jsonl"
            envelope = {
                "schema_version": 1,
                "phase": "rights_resolution",
                "status": "awaiting_user_answer",
                "awaiting_user_answer": True,
                "next_action": "ask_user_and_end_turn",
                "same_turn_allowed_actions": [],
            }
            records = [
                {
                    "id": "record-skill-001",
                    "callId": "call-skill-001",
                    "sessionId": "native-session",
                    "type": "function_call",
                    "name": "Skill",
                    "arguments": json.dumps({"skill": "arbibuddy"}),
                },
                {
                    "id": "result-skill-001",
                    "callId": "call-skill-001",
                    "sessionId": "native-session",
                    "type": "function_call_result",
                    "name": "Skill",
                    "status": "success",
                    "output": {"type": "text", "text": "Skill loaded"},
                },
                {
                    "id": "record-read-001",
                    "callId": "call-read-001",
                    "sessionId": "native-session",
                    "type": "function_call",
                    "name": "Read",
                    "arguments": json.dumps({"path": "SKILL.md"}),
                },
                {
                    "id": "result-read-001",
                    "callId": "call-read-001",
                    "sessionId": "native-session",
                    "type": "function_call_result",
                    "name": "Read",
                    "status": "success",
                    "output": {"type": "text", "text": "read-only"},
                },
                {
                    "id": "record-core-001",
                    "callId": "call-core-001",
                    "sessionId": "native-session",
                    "type": "function_call",
                    "name": "Bash",
                    "arguments": json.dumps({
                        "command": "python -m scripts.core_workflow.cli next-turn CASE-001"
                    }),
                },
                {
                    "id": "result-core-001",
                    "callId": "call-core-001",
                    "sessionId": "native-session",
                    "type": "function_call_result",
                    "name": "Bash",
                    "status": "success",
                    "output": {
                        "type": "text",
                        "text": (
                            "Command exited with code 0\nStdout:\n"
                            + json.dumps(envelope, ensure_ascii=False)
                        ),
                    },
                },
                {
                    "id": "record-core-002",
                    "callId": "call-core-002",
                    "sessionId": "native-session",
                    "type": "function_call",
                    "name": "Bash",
                    "arguments": json.dumps({
                        "command": "python -m scripts.core_workflow.cli resolve-right CASE-001 --item RI-001"
                    }),
                },
            ]
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id="CASE-001",
                transcript_jsonl=transcript,
                session_id="native-session",
            )

            diagnostic = report["transcript_diagnostic"]
            codes = {item["code"] for item in report["violations"]}
            self.assertTrue(diagnostic["skill_invocation_observed"])
            self.assertTrue(diagnostic["skill_result_observed"])
            self.assertTrue(diagnostic["core_envelope_observed"])
            self.assertTrue(diagnostic["first_business_action_is_core"])
            self.assertNotIn("barrier_bypassed_without_user_round", codes)

    def test_rc7_real_no_skill_fixture_fails_closed_as_one_root_cause_group(self):
        fixture = (
            ROOT / "tests" / "fixtures" / "workbuddy"
            / "rc7-no-skill-2026-08-21"
        )
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-RC7-NO-SKILL-FIXTURE"
            memory = workspace / ".workbuddy" / "memory"
            memory.mkdir(parents=True)
            (memory / "2026-08-21.md").write_text(
                f"{case_id} 劳动争议文书旁路记录。", encoding="utf-8"
            )
            (workspace / "gen_arbitration.py").write_text(
                "from docx import Document\n"
                "Document().save('仲裁申请书.docx')\n",
                encoding="utf-8",
            )
            (workspace / "仲裁申请书.docx").write_bytes(b"not-an-ooxml-package")

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=fixture / "audit.jsonl",
                session_id="fixture-rc7-no-skill",
                transcript_jsonl=fixture / "transcript.jsonl",
                client_log=fixture / "client.log",
                require_memory_isolation=True,
            )

            self.assertFalse(report["passed"])
            self.assertEqual(report["transcript_diagnostic"]["records"], 44)
            self.assertFalse(report["transcript_diagnostic"]["skill_invocation_observed"])
            self.assertFalse(report["transcript_diagnostic"]["first_barrier_obeyed"])
            lifecycle = report["skill_lifecycle"]
            self.assertEqual(
                {
                    key: lifecycle[key]
                    for key in (
                        "installed",
                        "reload_observed",
                        "registered",
                        "exposed_to_model",
                        "invoked",
                        "host_noise",
                        "fail_closed",
                        "missing_stages",
                        "next_action",
                    )
                },
                {
                    "installed": True,
                    "reload_observed": True,
                    "registered": False,
                    "exposed_to_model": False,
                    "invoked": False,
                    "host_noise": [
                        "builtin_tencent_docx_yaml_warning",
                        "file_version_store_enoent",
                        "session_run_state_machine_invalid_transition",
                    ],
                    "fail_closed": True,
                    "missing_stages": [
                        "registered",
                        "exposed_to_model",
                        "invoked",
                    ],
                    "next_action": "重载、重启或重新导入 WorkBuddy Skill 后重新验证生命周期证据；缺少完整链路前不得计入真实 Journey。",
                },
            )
            self.assertEqual(lifecycle["status"], "not_invoked")
            codes = {item["code"] for item in report["violations"]}
            for required in (
                "skill_invocation_missing",
                "skill_not_in_execution_chain",
                "invalid_case_file",
                "prohibited_generation_script",
                "prohibited_runtime_dependency_install",
                "prohibited_runtime_environment_creation",
                "prohibited_temporary_script",
                "prohibited_present_files",
                "unmanaged_docx",
                "invalid_docx_package",
                "workbuddy_memory_isolation_unverified",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, codes)
            self.assertEqual(
                report["root_causes"][0]["code"],
                "skill_not_in_execution_chain",
            )
            self.assertIn(
                "prohibited_present_files",
                report["root_causes"][0]["related_violations"],
            )
            self.assertNotIn(
                "session_run_state_machine_invalid_transition",
                codes,
            )

    def test_rc5_skill_call_does_not_overrule_failure_bypass_or_compliance_claim(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-WORKBUDDY-RC5-TRACE"
            case_id = self._create_delivery_case(workspace / ".arbibuddy" / "cases")
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "rc5-trace",
                            "timestamp": 100,
                            "commandPreview": "python -B -X utf8 -m scripts.core_workflow.cli preflight-package --request package.json",
                            "returncode": 2,
                            "response": {
                                "error_code": "runtime_temp_unavailable",
                                "marker": "SAFE_DELETE_FAIL_CLOSED",
                            },
                        },
                        {
                            "sessionId": "rc5-trace",
                            "timestamp": 150,
                            "commandPreview": "python -m scripts.core_workflow.cli acknowledge-preflight-failure CASE --fence-id PF-001",
                            "returncode": 0,
                        },
                        {
                            "sessionId": "rc5-trace",
                            "timestamp": 200,
                            "commandPreview": "python -c \"from docx import Document; Document().save('output/bypass.docx')\"",
                            "returncode": 0,
                        },
                        {
                            "sessionId": "rc5-trace",
                            "timestamp": 300,
                            "commandPreview": "present_files output/bypass.docx",
                            "returncode": 0,
                        },
                    )
                ),
                encoding="utf-8",
            )
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "rc5-trace",
                            "type": "tool_call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "rc5-trace",
                            "type": "assistant_output_metadata",
                            "compliance_claim": True,
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                transcript_jsonl=transcript,
                session_id="rc5-trace",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertTrue(report["transcript_diagnostic"]["skill_invocation_observed"])
            self.assertIn("safe_delete_failure_requires_stop", codes)
            self.assertIn("bypass_after_controlled_failure", codes)
            self.assertIn("failure_acknowledgement_requires_user_round", codes)
            self.assertIn("unsubstantiated_compliance_claim", codes)
            self.assertFalse(report["passed"])

    def test_safe_delete_failure_rejects_bypass_and_preserves_prior_authorized_files(self):
        base = Path(__import__("tempfile").gettempdir()) / f"v125-rc6-workbuddy-{time_ns()}"
        workspace = base
        base.mkdir(parents=True)
        case_id = "SH-2026-WORKBUDDY-SAFE-DELETE"
        case_root = workspace / ".arbibuddy" / "cases"
        (case_root / case_id).mkdir(parents=True)
        try:
            case_id = self._create_delivery_case(case_root)
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "safe-delete",
                            "timestamp": 100,
                            "commandPreview": "python -m scripts.core_workflow.cli preflight-package --request package.json",
                            "returncode": 2,
                            "response": {
                                "error_code": "runtime_temp_unavailable",
                                "safe_delete": "fail_closed",
                                "message": "SAFE_DELETE_FAIL_CLOSED",
                            },
                        },
                        {
                            "sessionId": "safe-delete",
                            "timestamp": 200,
                            "commandPreview": "python -c \"from docx import Document; Document().save('output/equivalent.docx')\"",
                            "returncode": 0,
                        },
                        {
                            "sessionId": "safe-delete",
                            "timestamp": 300,
                            "commandPreview": "present_files output/equivalent.docx",
                            "returncode": 0,
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="safe-delete",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("safe_delete_failure_requires_stop", codes)
            self.assertIn("bypass_after_controlled_failure", codes)
            self.assertFalse(report["passed"])
            self.assertFalse(report["managed_delivery_view"]["stopped"])
            self.assertTrue(report["managed_delivery_view"]["presentation_files"])
            self.assertNotIn("equivalent.docx", str(report["managed_delivery_view"]["presentation_files"]))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_model_write_personal_case_data_to_memory_is_diagnostic_only(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-PRIVATE-NO-ID"
            case_root = workspace / ".arbibuddy" / "cases"
            case_id = self._create_delivery_case(case_root)
            memory = workspace / ".workbuddy" / "memory" / "summary.md"
            memory.parent.mkdir(parents=True)
            memory.write_text(
                "申请人联系电话 13800000000，工资请求金额 12000元。",
                encoding="utf-8",
            )
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "memory-write-no-id",
                        "type": "tool_call",
                        "name": "Write",
                        "arguments": json.dumps({"path": str(memory)}),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="memory-write-no-id",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn(
                "model_wrote_workbuddy_memory",
                report["transcript_diagnostic"]["diagnostic_observations"],
            )
            self.assertNotIn("model_wrote_workbuddy_memory", codes)
            self.assertNotIn("skill_invoked_but_bypassed", codes)

    def test_model_read_of_workbuddy_memory_is_diagnostic_only(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-PRIVATE-EXACT"
            case_root = workspace / ".arbibuddy" / "cases"
            case_id = self._create_delivery_case(case_root)
            memory = workspace / ".workbuddy" / "memory" / "summary.md"
            memory.parent.mkdir(parents=True)
            memory.write_text(
                "用于 WorkBuddy 客户端验收的测试事实。", encoding="utf-8"
            )
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "memory-read",
                        "type": "tool_call",
                        "name": "Read",
                        "arguments": json.dumps({"path": str(memory)}),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="memory-read",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn(
                "model_read_untrusted_context",
                report["transcript_diagnostic"]["diagnostic_observations"],
            )
            self.assertNotIn("model_read_untrusted_context", codes)
            self.assertNotIn("skill_invoked_but_bypassed", codes)

    def test_memory_rule_terms_reused_in_current_analysis_are_diagnostic_only(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            memory_path = workspace / ".workbuddy" / "memory" / "2026-09-19.md"
            memory_path.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "memory-rule-terms",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory_path)},
                        },
                        {
                            "sessionId": "memory-rule-terms",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": (
                                "历史工作记录：全国层面规则，劳动关系终止后起算一年。"
                            ),
                        },
                        {
                            "sessionId": "memory-rule-terms",
                            "type": "assistant_output",
                            "content": (
                                "当前适用地区尚未提供，先按全国层面稳定规则说明；"
                                "如劳动关系终止，时效从终止之日起算一年。"
                            ),
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="memory-rule-terms",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn(
                "model_read_untrusted_context",
                report["transcript_diagnostic"]["diagnostic_observations"],
            )
            self.assertNotIn("cross_source_case_fact_pollution", codes)

    def test_same_case_memory_date_aliases_are_diagnostic_only(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录当前用户提供的入职事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "2025 年 1 月入职，当前仍在职。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            memory_path = workspace / ".workbuddy" / "memory" / "2026-09-20.md"
            memory_path.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "same-case-memory-date",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory_path)},
                        },
                        {
                            "sessionId": "same-case-memory-date",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "当前案件记录：2025-01 入职。",
                        },
                        {
                            "sessionId": "same-case-memory-date",
                            "type": "tool_call",
                            "id": "memory-edit",
                            "name": "Edit",
                            "arguments": {
                                "path": str(memory_path),
                                "old_string": "2025-01 入职",
                                "new_string": "2025-01 入职；仍在职。",
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="same-case-memory-date",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn(
                "model_read_untrusted_context",
                report["transcript_diagnostic"]["diagnostic_observations"],
            )
            self.assertNotIn("cross_source_case_fact_pollution", codes)

    def test_untrusted_historical_amount_and_duration_used_in_reply_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            memory_path = workspace / ".workbuddy" / "memory" / "history.md"
            memory_path.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "memory-case-fact",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory_path)},
                        },
                        {
                            "sessionId": "memory-case-fact",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "历史案件：月薪 29137 元，欠薪 7 个月。",
                        },
                        {
                            "sessionId": "memory-case-fact",
                            "type": "assistant_output",
                            "content": "当前案件月薪 29137 元，欠薪 7 个月。",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="memory-case-fact",
            )

            self.assertIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )

    def test_untrusted_history_fact_used_in_reply_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            memory_path = workspace / ".workbuddy" / "memory" / "history.md"
            memory_path.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "history-pollution",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory_path)},
                        },
                        {
                            "sessionId": "history-pollution",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "历史背景：CANARY-HANGZHOU-2026；适用地区：杭州。",
                        },
                        {
                            "sessionId": "history-pollution",
                            "type": "assistant_output",
                            "content": "根据杭州地区的规则，先按该地区处理。",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="history-pollution",
            )

            self.assertIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )
            self.assertTrue(
                report["transcript_diagnostic"]["cross_source_fact_pollution_observed"]
            )

    def test_untrusted_history_fact_not_repeated_when_model_keeps_region_unbound(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            memory_path = workspace / ".workbuddy" / "memory" / "history.md"
            memory_path.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "history-contained",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory_path)},
                        },
                        {
                            "sessionId": "history-contained",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "历史背景：CANARY-HANGZHOU-2026；适用地区：杭州。",
                        },
                        {
                            "sessionId": "history-contained",
                            "type": "assistant_output",
                            "content": "当前地区尚未提供，我会把地区和规则适用性留作待核验。",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="history-contained",
            )

            self.assertNotIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )

    def test_local_authority_region_without_current_case_binding_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "unbound-authority",
                        "type": "function_call",
                        "name": "Authority.verify",
                        "arguments": {"region": "上海市"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="unbound-authority",
            )

            self.assertIn(
                "unbound_local_authority",
                {item["code"] for item in report["violations"]},
            )

    def test_national_authority_does_not_require_a_local_region_binding(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对劳动规则"})
            case_id = created["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "national-authority",
                        "type": "function_call",
                        "name": "Authority.verify",
                        "arguments": {"region": "全国"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="national-authority",
            )

            self.assertNotIn(
                "unbound_local_authority",
                {item["code"] for item in report["violations"]},
            )

    def test_local_web_search_source_with_national_rule_is_rejected_without_current_case_binding(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "local-web-search",
                            "type": "function_call",
                            "name": "WebSearch",
                            "arguments": {"query": "上海 最低工资 shanghai.gov.cn"},
                        },
                        {
                            "sessionId": "local-web-search",
                            "type": "function_call",
                            "name": "Authority.verify",
                            "arguments": {
                                "scope": "全国劳动规则",
                                "jurisdiction": "全国",
                                "publisher_jurisdiction": "上海市",
                                "source_url": "https://shanghai.gov.cn/example/law",
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="local-web-search",
            )

            self.assertIn(
                "unbound_local_authority",
                {item["code"] for item in report["violations"]},
            )
            self.assertTrue(
                report["transcript_diagnostic"]["unbound_local_authority_observed"]
            )


    def test_present_files_must_match_managed_canonical_paths(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            authorized_files = list(files)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "managed-present",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call",
                            "id": "render-call",
                            "name": "document.render-v1",
                            "arguments": {
                                "case_id": case_id,
                                "archive_revision": revision,
                            },
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call_result",
                            "call_id": "render-call",
                            "name": "document.render-v1",
                            "status": "success",
                            "result": rendered,
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call",
                            "id": "managed-view-call",
                            "name": "Bash",
                            "arguments": {
                                "command": (
                                    "python -m scripts.documents.runtime_cli "
                                    "view --json request"
                                )
                            },
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call_result",
                            "call_id": "managed-view-call",
                            "name": "Bash",
                            "status": "success",
                            "result": view,
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call",
                            "id": "present-call",
                            "name": "present_files",
                            "arguments": {
                                "cwd": str(workspace),
                                "files": files,
                            },
                        },
                        {
                            "sessionId": "managed-present",
                            "type": "function_call_result",
                            "call_id": "present-call",
                            "name": "present_files",
                            "status": "success",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="managed-present",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("prohibited_present_files", codes)
            self.assertNotIn("unmanaged_docx", codes)
            self.assertTrue(report["delivery_validity"]["passed"])
            self.assertFalse(report["agent_bypass"]["observed"])

            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "unstructured-present",
                        "type": "function_call",
                        "name": "present_files",
                        "arguments": {
                            "command": "present_files " + " ".join(authorized_files)
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="unstructured-present",
            )
            self.assertIn(
                "prohibited_present_files",
                {item["code"] for item in report["violations"]},
            )

            files[-1] = str(workspace / "outside.docx")
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "unmanaged-present",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"cwd": str(workspace), "files": files},
                        },
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="unmanaged-present",
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertIn("prohibited_present_files", codes)

            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                json.dumps(
                    {
                        "sessionId": "audit-present",
                        "timestamp": 100,
                        "commandPreview": "present_files " + " ".join(
                            [*authorized_files, str(workspace / "extra.docx")]
                        ),
                        "returncode": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="audit-present",
            )
            self.assertIn(
                "prohibited_present_files",
                {item["code"] for item in report["violations"]},
            )

    def test_managed_view_exposes_a_single_copyable_present_files_arguments_object(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertEqual(
                view["present_files_arguments"],
                {
                    "cwd": str(workspace.resolve()),
                    "files": [item["path"] for item in view["presentation_files"]],
                },
            )

    def test_split_or_extra_present_files_are_distinct_managed_presentation_failures(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            manifest = Path(str(rendered["result"]["machine_manifest"])).resolve()

            split = workspace / "split.jsonl"
            split.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "split-present",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"cwd": str(workspace), "files": files[:1]},
                        },
                        {
                            "sessionId": "split-present",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"cwd": str(workspace), "files": files[1:]},
                        },
                    )
                ),
                encoding="utf-8",
            )
            split_report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=split,
                session_id="split-present",
            )
            self.assertIn(
                "split_managed_presentation",
                {item["code"] for item in split_report["violations"]},
            )

            extra = workspace / "extra.jsonl"
            extra.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "extra-present",
                            "type": "function_call",
                            "id": "extra-present-call",
                            "name": "present_files",
                            "arguments": {
                                "cwd": str(workspace),
                                "files": [
                                    *files,
                                    str(workspace / "outputs" / "旁路.md"),
                                ],
                            },
                        },
                        {
                            "sessionId": "extra-present",
                            "type": "function_call_result",
                            "call_id": "extra-present-call",
                            "name": "present_files",
                            "status": "success",
                        },
                    )
                ),
                encoding="utf-8",
            )
            extra_report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=extra,
                session_id="extra-present",
            )
            self.assertIn(
                "extra_unmanaged_presentation",
                {item["code"] for item in extra_report["violations"]},
            )
            self.assertIn(
                "prohibited_present_files",
                {item["code"] for item in extra_report["violations"]},
            )

    def test_unmanaged_archive_then_complete_bundle_is_not_split_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "archive-then-complete.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "archive-then-complete",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {
                                "cwd": str(workspace),
                                "files": [
                                    str(
                                        workspace
                                        / ".arbibuddy"
                                        / "cases"
                                        / case_id
                                        / "案情档案.md"
                                    )
                                ],
                            },
                        },
                        {
                            "sessionId": "archive-then-complete",
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"cwd": str(workspace), "files": files},
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="archive-then-complete",
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertIn("extra_unmanaged_presentation", codes)
            self.assertNotIn("split_managed_presentation", codes)

    def test_shell_wrapped_managed_view_supplies_revision_snapshot(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]

            def shell_result(command: str, payload: dict[str, object]) -> dict[str, object]:
                return {
                    "type": "text",
                    "text": (
                        f"Command: {command}\nStdout: "
                        + json.dumps(payload, ensure_ascii=False)
                        + "\n\nStderr:"
                    ),
                }

            transcript = workspace / "shell-managed-view.jsonl"
            records = [
                {
                    "sessionId": "shell-managed-view",
                    "type": "function_call",
                    "id": "render",
                    "name": "Bash",
                    "arguments": {
                        "command": "python -c scripts.documents.public.render"
                    },
                },
                {
                    "sessionId": "shell-managed-view",
                    "type": "function_call_result",
                    "call_id": "render",
                    "name": "Bash",
                    "status": "success",
                    "output": shell_result("render", rendered),
                },
                {
                    "sessionId": "shell-managed-view",
                    "type": "function_call",
                    "id": "managed",
                    "name": "Bash",
                    "arguments": {
                        "command": (
                            "python -m scripts.documents.runtime_cli "
                            "--workspace . view --json '{}'"
                        )
                    },
                },
                {
                    "sessionId": "shell-managed-view",
                    "type": "function_call_result",
                    "call_id": "managed",
                    "name": "Bash",
                    "status": "success",
                    "output": shell_result("managed-view", view),
                },
                {
                    "sessionId": "shell-managed-view",
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
                session_id="shell-managed-view",
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn(
                "invalid_revision_timeline",
                codes,
                report["transcript_diagnostic"]["revision_timeline"],
            )
            self.assertGreaterEqual(
                report["transcript_diagnostic"]["revision_timeline"]["render_count"],
                1,
            )

    def test_historical_managed_view_survives_stale_current_delivery_state(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            files = [item["path"] for item in view["presentation_files"]]

            transcript = workspace / "historical-view-stale-current.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                            "result": "Skill loaded",
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call",
                            "id": "render-call",
                            "name": "document.render-v1",
                            "arguments": {
                                "case_id": case_id,
                                "archive_revision": revision,
                            },
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call_result",
                            "call_id": "render-call",
                            "name": "document.render-v1",
                            "status": "success",
                            "result": rendered,
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call",
                            "id": "view-call",
                            "name": "Bash",
                            "arguments": {
                                "command": (
                                    "python -m scripts.documents.runtime_cli "
                                    "view --json request"
                                )
                            },
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call_result",
                            "call_id": "view-call",
                            "name": "Bash",
                            "status": "success",
                            "result": view,
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call",
                            "id": "present-call",
                            "name": "present_files",
                            "arguments": {
                                "cwd": str(workspace),
                                "files": files,
                            },
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "user_message",
                            "role": "user",
                            "content": "继续补充一项事实",
                        },
                        {
                            "sessionId": "historical-view-stale-current",
                            "type": "function_call",
                            "id": "commit-call",
                            "name": "CaseArchive.commit",
                            "arguments": {
                                "operation": "commit",
                                "case_id": case_id,
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录展示后的用户补充",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户补充：展示后继续核对。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            current_view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertEqual(current_view["delivery_state"], "unavailable", current_view)
            for path in files:
                Path(path).unlink()

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="historical-view-stale-current",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("invalid_model_delivery", codes, report)
            self.assertNotIn("stale_managed_delivery", codes, report)
            self.assertNotIn("invalid_revision_timeline", codes, report)
            self.assertNotIn("prohibited_present_files", codes, report)
            self.assertTrue(
                report["transcript_diagnostic"]["revision_timeline"]
                ["historical_presentations"][0]["valid_at_event"]
            )
            self.assertTrue(
                report["transcript_diagnostic"]["revision_timeline"]
                ["current_artifact_diagnostics"]
            )

    def test_revised_delivery_can_replace_prior_files_without_invalidating_history(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _ = create_model_case(workspace)
            first_render = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(first_render["ok"], first_render)
            first_view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in first_view["presentation_files"]]

            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "用户澄清正文事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户澄清后重生成候选文书。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            next_revision = revision + 1
            second_render = public_render(
                model_demand_request(case_id, next_revision), workspace
            )
            self.assertTrue(second_render["ok"], second_render)
            second_view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertEqual(
                [item["path"] for item in second_view["presentation_files"]], files
            )

            session_id = "revised-delivery-same-path"
            records = [
                {
                    "sessionId": session_id,
                    "type": "function_call",
                    "id": "skill-call",
                    "name": "Skill",
                    "arguments": {"skill": "arbibuddy"},
                },
                {
                    "sessionId": session_id,
                    "type": "function_call_result",
                    "call_id": "skill-call",
                    "name": "Skill",
                    "status": "success",
                    "result": "Skill loaded",
                },
            ]
            for round_index, (rendered, view) in enumerate(
                ((first_render, first_view), (second_render, second_view)), start=1
            ):
                if round_index == 2:
                    records.extend(
                        [
                            {
                                "sessionId": session_id,
                                "type": "user_message",
                                "role": "user",
                                "content": "请按澄清重新生成候选稿",
                            },
                            {
                                "sessionId": session_id,
                                "type": "function_call",
                                "name": "CaseArchive.commit",
                                "arguments": {"operation": "commit", "case_id": case_id},
                            },
                        ]
                    )
                render_id = f"render-{round_index}"
                view_id = f"view-{round_index}"
                records.extend(
                    [
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "id": render_id,
                            "name": "document.render-v1",
                            "arguments": {
                                "case_id": case_id,
                                "archive_revision": revision + round_index - 1,
                            },
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call_result",
                            "call_id": render_id,
                            "name": "document.render-v1",
                            "status": "success",
                            "result": rendered,
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "id": view_id,
                            "name": "Bash",
                            "arguments": {
                                "command": "python -m scripts.documents.runtime_cli view --json request"
                            },
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call_result",
                            "call_id": view_id,
                            "name": "Bash",
                            "status": "success",
                            "result": view,
                        },
                        {
                            "sessionId": session_id,
                            "type": "function_call",
                            "name": "present_files",
                            "arguments": {"cwd": str(workspace), "files": files},
                        },
                    ]
                )
            transcript = workspace / "revised-delivery-same-path.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id=session_id,
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("invalid_revision_timeline", codes, report)
            self.assertEqual(
                len(
                    report["transcript_diagnostic"]["revision_timeline"]
                    ["historical_presentations"]
                ),
                2,
            )

            Path(files[0]).write_bytes(b"tampered current document")
            tampered = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id=session_id,
            )
            self.assertIn(
                "invalid_revision_timeline",
                {item["code"] for item in tampered["violations"]},
            )

    def test_model_present_files_without_prior_managed_view_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "present-without-managed-view.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "present-without-managed-view",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": {"skill": "arbibuddy"},
                        },
                        {
                            "sessionId": "present-without-managed-view",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                        },
                        {
                            "sessionId": "present-without-managed-view",
                            "type": "function_call",
                            "id": "present-call",
                            "name": "present_files",
                            "arguments": {
                                "cwd": str(workspace),
                                "files": files,
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="present-without-managed-view",
            )

            self.assertIn(
                "prohibited_present_files",
                {item["code"] for item in report["violations"]},
            )

    def test_persistent_render_request_copy_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            request_dir = workspace / "render-requests"
            request_dir.mkdir()
            (request_dir / "application.json").write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "applicant": "测试申请人",
                        "sections": [{"section_id": "facts", "text": "案件事实"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(workspace=workspace, case_id=case_id)

            self.assertIn(
                "persistent_runtime_request_copy",
                {item["code"] for item in report["violations"]},
            )

    def test_runtime_source_introspection_is_reported_for_document_contract_discovery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _ = create_model_case(workspace)
            transcript = workspace / "source-inspection.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "source-inspection",
                        "type": "function_call",
                        "name": "Bash",
                        "arguments": {
                            "command": (
                                "python -c \"import inspect, "
                                "scripts.documents.public as p; "
                                "print(inspect.getsource(p.render))\""
                            )
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="source-inspection",
            )

            self.assertIn(
                "prohibited_runtime_source_inspection",
                {item["code"] for item in report["violations"]},
            )

    def test_revision_timeline_rejects_commit_before_present_without_new_user_round(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "timeline-invalid.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in _timeline_transcript(
                        session_id="timeline-invalid",
                        case_id=case_id,
                        revision=revision,
                        manifest=Path(str(rendered["result"]["machine_manifest"])),
                        files=files,
                        commit_before_present=True,
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="timeline-invalid",
            )

            self.assertIn(
                "invalid_revision_timeline",
                {item["code"] for item in report["violations"]},
            )

    def test_revision_timeline_keeps_historical_present_valid_after_follow_up_commit(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "timeline-historical.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in _timeline_transcript(
                        session_id="timeline-historical",
                        case_id=case_id,
                        revision=revision,
                        manifest=Path(str(rendered["result"]["machine_manifest"])),
                        files=files,
                        include_user_follow_up=True,
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="timeline-historical",
            )

            self.assertNotIn(
                "invalid_revision_timeline",
                {item["code"] for item in report["violations"]},
            )
            self.assertTrue(
                report["transcript_diagnostic"]["revision_timeline"]["historical_presentations"][0]["valid_at_event"]
            )

    def test_revision_timeline_rejects_old_present_after_follow_up_commit(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "timeline-represented.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in _timeline_transcript(
                        session_id="timeline-represented",
                        case_id=case_id,
                        revision=revision,
                        manifest=Path(str(rendered["result"]["machine_manifest"])),
                        files=files,
                        include_user_follow_up=True,
                        include_second_present=True,
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="timeline-represented",
            )

            self.assertIn(
                "invalid_revision_timeline",
                {item["code"] for item in report["violations"]},
            )

    def test_revision_timeline_accepts_new_render_after_follow_up_commit(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            transcript = workspace / "timeline-refreshed.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in _timeline_transcript(
                        session_id="timeline-refreshed",
                        case_id=case_id,
                        revision=revision,
                        manifest=Path(str(rendered["result"]["machine_manifest"])),
                        files=files,
                        include_user_follow_up=True,
                        include_second_render=True,
                        include_second_present=True,
                    )
                ),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="timeline-refreshed",
            )

            self.assertNotIn(
                "invalid_revision_timeline",
                {item["code"] for item in report["violations"]},
            )










    def test_host_memory_file_alone_is_not_a_skill_failure(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-PRIVATE-UNREADABLE"
            case_root = workspace / ".arbibuddy" / "cases"
            case_id = self._create_delivery_case(case_root)
            memory = workspace / ".workbuddy" / "memory" / "opaque.bin"
            memory.parent.mkdir(parents=True)
            memory.write_bytes(b"\xff\xfe\x00")

            report = audit_workspace(workspace=workspace, case_id=case_id)

            self.assertNotIn(
                "unreadable_workbuddy_memory",
                {item["code"] for item in report["violations"]},
            )







    def test_unspecified_expected_deliveries_use_authoritative_public_receipts(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-NO-EXPECTATION"
            case_root = workspace / ".arbibuddy" / "cases"
            case_id = self._create_delivery_case(case_root)

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                expected_deliveries=(),
            )

            self.assertTrue(report["passed"], report)
            self.assertNotIn(
                "missing_expected_deliveries",
                {item["code"] for item in report["violations"]},
            )







    def test_client_trace_rejects_document_generation_before_final_supplement(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-TRACE"
            case_dir = workspace / ".arbibuddy" / "cases" / case_id
            case_id = self._create_delivery_case(case_dir.parent)
            case_dir = case_dir.parent / case_id
            (case_dir / "output").mkdir(parents=True, exist_ok=True)
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "session-1",
                            "timestamp": 100,
                            "commandPreview": "python generate_docs.py",
                        },
                        {
                            "sessionId": "session-1",
                            "timestamp": 200,
                            "commandPreview": (
                                "python -m scripts.intake_orchestration.cli "
                                "record-supplement-answer --no-new-facts"
                            ),
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="session-1",
            )

            self.assertEqual(
                {item["code"] for item in report["violations"]},
                {
                    "document_before_final_supplement",
                    "document_before_confirmation",
                    "prohibited_document_command",
                    "prohibited_temporary_script",
                },
            )

    def test_client_trace_rejects_manual_recursive_cleanup(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-DELETE"
            case_dir = workspace / ".arbibuddy" / "cases" / case_id
            case_id = self._create_delivery_case(case_dir.parent)
            case_dir = case_dir.parent / case_id
            (case_dir / "output").mkdir(parents=True, exist_ok=True)
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                json.dumps(
                    {
                        "sessionId": "session-delete",
                        "timestamp": 100,
                        "commandPreview": (
                            "Remove-Item -LiteralPath .arbibuddy/cases/SH-2026-DELETE/output "
                            "-Recurse -Force"
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="session-delete",
            )

            self.assertIn(
                "prohibited_manual_cleanup",
                {item["code"] for item in report["violations"]},
            )


    @staticmethod
    def _write_minimal_docx(path: Path) -> None:
        from zipfile import ZIP_DEFLATED, ZipFile

        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<w:document/>")
            archive.writestr("word/styles.xml", "<w:styles/>")
            archive.writestr("word/footer1.xml", "<w:ftr/>")

    def test_strict_cli_audit_fails_closed_even_when_memory_is_empty(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-MEMORY-POLICY-STRICT"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            output = StringIO()

            with redirect_stdout(output):
                exit_code = workbuddy_acceptance_main([
                    "--workspace", str(workspace),
                    "--case-id", case_id,
                    "--require-memory-isolation",
                ])

            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertFalse(report["passed"])
            self.assertIn(
                "workbuddy_memory_isolation_unverified",
                {item["code"] for item in report["violations"]},
            )
            self.assertEqual(report["memory_policy"]["verified"], False)

    def test_managed_view_cli_stops_before_core_authorizes_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-WORKBUDDY-MANAGED-VIEW"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            output = StringIO()

            with redirect_stdout(output):
                exit_code = document_runtime_main([
                    "--workspace", str(workspace),
                    "view", "--json", json.dumps({"case_id": case_id}),
                ])

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["delivery_state"], "unavailable")
            self.assertEqual(payload["presentation_files"], [])

    def test_managed_view_normalizes_msys_workspace_path(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-WORKBUDDY-MSYS"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            msys_workspace = "/" + workspace.drive[0].casefold() + workspace.as_posix()[2:]

            native = managed_delivery_view(workspace=workspace, case_id=case_id)
            msys = managed_delivery_view(
                workspace=Path(msys_workspace), case_id=case_id
            )

            self.assertEqual(msys, native)

    def test_transcript_requires_structured_skill_call_not_output_self_description(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-WORKBUDDY-RC6-NO-SKILL"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "rc6-no-skill",
                            "type": "assistant_output",
                            "content": "严格按 ArbiBuddy 技能受控流程处理。",
                        },
                        {
                            "sessionId": "rc6-no-skill",
                            "type": "tool_call",
                            "name": "present_files",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="rc6-no-skill",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("skill_invocation_missing", codes)
            self.assertFalse(
                report["transcript_diagnostic"]["skill_invocation_observed"]
            )

    def test_memory_note_explanation_does_not_count_as_case_path_access(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-666666666666666666666666"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            memory = workspace / ".workbuddy" / "memory" / "2026-08-11.md"
            memory.parent.mkdir(parents=True)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "memory-explanation",
                        "type": "function_call",
                        "name": "Write",
                        "arguments": {
                            "file_path": str(memory),
                            "content": (
                                "案件事实只落在 `.arbibuddy/cases/<case_id>/"
                                "案情档案.md`，这不是一次文件读取。"
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="memory-explanation",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("cross_workspace_case_access", codes)

    def test_read_only_output_truncation_is_not_masked_exit_status(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-READONLY-PIPE"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                json.dumps(
                    {
                        "sessionId": "readonly-pipe",
                        "timestamp": 100,
                        "commandPreview": (
                            'ls -R "C:/Users/tester/.workbuddy/skills/arbibuddy" '
                            "| head -80"
                        ),
                        "returncode": 0,
                    }
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="readonly-pipe",
            )

            self.assertNotIn(
                "masked_exit_status",
                {item["code"] for item in report["violations"]},
            )

    def test_business_output_truncation_is_masked_exit_status(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "SH-2026-BUSINESS-PIPE"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            audit_log = workspace / "audit.jsonl"
            audit_log.write_text(
                json.dumps(
                    {
                        "sessionId": "business-pipe",
                        "timestamp": 100,
                        "commandPreview": (
                            "python -m scripts.case_archive.cli read "
                            f"{case_id} 2>&1 | head -30"
                        ),
                        "returncode": 0,
                    }
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                audit_log=audit_log,
                session_id="business-pipe",
            )

            self.assertIn(
                "masked_exit_status",
                {item["code"] for item in report["violations"]},
            )

    def test_user_visible_internal_revision_is_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-777777777777777777777777"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "internal-leak",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "已完成，档案最新修订版本 13。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="internal-leak",
            )

            self.assertIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_user_visible_revision_and_record_id_variants_are_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-888888888888888888888888"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "internal-variants",
                        "type": "assistant_output",
                        "content": "当前 revision: 4，record ID: F-001。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="internal-variants",
            )

            self.assertIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_user_visible_generic_internal_number_variants_are_rejected(self):
        for index, message in enumerate(
            (
                "已修正 X-001。",
                "内部编号：`X-001`。",
                "档案记录编号 X-001 已更新。",
            ),
            start=1,
        ):
            with self.subTest(message=message), TemporaryDirectory() as temp:
                workspace = Path(temp)
                case_id = f"case-{index:024d}"
                case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
                transcript = workspace / "transcript.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "sessionId": "generic-internal-leak",
                            "type": "assistant_output",
                            "content": message,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                report = audit_workspace(
                    workspace=workspace,
                    case_id=case_id,
                    transcript_jsonl=transcript,
                    session_id="generic-internal-leak",
                )

                self.assertIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_ml03_i_internal_process_details_are_rejected_without_false_positive(self):
        messages = (
            "事实纠正（replace 通道）：三个月欠薪已记录。",
            "中间出现过一次可恢复的输入路径类失败，原样重放后成功。",
            "之后按显式案件编号再读一次正常。",
        )
        for index, message in enumerate(messages, start=1):
            with self.subTest(message=message), TemporaryDirectory() as temp:
                workspace = Path(temp)
                case_id = f"case-{index:024d}"
                case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
                transcript = workspace / "transcript.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "sessionId": "ml03-i-internal-leak",
                            "type": "assistant_output",
                            "content": message,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                report = audit_workspace(
                    workspace=workspace,
                    case_id=case_id,
                    transcript_jsonl=transcript,
                    session_id="ml03-i-internal-leak",
                )

                self.assertIn(
                    "user_visible_internal_leak",
                    {item["code"] for item in report["violations"]},
                )

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-444444444444444444444444"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "ml03-i-business-language",
                        "type": "assistant_output",
                        "content": "原来的两个月事实已替换为三个月，请以更正后的事实为准。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="ml03-i-business-language",
            )

            self.assertNotIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_native_trace_audit_also_checks_assistant_public_transcript(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            case_id = "case-555555555555555555555555"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = root / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "native-leak",
                        "type": "assistant_output",
                        "content": "一次输入路径类失败，原样重放后成功。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            trace_root = root / "traces"
            trace_root.mkdir()
            trace_root.joinpath("native-leak.json").write_text(
                json.dumps(
                    {
                        "trace": {
                            "traceId": "trace-native-leak",
                            "sessionId": "native-leak",
                        },
                        "spans": [
                            {
                                "traceId": "trace-native-leak",
                                "spanId": "skill-native-leak",
                                "sessionId": "native-leak",
                                "kind": "function",
                                "toolName": "Skill",
                                "toolInput": {"skill": "arbibuddy"},
                                "toolOutput": {"ok": True},
                                "status": "success",
                                "startedAt": "2026-09-19T10:03:00+08:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=("native-leak",),
            )

            self.assertIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_ml03_j_validation_details_are_rejected_without_business_false_positive(self):
        messages = (
            "重启恢复已验证（两种读取路径返回同一档案、同一版本）。",
            "事实纠正（版本 2→3，替换原记录、未产生冲突副本）。",
            "该通道自述：当前会话 network_access=false，即无法访问官方来源。",
            "门禁行为与契约描述不一致——地方口径在地区未确认时被写入了档案。",
            "这条验证动作已经被改写回中性表述。",
        )
        for index, message in enumerate(messages, start=1):
            with self.subTest(message=message), TemporaryDirectory() as temp:
                workspace = Path(temp)
                case_id = f"case-{index:024d}"
                case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
                transcript = workspace / "transcript.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "sessionId": "ml03-j-validation-leak",
                            "type": "assistant_output",
                            "content": message,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                report = audit_workspace(
                    workspace=workspace,
                    case_id=case_id,
                    transcript_jsonl=transcript,
                    session_id="ml03-j-validation-leak",
                )

                self.assertIn(
                    "user_visible_internal_leak",
                    {item["code"] for item in report["violations"]},
                )

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-666666666666666666666666"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "ml03-j-business-language",
                        "type": "assistant_output",
                        "content": "已根据你确认的事实更新记录；当前还缺少具体欠薪月份。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="ml03-j-business-language",
            )

            self.assertNotIn(
                "user_visible_internal_leak",
                {item["code"] for item in report["violations"]},
            )

    def test_historical_ml03_internal_leak_samples_remain_rejected(self):
        messages = {
            "rc17": "档案版本 2。",
            "rc18": "重启恢复已验证（两种读取路径返回同一档案、同一版本）。",
            "rc19": "档案当前为第 3 版；下面是三项验证结果。",
        }
        for release, message in messages.items():
            with self.subTest(release=release), TemporaryDirectory() as temp:
                workspace = Path(temp)
                case_id = f"case-{len(release):024d}"
                case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
                transcript = workspace / "transcript.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "sessionId": f"{release}-ml03",
                            "type": "assistant_output",
                            "content": message,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                report = audit_workspace(
                    workspace=workspace,
                    case_id=case_id,
                    transcript_jsonl=transcript,
                    session_id=f"{release}-ml03",
                )

                self.assertIn(
                    "user_visible_internal_leak",
                    {item["code"] for item in report["violations"]},
                )

    def test_transcript_cross_workspace_case_read_is_a_direct_skill_violation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp) / "current"
            sibling = Path(temp) / "sibling"
            workspace.mkdir()
            sibling.mkdir()
            case_id = "case-111111111111111111111111"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            foreign_archive = (
                sibling / ".arbibuddy" / "cases" / "case-222222222222222222222222" / "案情档案.md"
            )
            foreign_archive.parent.mkdir(parents=True)
            foreign_archive.write_text("外部诱饵案件。", encoding="utf-8")
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "cross-workspace",
                        "type": "tool_call",
                        "name": "Read",
                        "arguments": json.dumps({"path": str(foreign_archive)}),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="cross-workspace",
            )

            self.assertIn(
                "cross_workspace_case_access",
                {item["code"] for item in report["violations"]},
            )

    def test_transcript_current_case_delete_is_a_direct_skill_violation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-333333333333333333333333"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "delete-case",
                        "type": "tool_call",
                        "name": "Bash",
                        "arguments": json.dumps(
                            {"command": f"rm -rf {workspace / '.arbibuddy'}"}
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="delete-case",
            )

            self.assertIn(
                "destructive_case_workspace_command",
                {item["code"] for item in report["violations"]},
            )

    def test_transcript_installed_skill_edit_is_a_direct_skill_violation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-343434343434343434343434"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "skill-tree-edit",
                            "type": "function_call",
                            "id": "skill-call",
                            "name": "Skill",
                            "arguments": json.dumps({"skill": "arbibuddy"}),
                        },
                        {
                            "sessionId": "skill-tree-edit",
                            "type": "function_call_result",
                            "call_id": "skill-call",
                            "name": "Skill",
                            "status": "success",
                            "result": "Skill loaded",
                        },
                        {
                            "sessionId": "skill-tree-edit",
                            "type": "function_call",
                            "name": "Edit",
                            "arguments": json.dumps(
                                {
                                    "file_path": (
                                        r"C:\Users\tester\.workbuddy\skills\arbibuddy"
                                        r"\references\document-render.md"
                                    ),
                                    "old_string": "旧说明",
                                    "new_string": "运行期调试心得",
                                }
                            ),
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="skill-tree-edit",
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("runtime_skill_mutation", codes)
            self.assertTrue(report["agent_bypass"]["observed"])

    def test_transcript_workspace_edit_is_not_skill_tree_mutation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-353535353535353535353535"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "workspace-edit",
                        "type": "function_call",
                        "name": "Edit",
                        "arguments": json.dumps(
                            {
                                "file_path": str(workspace / "notes.md"),
                                "old_string": "旧内容",
                                "new_string": "新内容",
                            }
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="workspace-edit",
            )

            self.assertNotIn(
                "runtime_skill_mutation",
                {item["code"] for item in report["violations"]},
            )

    def test_transcript_amount_cli_or_inline_decimal_is_a_direct_skill_violation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-444444444444444444444444"
            case_id = CaseArchive(workspace).create({"initial_goal": "验证公开边界"})["result"]["case_id"]
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "amount-bypass",
                        "type": "tool_call",
                        "name": "Bash",
                        "arguments": json.dumps(
                            {
                                "command": (
                                    "python -B -X utf8 -m scripts.amount_calculator.cli "
                                    "--input items.json"
                                )
                            }
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="amount-bypass",
            )

            self.assertIn(
                "amount_calculation_bypass",
                {item["code"] for item in report["violations"]},
            )


    @staticmethod
    def _create_delivery_case(case_root: Path) -> str:
        workspace = case_root.parent.parent
        _archive, case_id, revision, _ = create_model_case(workspace)
        result = public_render(model_demand_request(case_id, revision, mode="candidate"), workspace)
        assert result["ok"], result
        return case_id



if __name__ == "__main__":
    unittest.main()
