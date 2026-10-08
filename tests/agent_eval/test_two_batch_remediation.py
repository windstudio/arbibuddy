import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from scripts.case_archive import CaseArchive
from scripts.model_led_agent_eval import (
    JourneyOracle,
    JourneyPublicState,
    _observed_milestones,
    _summarize_runtime_events,
    load_journey,
)
from scripts.model_led_agent_eval.scenario_responder import (
    CodexSdkScenarioResponder,
    ReplayScenarioResponder,
    ScenarioFactOption,
    ScenarioResponseError,
    ScenarioResponseRequest,
    ScenarioResponderRuntimeError,
    _terminate_process_tree,
    _sdk_worker_main,
)
from scripts.model_led_agent_eval.scenario_worker import SdkWorkerProcess
from scripts.model_led_agent_eval import _actual_responder_identity


ROOT = Path(__file__).resolve().parents[2]


def _request() -> ScenarioResponseRequest:
    return ScenarioResponseRequest(
        scenario_id="repair-contract",
        persona="虚构劳动争议当事人",
        objective="如实回答当前问题",
        latest_agent_response="请说明劳动关系。",
        available_facts=(
            ScenarioFactOption("employment", "我已确认劳动关系事实。"),
        ),
        turn=2,
    )


def _worker_with_delayed_child(sentinel: str) -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; from pathlib import Path; time.sleep(2); "
            "Path(sys.argv[1]).write_text('late', encoding='utf-8')",
            sentinel,
        ]
    )
    time.sleep(30)


class TwoBatchModelBRemediationTests(unittest.TestCase):
    def test_case_archive_read_receipt_requires_success_and_current_revision(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户更正",
                    "changes": [
                        {"operation": "append", "record_type": "fact",
                         "content_markdown": "用户更正：公司拖欠三个月工资。"}
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            read_result = archive.read(case_id)
            command = (
                f'python -B -X utf8 -m scripts.case_archive.cli --root "{workspace}" '
                f"read {case_id}"
            )

            def summarize(result, *, exit_code=0):
                return _summarize_runtime_events(
                    ({"type": "item.completed", "item": {
                        "type": "command_execution", "command": command,
                        "exit_code": exit_code,
                        "aggregated_output": json.dumps(result, ensure_ascii=False),
                    }},),
                    workspace=workspace,
                )[0]

            self.assertTrue(summarize(read_result)["case_archive_read_succeeded"])
            failed = summarize(read_result, exit_code=1)
            self.assertFalse(failed["case_archive_read_succeeded"])
            wrong_operation = {**read_result, "operation": "commit"}
            self.assertFalse(summarize(wrong_operation)["case_archive_read_succeeded"])

            later = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 1,
                    "change_summary": "记录之后补充的事实",
                    "changes": [
                        {"operation": "append", "record_type": "fact",
                         "content_markdown": "欠薪月份仍待核。"}
                    ],
                }
            )
            self.assertTrue(later["ok"], later)
            self.assertFalse(summarize(read_result)["case_archive_read_succeeded"])

    def test_ml03_recovery_oracle_respects_recovery_only_response_contract(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        milestones = {milestone.id: milestone for milestone in journey.milestones}
        path = next(
            item
            for item in journey.completion_paths
            if item.id == "recovered_and_degraded"
        )

        self.assertTrue(milestones["archive_recovered_after_restart"].after_restart)
        self.assertTrue(
            milestones["archive_recovered_after_restart"].response_markers
        )
        self.assertEqual(
            milestones["archive_recovered_after_restart"].response_all, ()
        )
        self.assertIn("三个月", milestones["archive_recovered_after_restart"].archive_markers)
        self.assertFalse(milestones["degradation_explained"].after_restart)
        self.assertIn("degradation_explained", path.required_milestones)
        self.assertIn("degradation", path.required_response_groups_after_trigger)

    def test_ml03_recovery_milestone_accepts_an_archived_case_paraphrase(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            committed = archive.commit(
                {
                    "case_id": created["result"]["case_id"],
                    "expected_revision": 0,
                    "change_summary": "记录用户更正",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户更正：公司拖欠三个月工资，具体工资月份待核。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            transcripts = [
                {
                    "session_restarted": True,
                    "assistant_response": (
                        "我已从已保存的案情接续：公司拖欠三个月工资，"
                        "具体工资月份仍待核。"
                    ),
                    "runtime_event_summary": [
                        {
                            "event_type": "item.completed",
                            "item_type": "command_execution",
                            "python_module_names": ["scripts.case_archive.cli"],
                            "case_archive_read_succeeded": True,
                            "exit_code": 0,
                            "output_json_object_count": 1,
                        }
                    ],
                }
            ]

            observed = _observed_milestones(journey, workspace, transcripts)

            self.assertIn("archive_recovered_after_restart", observed)
            inaccurate = [{
                **transcripts[0],
                "assistant_response": "我已从已保存的案情接续：公司拖欠两个月工资。",
            }]
            self.assertNotIn(
                "archive_recovered_after_restart",
                _observed_milestones(journey, workspace, inaccurate),
            )
            failed_read = [{
                **transcripts[0],
                "runtime_event_summary": [{
                    "event_type": "item.completed",
                    "item_type": "command_execution",
                    "python_module_names": ["scripts.case_archive.cli"],
                    "case_archive_read_succeeded": False,
                    "exit_code": 1,
                    "output_json_object_count": 1,
                }],
            }]
            self.assertNotIn(
                "archive_recovered_after_restart",
                _observed_milestones(journey, workspace, failed_read),
            )

    def test_ml03_recovery_milestone_rejects_content_that_disagrees_with_current_fact(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit({
                "case_id": case_id,
                "expected_revision": 0,
                "change_summary": "记录欠薪月份数",
                "changes": [{"operation": "append", "record_type": "fact",
                             "content_markdown": "公司拖欠三个月工资。"}],
            })
            self.assertTrue(committed["ok"], committed)
            corrected = archive.commit({
                "case_id": case_id,
                "expected_revision": 1,
                "change_summary": "用户更正欠薪月份数",
                "changes": [{"operation": "replace", "record_type": "fact",
                             "record_id": "F-001", "reason": "用户更正此前说法",
                             "content_markdown": "公司拖欠两个月工资。"}],
            })
            self.assertTrue(corrected["ok"], corrected)
            markdown = archive.read(case_id)["result"]["markdown"]
            current_facts = markdown.split("## 事实\n", 1)[1].split(
                "## 主张与权益\n", 1
            )[0]
            self.assertIn("公司拖欠两个月工资", current_facts)
            self.assertNotIn("公司拖欠三个月工资", current_facts)
            transcripts = [{
                "session_restarted": True,
                "assistant_response": "我已从已保存的案情接续：公司拖欠三个月工资。",
                "runtime_event_summary": [{
                    "event_type": "item.completed", "item_type": "command_execution",
                    "python_module_names": ["scripts.case_archive.cli"],
                    "case_archive_read_succeeded": True, "exit_code": 0,
                    "output_json_object_count": 1,
                }],
            }]
            self.assertNotIn(
                "archive_recovered_after_restart",
                _observed_milestones(journey, workspace, transcripts),
            )

    def test_windows_spawn_config_carries_canary_before_allowlist(self):
        class FakeChild:
            def close(self):
                return None

        class FakeProcess:
            def __init__(self, *, args):
                self.args = args

            def start(self):
                return None

            def is_alive(self):
                return False

        class FakeContext:
            def __init__(self):
                self.process = None

            def Pipe(self, *, duplex):
                self.assert_duplex = duplex
                return object(), FakeChild()

            def Process(self, *, target, args, name, daemon):
                self.process = FakeProcess(args=args)
                return self.process

        with TemporaryDirectory() as temp:
            worker = SdkWorkerProcess(
                workspace=Path(temp),
                model="gpt-5.6-luna",
                timeout_seconds=1,
                effort=None,
                codex_bin=None,
                env={"PATH": "test-path"},
                agent_workspace=Path(temp) / "agent",
            )
            worker.configure_canaries(
                secret_name="ARBI_B_SECRET_CANARY_TEST",
                secret_value="secret-value",
                workspace_canary=None,
            )
            context = FakeContext()
            with (
                patch(
                    "scripts.model_led_agent_eval.scenario_worker.multiprocessing.get_context",
                    return_value=context,
                ),
                patch.object(
                    worker,
                    "_request",
                    return_value={
                        "ok": True,
                        "thread_id": "worker-thread",
                        "isolation_canary": "passed",
                        "canary_results": {},
                    },
                ),
            ):
                worker.start()

        self.assertTrue(context.assert_duplex)
        config = context.process.args[1]
        self.assertNotIn("ARBI_B_SECRET_CANARY_TEST", config["env"])
        self.assertEqual(config["secret_canary_name"], "ARBI_B_SECRET_CANARY_TEST")
        self.assertEqual(config["secret_canary_value"], "secret-value")
        self.assertNotIn("ARBI_B_SECRET_CANARY_TEST", worker.env)

    def test_replay_rejects_a_bare_decision_without_all_bindings(self):
        with self.assertRaisesRegex(ScenarioResponseError, "绑定字段"):
            ReplayScenarioResponder(
                [
                    {
                        "action": "answer",
                        "selected_fact_ids": ["employment"],
                        "confidence": "high",
                        "reason_code": "direct_question",
                    }
                ]
            )

    def test_semantic_repair_cannot_replace_unknown_fact_with_another_fact(self):
        class FakeThread:
            calls = 0

            def run(self, prompt, **kwargs):
                self.calls += 1
                selected = ["unknown"] if self.calls == 1 else ["employment"]
                return type(
                    "Result",
                    (),
                    {
                        "final_response": json.dumps(
                            {
                                "action": "answer",
                                "selected_fact_ids": selected,
                                "confidence": "high",
                                "reason_code": "direct_question",
                            }
                        )
                    },
                )()

        class FakeCodex:
            def __init__(self, config):
                self.thread = FakeThread()

            def thread_start(self, **kwargs):
                return self.thread

        with TemporaryDirectory() as temp:
            responder = CodexSdkScenarioResponder(
                workspace=Path(temp),
                model="gpt-5.6-luna",
                codex_factory=FakeCodex,
            )
            with self.assertRaises(ScenarioResponderRuntimeError) as raised:
                responder.respond(_request())

        self.assertEqual(raised.exception.details["failure_kind"], "semantic_invalid")
        self.assertEqual(
            raised.exception.details["attempts"][0]["selected_fact_ids"],
            ["unknown"],
        )
        self.assertEqual(
            raised.exception.details["attempts"][1]["selected_fact_ids"],
            ["employment"],
        )

    def test_actual_identity_never_falls_back_to_expected_cli_parameters(self):
        identity = _actual_responder_identity(
            None,
            responder_mode="model",
            provider="codex-sdk",
            model="expected-only",
        )

        self.assertFalse(identity["identity_verified"])
        self.assertEqual(identity["mode"], "unknown")
        self.assertEqual(identity["provider"], "unknown")
        self.assertIsNone(identity["model"])

    def test_ml01_degraded_path_requires_boundary_and_recovery_steps(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-wage-bonus-overtime.yaml"
        )
        state = JourneyPublicState(
            journey=journey,
            capabilities={"complete_document_pipeline_available": False},
            transcripts=(
                {"turn": 1, "assistant_response": "案情档案已保存。"},
                {
                    "turn": 2,
                    "assistant_response": "DOCX 无法提供。",
                    "selected_fact_ids": ["final_confirmation"],
                },
            ),
            sent_fact_ids=("final_confirmation",),
            observation={"case_archive": True, "docx": False, "checklist": False},
        )

        assessment = JourneyOracle(journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failure_category, "SUT/Skill")

    def test_ml03_recovery_context_must_match_latest_public_archive_revision(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        state = JourneyPublicState(
            journey=journey,
            transcripts=(
                {"turn": 1, "assistant_response": "三个月已修正并保存。"},
                {
                    "turn": 2,
                    "assistant_response": "已从当前档案接续，三个月仍是当前事实；无法核验，待核实。",
                    "session_restarted": True,
                    "selected_fact_ids": ["degradation"],
                },
            ),
            sent_fact_ids=("degradation",),
            observed_milestones=frozenset(
                {
                    "correction_committed",
                    "archive_recovered_after_restart",
                    "degradation_explained",
                }
            ),
            restarted=True,
            restart_context={
                "status": "available",
                "case_id": "case-111111111111111111111111",
                "archive_revision": 1,
            },
            restart_observation={
                "case_archives": [
                    {
                        "case_id": "case-111111111111111111111111",
                        "archive_revision": 2,
                    }
                ]
            },
            observation={
                "case_archive": True,
                "case_archives": [
                    {
                        "case_id": "case-111111111111111111111111",
                        "archive_revision": 5,
                    }
                ],
            },
            capabilities={"complete_document_pipeline_available": False},
        )

        assessment = JourneyOracle(journey).evaluate(state)

        self.assertFalse(assessment.passed)
        self.assertIn("revision", " ".join(assessment.missing))

    def test_ml03_restart_snapshot_remains_valid_after_later_archive_writes(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        case_id = "case-222222222222222222222222"
        archive_path = f".arbibuddy/cases/{case_id}/案情档案.md"
        state = JourneyPublicState(
            journey=journey,
            transcripts=(
                {"turn": 1, "assistant_response": "档案已更正为三个月。"},
                {
                    "turn": 2,
                    "assistant_response": "已从当前档案接续，三个月仍是当前事实；地方规则待核实。",
                    "session_restarted": True,
                    "selected_fact_ids": ["degradation"],
                },
            ),
            sent_fact_ids=("degradation",),
            observed_milestones=frozenset(
                {
                    "correction_committed",
                    "archive_recovered_after_restart",
                    "degradation_explained",
                }
            ),
            restarted=True,
            restart_context={
                "status": "available",
                "case_id": case_id,
                "archive_revision": 2,
                "archive_path": archive_path,
                "archive_sha256": "archive-at-restart",
                "confirmed_fact_summary_sha256": "facts-at-restart",
            },
            capabilities={"complete_document_pipeline_available": False},
            restart_observation={
                "case_archive": True,
                "case_archives": [
                    {
                        "case_id": case_id,
                        "archive_revision": 2,
                        "archive_path": archive_path,
                        "archive_sha256": "archive-at-restart",
                        "confirmed_fact_summary_sha256": "facts-at-restart",
                    }
                ],
            },
            observation={
                "case_archive": True,
                "invalid_delivery_count": 0,
                "case_archives": [
                    {
                        "case_id": case_id,
                        "archive_revision": 5,
                        "archive_path": archive_path,
                        "archive_sha256": "archive-after-resume-writes",
                        "confirmed_fact_summary_sha256": "facts-after-resume-writes",
                    }
                ],
            },
        )

        assessment = JourneyOracle(journey).evaluate(state)

        self.assertTrue(assessment.passed, assessment.message)
        self.assertEqual(assessment.completion_path, "recovered_and_degraded")

    def test_ml03_waiting_for_verification_synonym_is_counted_after_degradation_trigger(self):
        journey = load_journey(
            ROOT
            / "evals"
            / "model-led"
            / "codex-claude-interruption-correction-degradation.yaml"
        )
        case_id = "case-333333333333333333333333"
        archive_path = f".arbibuddy/cases/{case_id}/案情档案.md"
        archive = {
            "case_id": case_id,
            "archive_revision": 2,
            "archive_path": archive_path,
            "archive_sha256": "archive-at-restart",
            "confirmed_fact_summary_sha256": "facts-at-restart",
        }
        context = {
            "status": "available",
            "case_id": case_id,
            "archive_revision": 2,
            "archive_path": archive_path,
            "archive_sha256": "archive-at-restart",
            "confirmed_fact_summary_sha256": "facts-at-restart",
        }
        common = {
            "journey": journey,
            "sent_fact_ids": ("initial_facts", "correction", "degradation"),
            "observed_milestones": frozenset(
                {
                    "correction_committed",
                    "archive_recovered_after_restart",
                    "degradation_explained",
                }
            ),
            "restarted": True,
            "restart_context": context,
            "observation": {"case_archive": True, "case_archives": [archive]},
            "restart_observation": {"case_archive": True, "case_archives": [archive]},
        }
        before_trigger = JourneyPublicState(
            **common,
            transcripts=(
                {
                    "turn": 1,
                    "assistant_response": "案情档案已保存，欠薪原记为两个月。",
                    "selected_fact_ids": ["initial_facts"],
                },
                {
                    "turn": 2,
                    "assistant_response": "已更正并保存为三个月。",
                    "selected_fact_ids": ["correction"],
                },
                {
                    "turn": 3,
                    "assistant_response": "我已从当前档案恢复为三个月；地方规则仍待核验。",
                    "session_restarted": True,
                    "selected_fact_ids": [],
                },
                {
                    "turn": 4,
                    "assistant_response": "可以继续分析现有事实，高风险定稿先暂缓，下一步请先核对工资记录。",
                    "selected_fact_ids": ["degradation"],
                },
            ),
        )
        before_assessment = JourneyOracle(journey).evaluate(before_trigger)
        self.assertFalse(before_assessment.passed)
        self.assertIn(
            "response_group_after_trigger:degradation",
            before_assessment.missing,
        )

        after_trigger = JourneyPublicState(
            **common,
            transcripts=(
                *before_trigger.transcripts[:-1],
                {
                    "turn": 4,
                    "assistant_response": (
                        "地方规则仍待核验；可以继续分析现有事实，高风险定稿先暂缓，"
                        "下一步请先核对工资记录。"
                    ),
                    "selected_fact_ids": ["degradation"],
                },
            ),
        )
        assessment = JourneyOracle(journey).evaluate(after_trigger)

        self.assertTrue(assessment.passed, assessment.message)
        self.assertEqual(assessment.completion_path, "recovered_and_degraded")

    def test_claude_command_has_no_full_permission_bypass(self):
        from scripts.model_led_agent_eval import CliAgentRuntime

        runtime = CliAgentRuntime(
            platform="claude-code",
            command=["claude"],
            model="sonnet",
        )
        runtime.session_id = "session"
        start = runtime._start_command(Path("C:/isolated/workspace"), "继续")
        resume = runtime._resume_command(Path("C:/isolated/workspace"), "继续")

        self.assertNotIn("--dangerously-skip-permissions", start)
        self.assertNotIn("--dangerously-skip-permissions", resume)
        self.assertIn("--permission-mode", start)
        self.assertNotIn("--permission-prompts", start)
        self.assertIn("--allowed-tools", start)

    def test_timeout_termination_kills_worker_and_delayed_child(self):
        with TemporaryDirectory() as temp:
            sentinel = str(Path(temp) / "late-sentinel.txt")
            context = multiprocessing.get_context("spawn")
            worker = context.Process(
                target=_worker_with_delayed_child,
                args=(sentinel,),
            )
            worker.start()
            time.sleep(0.3)
            _terminate_process_tree(worker)
            time.sleep(2.3)

            self.assertFalse(worker.is_alive())
            self.assertFalse(Path(sentinel).exists())

    def test_worker_canary_is_parent_observed_and_not_returned_as_secret(self):
        class FakeThread:
            id = "fake-thread"

            def run(self, *_args, **_kwargs):
                return type(
                    "Result",
                    (),
                    {"final_response": secret_value},
                )()

        class FakeCodex:
            def __init__(self, _config):
                self.thread = FakeThread()

            def thread_start(self, **_kwargs):
                return self.thread

            def close(self):
                return None

        fake_module = type(
            "FakeCodexModule",
            (),
            {
                "CodexConfig": lambda **kwargs: kwargs,
                "Codex": FakeCodex,
                "__version__": "sdk-test",
            },
        )

        class FakeConnection:
            def __init__(self):
                self.requests = [{"op": "start"}, {"op": "close"}]
                self.sent = []

            def recv(self):
                return self.requests.pop(0)

            def send(self, value):
                self.sent.append(value)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            responder_workspace = root / "model-b"
            agent_workspace = root / "model-a"
            responder_workspace.mkdir()
            agent_workspace.mkdir()
            secret_name = "ARBI_B_SECRET_CANARY_TEST"
            secret_value = "secret-value-that-must-not-leak"
            canary = agent_workspace / "canary.txt"
            canary.write_text(secret_value, encoding="utf-8")
            connection = FakeConnection()
            connection.requests = [{"op": "start"}, {"op": "run", "prompt": "safe"}, {"op": "close"}]
            with (
                patch.dict(
                    sys.modules,
                    {"openai_codex": fake_module},
                ),
                patch.dict(os.environ, {secret_name: secret_value}, clear=False),
            ):
                _sdk_worker_main(
                    connection,
                    {
                        "workspace": str(responder_workspace),
                        "model": "test-model",
                        "effort": None,
                        "codex_bin": None,
                        "env": {"PATH": os.environ.get("PATH", "")},
                        "secret_canary_name": secret_name,
                        "secret_canary_value": secret_value,
                        "workspace_canary": str(canary),
                    },
                )

        encoded = json.dumps(connection.sent, ensure_ascii=False)
        self.assertNotIn(secret_value, encoded)
        self.assertTrue(connection.sent[0]["ok"])
        self.assertEqual(connection.sent[0]["isolation_canary"], "passed")
        self.assertTrue(connection.sent[0]["canary_results"]["secret_env"]["parent_placed"])
        self.assertTrue(
            connection.sent[0]["canary_results"]["secret_env"][
                "child_env_absent_after_allowlist"
            ]
        )
        self.assertTrue(
            connection.sent[0]["canary_results"]["workspace"][
                "outside_responder_workspace"
            ]
        )
        self.assertEqual(
            connection.sent[1]["failure_kind"], "isolation_canary_failed"
        )


if __name__ == "__main__":
    unittest.main()
