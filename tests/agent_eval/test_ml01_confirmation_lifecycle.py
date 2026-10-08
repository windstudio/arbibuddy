from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.model_led_agent_eval import load_journey, _build_scenario_response_request, _render_runtime_context
from scripts.model_led_agent_eval import CliAgentRuntime, RuntimeAdapterError, _foreign_skill_resource_hashes
from scripts.model_led_agent_eval.scenario_validation import (
    ScenarioResponseDecision, ScenarioResponseError, ScenarioResponseLedger,
    validate_scenario_response,
)


ROOT = Path(__file__).resolve().parents[2]
SUMMARY = (
    "4至6月工资未足额支付；这期季度绩效奖金未收到；同期有加班。"
    "本轮处理工资差额、季度绩效奖金和加班费，金额和材料缺口待核。请确认摘要准确吗？"
)


class Ml01ConfirmationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.journey = load_journey(
            ROOT / "evals/model-led/codex-claude-wage-bonus-overtime.yaml",
            as_of_date=date(2026, 9, 27),
        )
        self.initial = {fact_id: 1 for fact_id in self.journey.initial_fact_ids}

    def request(self, used, *, turn=2, response=SUMMARY):
        return _build_scenario_response_request(
            self.journey, response, [], used, 0, turn=turn,
        )

    @staticmethod
    def answer(*ids):
        return ScenarioResponseDecision("answer", ids, "high", "direct_question")

    def test_second_turn_summary_cannot_authorize_final_delivery(self):
        request = self.request(self.initial)
        with self.assertRaisesRegex(ScenarioResponseError, "确认前置事实未全部发送"):
            validate_scenario_response(request, self.answer("final_confirmation"))

    def test_early_summary_has_factual_confirmation_without_delivery_request(self):
        request = self.request(self.initial)
        rendered = ScenarioResponseLedger().apply(request, self.answer("initial_summary_confirmation"))
        self.assertIn("准确", rendered.message)
        self.assertNotIn("请按当前档案准备", rendered.message)
        self.assertIn("最终补充", rendered.message)

    def test_early_summary_confirmation_rejects_known_fact_conflict(self):
        request = self.request(
            self.initial,
            response=SUMMARY + " 摘要：没有发生加班。",
        )
        with self.assertRaisesRegex(ScenarioResponseError, "已知事实冲突"):
            validate_scenario_response(
                request, self.answer("initial_summary_confirmation")
            )

    def test_scenario_declares_early_summary_as_conflict_checked(self):
        request = self.request(self.initial)
        self.assertEqual(request.confirmation_conflict_fact_ids,
                         ("initial_summary_confirmation",))

    def test_updated_summary_can_be_confirmed_again_after_previous_confirmation(self):
        used = {**self.initial, "rights_scan_scope": 1, "final_supplement_none": 1,
                "initial_summary_confirmation": 1, "final_confirmation": 1, "last_demand": 1}
        request = self.request(used, turn=8)
        rendered = ScenarioResponseLedger().apply(request, self.answer("final_confirmation"))
        self.assertIn("三项处理范围确认", rendered.message)
        self.assertIn("待核", rendered.message)

    def test_repeat_confirmation_does_not_accept_known_fact_conflict(self):
        used = {**self.initial, "rights_scan_scope": 1, "final_supplement_none": 1,
                "final_confirmation": 1}
        request = self.request(used, turn=8, response=SUMMARY + "没有发生加班。")
        with self.assertRaisesRegex(ScenarioResponseError, "已知事实冲突"):
            validate_scenario_response(request, self.answer("final_confirmation"))

    def test_known_workplace_can_be_reaffirmed_after_initial_message(self):
        request = self.request(self.initial, response="请核对单位及主要工作地是否在上海？")
        rendered = ScenarioResponseLedger().apply(request, self.answer("party_and_workplace"))
        self.assertIn("上海市浦东新区", rendered.message)
        self.assertIn("地址我现在说不准", rendered.message)

    def test_runtime_context_binds_skill_entry_to_installed_copy(self):
        text = _render_runtime_context({"installed_skill_root": "C:/isolated/.agents/skills/arbibuddy"})
        self.assertIn("C:/isolated/.agents/skills/arbibuddy/SKILL.md", text)
        self.assertIn("同名", text)

    def test_codex_foreign_skill_read_is_reported_as_runtime_adapter_failure(self):
        import json
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            runtime = CliAgentRuntime(platform="codex", command=["fake-codex"], runtime_context={
                "workspace_root": str(workspace.resolve()),
                "installed_skill_root": str(workspace / ".agents/skills/arbibuddy"),
            })
            events = [
                {"type": "thread.started", "thread_id": "test-session"},
                {"type": "item.completed", "item": {"type": "command_execution",
                    "command": "Get-Content 'C:/Users/other/.agents/skills/arbibuddy/references/public-interface.md'", "exit_code": 0}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "已建档"}},
            ]
            result = SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(x) for x in events), stderr="")
            with patch("scripts.model_led_agent_eval.subprocess.run", return_value=result):
                with self.assertRaisesRegex(RuntimeAdapterError, "安装副本"):
                    runtime.turn(workspace=workspace, message="请帮我整理欠薪事实。")

    def test_current_installed_resource_and_agent_prose_do_not_trigger_source_failure(self):
        root = "C:/isolated workspace/.agents/skills/arbibuddy"
        events = [
            {"item": {"type": "command_execution", "command": f"Get-Content '{root}/SKILL.md'"}},
            {"item": {"type": "agent_message", "text": "C:/Users/other/.agents/skills/arbibuddy/SKILL.md"}},
        ]
        self.assertEqual(_foreign_skill_resource_hashes(events, root), ())

    def test_non_read_commands_that_mention_foreign_skill_do_not_trigger(self):
        foreign = "C:/Users/other/.agents/skills/arbibuddy"
        events = [
            {"item": {"type": "command_execution", "command": f"Write-Output '{foreign}/SKILL.md'"}},
            {"item": {"type": "command_execution", "command": f"Test-Path '{foreign}/SKILL.md'"}},
        ]
        self.assertEqual(
            _foreign_skill_resource_hashes(
                events, "C:/isolated/.agents/skills/arbibuddy"
            ),
            (),
        )

    def test_escaped_powershell_skill_path_is_also_detected(self):
        events = [{"item": {"type": "command_execution", "command":
            r'Get-Content "C:\\Users\\other\\.agents\\skills\\arbibuddy\\references\\public-interface.md"'}}]
        self.assertTrue(_foreign_skill_resource_hashes(events, "C:/isolated/.agents/skills/arbibuddy"))


if __name__ == "__main__":
    unittest.main()
