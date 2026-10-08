from __future__ import annotations

from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.workbuddy_acceptance.native_trace import adapt_native_traces
from scripts.workbuddy_acceptance.service import (
    audit_workspace,
    build_no_delivery_contract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "workbuddy" / "native-rc9"


def _native_transcript(root: Path) -> Path:
    transcript = root / "transcript.jsonl"
    transcript.write_text('{"type":"metadata"}\n', encoding="utf-8")
    return transcript



class WorkBuddyNativeTraceTests(unittest.TestCase):
    def test_rc9_native_shape_is_deduplicated_and_keeps_structured_payloads(self):
        evidence = adapt_native_traces(
            FIXTURE_ROOT,
            session_ids=("ml01-session-redacted",),
            include_files=("ml01.trace.json", "ml01-duplicate.trace.json"),
        )

        self.assertEqual(evidence["session_ids"], ["ml01-session-redacted"])
        self.assertEqual(evidence["summary"]["skill_calls"], 1)
        self.assertEqual(evidence["summary"]["render_calls"], 2)
        self.assertEqual(evidence["summary"]["present_files_calls"], 2)
        self.assertEqual(evidence["summary"]["present_file_counts"], [2, 2])
        self.assertGreaterEqual(evidence["summary"]["duplicate_spans_removed"], 1)
        render_call = next(
            item for item in evidence["records"]
            if item.get("event") == "function_call"
            and item.get("tool_name") == "document.render-v1"
        )
        self.assertIsInstance(render_call["arguments"], dict)
        self.assertEqual(render_call["arguments"]["document_type"], "arbitration_application")

    def test_unparseable_native_tool_payload_is_a_diagnostic_not_empty_success(self):
        evidence = adapt_native_traces(
            FIXTURE_ROOT,
            session_ids=("ml01-session-redacted",),
            include_files=("ml01.trace.json",),
        )
        self.assertFalse(evidence["diagnostics"])

        malformed = evidence["records"][0].copy()
        malformed["arguments"] = "{not-json"
        # The adapter contract must expose an explicit diagnostic when a native
        # payload cannot be decoded; callers must not silently treat it as {}.
        evidence = adapt_native_traces(
            FIXTURE_ROOT,
            session_ids=("ml01-session-redacted",),
            include_files=("ml01-malformed.trace.json",),
        )
        self.assertIn("native_trace_tool_payload_unparseable", evidence["diagnostic_codes"])

    def test_ml03_preserves_ordered_two_session_evidence(self):
        evidence = adapt_native_traces(
            FIXTURE_ROOT,
            session_ids=(
                "ml03-session-1-redacted",
                "ml03-session-2-redacted",
            ),
            include_files=("ml03-session-1.trace.json", "ml03-session-2.trace.json"),
        )
        self.assertEqual(
            evidence["session_ids"],
            ["ml03-session-1-redacted", "ml03-session-2-redacted"],
        )
        self.assertEqual(evidence["sessions"][1]["session_id"], "ml03-session-2-redacted")
        self.assertEqual(evidence["sessions"][1]["render_calls"], 0)
        self.assertEqual(evidence["sessions"][1]["present_files_calls"], 0)

    def test_ml03_v2_binds_two_sessions_and_attributes_restart_failures(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "验证事实纠正和恢复"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()
            for name in ("ml03-session-1.trace.json", "ml03-session-2.trace.json"):
                payload = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
                text = json.dumps(payload, ensure_ascii=False).replace(
                    "case-cccccccccccccccccccccccc", case_id
                )
                (trace_root / name).write_text(text, encoding="utf-8")
            transcript = _native_transcript(root)
            contract_path = root / "contract.json"
            contract = build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(
                    "ml03-session-1-redacted",
                    "ml03-session-2-redacted",
                ),
            )
            self.assertEqual(contract["schema_version"], 2)
            self.assertEqual(
                contract["session_ids"],
                ["ml03-session-1-redacted", "ml03-session-2-redacted"],
            )
            self.assertRegex(contract["transcript_sha256"], r"^[0-9a-f]{64}$")

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(
                    "ml03-session-1-redacted",
                    "ml03-session-2-redacted",
                ),
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertIn("restart_skill_invocation_missing", codes)
            self.assertIn("restart_first_action_not_current_case_read", codes)
            self.assertIn("destructive_case_workspace_command", codes)
            self.assertFalse(report["passed"], report)

    def test_ml03_v2_rejects_overlapping_session_timeline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证两个会话的真实时间边界"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()
            first_session = "ml03-session-1-redacted"
            second_session = "ml03-session-2-redacted"
            for name in ("ml03-session-1.trace.json", "ml03-session-2.trace.json"):
                text = (FIXTURE_ROOT / name).read_text(encoding="utf-8")
                text = text.replace("case-cccccccccccccccccccccccc", case_id)
                if name.endswith("session-2.trace.json"):
                    text = text.replace("2026-09-17T04:", "2026-09-17T02:")
                (trace_root / name).write_text(text, encoding="utf-8")

            transcript = _native_transcript(root)

            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )

            self.assertIn(
                "journey_session_overlap",
                {item["code"] for item in report["violations"]},
            )
            self.assertFalse(report["passed"], report)

    def test_ml03_restart_rejects_pre_read_probe_before_current_case_read(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证只从当前工作区恢复"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()

            def span(
                trace_id: str,
                session_id: str,
                span_id: str,
                tool_name: str,
                tool_input: object,
                tool_output: object,
                started_at: str,
            ) -> dict[str, object]:
                return {
                    "traceId": trace_id,
                    "spanId": span_id,
                    "sessionId": session_id,
                    "kind": "function",
                    "toolName": tool_name,
                    "toolInput": tool_input,
                    "toolOutput": tool_output,
                    "status": "success",
                    "startedAt": started_at,
                    "endedAt": started_at,
                }

            first_session = "ml03-prep-session-1"
            second_session = "ml03-prep-session-2"
            traces = (
                {
                    "trace": {"traceId": "trace-prep-1", "sessionId": first_session},
                    "spans": [
                        span(
                            "trace-prep-1",
                            first_session,
                            "skill-1",
                            "Skill",
                            {"skill": "arbibuddy"},
                            {"ok": True},
                            "2026-09-18T03:00:00Z",
                        ),
                        span(
                            "trace-prep-1",
                            first_session,
                            "commit-1",
                            "CaseArchive.commit",
                            {"operation": "commit", "case_id": case_id},
                            {"ok": True, "result": {"revision": 1}},
                            "2026-09-18T03:01:00Z",
                        ),
                    ],
                },
                {
                    "trace": {"traceId": "trace-prep-2", "sessionId": second_session},
                    "spans": [
                        span(
                            "trace-prep-2",
                            second_session,
                            "skill-2",
                            "Skill",
                            {"skill": "arbibuddy"},
                            {"ok": True},
                            "2026-09-18T04:00:00Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "list-workspace",
                            "Bash",
                            {"command": f'ls -la "{workspace.as_posix()}"'},
                            {"ok": True},
                            "2026-09-18T04:00:01Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "read-reference",
                            "Read",
                            {"file_path": "references/model-led-case-archive.md"},
                            {"ok": True},
                            "2026-09-18T04:00:02Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "powershell-list-workspace",
                            "PowerShell",
                            {
                                "command": (
                                    f'Get-ChildItem -Path "{workspace}" -Force '
                                    "| Select-Object Mode,Length,Name "
                                    "| Format-Table -AutoSize"
                                )
                            },
                            {"ok": True},
                            "2026-09-18T04:00:02.025Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "powershell-recursive-list",
                            "PowerShell",
                            {
                                "command": (
                                    f'$p="{workspace}"; "ROOT EXISTS: " + (Test-Path $p); '
                                    "Get-ChildItem -Path $p -Force -Recurse -Depth 3 "
                                    "| ForEach-Object { $_.FullName.Replace($p,'') }"
                                )
                            },
                            {"ok": True},
                            "2026-09-18T04:00:02.050Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "powershell-json-list",
                            "PowerShell",
                            {
                                "command": (
                                    f'Get-ChildItem -Path "{workspace}" -Force -Recurse '
                                    "| Select-Object FullName,Length,LastWriteTime "
                                    "| ConvertTo-Json -Depth 3"
                                )
                            },
                            {"ok": True},
                            "2026-09-18T04:00:02.075Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "python-version",
                            "PowerShell",
                            {"command": "python -V"},
                            {"ok": True, "output": "Python 3.13.12"},
                            "2026-09-18T04:00:02.100Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "bash-python-version",
                            "Bash",
                            {"command": "python -V; echo OK"},
                            {"ok": True, "output": "Python 3.13.12\nOK"},
                            "2026-09-18T04:00:02.200Z",
                        ),
                        span(
                            "trace-prep-2",
                            second_session,
                            "read-current",
                            "CaseArchive.read",
                            {"operation": "read", "case_id": case_id},
                            {"ok": True, "result": {"case_id": case_id}},
                            "2026-09-18T04:00:03Z",
                        ),
                    ],
                },
            )
            for index, payload in enumerate(traces, start=1):
                (trace_root / f"session-{index}.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

            transcript = _native_transcript(root)

            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )

            self.assertIn(
                "restart_pre_read_probe",
                {item["code"] for item in report["violations"]},
                report,
            )
            self.assertFalse(report["passed"], report)

    def test_ml03_restart_accepts_skill_then_exact_current_case_read_with_memory_diagnostic(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证恢复只读取当前档案"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()

            def span(session_id: str, span_id: str, tool: str, tool_input: object, output: object, at: str):
                return {
                    "traceId": f"trace-{session_id}",
                    "spanId": span_id,
                    "sessionId": session_id,
                    "kind": "function",
                    "toolName": tool,
                    "toolInput": tool_input,
                    "toolOutput": output,
                    "status": "success",
                    "startedAt": at,
                    "endedAt": at,
                }

            first = "ml03-clean-session-1"
            second = "ml03-clean-session-2"
            traces = (
                {
                    "trace": {"traceId": "trace-first", "sessionId": first},
                    "spans": [
                        span(first, "skill-first", "Skill", {"skill": "arbibuddy"}, {"ok": True}, "2026-09-18T03:00:00Z"),
                        span(first, "commit-first", "CaseArchive.commit", {"operation": "commit", "case_id": case_id}, {"ok": True, "result": {"revision": 1}}, "2026-09-18T03:01:00Z"),
                    ],
                },
                {
                    "trace": {"traceId": "trace-second", "sessionId": second},
                    "spans": [
                        span(second, "skill-second", "Skill", {"skill": "arbibuddy"}, {"ok": True}, "2026-09-18T04:00:00Z"),
                        span(
                            second,
                            "memory-read",
                            "Read",
                            {"file_path": str(workspace / ".workbuddy" / "memory" / "summary.md")},
                            {"ok": True, "text": "host memory only"},
                            "2026-09-18T04:00:00.500Z",
                        ),
                        span(second, "read-current", "CaseArchive.read", {"operation": "read", "case_id": case_id}, {"ok": True, "result": {"case_id": case_id}}, "2026-09-18T04:00:01Z"),
                    ],
                },
            )
            for index, payload in enumerate(traces, start=1):
                (trace_root / f"session-{index}.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

            transcript = _native_transcript(root)

            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )

            self.assertNotIn(
                "restart_pre_read_probe",
                {item["code"] for item in report["violations"]},
            )

            self.assertIn(
                "model_read_untrusted_context",
                report["transcript_diagnostic"]["diagnostic_observations"],
            )
            self.assertNotIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )

    def test_ml03_restart_rejects_failed_read_retries_before_successful_recovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证失败读取重试后的恢复结果"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()

            def span(
                trace_id: str,
                session_id: str,
                span_id: str,
                tool: str,
                tool_input: object,
                tool_output: object,
                at: str,
                *,
                status: str = "success",
            ) -> dict[str, object]:
                return {
                    "traceId": trace_id,
                    "spanId": span_id,
                    "sessionId": session_id,
                    "kind": "function",
                    "toolName": tool,
                    "toolInput": tool_input,
                    "toolOutput": tool_output,
                    "status": status,
                    "startedAt": at,
                    "endedAt": at,
                }

            first = "ml03-retry-session-1"
            second = "ml03-retry-session-2"
            exact_read = (
                f'cd "{ROOT.as_posix()}" && "{Path(sys.executable).as_posix()}" '
                f'-B -X utf8 -m scripts.case_archive.cli --root '
                f'"{workspace.as_posix()}" read-current'
            )
            first_payload = {
                "trace": {"traceId": "trace-retry-1", "sessionId": first},
                "spans": [
                    span(
                        "trace-retry-1",
                        first,
                        "skill-first",
                        "Skill",
                        {"skill": "arbibuddy"},
                        {"ok": True},
                        "2026-09-20T03:00:00Z",
                    ),
                    span(
                        "trace-retry-1",
                        first,
                        "commit-first",
                        "CaseArchive.commit",
                        {"operation": "commit", "case_id": case_id},
                        {"ok": True, "result": {"revision": 1}},
                        "2026-09-20T03:01:00Z",
                    ),
                ],
            }
            second_payload = {
                "trace": {"traceId": "trace-retry-2", "sessionId": second},
                "spans": [
                    span(
                        "trace-retry-2",
                        second,
                        "skill-second",
                        "Skill",
                        {"skill": "arbibuddy"},
                        {"ok": True},
                        "2026-09-20T04:00:00Z",
                    ),
                    span(
                        "trace-retry-2",
                        second,
                        "wrong-module",
                        "Bash",
                        {
                            "command": (
                                f'cd "{ROOT.as_posix()}" && python -m '
                                "scripts.case_archive.runtime_cli read-current"
                            )
                        },
                        {"ok": False, "error": "module not found", "exitCode": 1},
                        "2026-09-20T04:00:01Z",
                        status="error",
                    ),
                    span(
                        "trace-retry-2",
                        second,
                        "wrong-script",
                        "Bash",
                        {
                            "command": (
                                f'cd "{ROOT.as_posix()}" && python '
                                "scripts/case_archive/runtime_cli.py read-current"
                            )
                        },
                        {"ok": False, "error": "file not found", "exitCode": 2},
                        "2026-09-20T04:00:02Z",
                        status="error",
                    ),
                    span(
                        "trace-retry-2",
                        second,
                        "read-reference",
                        "Read",
                        {
                            "file_path": str(
                                ROOT / "references" / "model-led-case-archive.md"
                            )
                        },
                        {"ok": True},
                        "2026-09-20T04:00:03Z",
                    ),
                    span(
                        "trace-retry-2",
                        second,
                        "read-current",
                        "Bash",
                        {"command": exact_read},
                        {
                            "ok": True,
                            "operation": "read",
                            "result": {"case_id": case_id},
                        },
                        "2026-09-20T04:00:04Z",
                    ),
                ],
            }
            (trace_root / "session-1.json").write_text(
                json.dumps(first_payload, ensure_ascii=False), encoding="utf-8"
            )
            (trace_root / "session-2.json").write_text(
                json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
            )

            transcript = _native_transcript(root)

            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )

            codes = {item["code"] for item in report["violations"]}
            diagnostics = set(
                report["transcript_diagnostic"]["diagnostic_observations"]
            )
            self.assertIn("restart_pre_read_probe", codes)
            self.assertIn("restart_first_action_not_current_case_read", codes)
            self.assertIn("prohibited_case_archive_transport", codes)
            self.assertIn("prohibited_temporary_script", codes)
            self.assertIn("skill_invoked_but_bypassed", codes)
            self.assertIn("recoverable_case_archive_transport_retry", diagnostics)
            self.assertIn(
                "recovery_reference_read_before_current_archive", diagnostics
            )

    def test_ml03_restart_rejects_tool_before_skill(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create(
                {"initial_goal": "验证恢复会话首个工具调用"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            trace_root = root / "traces"
            trace_root.mkdir()

            def span(session_id: str, span_id: str, tool: str, tool_input: object, output: object, at: str):
                return {
                    "traceId": f"trace-{session_id}",
                    "spanId": span_id,
                    "sessionId": session_id,
                    "kind": "function",
                    "toolName": tool,
                    "toolInput": tool_input,
                    "toolOutput": output,
                    "status": "success",
                    "startedAt": at,
                    "endedAt": at,
                }

            first = "ml03-first-tool-session-1"
            second = "ml03-first-tool-session-2"
            traces = (
                {
                    "trace": {"traceId": "trace-first-tool-1", "sessionId": first},
                    "spans": [
                        span(first, "skill-first", "Skill", {"skill": "arbibuddy"}, {"ok": True}, "2026-09-18T03:00:00Z"),
                        span(first, "commit-first", "CaseArchive.commit", {"operation": "commit", "case_id": case_id}, {"ok": True}, "2026-09-18T03:01:00Z"),
                    ],
                },
                {
                    "trace": {"traceId": "trace-first-tool-2", "sessionId": second},
                    "spans": [
                        span(second, "probe", "Bash", {"command": "ls -la"}, {"ok": True}, "2026-09-18T04:00:00Z"),
                        span(second, "skill-second", "Skill", {"skill": "arbibuddy"}, {"ok": True}, "2026-09-18T04:00:01Z"),
                        span(second, "read-current", "CaseArchive.read", {"operation": "read", "case_id": case_id}, {"ok": True}, "2026-09-18T04:00:02Z"),
                    ],
                },
            )
            for index, payload in enumerate(traces, start=1):
                (trace_root / f"session-{index}.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

            transcript = _native_transcript(root)

            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first, second),
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertIn("restart_first_tool_not_skill", codes, report)
            self.assertNotIn("skill_invoked_but_bypassed", codes)
            self.assertNotIn("cross_source_case_fact_pollution", codes)
            self.assertFalse(report["passed"], report)

    def test_ml03_restart_rejects_substantive_action_before_current_case_read(self):
        actions = (
            (
                "commit",
                "CaseArchive.commit",
                {"operation": "commit", "case_id": "{case_id}"},
                {"ok": True, "result": {"revision": 2}},
            ),
            (
                "calculate",
                "amount.calculate-v1",
                {"case_id": "{case_id}"},
                {"ok": True, "result": {"amount": "100.00"}},
            ),
            (
                "render",
                "document.render-v1",
                {"case_id": "{case_id}"},
                {"ok": True, "result": {"presentation_files": []}},
            ),
            (
                "delete",
                "Bash",
                {"command": "Remove-Item .arbibuddy/cases/{case_id}/output -Recurse"},
                {"ok": True},
            ),
            (
                "list_then_commit",
                "PowerShell",
                {
                    "command": (
                        "Get-ChildItem -Force; python -B -X utf8 -m "
                        "scripts.case_archive.cli --root . commit "
                        "--json '{{\"case_id\":\"{case_id}\"}}'"
                    )
                },
                {"ok": True, "result": {"revision": 2}},
            ),
        )

        for action_name, tool_name, tool_input, tool_output in actions:
            with self.subTest(action=action_name), TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "workspace"
                workspace.mkdir()
                created = CaseArchive(workspace).create(
                    {"initial_goal": "验证恢复前不得执行业务动作"}
                )
                self.assertTrue(created["ok"], created)
                case_id = created["result"]["case_id"]
                trace_root = root / "traces"
                trace_root.mkdir()

                def span(
                    trace_id: str,
                    session_id: str,
                    span_id: str,
                    tool: str,
                    tool_input_value: object,
                    tool_output_value: object,
                    started_at: str,
                ) -> dict[str, object]:
                    return {
                        "traceId": trace_id,
                        "spanId": span_id,
                        "sessionId": session_id,
                        "kind": "function",
                        "toolName": tool,
                        "toolInput": tool_input_value,
                        "toolOutput": tool_output_value,
                        "status": "success",
                        "startedAt": started_at,
                        "endedAt": started_at,
                    }

                first_session = f"ml03-negative-{action_name}-1"
                second_session = f"ml03-negative-{action_name}-2"
                first_payload = {
                    "trace": {
                        "traceId": f"trace-negative-{action_name}-1",
                        "sessionId": first_session,
                    },
                    "spans": [
                        span(
                            f"trace-negative-{action_name}-1",
                            first_session,
                            "skill-1",
                            "Skill",
                            {"skill": "arbibuddy"},
                            {"ok": True},
                            "2026-09-18T03:00:00Z",
                        ),
                        span(
                            f"trace-negative-{action_name}-1",
                            first_session,
                            "commit-1",
                            "CaseArchive.commit",
                            {"operation": "commit", "case_id": case_id},
                            {"ok": True, "result": {"revision": 1}},
                            "2026-09-18T03:01:00Z",
                        ),
                    ],
                }
                second_payload = {
                    "trace": {
                        "traceId": f"trace-negative-{action_name}-2",
                        "sessionId": second_session,
                    },
                    "spans": [
                        span(
                            f"trace-negative-{action_name}-2",
                            second_session,
                            "skill-2",
                            "Skill",
                            {"skill": "arbibuddy"},
                            {"ok": True},
                            "2026-09-18T04:00:00Z",
                        ),
                        span(
                            f"trace-negative-{action_name}-2",
                            second_session,
                            f"action-{action_name}",
                            tool_name,
                            {
                                key: (
                                    value.format(case_id=case_id)
                                    if isinstance(value, str)
                                    else value
                                )
                                for key, value in tool_input.items()
                            },
                            tool_output,
                            "2026-09-18T04:00:01Z",
                        ),
                        span(
                            f"trace-negative-{action_name}-2",
                            second_session,
                            "read-current",
                            "CaseArchive.read",
                            {"operation": "read", "case_id": case_id},
                            {"ok": True, "result": {"case_id": case_id}},
                            "2026-09-18T04:00:02Z",
                        ),
                    ],
                }
                (trace_root / "session-1.json").write_text(
                    json.dumps(first_payload, ensure_ascii=False), encoding="utf-8"
                )
                (trace_root / "session-2.json").write_text(
                    json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
                )

                transcript = _native_transcript(root)

                contract_path = root / "contract.json"
                build_no_delivery_contract(
                    journey="ML03-v2",
                    case_id=case_id,
                    output=contract_path,
                    transcript_jsonl=transcript,
                    workbuddy_trace_root=trace_root,
                    session_ids=(first_session, second_session),
                )
                report = audit_workspace(
                    workspace=workspace,
                    case_id=case_id,
                    transcript_jsonl=transcript,
                    allow_no_delivery=True,
                    journey_contract=contract_path,
                    workbuddy_trace_root=trace_root,
                    session_ids=(first_session, second_session),
                )

                self.assertIn(
                    "restart_first_action_not_current_case_read",
                    {item["code"] for item in report["violations"]},
                    report,
                )
                self.assertFalse(report["passed"], report)

    def test_runtime_context_case_archive_command_is_allowed_but_handwritten_root_is_not(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "验证公开档案 transport"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            records = [
                {
                    "sessionId": "transport-good",
                    "type": "function_call",
                    "id": "skill",
                    "name": "Skill",
                    "arguments": {"skill": "arbibuddy"},
                },
                {
                    "sessionId": "transport-good",
                    "type": "function_call_result",
                    "call_id": "skill",
                    "name": "Skill",
                    "status": "success",
                },
                {
                    "sessionId": "transport-good",
                    "type": "function_call",
                    "id": "archive",
                    "name": "Bash",
                    "arguments": {
                        "cwd": str(ROOT),
                        "argv": [
                            sys.executable,
                            "-B",
                            "-X",
                            "utf8",
                            "-m",
                            "scripts.case_archive.cli",
                            "--root",
                            str(workspace),
                            "read",
                            case_id,
                        ],
                    },
                },
                {
                    "sessionId": "transport-good",
                    "type": "function_call_result",
                    "call_id": "archive",
                    "name": "Bash",
                    "status": "success",
                },
            ]
            transcript = workspace / "transport.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="transport-good",
            )
            codes = {item["code"] for item in report["violations"]}
            self.assertNotIn("prohibited_case_archive_transport", codes)

            records[2]["arguments"]["argv"][7] = str(workspace / "other")
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="transport-good",
            )
            self.assertIn(
                "prohibited_case_archive_transport",
                {item["code"] for item in report["violations"]},
            )

    def test_case_archive_managed_input_is_required_for_commit_transport(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "验证 managed input transport"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            managed_input = workspace / ".arbibuddy" / "runtime-input" / "commit.json"
            outside_input = workspace / "outside.json"
            command = (
                f'cd "{ROOT.as_posix()}" && '
                f'"{Path(sys.executable).as_posix()}" -B -X utf8 '
                "-m scripts.case_archive.cli "
                f'--root "{workspace.as_posix()}" commit --input "{managed_input.as_posix()}"'
            )
            records = [
                {
                    "sessionId": "managed-input-transport",
                    "type": "function_call",
                    "id": "archive",
                    "name": "Bash",
                    "arguments": {"command": command},
                },
                {
                    "sessionId": "managed-input-transport",
                    "type": "function_call_result",
                    "call_id": "archive",
                    "name": "Bash",
                    "status": "success",
                },
            ]
            transcript = workspace / "managed-input.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="managed-input-transport",
            )
            self.assertNotIn(
                "prohibited_case_archive_transport",
                {item["code"] for item in report["violations"]},
            )

            outside_command = command.replace(
                managed_input.as_posix(), outside_input.as_posix()
            )
            records[0]["arguments"]["command"] = outside_command
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="managed-input-transport",
            )
            self.assertIn(
                "prohibited_case_archive_transport",
                {item["code"] for item in report["violations"]},
            )

    def test_echoed_case_archive_command_in_result_is_not_reaudited_as_transport(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create({"initial_goal": "验证结果回显"})
            case_id = created["result"]["case_id"]
            command = (
                f'cd "{ROOT.as_posix()}" && '
                f'"{Path(sys.executable).as_posix()}" -B -X utf8 -m '
                f'scripts.case_archive.cli --root "{workspace.as_posix()}" read {case_id}'
            )
            records = [
                {
                    "sessionId": "echo-result",
                    "type": "function_call",
                    "id": "skill",
                    "name": "Skill",
                    "arguments": {"skill": "arbibuddy"},
                },
                {
                    "sessionId": "echo-result",
                    "type": "function_call_result",
                    "call_id": "skill",
                    "name": "Skill",
                    "status": "success",
                },
                {
                    "sessionId": "echo-result",
                    "type": "function_call",
                    "id": "archive",
                    "name": "Bash",
                    "arguments": {"command": command},
                },
                {
                    "sessionId": "echo-result",
                    "type": "function_call_result",
                    "call_id": "archive",
                    "name": "Bash",
                    "status": "success",
                    "result": {"command": command, "stdout": "{}"},
                },
            ]
            transcript = workspace / "echo.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="echo-result",
            )

        self.assertNotIn(
            "prohibited_case_archive_transport",
            {item["code"] for item in report["violations"]},
        )

    def test_deleting_transient_amount_requests_is_not_case_archive_destruction(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            created = CaseArchive(workspace).create({"initial_goal": "验证临时输入清理"})
            case_id = created["result"]["case_id"]
            transient = workspace / ".arbibuddy" / "runtime-input" / "amount.json"
            records = [
                {
                    "sessionId": "transient-delete",
                    "type": "function_call",
                    "id": "skill",
                    "name": "Skill",
                    "arguments": {"skill": "arbibuddy"},
                },
                {
                    "sessionId": "transient-delete",
                    "type": "function_call_result",
                    "call_id": "skill",
                    "name": "Skill",
                    "status": "success",
                },
                {
                    "sessionId": "transient-delete",
                    "type": "function_call",
                    "id": "cleanup",
                    "name": "Bash",
                    "arguments": {"command": f'Remove-Item -LiteralPath "{transient}"'},
                },
            ]
            transcript = workspace / "transient-delete.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="transient-delete",
            )

        self.assertNotIn(
            "destructive_case_workspace_command",
            {item["code"] for item in report["violations"]},
        )

    def test_native_bash_cd_wrapper_preserves_exact_case_archive_transport(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "验证 WorkBuddy Bash transport"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            command = (
                f'cd "{ROOT.as_posix()}" && '
                f'"{Path(sys.executable).as_posix()}" -B -X utf8 '
                "-m scripts.case_archive.cli "
                f'--root "{workspace.as_posix()}" read {case_id}'
            )
            transcript = workspace / "native-bash-wrapper.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "native-bash-wrapper",
                            "type": "function_call",
                            "id": "archive",
                            "name": "Bash",
                            "arguments": {"command": command},
                        },
                        {
                            "sessionId": "native-bash-wrapper",
                            "type": "function_call_result",
                            "call_id": "archive",
                            "name": "Bash",
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
                session_id="native-bash-wrapper",
            )

            self.assertNotIn(
                "prohibited_case_archive_transport",
                {item["code"] for item in report["violations"]},
            )

    def test_read_current_transport_is_a_public_current_case_read(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "恢复当前案件"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            transcript = workspace / "read-current.jsonl"
            command = (
                f'cd "{ROOT.as_posix()}" && '
                f'"{Path(sys.executable).as_posix()}" -B -X utf8 '
                "-m scripts.case_archive.cli "
                f'--root "{workspace.as_posix()}" read-current'
            )
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "read-current",
                            "type": "function_call",
                            "id": "archive",
                            "name": "Bash",
                            "arguments": {"command": command},
                        },
                        {
                            "sessionId": "read-current",
                            "type": "function_call_result",
                            "call_id": "archive",
                            "name": "Bash",
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
                session_id="read-current",
            )

            diagnostic = report["transcript_diagnostic"]
            self.assertTrue(diagnostic["current_case_archive_action_observed"])
            self.assertNotIn(
                "prohibited_case_archive_transport",
                {item["code"] for item in report["violations"]},
            )


    def test_ml03_journey_contract_rejects_legacy_schema_without_native_v2_evidence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create({"initial_goal": "验证 v2 contract"})
            case_id = created["result"]["case_id"]
            transcript = root / "transcript.jsonl"
            transcript.write_text('{"type":"metadata"}\n', encoding="utf-8")
            contract = root / "legacy-contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": "workbuddy-journey-harness",
                        "journey_id": "ML03-v2",
                        "case_id": case_id,
                        "expected_deliveries": [],
                        "transcript_sha256": __import__("hashlib").sha256(
                            transcript.read_bytes()
                        ).hexdigest(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract,
            )

            messages = [
                item["message"]
                for item in report["violations"]
                if item["code"] == "invalid_no_delivery_mode"
            ]
            self.assertTrue(
                any("workbuddy-journey-contract-v2" in message for message in messages),
                report,
            )

    def test_memory_fact_used_in_legal_verification_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create({"initial_goal": "核验适用规则"})
            case_id = created["result"]["case_id"]
            memory = workspace / ".workbuddy" / "memory" / "history.md"
            memory.parent.mkdir(parents=True)
            transcript = root / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "memory-legal",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory)},
                        },
                        {
                            "sessionId": "memory-legal",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "历史案件：CANARY-HANGZHOU-2026；适用地区：杭州。",
                        },
                        {
                            "sessionId": "memory-legal",
                            "type": "function_call",
                            "id": "legal-check",
                            "name": "scripts.legal_verification.runtime_cli",
                            "arguments": {
                                "jurisdiction": "杭州",
                                "fact_marker": "CANARY-HANGZHOU-2026",
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
                session_id="memory-legal",
            )

            self.assertIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )

    def test_hidden_assistant_record_is_not_a_public_memory_business_result(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create({"initial_goal": "核对工资争议事实"})
            case_id = created["result"]["case_id"]
            memory = workspace / ".workbuddy" / "memory" / "history.md"
            memory.parent.mkdir(parents=True)
            transcript = root / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "sessionId": "hidden-assistant",
                            "type": "tool_call",
                            "id": "memory-read",
                            "name": "Read",
                            "arguments": {"path": str(memory)},
                        },
                        {
                            "sessionId": "hidden-assistant",
                            "type": "tool_result",
                            "call_id": "memory-read",
                            "output": "历史案件：月薪 29137 元，欠薪 7 个月。",
                        },
                        {
                            "sessionId": "hidden-assistant",
                            "role": "assistant",
                            "content": "内部草稿：月薪 29137 元，欠薪 7 个月。",
                        },
                    )
                ),
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="hidden-assistant",
            )

            self.assertNotIn(
                "cross_source_case_fact_pollution",
                {item["code"] for item in report["violations"]},
            )


if __name__ == "__main__":
    unittest.main()
