"""Focused regressions for the two ML02 Journey failures."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.model_led_agent_eval import (
    JourneyOracle,
    JourneyPublicState,
    _managed_view_command_tokens,
    _managed_view_command_request,
    _observe_workspace,
    _retain_verified_deliveries,
    _summarize_runtime_events,
    load_journey,
)
from scripts.documents.view import managed_delivery_view
from tests.agent_eval.public_delivery_fixture import write_public_contract_delivery


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "evals" / "model-led" / "codex-claude-termination-high-risk.yaml"


class Issue10Ml02Regressions(unittest.TestCase):
    def test_successful_view_after_64_events_overrides_transient_rejection(self):
        with nullcontext(ROOT) as workspace:
            request = {
                "case_id": "case-0123456789abcdef01234567",
                "delivery_set_id": "delivery-set-test-1",
            }
            command = [
                sys.executable, "-B", "-X", "utf8", "-m",
                "scripts.documents.runtime_cli", "--workspace", str(workspace),
                "view", "--json", json.dumps(request),
            ]
            rejected = {
                "schema_version": 1, "case_id": request["case_id"],
                "delivery_state": "unavailable", "stopped": True,
                "error_code": "invalid_model_delivery", "presentation_files": [],
            }
            accepted = {
                "schema_version": 1, "case_id": request["case_id"],
                "delivery_state": "candidate_ready", "stopped": False,
                "presentation_files": [{"path": "candidate.docx"}],
            }
            noise = [{"type": "item.completed", "item": {"type": "agent_message"}} for _ in range(62)]
            events = tuple(noise + [
                {"type": "item.started", "item": {"type": "command_execution", "command": command}},
                {"type": "item.completed", "item": {
                    "type": "command_execution", "command": command,
                    "exit_code": 1, "aggregated_output": json.dumps(rejected),
                }},
                {"type": "item.started", "item": {"type": "command_execution", "command": command}},
                {"type": "item.completed", "item": {
                    "type": "command_execution", "command": command,
                    "exit_code": 0, "aggregated_output": json.dumps(accepted),
                }},
            ])
            with patch(
                "scripts.model_led_agent_eval._normalize_managed_view_receipt",
                side_effect=lambda response, **_: {"verified": True}
                if response.get("delivery_state") == "candidate_ready" else None,
            ):
                summaries = _summarize_runtime_events(events, workspace=workspace)

        statuses = [
            item["managed_view_receipt_status"] for item in summaries
            if item.get("view_token_seen")
        ]
        self.assertEqual(statuses, [
            "missing_or_unparseable_view_response", "rejected",
            "missing_or_unparseable_view_response", "passed",
        ])

    def test_ml02_uses_semantic_confirmation_without_literal_summary_markers(self):
        journey = load_journey(SCENARIO)
        facts = {fact.id: fact for fact in journey.fact_pool}

        self.assertEqual(journey.simulator.confirmation_fact_id, "final_confirmation")
        self.assertEqual(journey.simulator.confirmation_summary_required_groups, ())
        self.assertIn("candidate_scope_confirmation", facts)
        self.assertIn("暂定理由", facts["candidate_scope_confirmation"].text)
        self.assertIn("不要替我签署或发送", facts["candidate_scope_confirmation"].text)
        self.assertEqual(journey.max_turns, 14)

    def test_generated_files_do_not_finish_a_timed_out_turn(self):
        journey = load_journey(SCENARIO)
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            state = JourneyPublicState(
                journey=journey,
                capabilities={"complete_document_pipeline_available": True},
                transcripts=(
                    {
                        "turn": 1,
                        "assistant_response": "风险与后果已说明。",
                        "selected_fact_ids": ["candidate_request"],
                    },
                    {
                        "turn": 2,
                        "assistant_response": "确认后生成 DOCX 通知书与核对清单。",
                        "selected_fact_ids": ["final_confirmation"],
                    },
                ),
                sent_fact_ids=("candidate_request", "final_confirmation"),
                observation=_observe_workspace(workspace),
                runtime_completed=False,
            )

            assessment = JourneyOracle(journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertIn("runtime_turn_incomplete", assessment.missing)

    def test_generated_files_do_not_override_failed_managed_view(self):
        journey = load_journey(SCENARIO)
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            observation = _observe_workspace(workspace)
            observation["managed_view_status"] = "rejected"
            state = JourneyPublicState(
                journey=journey,
                capabilities={"complete_document_pipeline_available": True},
                transcripts=(
                    {
                        "turn": 1,
                        "assistant_response": "风险与后果已说明。",
                        "selected_fact_ids": ["candidate_request"],
                    },
                    {
                        "turn": 2,
                        "assistant_response": "确认后生成 DOCX 通知书与核对清单。",
                        "selected_fact_ids": ["final_confirmation"],
                    },
                ),
                sent_fact_ids=("candidate_request", "final_confirmation"),
                observation=observation,
            )

            assessment = JourneyOracle(journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertIn("managed_view_failed", assessment.missing)

    def test_codex_windows_wrapper_with_json_quotes_binds_view_command(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            request = json.dumps(
                {
                    "case_id": "case-0123456789abcdef01234567",
                    "delivery_set_id": "delivery-set-test-1",
                },
                separators=(",", ":"),
            )
            inner = (
                f'{sys.executable} -B -X utf8 -m scripts.documents.runtime_cli '
                f'--workspace "{workspace}" view --json \'{request}\''
            )
            wrapped = subprocess.list2cmdline(
                [r"C:\Program Files\PowerShell\7\pwsh.exe", "-Command", inner]
            )

            tokens = _managed_view_command_tokens(wrapped)

        self.assertIsNotNone(tokens)
        self.assertEqual(tokens[8:10], ["view", "--json"])
        self.assertEqual(json.loads(tokens[10]), json.loads(request))
        self.assertEqual(
            _managed_view_command_request(tokens, workspace),
            json.loads(request),
        )

    def test_transient_manifest_directory_denial_recovers_with_one_retry(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id, _ = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            original_iterdir = Path.iterdir
            denied = False

            def one_denial(path: Path):
                nonlocal denied
                if path.name == "forced_termination_notice" and not denied:
                    denied = True
                    raise PermissionError(13, "access denied")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", one_denial):
                view = managed_delivery_view(workspace=workspace, case_id=case_id)

        self.assertTrue(denied)
        self.assertFalse(view["stopped"], view)
        self.assertEqual(len(view["presentation_files"]), 2)

    def test_persistent_manifest_denial_requests_view_retry_not_rerender(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id, _ = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            original_iterdir = Path.iterdir

            def deny_manifest(path: Path):
                if path.name == "forced_termination_notice":
                    raise PermissionError(13, "access denied")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", deny_manifest):
                view = managed_delivery_view(workspace=workspace, case_id=case_id)

        self.assertTrue(view["stopped"])
        self.assertEqual(view["next_action"], "retry_managed_view_with_access")
        self.assertIn("PermissionError", view["reason"])

    def test_managed_view_ignores_uncommitted_renderer_staging(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id, _ = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
            (output_root / ".forced_termination_notice.staging-jmcn2zlp").mkdir()

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

        self.assertFalse(view["stopped"], view)
        self.assertEqual(len(view["presentation_files"]), 2)

    def test_managed_view_still_rejects_unexpected_output_directory(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            case_id, _ = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
            (output_root / ".unrecognized.staging-jmcn2zlp").mkdir()

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

        self.assertTrue(view["stopped"])
        self.assertEqual(view["error_code"], "invalid_model_delivery")

    def test_verified_candidate_files_can_be_retained_for_host_preview(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            case_id, _ = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            observation = _observe_workspace(workspace)
            retained = _retain_verified_deliveries(workspace, root, observation)

            self.assertEqual(len(retained), 2)
            self.assertEqual({item["kind"] for item in retained}, {"docx", "checklist"})
            for item in retained:
                copied = Path(item["path"])
                self.assertTrue(copied.is_file())
                self.assertIn("retained-deliveries", copied.parts)
                self.assertEqual(copied.stat().st_size, item["size"])
                original = workspace.joinpath(*item["source_path"].split("/"))
                self.assertEqual(
                    item["sha256"], hashlib.sha256(original.read_bytes()).hexdigest()
                )
                self.assertNotIn("案情档案", copied.name)
            self.assertTrue(case_id)

    def test_retention_rejects_delivery_path_outside_workspace(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            observation = {
                "deliveries": [
                    {
                        "document_type": "forced_termination_notice",
                        "document_path": "../outside.docx",
                        "checklist_path": "../outside.txt",
                    }
                ]
            }
            with self.assertRaises(ValueError):
                _retain_verified_deliveries(workspace, root, observation)
            self.assertFalse((root / "retained-deliveries").exists())


if __name__ == "__main__":
    unittest.main()
