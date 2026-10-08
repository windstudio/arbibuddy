"""Review 回归夹具：旧 Harness 曾放行的两个 false PASS。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import unittest

from scripts.model_led_agent_eval import (
    JourneyOracle,
    JourneyPublicState,
    load_journey,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "agent_eval" / "fixtures" / "model_led"


class ModelLedOracleFixtureTests(unittest.TestCase):
    def test_partial_trigger_does_not_attribute_unsent_facts_to_skill(self):
        state = self._state(
            "ml01-r23-false-pass.json", "codex-claude-wage-bonus-overtime.yaml"
        )
        path = replace(
            state.journey.completion_paths[0],
            requires_sent_facts=("final_confirmation", "unknown_detail"),
        )
        journey = replace(state.journey, completion_paths=(path,))
        state = replace(
            state, journey=journey,
            sent_fact_ids=state.sent_fact_ids + ("final_confirmation",),
        )
        assessment = JourneyOracle(journey).evaluate(state)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "Scenario")
        self.assertEqual(assessment.failure_code, "completion_trigger_not_sent")

    def test_inapplicable_degraded_path_cannot_complete_normal_path_triggers(self):
        state = self._state(
            "ml01-r23-false-pass.json", "codex-claude-wage-bonus-overtime.yaml"
        )
        normal = replace(
            state.journey.completion_paths[0],
            requires_sent_facts=("final_confirmation", "postdelivery_status"),
        )
        degraded = replace(
            normal, id="text_only", requires_sent_facts=("final_confirmation",),
            requires_capabilities_unavailable=("complete_document_pipeline_available",),
        )
        journey = replace(state.journey, completion_paths=(normal, degraded))
        state = replace(
            state, journey=journey,
            capabilities={"complete_document_pipeline_available": True},
            sent_fact_ids=state.sent_fact_ids + ("final_confirmation",),
        )
        assessment = JourneyOracle(journey).evaluate(state)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "Scenario")
        self.assertEqual(assessment.failure_code, "completion_trigger_not_sent")
        # 真实进入可用的降级分支并送完其触发事实后，缺交付仍可归因 SUT。
        degraded_state = replace(
            state, capabilities={"complete_document_pipeline_available": False}
        )
        assessment = JourneyOracle(journey).evaluate(degraded_state)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "SUT/Skill")

    def _state(self, fixture_name: str, journey_name: str) -> JourneyPublicState:
        fixture = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        journey = load_journey(ROOT / "evals" / "model-led" / journey_name)
        return JourneyPublicState(
            journey=journey,
            capabilities=fixture["capabilities"],
            transcripts=tuple(fixture["transcripts"]),
            sent_fact_ids=tuple(fixture["sent_fact_ids"]),
            observed_milestones=frozenset(fixture["observed_milestones"]),
            restarted=fixture["restarted"],
            restart_context=fixture.get("restart_context"),
            observation=fixture["observation"],
        )

    def test_ml01_r23_without_final_confirmation_is_not_a_pass(self):
        state = self._state(
            "ml01-r23-false-pass.json",
            "codex-claude-wage-bonus-overtime.yaml",
        )
        assessment = JourneyOracle(state.journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "Scenario")
        self.assertEqual(assessment.failure_code, "completion_trigger_not_sent")

    def test_ml03_r2_without_post_restart_three_month_recovery_is_not_a_pass(self):
        state = self._state(
            "ml03-r2-false-pass.json",
            "codex-claude-interruption-correction-degradation.yaml",
        )
        assessment = JourneyOracle(state.journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertIn(assessment.failure_category, {"SUT/Skill", "Harness"})
        self.assertIn("milestone", " ".join(assessment.missing))

    def test_ml01_delivery_requires_explicit_confirmation_and_valid_delivery(self):
        state = self._state(
            "ml01-r23-false-pass.json",
            "codex-claude-wage-bonus-overtime.yaml",
        )
        observation = dict(state.observation)
        observation.update(
            {
                "docx": True,
                "checklist": True,
                "deliveries": [
                    {
                        "case_id": "case-0123456789abcdef01234567",
                        "document_type": "arbitration_application",
                        "mode": "candidate",
                        "document_path": "output/arbitration_application/a.docx",
                        "checklist_path": "output/arbitration_application/a.txt",
                        "case_archive_path": ".arbibuddy/cases/case-0123456789abcdef01234567/案情档案.md",
                        "archive_revision": 10,
                        "current_archive_revision": 10,
                    },
                    {
                        "case_id": "case-0123456789abcdef01234567",
                        "document_type": "evidence_catalog",
                        "mode": "candidate",
                        "document_path": "output/evidence_catalog/e.docx",
                        "checklist_path": "output/evidence_catalog/e.txt",
                        "case_archive_path": ".arbibuddy/cases/case-0123456789abcdef01234567/案情档案.md",
                        "archive_revision": 10,
                        "current_archive_revision": 10,
                    }
                ],
            }
        )
        completed = JourneyPublicState(
            journey=state.journey,
            capabilities=state.capabilities,
            transcripts=state.transcripts
            + (
                {
                    "turn": 18,
                    "assistant_response": "DOCX 文书和内部核验清单已按候选稿交付。",
                    "selected_fact_ids": ["final_confirmation"],
                },
            ),
            sent_fact_ids=state.sent_fact_ids + ("final_confirmation",),
            observed_milestones=state.observed_milestones,
            restarted=False,
            observation=observation,
        )
        assessment = JourneyOracle(completed.journey).evaluate(completed)

        self.assertTrue(assessment.passed, assessment.message)
        self.assertEqual(assessment.completion_path, "delivered")

    def test_ml01_accepts_the_application_and_its_required_evidence_catalog(self):
        state = self._state(
            "ml01-r23-false-pass.json",
            "codex-claude-wage-bonus-overtime.yaml",
        )
        observation = dict(state.observation)
        observation.update(
            {
                "docx": True,
                "checklist": True,
                "deliveries": [
                    {
                        "case_id": "case-0123456789abcdef01234567",
                        "document_type": "arbitration_application",
                        "mode": "candidate",
                        "document_path": "output/arbitration_application/a.docx",
                        "checklist_path": "output/arbitration_application/a.txt",
                        "case_archive_path": ".arbibuddy/cases/case-0123456789abcdef01234567/案情档案.md",
                        "archive_revision": 10,
                        "current_archive_revision": 10,
                    },
                    {
                        "case_id": "case-0123456789abcdef01234567",
                        "document_type": "evidence_catalog",
                        "mode": "candidate",
                        "document_path": "output/evidence_catalog/e.docx",
                        "checklist_path": "output/evidence_catalog/e.txt",
                        "case_archive_path": ".arbibuddy/cases/case-0123456789abcdef01234567/案情档案.md",
                        "archive_revision": 10,
                        "current_archive_revision": 10,
                    },
                ],
            }
        )
        completed = JourneyPublicState(
            journey=state.journey,
            capabilities=state.capabilities,
            transcripts=state.transcripts
            + (
                {
                    "turn": 18,
                    "assistant_response": "DOCX 文书和内部核验清单已按候选稿交付。",
                    "selected_fact_ids": ["final_confirmation"],
                },
            ),
            sent_fact_ids=state.sent_fact_ids + ("final_confirmation",),
            observed_milestones=state.observed_milestones,
            restarted=False,
            observation=observation,
        )
        assessment = JourneyOracle(completed.journey).evaluate(completed)

        self.assertTrue(assessment.passed, assessment.message)
        self.assertEqual(assessment.completion_path, "delivered")

    def test_permission_denied_output_observation_is_runtime_adapter_failure(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-termination-high-risk.yaml"
        )
        state = JourneyPublicState(
            journey=journey,
            capabilities={"complete_document_pipeline_available": True},
            transcripts=(
                {
                    "turn": 5,
                    "assistant_response": "风险、后果、确认候选；DOCX 通知书和清单。",
                    "selected_fact_ids": ["candidate_request"],
                },
            ),
            sent_fact_ids=("candidate_request",),
            observation={
                "case_archive": True,
                "docx": False,
                "checklist": False,
                "case_archive_count": 1,
                "docx_count": 0,
                "checklist_count": 0,
                "invalid_docx_count": 0,
                "invalid_delivery_count": 1,
                "permission_denied_archive_count": 0,
                "permission_denied_delivery_count": 1,
                "permission_denied_docx_count": 0,
                "case_archives": [],
                "deliveries": [],
                "managed_view_receipts": [],
            },
        )

        assessment = JourneyOracle(journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "Runtime Adapter")
        self.assertEqual(
            assessment.failure_code,
            "public_artifact_observer_permission_denied",
        )


if __name__ == "__main__":
    unittest.main()
