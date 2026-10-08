from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.case_archive import CaseArchive
from scripts.model_led_agent_eval import (
    CliAgentRuntime,
    DeliveryRequirement,
    JourneyError,
    JourneyResult,
    JourneyProgress,
    RuntimeAdapterError,
    _artifacts_satisfied,
    _default_platform_command,
    _observe_workspace,
    load_journey,
    run_journey,
    run_journey_with_retries,
)
from scripts.model_led_agent_eval import (
    _extract_managed_view_receipts,
    _managed_view_command_request,
    _managed_view_command_tokens,
    _normalize_managed_view_receipt,
    _observed_milestones,
    _parse_json_lines,
    _response_text,
    _summarize_runtime_events,
)
from scripts.model_led_agent_eval.cli import main as model_led_cli_main
from tests.agent_eval.public_delivery_fixture import (
    write_public_archive,
    write_public_contract_delivery,
)


ROOT = Path(__file__).resolve().parents[2]
_INSTALLED_ARCHIVE_CREATE = (
    "import subprocess\n"
    "skill_root = workspace / '.agents' / 'skills' / 'arbibuddy'\n"
    "if not skill_root.is_dir():\n"
    "    skill_root = workspace / '.claude' / 'skills' / 'arbibuddy'\n"
    "created_process = subprocess.run(\n"
    "    [sys.executable, '-B', '-X', 'utf8', '-m',\n"
    "     'scripts.case_archive.cli', '--root', str(workspace), 'create'],\n"
    "    cwd=skill_root, capture_output=True, text=True, encoding='utf-8',\n"
    "    check=False,\n"
    ")\n"
    "assert created_process.returncode == 0, created_process.stderr\n"
    "created = json.loads(created_process.stdout)\n"
    "assert created['ok'], created\n"
)


class ModelLedHarnessTests(unittest.TestCase):
    """Exercise observable Agent Eval and public-file contract seams."""

    def test_claude_stream_json_returns_terminal_result_once(self):
        events = [
            {
                "type": "assistant",
                "session_id": "raw-stream-session",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "档案已更新到最终 revision。",
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            },
            {
                "type": "assistant",
                "session_id": "raw-stream-session",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Probe complete."}],
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "raw-stream-session",
                "result": "Probe complete.",
            },
        ]
        stdout = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)

        response = _response_text(
            _parse_json_lines(stdout), stdout, platform="claude-code"
        )

        self.assertEqual(response, "Probe complete.")
        self.assertNotIn("档案已更新到最终 revision", response)
        self.assertEqual(response.count("Probe complete."), 1)

    def test_claude_stream_json_never_uses_progress_or_raw_stdout_as_final(self):
        events = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "进度文字，不是最终回复。"}],
                    "stop_reason": "tool_use",
                },
            }
        ]
        stdout = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)

        response = _response_text(
            _parse_json_lines(stdout), stdout, platform="claude-code"
        )

        self.assertEqual(response, "")

    def test_claude_archive_read_receipts_survive_restore_summary_and_bind_to_current_case(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            skill_root = workspace / ".claude" / "skills" / "arbibuddy"
            skill_root.mkdir(parents=True)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录欠薪事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "公司拖欠三个月工资。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            current = archive.read(case_id)["result"]
            executable = str(Path(sys.executable)).replace("\\", "/")
            command = (
                f'cd "{skill_root}" && "{executable}" -B -X utf8 -m '
                f'scripts.case_archive.cli --root "{workspace}" read-current'
            )
            failed_command = command.replace(f'"{executable}"', executable, 1)
            events = [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "read-failed",
                                "name": "Bash",
                                "input": {"command": failed_command},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "read-failed",
                                "is_error": True,
                                "content": "Exit code 127\ncommand not found",
                            }
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "read-succeeded",
                                "name": "Bash",
                                "input": {"command": command},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "read-succeeded",
                                "is_error": False,
                                "content": json.dumps(
                                    {
                                        "contract_version": "case-archive-v1",
                                        "operation": "read",
                                        "ok": True,
                                        "errors": [],
                                        "result": {
                                            "case_id": case_id,
                                            "revision": current["revision"],
                                            "canonical_location": current[
                                                "canonical_location"
                                            ],
                                        },
                                    }
                                ),
                            }
                        ],
                    },
                },
            ]

            summaries = _summarize_runtime_events(tuple(events), workspace=workspace)

            self.assertEqual(len(summaries), 2)
            self.assertFalse(summaries[0]["case_archive_read_succeeded"])
            self.assertEqual(summaries[0]["exit_code"], 127)
            self.assertTrue(summaries[1]["case_archive_read_succeeded"])
            self.assertEqual(summaries[1]["case_id"], case_id)
            self.assertEqual(summaries[1]["archive_revision"], current["revision"])
            self.assertNotIn("markdown", summaries[1])

            journey = load_journey(
                ROOT
                / "evals"
                / "model-led"
                / "codex-claude-interruption-correction-degradation.yaml"
            )
            transcript = [
                {
                    "session_restarted": True,
                    "assistant_response": (
                        "已从当前工作区的案情档案接续。公司拖欠三个月工资。"
                    ),
                    "runtime_event_summary": summaries,
                }
            ]
            self.assertIn(
                "archive_recovered_after_restart",
                _observed_milestones(journey, workspace, transcript),
            )

            stale_events = json.loads(json.dumps(events))
            stale_receipt = json.loads(
                stale_events[3]["message"]["content"][0]["content"]
            )
            stale_receipt["result"]["revision"] -= 1
            stale_events[3]["message"]["content"][0]["content"] = json.dumps(
                stale_receipt
            )
            stale_summaries = _summarize_runtime_events(
                tuple(stale_events[2:]), workspace=workspace
            )
            self.assertFalse(stale_summaries[0]["case_archive_read_succeeded"])
            stale_transcript = [{
                **transcript[0],
                "runtime_event_summary": stale_summaries,
            }]
            self.assertNotIn(
                "archive_recovered_after_restart",
                _observed_milestones(journey, workspace, stale_transcript),
            )

    def test_claude_stream_json_rejects_non_success_terminal_result(self):
        event = {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "未完成的部分文本。",
        }

        response = _response_text(
            [event], json.dumps(event, ensure_ascii=False), platform="claude-code"
        )

        self.assertEqual(response, "")

    def test_codex_response_extraction_is_unchanged(self):
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Codex final response."},
        }

        self.assertEqual(
            _response_text([event], json.dumps(event), platform="codex"),
            "Codex final response.",
        )

    def test_windows_default_platform_command_prefers_native_executable(self):
        executable = r"C:\codex\codex.exe"
        shim = r"C:\npm\codex.cmd"
        with (
            patch("scripts.model_led_agent_eval.os.name", "nt"),
            patch(
                "scripts.model_led_agent_eval.shutil.which",
                side_effect=lambda name: {
                    "codex.exe": executable,
                    "codex": shim,
                }.get(name),
            ),
        ):
            self.assertEqual(_default_platform_command("codex"), [executable])

    def test_ml01_overtime_fact_is_not_replayed_for_each_unknown_subdetail(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-wage-bonus-overtime.yaml"
        )
        overtime = next(item for item in journey.fact_pool if item.id == "overtime")

        self.assertEqual(overtime.max_uses, 1)
        self.assertIn("工时制度", overtime.text)

    def test_codex_managed_view_receipt_is_bound_to_successful_public_command(self):
        case_id = "case-0123456789abcdef01234567"
        delivery_set_id = "delivery-set-test-1"
        document_type = "forced_termination_notice"
        base = f".arbibuddy/cases/{case_id}/output/{document_type}"
        view = {
            "schema_version": 1,
            "case_id": case_id,
            "delivery_state": "candidate_ready",
            "stopped": False,
            "presentation_files": [
                {
                    "delivery_label": document_type,
                    "kind": "document",
                    "order": 1,
                    "path": f"{base}/candidate.docx",
                    "sha256": "a" * 64,
                    "state": "candidate_ready",
                },
                {
                    "delivery_label": document_type,
                    "kind": "checklist",
                    "order": 2,
                    "path": f"{base}/checklist.txt",
                    "sha256": "b" * 64,
                    "state": "candidate_ready",
                },
            ],
            "delivery_summaries": [
                {
                    "case_id": case_id,
                    "archive_revision": 4,
                    "document_type": document_type,
                    "mode": "candidate",
                    "delivery_state": "candidate_ready",
                    "validation_summary": {
                        "status": "passed",
                        "checks": ["DOCX 与核验清单通过校验"],
                    },
                }
            ],
            "delivery_set": {
                "contract_version": "document.delivery-set-v1",
                "delivery_set_id": delivery_set_id,
                "archive_revision": 4,
                "expected_document_types": [document_type],
                "completed_document_types": [document_type],
                "missing_document_types": [],
            },
        }
        request = json.dumps(
            {"case_id": case_id, "delivery_set_id": delivery_set_id}
        )
        command = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            "-m",
            "scripts.documents.runtime_cli",
            "--workspace",
            "WORKSPACE_PLACEHOLDER",
            "view",
            "--json",
            request,
        ]

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            command[7] = str(workspace)
            command_tokens = _managed_view_command_tokens(command)
            self.assertEqual(command_tokens, command)
            command_request = _managed_view_command_request(
                command_tokens, workspace
            )
            self.assertEqual(
                command_request,
                {"case_id": case_id, "delivery_set_id": delivery_set_id},
            )
            self.assertIsNotNone(
                _normalize_managed_view_receipt(view, request=command_request)
            )
            absolute_view = json.loads(json.dumps(view))
            for file_item in absolute_view["presentation_files"]:
                file_item["path"] = str(workspace / Path(file_item["path"]))
            normalized_absolute = _normalize_managed_view_receipt(
                absolute_view,
                request=command_request,
                workspace=workspace,
            )
            self.assertIsNotNone(normalized_absolute)
            self.assertEqual(
                normalized_absolute["deliveries"][0]["document_path"],
                f"{base}/candidate.docx",
            )
            self.assertEqual(
                normalized_absolute["deliveries"][0]["checklist_path"],
                f"{base}/checklist.txt",
            )
            self.assertNotIn(str(workspace), json.dumps(normalized_absolute))
            self.assertIsNone(
                _normalize_managed_view_receipt(
                    absolute_view,
                    request=command_request,
                    workspace=workspace / "different-workspace",
                )
            )
            event = {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "exit_code": 0,
                    "aggregated_output": json.dumps(view),
                },
            }

            receipts = _extract_managed_view_receipts(
                (event,), platform="codex", workspace=workspace
            )
            shell_command = (
                f'{sys.executable} -B -X utf8 -m scripts.documents.runtime_cli '
                f'--workspace "{workspace}" view --json \'{request}\''
            )
            shell_event = {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": shell_command,
                    "exit_code": 0,
                    "aggregated_output": json.dumps(view),
                },
            }
            shell_receipts = _extract_managed_view_receipts(
                (shell_event,), platform="codex", workspace=workspace
            )
            skill_root = workspace / ".agents" / "skills" / "arbibuddy"
            location_event = {
                **shell_event,
                "item": {
                    **shell_event["item"],
                    "command": f"Set-Location '{skill_root}' && & " + shell_command,
                },
            }
            self.assertEqual(
                _extract_managed_view_receipts(
                    (location_event,), platform="codex", workspace=workspace
                ),
                receipts,
            )
            location_summary = _summarize_runtime_events(
                (location_event,), workspace=workspace, installed_skill_root=skill_root
            )
            self.assertEqual(location_summary[0]["managed_view_receipt_status"], "passed")
            for unsafe_prefix in (
                f"Set-Location '{workspace / 'other'}' && & ",
                f"Set-Location '{skill_root}' ; & ",
                f"Set-Location '{skill_root}' && Write-Output forged && & ",
            ):
                unsafe_event = {
                    **location_event,
                    "item": {**location_event["item"], "command": unsafe_prefix + shell_command},
                }
                self.assertEqual(
                    _extract_managed_view_receipts(
                        (unsafe_event,), platform="codex", workspace=workspace
                    ),
                    [],
                )
            wrapped_shell_receipts = []
            wrapped_shell_summary = []
            if os.name == "nt":
                inner_command = (
                    f"& '{sys.executable}' -B -X utf8 -m scripts.documents.runtime_cli "
                    f"--workspace '{workspace}' view --json '{request}'"
                )
                wrapped_shell_command = subprocess.list2cmdline(
                    [
                        r"C:\Program Files\PowerShell\7\pwsh.exe",
                        "-NoProfile",
                        "-Command",
                        inner_command,
                    ]
                )
                wrapped_shell_event = {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": wrapped_shell_command,
                        "exit_code": 0,
                        "aggregated_output": json.dumps(view),
                    },
                }
                wrapped_shell_receipts = _extract_managed_view_receipts(
                    (wrapped_shell_event,),
                    platform="codex",
                    workspace=workspace,
                )
                wrapped_shell_summary = _summarize_runtime_events(
                    (wrapped_shell_event,), workspace=workspace
                )
            call_operator_event = {
                **shell_event,
                "item": {
                    **shell_event["item"],
                    "command": "& " + shell_command,
                },
            }
            call_operator_receipts = _extract_managed_view_receipts(
                (call_operator_event,), platform="codex", workspace=workspace
            )
            event_summary = _summarize_runtime_events(
                (event,), workspace=workspace
            )
            native_tool_summary = _summarize_runtime_events(
                (
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "mcp_tool_call",
                            "name": "present_files",
                            "arguments": {"path": "PRIVATE"},
                        },
                    },
                ),
                workspace=workspace,
            )
            unavailable_view = {
                "schema_version": 1,
                "case_id": case_id,
                "delivery_state": "unavailable",
                "stopped": True,
                "error_code": "invalid_model_delivery",
                "presentation_files": [],
            }
            unavailable_event = {
                **event,
                "item": {
                    **event["item"],
                    "aggregated_output": json.dumps(unavailable_view),
                },
            }
            unavailable_summary = _summarize_runtime_events(
                (unavailable_event,), workspace=workspace
            )
            wrong_workspace_command = list(command)
            wrong_workspace_command[7] = str(workspace / "other")
            wrong_workspace = {
                **event,
                "item": {**event["item"], "command": wrong_workspace_command},
            }
            failed_command = {
                **event,
                "item": {**event["item"], "exit_code": 1},
            }
            fake_message = {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(view)},
            }
            wrong_delivery_set_view = {
                **view,
                "delivery_set": {
                    **view["delivery_set"],
                    "delivery_set_id": "different-set",
                },
            }
            wrong_delivery_set_event = {
                **event,
                "item": {
                    **event["item"],
                    "aggregated_output": json.dumps(wrong_delivery_set_view),
                },
            }
            rejected = _extract_managed_view_receipts(
                (
                    wrong_workspace,
                    failed_command,
                    fake_message,
                    wrong_delivery_set_event,
                ),
                platform="codex",
                workspace=workspace,
            )
            claude_receipts = _extract_managed_view_receipts(
                (event,), platform="claude-code", workspace=workspace
            )

        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["case_id"], case_id)
        self.assertEqual(receipts[0]["archive_revision"], 4)
        self.assertEqual(
            receipts[0]["deliveries"][0]["document_type"], document_type
        )
        self.assertEqual(shell_receipts, receipts)
        if os.name == "nt":
            self.assertEqual(wrapped_shell_receipts, receipts)
            self.assertTrue(wrapped_shell_summary[0]["strict_command_match"])
            self.assertTrue(wrapped_shell_summary[0]["view_token_seen"])
            self.assertEqual(
                wrapped_shell_summary[0]["managed_view_receipt_status"],
                "passed",
            )
        self.assertEqual(call_operator_receipts, receipts)
        self.assertTrue(event_summary[0]["strict_command_match"])
        self.assertEqual(
            event_summary[0]["managed_view_receipt_status"], "passed"
        )
        self.assertEqual(event_summary[0]["presentation_file_count"], 2)
        self.assertEqual(
            native_tool_summary,
            [
                {
                    "event_type": "item.completed",
                    "item_type": "mcp_tool_call",
                    "item_name": "present_files",
                }
            ],
        )
        self.assertNotIn(str(workspace), json.dumps(event_summary))
        self.assertEqual(
            unavailable_summary[0]["managed_view_receipt_status"], "rejected"
        )
        self.assertEqual(
            unavailable_summary[0]["managed_view_error_code"],
            "invalid_model_delivery",
        )
        self.assertEqual(rejected, [])
        self.assertEqual(claude_receipts, [])

    @unittest.skipUnless(os.name == "nt", "Windows Codex shell command wrapper")
    def test_codex_powershell_wrapper_binds_managed_view_command(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            request = json.dumps(
                {
                    "case_id": "case-0123456789abcdef01234567",
                    "delivery_set_id": "delivery-set-test-1",
                }
            )
            inner_command = (
                f"& '{sys.executable}' -B -X utf8 -m scripts.documents.runtime_cli "
                f"--workspace '{workspace}' view --json '{request}'"
            )
            wrapped_command = subprocess.list2cmdline(
                [
                    r"C:\Program Files\PowerShell\7\pwsh.exe",
                    "-NoProfile",
                    "-Command",
                    inner_command,
                ]
            )
            expected = [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-m",
                "scripts.documents.runtime_cli",
                "--workspace",
                str(workspace),
                "view",
                "--json",
                request,
            ]

            self.assertEqual(_managed_view_command_tokens(wrapped_command), expected)
            unsafe_wrapper = subprocess.list2cmdline(
                [
                    r"C:\Program Files\PowerShell\7\pwsh.exe",
                    "-NoProfile",
                    "-Command",
                    inner_command + "; Get-ChildItem",
                ]
            )
            self.assertIsNone(_managed_view_command_tokens(unsafe_wrapper))

    def test_render_response_is_not_classified_as_managed_view(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id = "case-0123456789abcdef01234567"
            render_response = {
                "schema_version": 1,
                "case_id": case_id,
                "presentation_files": [{}, {}],
            }
            event = {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        "-m",
                        "scripts.documents.runtime_cli",
                        "--workspace",
                        str(workspace),
                        "render",
                        "--json",
                        "{}",
                    ],
                    "exit_code": 0,
                    "aggregated_output": json.dumps(render_response),
                },
            }

            summary = _summarize_runtime_events((event,), workspace=workspace)

        self.assertEqual(summary[0]["managed_view_response_count"], 0)
        self.assertEqual(
            summary[0]["managed_view_receipt_status"], "not_a_view_response"
        )

    def test_managed_view_receipt_cannot_replace_final_public_file_inspection(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id, artifacts = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            manifest = json.loads(
                artifacts["manifest"].read_text(encoding="utf-8")
            )
            receipt = {
                "schema_version": 1,
                "source": "codex_cli_managed_view",
                "case_id": case_id,
                "archive_revision": 4,
                "delivery_set_id": "delivery-set-test-1",
                "expected_document_types": ["forced_termination_notice"],
                "completed_document_types": ["forced_termination_notice"],
                "missing_document_types": [],
                "deliveries": [
                    {
                        "case_id": case_id,
                        "document_type": "forced_termination_notice",
                        "mode": "candidate",
                        "archive_revision": 4,
                        "current_archive_revision": 4,
                        "case_archive_path": (
                            f".arbibuddy/cases/{case_id}/案情档案.md"
                        ),
                        "document_path": artifacts["document"]
                        .relative_to(workspace)
                        .as_posix(),
                        "checklist_path": artifacts["checklist"]
                        .relative_to(workspace)
                        .as_posix(),
                        "document_sha256": manifest["document"]["sha256"],
                        "checklist_sha256": manifest["verification_checklist"][
                            "sha256"
                        ],
                        "validation_status": "passed",
                    }
                ],
            }
            observation = _observe_workspace(workspace)
            observation["docx"] = False
            observation["checklist"] = False
            observation["deliveries"] = []
            observation["invalid_delivery_count"] = 1
            observation["permission_denied_delivery_count"] = 1
            observation["managed_view_receipts"] = [receipt]
            requirement = DeliveryRequirement(
                "forced_termination_notice", "candidate", True, True, True, True
            )

            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(requirement,),
                )
            )

            malformed_plus_permission = dict(observation)
            malformed_plus_permission["invalid_delivery_count"] = 2
            self.assertFalse(
                _artifacts_satisfied(
                    malformed_plus_permission,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(requirement,),
                )
            )

            stale_receipt = {**receipt, "archive_revision": 3}
            stale_observation = {
                **observation,
                "managed_view_receipts": [stale_receipt],
            }
            self.assertFalse(
                _artifacts_satisfied(
                    stale_observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(requirement,),
                )
            )

    def test_current_document_and_checklist_hashes_are_rechecked_after_view(self):
        requirement = DeliveryRequirement(
            "forced_termination_notice", "candidate", True, True, True, True
        )
        for artifact_name in ("document", "checklist"):
            with self.subTest(artifact=artifact_name), TemporaryDirectory() as temp:
                workspace = Path(temp)
                _, artifacts = write_public_contract_delivery(
                    workspace,
                    document_type="forced_termination_notice",
                    mode="candidate",
                    archive_revision=4,
                )
                original = artifacts[artifact_name].read_bytes()
                artifacts[artifact_name].write_bytes(original + b"tampered")
                observation = _observe_workspace(workspace)
                observation["managed_view_receipts"] = [
                    {"source": "codex_cli_managed_view", "validation_status": "passed"}
                ]

                self.assertGreater(observation["invalid_delivery_count"], 0)
                self.assertFalse(
                    _artifacts_satisfied(
                        observation,
                        ("case_archive", "docx", "checklist"),
                        delivery_requirements=(requirement,),
                    )
                )

    def test_windows_runtime_uses_userprofile_when_home_is_empty(self):
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                returncode=0,
                stdout='{"thread_id":"thread-1","text":"已收到"}\n',
                stderr="",
            )

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            runtime = CliAgentRuntime(platform="codex", command=[sys.executable])
            with (
                patch("scripts.platform_adapters.service.os.name", "nt"),
                patch.dict(
                    os.environ,
                    {"HOME": "", "USERPROFILE": r"C:\Users\runtime-test"},
                    clear=False,
                ),
                patch(
                    "scripts.model_led_agent_eval.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                response = runtime.turn(
                    workspace=workspace,
                    message="请帮我梳理劳动争议。",
                )

        self.assertEqual(response.session_id, "thread-1")
        self.assertEqual(captured["cwd"], workspace)
        self.assertEqual(captured["env"]["HOME"], r"C:\Users\runtime-test")
        self.assertNotIn(str(workspace), captured["env"]["HOME"])

    def test_windows_runtime_preserves_explicit_home(self):
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.test\n",
                stderr="",
            )

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            runtime = CliAgentRuntime(platform="codex", command=[sys.executable])
            with (
                patch("scripts.platform_adapters.service.os.name", "nt"),
                patch.dict(
                    os.environ,
                    {
                        "HOME": r"C:\Users\explicit-home",
                        "USERPROFILE": r"C:\Users\runtime-test",
                    },
                    clear=False,
                ),
                patch(
                    "scripts.model_led_agent_eval.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                version = runtime.probe_version(workspace=workspace)

        self.assertEqual(version, "codex-cli 0.test")
        self.assertEqual(captured["cwd"], workspace)
        self.assertEqual(captured["env"]["HOME"], r"C:\Users\explicit-home")

    def test_codex_resume_reasserts_approval_and_workspace_before_subcommand(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            runtime = CliAgentRuntime(platform="codex", command=[sys.executable])
            runtime.session_id = "resume-session"

            command = runtime._resume_command(workspace, "继续整理已知事实。")

        resume_index = command.index("resume")
        self.assertEqual(command[1], "exec")
        self.assertIn("--approve-for-me", command[1:resume_index])
        self.assertLess(command.index("-C"), resume_index)
        self.assertEqual(command[command.index("-C") + 1], str(workspace.resolve()))
        self.assertEqual(command[resume_index + 1], "resume-session")

    def test_missing_runtime_workspace_fails_before_starting_platform_cli(self):
        with TemporaryDirectory() as temp:
            missing_workspace = Path(temp) / "removed-workspace"
            runtime = CliAgentRuntime(platform="codex", command=[sys.executable])
            with patch("scripts.model_led_agent_eval.subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeAdapterError, "cwd 不存在") as caught:
                    runtime.turn(
                        workspace=missing_workspace,
                        message="继续整理已知事实。",
                    )

        run.assert_not_called()
        state = caught.exception.details["working_directory"]
        self.assertFalse(state["exists"])
        self.assertFalse(state["is_directory"])
        self.assertEqual(state["role"], "journey_agent_workspace")
        self.assertNotIn(str(missing_workspace), json.dumps(state))

    def test_timeout_diagnostic_records_workspace_lifecycle(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            runtime = CliAgentRuntime(
                platform="codex", command=[sys.executable], timeout_seconds=1
            )
            runtime.session_id = "resume-session"
            partial_output = "\n".join(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for event in (
                    {
                        "type": "item.started",
                        "timestamp": "2026-09-24T04:00:00.000Z",
                        "item": {
                            "id": "tool-1",
                            "type": "command_execution",
                        },
                    },
                    {
                        "type": "item.completed",
                        "timestamp": "2026-09-24T04:00:01.000Z",
                        "item": {
                            "id": "tool-1",
                            "type": "command_execution",
                            "exit_code": 0,
                        },
                    },
                    {
                        "type": "item.started",
                        "timestamp": "2026-09-24T04:00:02.000Z",
                        "item": {"id": "tool-2", "type": "web_search"},
                    },
                )
            )

            def remove_workspace_then_timeout(command, **kwargs):
                self.assertEqual(kwargs["cwd"], workspace.resolve())
                workspace.rmdir()
                raise subprocess.TimeoutExpired(
                    command,
                    1,
                    output=partial_output.encode("utf-8"),
                    stderr=b"CreateProcessAsUserW failed: 267",
                )

            with patch(
                "scripts.model_led_agent_eval.subprocess.run",
                side_effect=remove_workspace_then_timeout,
            ):
                with self.assertRaisesRegex(RuntimeAdapterError, "轮次超时") as caught:
                    runtime.turn(workspace=workspace, message="继续整理已知事实。")

        cwd = caught.exception.details["working_directory"]
        self.assertTrue(cwd["before_call"]["exists"])
        self.assertTrue(cwd["before_call"]["is_directory"])
        self.assertFalse(cwd["at_timeout"]["exists"])
        self.assertFalse(cwd["at_timeout"]["is_directory"])
        self.assertEqual(len(cwd["before_call"]["path_sha256"]), 64)
        self.assertNotIn(str(workspace), json.dumps(caught.exception.details))
        activity = caught.exception.details["runtime_activity"]
        self.assertEqual(activity["completed_tool_call_count"], 1)
        self.assertEqual(activity["pending_tool_call_count"], 1)
        self.assertEqual(activity["last_activity_state"], "tool_in_flight_at_timeout")
        self.assertEqual(
            activity["last_event_timestamp_utc"],
            "2026-09-24T04:00:02.000+00:00",
        )
        self.assertTrue(activity["timeout_observed_at_utc"])

    def test_journey_schema_requires_natural_messages_and_artifact_expectations(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "journey.yaml"
            path.write_text(
                "schema_version: 2\n"
                "id: test-journey\n"
                "title: 测试主路径\n"
                "initial_user_message: 我想梳理拖欠工资并准备仲裁材料。\n"
                "fact_pool:\n"
                "  - id: employment\n"
                "    message: 我补充劳动关系和入职情况。\n"
                "    legacy:\n"
                "      when_any: [入职, 劳动关系]\n"
                "milestones:\n"
                "  - id: archive_written\n"
                "    response_any: [档案]\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx, checklist]\n"
                "  max_turns: 4\n",
                encoding="utf-8",
            )

            journey = load_journey(path)

            self.assertEqual(journey.id, "test-journey")
            self.assertEqual(journey.min_turns, 1)
            self.assertNotIn("$arbibuddy", journey.initial_user_message)
            self.assertNotIn("agent_task", journey.initial_user_message)
            self.assertEqual(len(journey.fact_pool), 1)
            self.assertEqual(journey.fact_pool[0].id, "employment")
            self.assertEqual(journey.milestones[0].id, "archive_written")
            self.assertEqual(
                journey.required_artifacts,
                ("case_archive", "docx", "checklist"),
            )

    def test_journey_schema_declares_typed_delivery_expectations(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "journey.yaml"
            path.write_text(
                "schema_version: 2\n"
                "id: typed-delivery\n"
                "title: 按公开契约核对交付\n"
                "initial_user_message: 我想准备一份文书并核对交付。\n"
                "fact_pool: []\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx, checklist]\n"
                "  required_deliveries:\n"
                "    - document_type: arbitration_application\n"
                "      mode: candidate\n"
                "      require_case_archive: true\n"
                "      require_checklist: true\n"
                "      require_same_case_id: true\n"
                "      require_current_archive_revision: true\n"
                "  allow_extra_deliveries: false\n"
                "  max_turns: 1\n",
                encoding="utf-8",
            )

            journey = load_journey(path)

            self.assertEqual(len(journey.required_deliveries), 1)
            delivery = journey.required_deliveries[0]
            self.assertEqual(delivery.document_type, "arbitration_application")
            self.assertEqual(delivery.mode, "candidate")
            self.assertTrue(delivery.require_case_archive)
            self.assertTrue(delivery.require_checklist)
            self.assertTrue(delivery.require_same_case_id)
            self.assertTrue(delivery.require_current_archive_revision)
            self.assertFalse(journey.allow_extra_deliveries)

    def test_journey_schema_rejects_fixed_follow_up_messages(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "journey.yaml"
            path.write_text(
                "schema_version: 1\n"
                "id: legacy-journey\n"
                "title: 不应继续播放固定消息\n"
                "initial_user_message: 我想梳理劳动争议。\n"
                "follow_up_messages:\n"
                "  - 无关的固定补充\n"
                "expect:\n"
                "  required_artifacts: [case_archive]\n"
                "  max_turns: 2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(JourneyError, "follow_up_messages"):
                load_journey(path)

    def test_codex_and_claude_runs_are_isolated_and_observe_only_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_cli = root / "fake-agent.py"
            fake_cli.write_text(
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if '--version' in args:\n"
                "    print('fake-agent 0.test')\n"
                "    raise SystemExit(0)\n"
                "workspace = pathlib.Path.cwd()\n"
                + _INSTALLED_ARCHIVE_CREATE
                + "text = '已建立案情档案，请补充入职时间和劳动合同情况。'\n"
                "if 'exec' in args:\n"
                "    events = [{'type': 'thread.started', 'thread_id': 'fake-codex'}]\n"
                "    events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}})\n"
                "else:\n"
                "    events = [{'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'fake-claude', 'result': text}]\n"
                "for event in events:\n"
                "    print(json.dumps(event, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: fake-journey\n"
                "title: 假运行时主路径\n"
                "initial_user_message: 我想梳理拖欠工资并准备材料。\n"
                "fact_pool:\n"
                "  - id: employment\n"
                "    message: 我补充劳动合同、工资流水和考勤证据。\n"
                "    legacy:\n"
                "      when_any: [入职, 劳动合同]\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive]\n"
                "  max_turns: 3\n",
                encoding="utf-8",
            )
            journey = load_journey(journey_path)

            for platform in ("codex", "claude-code"):
                progress: list[JourneyProgress] = []
                result = run_journey(
                    journey,
                    platform=platform,
                    source_root=ROOT,
                    temp_root=root / "runs" / platform,
                    platform_command=[sys.executable, str(fake_cli)],
                    on_progress=progress.append,
                )

                with self.subTest(platform=platform):
                    self.assertTrue(result.passed, result.failure_message)
                    self.assertEqual(result.failure_category, None)
                    self.assertGreaterEqual(len(progress), 1)
                    self.assertTrue(result.observation["case_archive"])
                    self.assertFalse(result.observation["docx"])
                    self.assertFalse(result.observation["checklist"])
                    self.assertTrue(result.evidence_path.is_file())
                    conversation = result.evidence_path.with_name("conversation.md")
                    self.assertTrue(conversation.is_file())
                    self.assertIn("### 第 1 轮", conversation.read_text(encoding="utf-8"))
                    evidence = json.loads(
                        result.evidence_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(evidence["platform"], platform)
                    self.assertTrue(evidence["isolation"]["separate_workspace"])
                    self.assertEqual(evidence["attribution"]["category"], "pass")
                    evidence_text = result.evidence_path.read_text(encoding="utf-8")
                    self.assertNotIn("$arbibuddy", evidence_text)
                    self.assertNotIn("agent_task", evidence_text)
                    self.assertFalse(evidence["discovery"]["runtime_discovered"])

    def test_harness_does_not_count_machine_manifest_as_user_checklist(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _case_id, artifacts = write_public_contract_delivery(workspace)
            artifacts["checklist"].unlink()

            observation = _observe_workspace(workspace)

            self.assertTrue(observation["case_archive"])
            self.assertFalse(observation["docx"])
            self.assertFalse(observation["checklist"])
            self.assertFalse(
                _artifacts_satisfied(
                    observation, ("case_archive", "docx", "checklist")
                )
            )

    def test_harness_rejects_well_named_but_unreadable_ooxml(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _case_id, artifacts = write_public_contract_delivery(workspace)
            with ZipFile(artifacts["document"]) as package:
                parts = {name: package.read(name) for name in package.namelist()}
            parts["word/document.xml"] = b"not XML"
            with ZipFile(
                artifacts["document"], "w", compression=ZIP_DEFLATED
            ) as package:
                for name, data in parts.items():
                    package.writestr(name, data)
            manifest = json.loads(artifacts["manifest"].read_text(encoding="utf-8"))
            manifest["document"]["sha256"] = hashlib.sha256(
                artifacts["document"].read_bytes()
            ).hexdigest()
            artifacts["manifest"].write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )

            observation = _observe_workspace(workspace)

            self.assertTrue(observation["case_archive"])
            self.assertFalse(observation["docx"])
            self.assertFalse(observation["checklist"])
            self.assertEqual(observation["invalid_docx_count"], 1)

    def test_harness_rejects_checklist_with_only_external_warning(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _case_id, artifacts = write_public_contract_delivery(workspace)
            artifacts["checklist"].write_text(
                "本清单仅供核对，请勿外发。\n", encoding="utf-8"
            )
            manifest = json.loads(artifacts["manifest"].read_text(encoding="utf-8"))
            manifest["verification_checklist"]["sha256"] = hashlib.sha256(
                artifacts["checklist"].read_bytes()
            ).hexdigest()
            artifacts["manifest"].write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )

            observation = _observe_workspace(workspace)

            self.assertTrue(observation["case_archive"])
            self.assertFalse(observation["docx"])
            self.assertFalse(observation["checklist"])

    def test_harness_rejects_keyword_pile_checklist_independently(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _case_id, artifacts = write_public_contract_delivery(workspace)
            checklist_bytes = (
                "《劳动人事争议仲裁申请书》提交前核验清单\n\n"
                "本清单仅供核对，请勿外发。\n\n"
                "## 一、文书状态\n- 外发定稿，提交前人工核对。\n\n"
                "## 二、请核对的内容\n"
                "- 核对主体、身份、请求、事实、金额、日期、证据、落款和材料。\n"
                "- 提交前请在 Word/WPS 中预览。\n"
                "- 本清单不得与正式文书一并提交或发送。\n"
            ).encode("utf-8")
            artifacts["checklist"].write_bytes(checklist_bytes)
            manifest = json.loads(artifacts["manifest"].read_text(encoding="utf-8"))
            manifest["verification_checklist"]["sha256"] = hashlib.sha256(
                checklist_bytes
            ).hexdigest()
            artifacts["manifest"].write_bytes(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
                .encode("utf-8")
            )

            observation = _observe_workspace(workspace)

            self.assertEqual(observation["invalid_delivery_count"], 1)
            self.assertFalse(observation["docx"])
            self.assertFalse(observation["checklist"])

    def test_invalid_managed_docx_vetoes_another_valid_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(workspace, case_id="case-0123456789abcdef01234567")
            invalid_output = (
                workspace
                / ".arbibuddy"
                / "cases"
                / "case-1123456789abcdef01234567"
                / "output"
                / "arbitration_application"
            )
            invalid_output.mkdir(parents=True)
            (invalid_output / "invalid.docx").write_bytes(b"not a DOCX package")
            write_public_archive(
                workspace, "case-1123456789abcdef01234567", revision=1
            )

            observation = _observe_workspace(workspace)

            self.assertTrue(observation["docx"])
            self.assertTrue(observation["checklist"])
            self.assertEqual(observation["invalid_docx_count"], 1)
            self.assertFalse(
                _artifacts_satisfied(
                    observation, ("case_archive", "docx", "checklist")
                )
            )

    def test_harness_binds_delivery_to_journey_document_type_and_mode(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(workspace)
            observation = _observe_workspace(workspace)
            required_artifacts = ("case_archive", "docx", "checklist")

            wrong_type = DeliveryRequirement(
                "arbitration_defense", "external_final", True, True, True, True
            )
            wrong_mode = DeliveryRequirement(
                "arbitration_application", "candidate", True, True, True, True
            )

            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    required_artifacts,
                    delivery_requirements=(wrong_type,),
                )
            )
            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    required_artifacts,
                    delivery_requirements=(wrong_mode,),
                )
            )

    def test_harness_rejects_archive_and_delivery_from_different_cases(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_archive(workspace, "case-0123456789abcdef01234567", 4)
            write_public_contract_delivery(
                workspace, case_id="case-1123456789abcdef01234567"
            )
            (
                workspace
                / ".arbibuddy"
                / "cases"
                / "case-1123456789abcdef01234567"
                / "案情档案.md"
            ).unlink()
            observation = _observe_workspace(workspace)
            required = DeliveryRequirement(
                "arbitration_application", "external_final", True, True, True, True
            )

            self.assertEqual(
                [item["case_id"] for item in observation["case_archives"]],
                ["case-0123456789abcdef01234567"],
            )
            self.assertEqual(observation["deliveries"], [])
            self.assertEqual(observation["invalid_delivery_count"], 1)
            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(required,),
                )
            )

    def test_harness_rejects_manifest_revision_different_from_current_archive(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(
                workspace, archive_revision=3, manifest_revision=2
            )
            observation = _observe_workspace(workspace)
            required = DeliveryRequirement(
                "arbitration_application", "external_final", True, True, True, True
            )

            self.assertTrue(observation["case_archive"])
            self.assertEqual(observation["deliveries"], [])
            self.assertEqual(observation["invalid_delivery_count"], 1)
            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(required,),
                )
            )

    def test_harness_ignores_unmanaged_user_evidence_docx(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(workspace)
            evidence = workspace / "evidence" / "user-supplied.docx"
            evidence.parent.mkdir()
            evidence.write_bytes(b"not a managed delivery")
            observation = _observe_workspace(workspace)
            required = DeliveryRequirement(
                "arbitration_application", "external_final", True, True, True, True
            )

            self.assertEqual(observation["invalid_docx_count"], 0)
            self.assertTrue(
                _artifacts_satisfied(
                    observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(required,),
                )
            )

    def test_harness_rejects_extra_valid_managed_delivery_by_default(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(workspace)
            write_public_contract_delivery(
                workspace, document_type="arbitration_defense"
            )
            observation = _observe_workspace(workspace)
            required = DeliveryRequirement(
                "arbitration_application", "external_final", True, True, True, True
            )

            self.assertEqual(len(observation["deliveries"]), 2)
            self.assertFalse(
                _artifacts_satisfied(
                    observation,
                    ("case_archive", "docx", "checklist"),
                    delivery_requirements=(required,),
                )
            )

    def test_harness_oracle_does_not_import_production_business_validators(self):
        source = (
            ROOT / "scripts" / "model_led_agent_eval" / "__init__.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "scripts.case_archive",
            "scripts.case_archive.service",
            "scripts.documents.receipts",
            "scripts.documents.public",
            "scripts.documents.ooxml_check",
        }
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)

        self.assertEqual(
            sorted(set(imported) & forbidden),
            [],
            "Harness artifact observation must be an independent public-contract oracle",
        )
        oracle_source = (
            ROOT / "scripts" / "model_led_agent_eval" / "artifact_oracle.py"
        ).read_text(encoding="utf-8")
        oracle_tree = ast.parse(oracle_source)
        oracle_imports = []
        for node in ast.walk(oracle_tree):
            if isinstance(node, ast.Import):
                oracle_imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                oracle_imports.append(node.module.split(".", 1)[0])
        self.assertTrue(
            set(oracle_imports) <= sys.stdlib_module_names,
            f"artifact oracle imports non-standard modules: {sorted(set(oracle_imports) - sys.stdlib_module_names)}",
        )

    def test_shared_suite_contains_three_natural_journeys_for_both_platforms(self):
        suite = (ROOT / "evals" / "suites" / "model-led-codex-claude.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(suite.count("evals/model-led/"), 3)
        journey_paths = sorted((ROOT / "evals" / "model-led").glob("*.yaml"))
        self.assertEqual(len(journey_paths), 3)
        expected_deliveries = {
            "ML01-wage-bonus-overtime": (
                ("arbitration_application", "candidate"),
                ("evidence_catalog", "candidate"),
            ),
            "ML02-termination-high-risk": (
                ("forced_termination_notice", "candidate"),
            ),
            "ML03-interruption-correction-degradation": (),
        }
        for path in journey_paths:
            journey = load_journey(path)
            with self.subTest(journey=journey.id):
                self.assertGreaterEqual(len(journey.initial_user_message), 20)
                self.assertTrue(journey.fact_pool)
                self.assertTrue(journey.required_artifacts)
                self.assertEqual(
                    tuple(
                        (item.document_type, item.mode)
                        for item in journey.required_deliveries
                    ),
                    expected_deliveries[journey.id],
                )
                self.assertFalse(journey.allow_extra_deliveries)
                for message in (
                    journey.initial_user_message,
                    *(item.text for item in journey.fact_pool),
                ):
                    self.assertNotIn("$arbibuddy", message)
                    self.assertNotIn("agent_task", message)

    def test_suite_cli_normalizes_yaml_journey_paths_before_dispatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            suite_path = root / "suite.yaml"
            suite_path.write_text(
                "schema_version: 1\n"
                "journeys:\n"
                "  - evals/model-led/codex-claude-wage-bonus-overtime.yaml\n",
                encoding="utf-8",
            )
            evidence_path = root / "evidence.json"
            fake_result = SimpleNamespace(
                journey_id="ML01-wage-bonus-overtime",
                platform="codex",
                passed=True,
                failure_category=None,
                failure_message="",
                turns=1,
                duration_seconds=0.0,
                observation={},
                evidence_path=evidence_path,
            )

            with patch(
                "scripts.model_led_agent_eval.cli.run_journey_with_retries",
                return_value=fake_result,
            ) as mock_run_journey_with_retries:
                code = model_led_cli_main(
                    [
                        "suite",
                        str(suite_path),
                        "--platform",
                        "codex",
                        "--source",
                        str(ROOT),
                        "--temp-root",
                        str(root / "runs"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            mock_run_journey_with_retries.assert_called_once()
            self.assertEqual(
                mock_run_journey_with_retries.call_args.args[0].id,
                "ML01-wage-bonus-overtime",
            )
            self.assertEqual(
                mock_run_journey_with_retries.call_args.kwargs["timeout_per_turn_seconds"],
                1200,
            )

    def test_allowed_degraded_delivery_requires_marker_and_case_archive(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_cli = root / "degraded-agent.py"
            fake_cli.write_text(
                "import json, pathlib, sys\n"
                "workspace = pathlib.Path.cwd()\n"
                + _INSTALLED_ARCHIVE_CREATE
                + "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'degraded', 'result': '当前能力无法生成 DOCX，已降级交付案情档案。'}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: degraded-journey\n"
                "title: 允许降级的公开交付\n"
                "initial_user_message: 我想梳理劳动争议并保存案情档案。\n"
                "fact_pool: []\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx, checklist]\n"
                "  max_turns: 1\n"
                "  degradation_markers: [无法, 降级]\n"
                "  allow_degraded_delivery: true\n",
                encoding="utf-8",
            )

            with patch(
                "scripts.model_led_agent_eval.probe_capabilities",
                return_value={
                    "capabilities": {"complete_document_pipeline_available": False},
                    "document_capabilities": {},
                    "complete_document_pipeline_available": False,
                    "platform_version_probe": {"available": True},
                    "attachment_presentation": {"available": True},
                    "tested_at": "fixture",
                },
            ):
                result = run_journey(
                    load_journey(journey_path),
                    platform="claude-code",
                    source_root=ROOT,
                    temp_root=root / "run",
                    platform_command=[sys.executable, str(fake_cli)],
                )

            self.assertTrue(result.passed, result.failure_message)
            self.assertTrue(result.observation["delivery_degraded"])
            self.assertTrue(result.observation["case_archive"])
            self.assertFalse(result.observation["docx"])
            self.assertFalse(result.observation["checklist"])

    def test_explicit_legacy_rule_does_not_send_unrelated_fact(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_cli = root / "unmatched-agent.py"
            fake_cli.write_text(
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if '--version' in args:\n"
                "    print('fake-agent 0.test')\n"
                "    raise SystemExit(0)\n"
                "workspace = pathlib.Path.cwd()\n"
                + _INSTALLED_ARCHIVE_CREATE
                + "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'unmatched', 'result': '这是一个不匹配事实池的问题。'}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: unmatched-journey\n"
                "title: 不匹配时停止\n"
                "initial_user_message: 我想梳理劳动争议并保存案情档案。\n"
                "fact_pool:\n"
                "  - id: employment\n"
                "    message: 我补充入职时间和劳动合同情况。\n"
                "    legacy:\n"
                "      when_any: [入职, 劳动合同]\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx]\n"
                "  max_turns: 2\n",
                encoding="utf-8",
            )

            result = run_journey(
                load_journey(journey_path),
                platform="claude-code",
                source_root=ROOT,
                temp_root=root / "run",
                platform_command=[sys.executable, str(fake_cli)],
                responder_mode="rule",
                keep_workspace_on_failure=True,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.failure_category, "Scenario")
            self.assertIn("公开文件产物", result.failure_message)
            evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(len(evidence["transcript"]), 2)
            self.assertIsNone(evidence["transcript"][0]["fact_id"])
            self.assertEqual(evidence["transcript"][0]["selection"], "scenario-initial-message")
            self.assertEqual(evidence["transcript"][1]["selected_fact_ids"], [])
            self.assertEqual(evidence["transcript"][1]["selection"], "scenario-responder")
            self.assertEqual(evidence["scenario_responder"][0]["action"], "unavailable")
            self.assertNotIn("入职时间和劳动合同情况", evidence["transcript"][1]["user_message"])

    def test_missing_public_artifact_is_scenario_failure_without_skill_attribution(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_cli = root / "artifact-missing-agent.py"
            fake_cli.write_text(
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if '--version' in args:\n"
                "    print('fake-agent 0.test')\n"
                "    raise SystemExit(0)\n"
                "workspace = pathlib.Path.cwd()\n"
                + _INSTALLED_ARCHIVE_CREATE
                + "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'artifact-missing', 'result': '已保存案情档案。'}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: artifact-missing-journey\n"
                "title: 产物缺失归因\n"
                "initial_user_message: 我想梳理劳动争议并保存材料。\n"
                "fact_pool: []\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx]\n"
                "  max_turns: 1\n",
                encoding="utf-8",
            )

            result = run_journey(
                load_journey(journey_path),
                platform="claude-code",
                source_root=ROOT,
                temp_root=root / "run",
                platform_command=[sys.executable, str(fake_cli)],
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.failure_category, "Scenario")
            self.assertIn("公开文件产物", result.failure_message)

    def test_responder_stop_attribution_uses_stop_reason_not_oracle_code(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_cli = root / "stopped-agent.py"
            fake_cli.write_text(
                "import json, pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('fake-agent 0.test')\n"
                "    raise SystemExit(0)\n"
                "workspace = pathlib.Path.cwd()\n"
                + _INSTALLED_ARCHIVE_CREATE
                + "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'stopped', "
                "'result': '已保存案情档案。下一步请补充材料。'}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: responder-stop-journey\n"
                "title: 事实池不足时的归因\n"
                "initial_user_message: 请建立案情档案。\n"
                "fact_pool: []\n"
                "milestones: []\n"
                "simulator:\n"
                "  max_unavailable_uses: 1\n"
                "expect:\n"
                "  required_artifacts: [case_archive, docx]\n"
                "  max_turns: 3\n",
                encoding="utf-8",
            )

            result = run_journey(
                load_journey(journey_path),
                platform="claude-code",
                source_root=ROOT,
                temp_root=root / "run",
                platform_command=[sys.executable, str(fake_cli)],
                responder_mode="rule",
                keep_workspace_on_failure=True,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.failure_category, "Scenario", result.failure_message)
            evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["scenario_responder"][-1]["action"], "stop")
            self.assertEqual(
                evidence["attribution"]["code"], "scenario_responder_stop"
            )
            self.assertNotEqual(
                evidence["attribution"]["code"], evidence["oracle"]["failure_code"]
            )

    def test_retry_summary_marks_a_later_pass_as_non_defect(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            journey_path = root / "journey.yaml"
            journey_path.write_text(
                "schema_version: 2\n"
                "id: retry-journey\n"
                "title: 重跑归因\n"
                "initial_user_message: 我想梳理劳动争议并保存案情档案。\n"
                "fact_pool: []\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: [case_archive]\n"
                "  max_turns: 1\n",
                encoding="utf-8",
            )
            journey = load_journey(journey_path)
            first_evidence = root / "first-evidence.json"
            second_evidence = root / "second-evidence.json"
            for path in (first_evidence, second_evidence):
                path.write_text('{"attribution": {"category": "SUT/Skill"}}', encoding="utf-8")
            first = JourneyResult(
                journey_id=journey.id,
                platform="codex",
                passed=False,
                failure_category="SUT/Skill",
                failure_message="临时失败",
                turns=1,
                duration_seconds=1.0,
                observation={},
                evidence_path=first_evidence,
            )
            second = JourneyResult(
                journey_id=journey.id,
                platform="codex",
                passed=True,
                failure_category=None,
                failure_message="",
                turns=1,
                duration_seconds=1.0,
                observation={"case_archive": True},
                evidence_path=second_evidence,
            )

            with patch(
                "scripts.model_led_agent_eval.run_journey",
                side_effect=[first, second],
            ):
                result = run_journey_with_retries(
                    journey,
                    platform="codex",
                    source_root=ROOT,
                    temp_root=root / "runs",
                    retries=1,
                )

            self.assertTrue(result.passed)
            evidence = json.loads(second_evidence.read_text(encoding="utf-8"))
            self.assertEqual(len(evidence["retries"]), 2)
            self.assertEqual(evidence["attribution"]["category"], "pass")


if __name__ == "__main__":
    unittest.main()
