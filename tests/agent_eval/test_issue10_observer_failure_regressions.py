from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import scripts.model_led_agent_eval as eval_module
from scripts.model_led_agent_eval import (
    DeliveryRequirement,
    JourneyOracle,
    JourneyPublicState,
    _artifacts_satisfied,
    _observe_workspace,
    load_journey,
    run_journey,
)
from scripts.case_archive import CaseArchive
from tests.agent_eval.public_delivery_fixture import write_public_contract_delivery


ROOT = Path(__file__).resolve().parents[2]
TERMINATION_SCENARIO = (
    ROOT / "evals" / "model-led" / "codex-claude-termination-high-risk.yaml"
)


class Issue10ObserverFailureRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.journey = load_journey(TERMINATION_SCENARIO)

    def _state(self, observation):
        return JourneyPublicState(
            journey=self.journey,
            capabilities={"complete_document_pipeline_available": True},
            transcripts=(
                {
                    "turn": 1,
                    "assistant_response": "已说明候选稿路径。",
                    "selected_fact_ids": ["candidate_request"],
                },
            ),
            sent_fact_ids=("candidate_request",),
            observation=observation,
        )

    def test_cases_and_output_root_errors_are_recorded_as_runtime_observer_failures(self):
        original_iterdir = Path.iterdir
        for target_name, error_type, expected_code in (
            ("cases", PermissionError, "public_artifact_observer_permission_denied"),
            ("cases", OSError, "public_artifact_observer_failed"),
            ("output", PermissionError, "public_artifact_observer_permission_denied"),
            ("output", OSError, "public_artifact_observer_failed"),
            ("output_type", PermissionError, "public_artifact_observer_permission_denied"),
        ):
            with self.subTest(target=target_name, error=error_type.__name__), TemporaryDirectory() as temp:
                workspace = Path(temp)
                case_id, artifacts = write_public_contract_delivery(
                    workspace,
                    document_type="forced_termination_notice",
                    mode="candidate",
                    archive_revision=4,
                )
                target = (
                    workspace / ".arbibuddy" / "cases"
                    if target_name == "cases"
                    else artifacts["manifest"].parent
                    if target_name == "output_type"
                    else artifacts["manifest"].parent.parent
                )

                def fail_target(path: Path):
                    if path == target:
                        raise error_type(5, "injected observer read failure")
                    return original_iterdir(path)

                with patch.object(Path, "iterdir", fail_target):
                    observation = _observe_workspace(workspace)

                assessment = JourneyOracle(self.journey).evaluate(
                    self._state(observation)
                )

            self.assertFalse(assessment.passed)
            self.assertEqual(assessment.failure_category, "Runtime Adapter")
            self.assertEqual(assessment.failure_code, expected_code)
            if error_type is OSError:
                self.assertEqual(observation["inspection_error_count"], 1)
            elif target_name == "cases":
                self.assertEqual(observation["permission_denied_archive_count"], 1)
            else:
                self.assertEqual(observation["permission_denied_delivery_count"], 1)

    def test_output_type_directory_oserror_is_not_misattributed_to_the_skill(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _, artifacts = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            output_type_dir = artifacts["manifest"].parent
            original_iterdir = Path.iterdir

            def fail_output_type(path: Path):
                if path == output_type_dir:
                    raise OSError(5, "injected output directory failure")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", fail_output_type):
                observation = _observe_workspace(workspace)

        assessment = JourneyOracle(self.journey).evaluate(self._state(observation))
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "Runtime Adapter")
        self.assertEqual(assessment.failure_code, "public_artifact_observer_failed")
        self.assertGreaterEqual(observation["inspection_error_count"], 1)

    def test_recursive_output_scan_oserror_is_a_hard_observer_failure(self):
        requirement = DeliveryRequirement(
            "forced_termination_notice", "candidate", True, True, True, True
        )
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _, artifacts = write_public_contract_delivery(
                workspace,
                document_type="forced_termination_notice",
                mode="candidate",
                archive_revision=4,
            )
            output_root = artifacts["manifest"].parent.parent
            original_iterdir = Path.iterdir
            output_root_reads = 0

            def fail_recursive_scan(path: Path):
                nonlocal output_root_reads
                if path == output_root:
                    output_root_reads += 1
                    if output_root_reads == 2:
                        raise OSError(5, "injected recursive enumeration failure")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", fail_recursive_scan):
                observation = _observe_workspace(workspace)

        satisfied = _artifacts_satisfied(
            observation,
            ("case_archive", "docx", "checklist"),
            delivery_requirements=(requirement,),
        )
        self.assertFalse(
            satisfied,
            "rglob OSError must leave an observer failure and prevent PASS; "
            f"satisfied={satisfied}, observation={observation}",
        )
        self.assertGreater(observation["inspection_error_count"], 0)

    def _run_minimal_journey(self, root: Path, failure: str):
        scenario_path = root / "observer-test.yaml"
        scenario_path.write_text(
            "schema_version: 2\n"
            "id: observer-failure-test\n"
            "title: 最终观察失败留证\n"
            "initial_user_message: 请记录这条虚构劳动争议。\n"
            "fact_pool: []\n"
            "milestones: []\n"
            "expect:\n"
            "  required_artifacts: [case_archive]\n"
            "  max_turns: 1\n",
            encoding="utf-8",
        )
        closed = {"value": False}

        class FakeRuntime:
            def __init__(self, **kwargs):
                self.command = tuple(kwargs["command"])
                self.context_delivery = "test-double"

            def set_runtime_context(self, context):
                return None

            def probe_version(self, *, workspace):
                return "fake-agent 0.test"

            def turn(self, *, workspace, message):
                created = CaseArchive(workspace).create()
                if not created["ok"]:
                    raise AssertionError(created)
                return SimpleNamespace(
                    text="已收到这条争议情况。",
                    session_id="fake-session",
                    events=(),
                    command=self.command,
                    duration_seconds=0.0,
                )

            def close(self):
                closed["value"] = True

        capability_report = {
            "capabilities": {},
            "document_capabilities": {},
            "complete_document_pipeline_available": False,
            "platform_version_probe": {},
            "tested_at": "2026-09-27T00:00:00+00:00",
            "attachment_presentation": {},
        }
        original_observer = eval_module._observe_workspace

        def fail_only_final_observation(workspace: Path):
            if failure == "observation" and closed["value"]:
                raise PermissionError(5, "injected final observation failure")
            return original_observer(workspace)

        inventory_patch = (
            patch.object(
                eval_module,
                "_artifact_files",
                side_effect=OSError(5, "injected final inventory failure"),
            )
            if failure == "inventory"
            else patch.object(eval_module, "_artifact_files", wraps=eval_module._artifact_files)
        )
        with (
            patch.object(eval_module, "CliAgentRuntime", FakeRuntime),
            patch.object(
                eval_module,
                "probe_capabilities",
                return_value=capability_report,
            ),
            patch.object(
                eval_module, "_observe_workspace", fail_only_final_observation
            ),
            inventory_patch,
        ):
            result = run_journey(
                load_journey(scenario_path),
                platform="codex",
                source_root=ROOT,
                temp_root=root / "runs",
                platform_command=["fake-agent"],
                responder_mode="rule",
                timeout_per_turn_seconds=5,
            )
        evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
        return result, evidence

    def test_final_observation_oserror_writes_failure_evidence_and_never_passes(self):
        with TemporaryDirectory() as temp:
            result, evidence = self._run_minimal_journey(Path(temp), "observation")

        self.assertFalse(result.passed)
        self.assertEqual(evidence["attribution"]["category"], "Runtime Adapter")
        self.assertEqual(
            evidence["attribution"]["code"], "public_artifact_observer_failed"
        )
        self.assertFalse(evidence["oracle"]["passed"])
        self.assertEqual(
            evidence["runtime_diagnostic"]["final_observation_error"]["error_type"],
            "PermissionError",
        )

    def test_final_artifact_inventory_oserror_writes_failure_evidence_and_never_passes(self):
        with TemporaryDirectory() as temp:
            result, evidence = self._run_minimal_journey(Path(temp), "inventory")

        self.assertFalse(result.passed)
        self.assertEqual(evidence["attribution"]["category"], "Runtime Adapter")
        self.assertEqual(
            evidence["attribution"]["code"], "public_artifact_inventory_failed"
        )
        self.assertTrue(evidence["oracle"]["passed"])
        self.assertEqual(evidence["artifacts"], [])
        self.assertEqual(
            evidence["runtime_diagnostic"]["artifact_inventory_error"]["error_type"],
            "OSError",
        )


if __name__ == "__main__":
    unittest.main()
