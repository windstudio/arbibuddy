from __future__ import annotations

from pathlib import Path
import unittest

from scripts.model_led_agent_eval import (
    _build_scenario_response_request,
    load_journey,
)
from scripts.model_led_agent_eval.conversation_report import (
    render_evidence_report,
    render_workbuddy_report,
)
from scripts.model_led_agent_eval.scenario_validation import (
    ScenarioResponseDecision,
    ScenarioResponseLedger,
)


ROOT = Path(__file__).resolve().parents[2]


class JourneyConversationReportTests(unittest.TestCase):
    def test_seventh_distinct_unknown_does_not_stop_ml01(self):
        journey = load_journey(
            ROOT / "evals/model-led/codex-claude-wage-bonus-overtime.yaml"
        )
        ledger = ScenarioResponseLedger()
        used_facts = {fact_id: 1 for fact_id in journey.initial_fact_ids}
        for used in range(7):
            request = _build_scenario_response_request(
                journey,
                f"第 {used + 1} 个尚未声明的独立细节是什么？",
                [],
                used_facts,
                used,
                turn=used + 2,
            )
            self.assertIsNone(request.remaining_unavailable_uses)
            response = ledger.apply(
                request,
                ScenarioResponseDecision(
                    "unavailable", (), "high", "no_matching_fact"
                ),
            )
            self.assertEqual(response.action, "unavailable")
            self.assertIn("待核", response.message)
        self.assertEqual(ledger.snapshot()["unavailable"], 7)

    def test_initial_fact_ids_are_counted_and_later_authorizations_are_not(self):
        ml01 = load_journey(
            ROOT / "evals/model-led/codex-claude-wage-bonus-overtime.yaml"
        )
        ml02 = load_journey(
            ROOT / "evals/model-led/codex-claude-termination-high-risk.yaml"
        )
        self.assertEqual(
            set(ml01.simulator.confirmation_prerequisite_fact_ids) - set(ml01.initial_fact_ids),
            {"rights_scan_scope", "final_supplement_none"},
        )
        self.assertNotIn("final_confirmation", ml01.initial_fact_ids)
        self.assertNotIn("candidate_request", ml02.initial_fact_ids)
        self.assertNotIn("finalization_request", ml02.initial_fact_ids)
        self.assertIn("不准备现在签字、发通知或授权起草通知书", ml02.initial_user_message)

    def test_evidence_report_shows_turns_and_unsent_stop(self):
        report = render_evidence_report(
            {
                "journey_id": "ML01-wage-bonus-overtime",
                "platform": "codex",
                "started_at": "2026-09-25T00:00:00Z",
                "transcript": [
                    {
                        "turn": 1,
                        "session_id": "session-1",
                        "user_message": "初始案情",
                        "assistant_response": "请补充",
                        "selected_fact_ids": ["wage_dispute"],
                    }
                ],
                "scenario_responder": [
                    {
                        "turn": 2,
                        "action": "stop",
                        "result_code": "stop:safety_stop",
                    }
                ],
                "attribution": {"category": "Scenario", "message": "测试停止"},
            }
        )
        for term in (
            "ML01-wage-bonus-overtime / codex",
            "第 1 轮",
            "**用户**",
            "初始案情",
            "**助手**",
            "请补充",
            "第 2 轮未送达用户",
            "测试停止",
        ):
            self.assertIn(term, report)

    def test_evidence_report_shows_responder_message_when_agent_times_out(self):
        report = render_evidence_report(
            {
                "journey_id": "ML01-wage-bonus-overtime",
                "platform": "codex",
                "transcript": [
                    {
                        "turn": 1,
                        "user_message": "初始案情",
                        "assistant_response": "请确认摘要",
                    }
                ],
                "scenario_responder": [
                    {
                        "turn": 2,
                        "status": "accepted",
                        "action": "answer",
                        "selected_fact_ids": ["final_confirmation"],
                        "rendered_message": "这是已接受但没有等到助手回复的确认文本。",
                    }
                ],
                "attribution": {
                    "category": "Runtime Adapter",
                    "message": "平台 Agent 轮次超时",
                },
            }
        )

        self.assertIn("第 2 轮（助手响应未完成）", report)
        self.assertIn("**用户**", report)
        self.assertIn("这是已接受但没有等到助手回复的确认文本。", report)
        self.assertIn("场景事实 ID：final_confirmation", report)
        self.assertIn("平台 Agent 轮次超时", report)

    def test_workbuddy_report_shows_sessions_and_public_messages_only(self):
        report = render_workbuddy_report(
            [
                {"sessionId": "one", "type": "user_message", "content": "建档"},
                {"sessionId": "one", "role": "assistant", "content": [{"text": "已记录"}]},
                {"sessionId": "one", "role": "assistant", "type": "function_call", "content": "秘密工具参数"},
                {"sessionId": "two", "type": "user_message", "content": "恢复"},
                {"sessionId": "two", "type": "assistant_message", "content": "已恢复"},
            ],
            journey_id="ML03-v2",
        )
        for term in ("ML03-v2 / WorkBuddy", "会话 1", "会话 2", "第 1 轮", "第 2 轮", "已恢复"):
            self.assertIn(term, report)
        self.assertNotIn("秘密工具参数", report)


if __name__ == "__main__":
    unittest.main()
