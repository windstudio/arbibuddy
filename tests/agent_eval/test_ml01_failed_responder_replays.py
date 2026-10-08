from __future__ import annotations

from pathlib import Path
from datetime import date
import unittest

from scripts.model_led_agent_eval import (
    _build_scenario_response_request,
    load_journey,
)
from scripts.model_led_agent_eval.scenario_validation import (
    ScenarioResponseDecision,
    ScenarioResponseError,
    ScenarioResponseLedger,
    build_scenario_responder_prompt,
    validate_scenario_response,
)


ROOT = Path(__file__).resolve().parents[2]
SCOPE_QUESTION = (
    "除了工资、季度绩效奖金和加班费，这些方向或清单外的事实、材料中，"
    "是否有你希望补充、暂缓或一并处理的事项？可以直接列出，也可以回答"
    "“没有”或“暂不确定”。"
)
PARTIAL_CONFIRMATION = "我已把工资、奖金和加班争议记入档案，金额及证据缺口标为待核。是否按这个方案继续？"
COMPLETE_CONFIRMATION = (
    "我已核对摘要：你于2024年3月1日入职上海澄明云科信息技术有限公司，任产品经理，主要在浦东工作，合同月薪12000元；"
    "2025年8月31日离职。2025年6月至8月工资未足额发放，但12000元是否为固定月薪总额、有无津贴提成及每月应发、到账和差额仍待核。"
    "合同约定季度绩效奖金，本次主张的一期未付；具体季度、发放条件、金额以及以往奖金记录待查。6月至8月有加班，部分由领导在工作群安排；具体日期、时数、审批记录和工时制度待核。"
    "现有材料包括劳动合同、部分工资流水和工资条、考勤截图、项目群聊天、内部邮件及催款记录。离职方式及手续、单位地址、姓名证件和联系方式待补；"
    "离职后半年内追讨过工资，最后催要日期和公司回复待查，尚未仲裁、起诉或调解。"
    "本次范围是工资差额、这期季度奖金和加班费，其他方向暂不纳入；先说明材料支持、金额缺口与补证方向，准备带待核项的仲裁申请候选稿、证据目录和提交前核对清单，暂不定稿。"
    "请你确认：上面的摘要准确吗？范围与口径是否按此执行？"
)
ACTUAL_INCOMPLETE_CONFIRMATION = "请你确认：上面的摘要准确吗？范围与口径是否按此执行？"
ACTUAL_CONFIRMATION_VARIANTS = (
    ACTUAL_INCOMPLETE_CONFIRMATION,
    "请确认：这次的摘要准确吗？范围与口径按此执行吗？",
    "记录有记错的地方吗？全对的话，请回复「没有记错」。",
)


class Ml01FailedResponderReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.journey = load_journey(
            ROOT / "evals/model-led/codex-claude-wage-bonus-overtime.yaml",
            # 保留的历史对话早于相对日期场景；显式按历史 2025 日期回放，
            # 当前 2026 场景由单独的固定日期回归覆盖。
            as_of_date=date(2025, 11, 27),
        )

    def _request(
        self,
        latest_response: str,
        *,
        turn: int,
        unavailable_uses: int,
        used_fact_ids: tuple[str, ...] | None = None,
        after_final_supplement: bool = False,
    ):
        used_facts = {
            fact_id: 1
            for fact_id in (
                self.journey.initial_fact_ids
                if used_fact_ids is None
                else used_fact_ids
            )
        }
        if after_final_supplement:
            # Historical wording fixtures test summary semantics after collection.
            used_facts.update(rights_scan_scope=1, final_supplement_none=1)
        return _build_scenario_response_request(
            self.journey,
            latest_response,
            [],
            used_facts,
            unavailable_uses,
            turn=turn,
        )

    def test_claude_v11_natural_confirmation_is_accepted_without_literal_marker(self):
        summary = (
            ROOT
            / "tests/agent_eval/fixtures/model_led/ml01-claude-v11-turn6-confirmation.txt"
        ).read_text(encoding="utf-8")
        request = self._request(summary, turn=7, unavailable_uses=1, after_final_supplement=True)
        self.assertFalse(
            any(
                marker.casefold() in summary.casefold()
                for marker in request.confirmation_request_markers
            )
        )
        validated = validate_scenario_response(
            request,
            ScenarioResponseDecision(
                "answer", ("final_confirmation",), "high", "direct_question"
            ),
        )
        self.assertEqual(
            tuple(fact.id for fact in validated.selected_facts),
            ("final_confirmation",),
        )

    def test_scope_question_has_dedicated_unsent_answer(self):
        request = self._request(SCOPE_QUESTION, turn=3, unavailable_uses=1)
        scope = next(fact for fact in request.available_facts if fact.id == "rights_scan_scope")

        self.assertIn("analysis_scope", self.journey.initial_fact_ids)
        self.assertNotIn("rights_scan_scope", self.journey.initial_fact_ids)
        self.assertEqual(scope.used_count, 0)
        self.assertEqual(scope.max_uses, 1)
        self.assertIn("其他方向暂不纳入", scope.message)
        self.assertIn("rights_scan_scope", request.objective)

        rendered = ScenarioResponseLedger().apply(
            request,
            ScenarioResponseDecision(
                "answer", ("rights_scan_scope",), "high", "direct_question"
            ),
        )
        self.assertEqual(rendered.message, scope.message)
        self.assertEqual(rendered.action, "answer")

    def test_plan_confirmation_phrase_allows_final_confirmation_card(self):
        request = self._request(COMPLETE_CONFIRMATION, turn=4, unavailable_uses=2, after_final_supplement=True)
        confirmation = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )
        rendered = ScenarioResponseLedger().apply(request, confirmation)
        self.assertIn("确认", rendered.message)
        self.assertNotIn("2025 年 6–8 月", rendered.message)

    def test_final_supplement_has_dedicated_unsent_answer(self):
        request = self._request("还有其他事实或材料要补充吗？", turn=3, unavailable_uses=1)
        supplement = next(
            fact for fact in request.available_facts if fact.id == "final_supplement_none"
        )
        self.assertEqual(supplement.used_count, 0)
        self.assertIn("没有其他事实或材料要补充", supplement.message)
        rendered = ScenarioResponseLedger().apply(
            request,
            ScenarioResponseDecision(
                "answer", ("final_supplement_none",), "high", "direct_question"
            ),
        )
        self.assertEqual(rendered.message, supplement.message)

    def test_scope_question_prompt_routes_to_scope_answer_not_confirmation(self):
        request = self._request(SCOPE_QUESTION, turn=3, unavailable_uses=1)
        prompt = build_scenario_responder_prompt(request)
        scope = ScenarioResponseDecision(
            "answer", ("rights_scan_scope",), "high", "direct_question"
        )

        validated = validate_scenario_response(request, scope)

        self.assertIn("权益扫描和补充询问不是确认请求", prompt)
        self.assertEqual(
            tuple(fact.id for fact in validated.selected_facts),
            ("rights_scan_scope",),
        )

    def test_obvious_summary_gap_prompts_for_semantic_correction(self):
        request = self._request(
            PARTIAL_CONFIRMATION, turn=6, unavailable_uses=2
        )
        prompt = build_scenario_responder_prompt(request)
        revision = next(
            fact
            for fact in request.available_facts
            if fact.id == "confirmation_summary_revision_requested"
        )
        correction = ScenarioResponseDecision(
            "answer", (revision.id,), "high", "correction"
        )
        validated = validate_scenario_response(request, correction)

        self.assertIn("明显缺失时，优先选补正事实卡", prompt)
        self.assertEqual(revision.max_uses, 1)
        self.assertIn("简要补进摘要", revision.message)
        rendered = ScenarioResponseLedger().apply(
            request, validated.decision
        )
        self.assertEqual(rendered.message, revision.message)

    def test_confirmation_summary_rules_are_semantic_prompt_hints(self):
        for wording in ACTUAL_CONFIRMATION_VARIANTS:
            with self.subTest(wording=wording):
                request = self._request(wording, turn=6, unavailable_uses=2)
                prompt = build_scenario_responder_prompt(request)
                self.assertIn("词组只是各组语义的例子", prompt)
                self.assertIn("确认摘要在语义上覆盖各组核心内容", prompt)

    def test_complete_summary_and_proceed_phrase_allow_confirmation(self):
        request = self._request(
            COMPLETE_CONFIRMATION, turn=7, unavailable_uses=2, after_final_supplement=True
        )
        decision = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )

        validated = validate_scenario_response(request, decision)

        self.assertEqual(
            tuple(fact.id for fact in validated.selected_facts),
            ("final_confirmation",),
        )
        self.assertEqual(
            len(request.prompt_payload()["confirmation_summary_required_groups"]),
            len(self.journey.simulator.confirmation_summary_required_groups),
        )
        self.assertIn(
            "confirmation_summary_required_groups",
            build_scenario_responder_prompt(request),
        )

    def test_actual_claude_run2_confirmation_summaries_allow_confirmation(self):
        confirmation = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )
        for turn in (6, 7):
            with self.subTest(turn=turn):
                summary = (
                    ROOT
                    / "tests/agent_eval/fixtures/model_led"
                    / f"ml01-claude-run2-turn{turn}-confirmation.txt"
                ).read_text(encoding="utf-8")
                request = self._request(
                    summary, turn=turn + 1, unavailable_uses=turn - 2, after_final_supplement=True
                )
                validated = validate_scenario_response(request, confirmation)
                self.assertEqual(
                    tuple(fact.id for fact in validated.selected_facts),
                    ("final_confirmation",),
                )

    def test_semantic_synonyms_are_accepted_but_known_conflict_is_rejected(self):
        summary = (
            "2025 年 6 月至 8 月企业支付的劳动报酬少于应付金额，合同约定的本期绩效报酬尚未到账，"
            "这几个月我做过延时劳动。本次仅处理少发工资、尚未到账的本期绩效报酬和延时劳动报酬；"
            "现有劳动合同、部分转账凭证和考勤资料可供核对，具体期间、金额和记录仍待查。"
            "上述事实与本轮安排是否可以？"
        )
        confirmation = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )
        request = self._request(summary, turn=7, unavailable_uses=4, after_final_supplement=True)
        groups = request.confirmation_summary_required_groups
        self.assertTrue(groups)
        self.assertFalse(
            any(
                marker.casefold() in summary.casefold()
                for _, markers in groups
                for marker in markers
            ),
            "synonym fixture should not rely on the old literal markers",
        )
        self.assertEqual(
            tuple(fact.id for fact in validate_scenario_response(request, confirmation).selected_facts),
            ("final_confirmation",),
        )

        conflict_request = self._request(
            summary + "\n没有发生加班。", turn=7, unavailable_uses=4, after_final_supplement=True
        )
        with self.assertRaisesRegex(ScenarioResponseError, "已知事实冲突"):
            validate_scenario_response(conflict_request, confirmation)

    def test_confirmation_is_rejected_until_prerequisite_facts_were_sent(self):
        request = self._request(
            COMPLETE_CONFIRMATION,
            turn=7,
            unavailable_uses=2,
            used_fact_ids=(),
        )
        confirmation = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )

        with self.assertRaisesRegex(ScenarioResponseError, "确认前置事实未全部发送"):
            validate_scenario_response(request, confirmation)

    def test_v9_real_summary_does_not_require_another_revision(self):
        summary = (
            ROOT
            / "tests/agent_eval/fixtures/model_led/ml01-claude-v9-run1-turn4-incomplete-confirmation.txt"
        ).read_text(encoding="utf-8")
        request = self._request(summary, turn=5, unavailable_uses=3, after_final_supplement=True)
        validated = validate_scenario_response(
            request,
            ScenarioResponseDecision(
                "answer", ("final_confirmation",), "high", "direct_question"
            ),
        )
        self.assertEqual(
            tuple(fact.id for fact in validated.selected_facts),
            ("final_confirmation",),
        )

    def test_claude_v10_real_summaries_are_sufficient_for_user_confirmation(self):
        confirmation = ScenarioResponseDecision(
            "answer", ("final_confirmation",), "high", "direct_question"
        )
        for turn in (4, 5, 7, 8):
            with self.subTest(turn=turn):
                summary = (
                    ROOT
                    / "tests/agent_eval/fixtures/model_led"
                    / f"ml01-claude-v10-turn{turn}-confirmation.txt"
                ).read_text(encoding="utf-8")
                request = self._request(summary, turn=turn + 1, unavailable_uses=1, after_final_supplement=True)
                self.assertEqual(
                    tuple(
                        fact.id
                        for fact in validate_scenario_response(
                            request, confirmation
                        ).selected_facts
                    ),
                    ("final_confirmation",),
                )


if __name__ == "__main__":
    unittest.main()
