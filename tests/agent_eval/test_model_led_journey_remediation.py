from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.case_archive import CaseArchive
from scripts.model_led_agent_eval import (
    CliAgentRuntime,
    JourneyResult,
    RuntimeAdapterError,
    ScenarioResponderRuntimeError,
    _has_successful_case_archive_read,
    _existing_archive_fact_list,
    _persistence_claim_references_existing_archive,
    _probe_runtime_persistence_claim,
    _runtime_process_environment,
    load_journey,
    run_journey,
)
from scripts.model_led_agent_eval.scenario_contract import (
    ResponderIdentity,
    ScenarioAttempt,
    ScenarioResponseResult,
    scenario_request_sha256,
)
from scripts.model_led_agent_eval.scenario_responder import ScenarioResponseDecision
from scripts.model_led_agent_eval.cli import _suite_model
from scripts.platform_adapters.service import _probe_word, install_skill


ROOT = Path(__file__).resolve().parents[2]


def _capability_report(*, complete_document_pipeline_available: bool = True) -> dict:
    available = {"available": True, "state": "可用", "method": "deterministic test"}
    unavailable = {"available": False, "state": "不可用", "method": "deterministic test"}
    word = available if complete_document_pipeline_available else unavailable
    structure = available if complete_document_pipeline_available else unavailable
    return {
        "capabilities": {
            "filesystem": available,
            "network": {"available": False, "state": "未测试", "method": "test"},
            "scripts": available,
            "word": word,
            "structure_inspection": structure,
        },
        "document_capabilities": {
            "docx_generation": word,
            "structure_inspection": structure,
        },
        "complete_document_pipeline_available": complete_document_pipeline_available,
        "platform_version": "fake-agent 0.test",
        "platform_version_probe": available,
        "attachment_presentation": available,
        "tested_at": "2026-09-14T00:00:00+00:00",
    }


def _minimal_journey(path: Path, *, required_artifacts: str = "case_archive"):
    path.write_text(
        "schema_version: 2\n"
        "id: remediation-test\n"
        "title: 修复回归\n"
        "initial_user_message: 我想梳理一件劳动争议。\n"
        "fact_pool: []\n"
        "milestones: []\n"
        "expect:\n"
        f"  required_artifacts: [{required_artifacts}]\n"
        "  max_turns: 1\n",
        encoding="utf-8",
    )
    return load_journey(path)


class ModelLedJourneyRemediationTests(unittest.TestCase):
    def test_installed_case_archive_uses_skill_cwd_and_explicit_workspace_root(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            install_target = root / "installed-client"
            workspace = root / "isolated-project"
            workspace.mkdir()
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=install_target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            with self.assertRaisesRegex(ValueError, "Skill 安装目录"):
                from scripts.case_archive import build_case_archive_invocation

                build_case_archive_invocation(
                    skill_root=skill_root,
                    workspace_root=skill_root,
                    python_executable=sys.executable,
                )
            invocation = build_case_archive_invocation(
                skill_root=skill_root,
                workspace_root=workspace,
                python_executable=sys.executable,
            )
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            process = subprocess.run(
                [*invocation.command_prefix, "create", "--initial-goal", "梳理劳动争议"],
                cwd=invocation.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            created = json.loads(process.stdout)
            self.assertTrue(created["ok"], created)
            self.assertTrue((workspace / ".arbibuddy" / "cases").is_dir())
            self.assertFalse((skill_root / ".arbibuddy").exists())
            self.assertEqual(invocation.cwd, skill_root)
            self.assertIn("--root", invocation.command_prefix)

    def test_codex_environment_derives_codex_home_without_overwriting_explicit_home(self):
        with patch.dict(
            os.environ,
            {
                "HOME": "",
                "USERPROFILE": r"C:\Users\runtime-test",
                "CODEX_HOME": "",
                "PYTHONPATH": str(ROOT),
            },
            clear=False,
        ):
            environment = _runtime_process_environment("codex")

        self.assertEqual(environment["CODEX_HOME"], r"C:\Users\runtime-test\.codex")
        self.assertNotIn("PYTHONPATH", environment)

        with patch.dict(
            os.environ,
            {
                "HOME": r"C:\Users\explicit-home",
                "USERPROFILE": r"C:\Users\runtime-test",
                "CODEX_HOME": r"C:\Users\explicit-codex",
            },
            clear=False,
        ):
            environment = _runtime_process_environment("codex")
        self.assertEqual(environment["CODEX_HOME"], r"C:\Users\explicit-codex")

    def test_cli_environment_resolves_skill_python_to_harness_interpreter(self):
        active_python = r"C:\eval-venv\Scripts\python.exe"
        with (
            patch.object(sys, "executable", active_python),
            patch.dict(
                os.environ,
                {
                    "PATH": r"C:\system-python;C:\tools",
                    "PYTHONPATH": str(ROOT),
                },
                clear=False,
            ),
        ):
            environment = _runtime_process_environment("claude-code")

        path_key = next(
            key for key in environment if key.casefold() == "path"
        )
        self.assertEqual(
            environment[path_key].split(os.pathsep)[0],
            str(Path(active_python).parent),
        )
        self.assertNotIn("PYTHONPATH", environment)

    def test_claude_start_and_resume_commands_forward_explicit_model(self):
        runtime = CliAgentRuntime(
            platform="claude-code",
            command=["claude"],
            model="sonnet",
        )
        start = runtime._start_command(Path("C:/workspace"), "虚构用户消息")
        runtime.session_id = "session-1"
        resume = runtime._resume_command(Path("C:/workspace"), "下一轮虚构消息")

        self.assertEqual(start[start.index("--model") + 1], "sonnet")
        self.assertEqual(resume[resume.index("--model") + 1], "sonnet")
        self.assertNotIn("--dangerously-skip-permissions", start)
        self.assertNotIn("--dangerously-skip-permissions", resume)
        self.assertIn("--permission-mode", start)
        self.assertNotIn("--permission-prompts", start)
        self.assertIn("--allowed-tools", start)

    def test_suite_uses_platform_model_overrides_and_rejects_shared_all_model(self):
        args = SimpleNamespace(
            platform="all",
            model=None,
            codex_model="gpt-5.6-luna",
            claude_model="sonnet",
        )
        self.assertEqual(_suite_model(args, "codex"), "gpt-5.6-luna")
        self.assertEqual(_suite_model(args, "claude-code"), "sonnet")

        args.model = "shared-model"
        with self.assertRaisesRegex(ValueError, "共享"):
            _suite_model(args, "codex")

    def test_legacy_fact_markers_are_loadable_but_not_required(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.yaml"
            path.write_text(
                "schema_version: 2\n"
                "id: legacy-baseline\n"
                "title: legacy\n"
                "initial_user_message: 我想梳理劳动争议。\n"
                "fact_pool:\n"
                "  - id: wage\n"
                "    message: 虚构工资事实。\n"
                "    legacy:\n"
                "      when_any: [工资]\n"
                "milestones: []\n"
                "expect:\n"
                "  required_artifacts: []\n"
                "  max_turns: 1\n",
                encoding="utf-8",
            )
            journey = load_journey(path)

        self.assertEqual(journey.fact_pool[0].response_markers, ("工资",))

    def test_ml02_has_an_explicit_deferred_decision_fact(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-termination-high-risk.yaml"
        )
        facts = {fact.id: fact.text for fact in journey.fact_pool}

        self.assertIn("decision_deferred", facts)
        self.assertRegex(facts["decision_deferred"], r"还没决定|尚未决定|还没有决定")
        self.assertRegex(
            facts["decision_deferred"],
            r"不打算.{0,12}授权起草|暂不起草|先不写",
        )
        self.assertIn("candidate_request", facts)

    def test_ml01_bonus_fact_can_answer_a_repeated_question(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-wage-bonus-overtime.yaml"
        )
        facts = {fact.id: fact for fact in journey.fact_pool}

        self.assertEqual(facts["performance_bonus"].max_uses, 2)

    def test_ml01_covers_unverified_prior_bonus_payment_history(self):
        journey = load_journey(
            ROOT / "evals" / "model-led" / "codex-claude-wage-bonus-overtime.yaml"
        )
        facts = {fact.id: fact.text for fact in journey.fact_pool}

        self.assertIn("bonus_history_unknown", facts)
        self.assertRegex(facts["bonus_history_unknown"], r"以前|此前|以往")
        self.assertRegex(facts["bonus_history_unknown"], r"还没核对|尚未核对|未核实")
        self.assertIn("工资条", facts["bonus_history_unknown"])

    def test_timeout_recovery_accepts_a_completed_turn_without_hiding_failure(self):
        partial_stdout = (
            '{"type":"thread.started","thread_id":"thread-timeout"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"请告诉我目前是否仍在职。"}}\n'
            '{"type":"turn.completed"}\n'
        )

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 3, output=partial_stdout, stderr="")

        with TemporaryDirectory() as temp:
            runtime = CliAgentRuntime(
                platform="codex",
                command=["fake-agent"],
                timeout_seconds=3,
            )
            with patch(
                "scripts.model_led_agent_eval.subprocess.run", side_effect=fake_run
            ):
                response = runtime.turn(
                    workspace=Path(temp),
                    message="真实用户消息",
                )

        self.assertEqual(response.session_id, "thread-timeout")
        self.assertEqual(response.text, "请告诉我目前是否仍在职。")

    def test_timeout_diagnostic_is_bounded_and_redacted(self):
        long_stderr = (
            r"ImportError: C:\Users\alice\private\module.py token=secret-value "
            + "x" * 5000
        )
        partial_stdout = "\n".join(
            json.dumps(event, ensure_ascii=False)
            for event in (
                {"type": "thread.started", "thread_id": "thread-timeout"},
                {
                    "type": "item.started",
                    "item": {
                        "type": "command_execution",
                        "command": [
                            "python",
                            "-m",
                            "scripts.case_archive.cli",
                            "--root",
                            r"C:\Users\alice\workspace",
                        ],
                    },
                },
            )
        ) + "\n"

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(
                command,
                2,
                output=partial_stdout,
                stderr=long_stderr,
            )

        with TemporaryDirectory() as temp:
            runtime = CliAgentRuntime(
                platform="codex",
                command=["fake-agent", "--token", "secret-value"],
                timeout_seconds=2,
            )
            with patch(
                "scripts.model_led_agent_eval.subprocess.run", side_effect=fake_run
            ):
                with self.assertRaisesRegex(RuntimeAdapterError, "超时") as raised:
                    runtime.turn(workspace=Path(temp), message="真实用户消息")

        diagnostic = raised.exception.details
        self.assertLessEqual(len(diagnostic["stdout"].encode("utf-8")), 4096)
        self.assertLessEqual(len(diagnostic["stderr"].encode("utf-8")), 4096)
        self.assertEqual(
            diagnostic["partial_runtime_event_summary"][-1]["item_type"],
            "command_execution",
        )
        self.assertEqual(
            diagnostic["partial_runtime_event_summary"][-1]["python_module_names"],
            ["scripts.case_archive.cli"],
        )
        diagnostic_text = json.dumps(diagnostic, ensure_ascii=False)
        self.assertNotIn(r"C:\Users\alice", diagnostic_text)
        self.assertNotIn("secret-value", diagnostic_text)

    def test_private_capability_context_does_not_rewrite_user_message_or_leak_fact_pool(self):
        captured = []

        class CapturingRuntime:
            def __init__(self, **kwargs):
                self.context = None
                self.messages = []
                self.command = ("fake-agent",)
                captured.append(self)

            def set_runtime_context(self, context):
                self.context = context

            def probe_version(self, *, workspace):
                return "fake-agent 0.test"

            def turn(self, *, workspace, message):
                self.messages.append(message)
                created = CaseArchive(workspace).create()
                assert created["ok"], created
                return SimpleNamespace(
                    text="已建立案情档案。",
                    session_id="session",
                    events=(),
                    command=("fake-agent",),
                    duration_seconds=0.0,
                )

            def close(self):
                return None

        with TemporaryDirectory() as temp:
            root = Path(temp)
            scenario = _minimal_journey(root / "scenario.yaml")
            with (
                patch("scripts.model_led_agent_eval.CliAgentRuntime", CapturingRuntime),
                patch(
                    "scripts.model_led_agent_eval.probe_capabilities",
                    return_value=_capability_report(complete_document_pipeline_available=False),
                ),
            ):
                result = run_journey(
                    scenario,
                    platform="claude-code",
                    source_root=ROOT,
                    temp_root=root / "runs",
                    platform_command=["fake-agent"],
                )

        self.assertTrue(result.passed, result.failure_message)
        self.assertEqual(captured[0].messages, [scenario.initial_user_message])
        self.assertNotIn("fact_pool", captured[0].context)
        self.assertNotIn("initial_user_message", captured[0].context)
        self.assertFalse(captured[0].context["degradation"]["docx"]["allowed"])

    def test_runtime_context_uses_platform_private_channels(self):
        context = {
            "workspace_root": "PLACEHOLDER",
            "capabilities": {"complete_document_pipeline_available": False},
        }
        for platform in ("codex", "claude-code"):
            with self.subTest(platform=platform), TemporaryDirectory() as temp:
                workspace = Path(temp)
                platform_context = {**context, "workspace_root": str(workspace.resolve())}
                captured = {}

                def fake_run(command, **kwargs):
                    captured["command"] = command
                    captured.update(kwargs)
                    if platform == "codex":
                        stdout = '{"thread_id":"codex-context","text":"收到"}\n'
                    else:
                        stdout = '{"type":"result","subtype":"success","is_error":false,"session_id":"claude-context","result":"收到"}\n'
                    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

                runtime = CliAgentRuntime(
                    platform=platform,
                    command=["fake-agent"],
                    runtime_context=platform_context,
                )
                with patch(
                    "scripts.model_led_agent_eval.subprocess.run", side_effect=fake_run
                ):
                    runtime.turn(workspace=workspace, message="请按原文处理虚构消息。")

                self.assertNotIn("PYTHONPATH", captured["env"])
                if platform == "codex":
                    self.assertTrue((workspace / "AGENTS.md").exists())
                    self.assertEqual(runtime.context_delivery, "codex-project-agents")
                else:
                    self.assertIn("--append-system-prompt", captured["command"])
                    self.assertEqual(runtime.context_delivery, "claude-append-system-prompt")

    def test_existing_document_body_write_is_not_an_archive_write_claim(self):
        for response in (
            "**金额来源标示（三份文书正文均已按此写入）**",
            "正文内容已保存，三份候选和独立清单保持候选状态。",
            "已将16000元写入催告函正文，原件仍待核。",
        ):
            with self.subTest(response=response):
                self.assertFalse(_probe_runtime_persistence_claim(response))

    def test_document_body_claim_cannot_mask_an_archive_write(self):
        for response in (
            "文书正文均已按此写入；本轮新增事实已保存。",
            "正文已保存，本轮新事实已写入案情档案。",
            "已将文书正文和案情档案保存。",
            "已将新增事实写入档案正文。",
            "正文和本轮新增事实均已写入。",
            "本轮新增事实已保存。",
        ):
            with self.subTest(response=response):
                self.assertTrue(_probe_runtime_persistence_claim(response))

    def test_false_persistence_claim_is_separate_from_missing_artifact(self):
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已知部分已整理进仲裁申请候选稿、证据目录和提交前清单。"
            )
        )
        self.assertTrue(
            _probe_runtime_persistence_claim("已提交案情档案；提交前清单另存。")
        )
        self.assertTrue(_probe_runtime_persistence_claim("已保存案情档案。"))
        self.assertFalse(_probe_runtime_persistence_claim("将保存案情档案。"))
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "我会做只读门槛检查：确认现行依据是否已形成可用于外发的核验记录。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "你刚才的信息与已记录内容一致，暂不重复登记，也不估算金额。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "你这次补充与已记录的加班事实一致，我不重复记载。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这部分内容已在案情档案中保存，现有工资金额继续保持待补。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "我会先按劳动争议辅助流程读取已保存的案情档案，再以档案内容为准继续；"
                "不会仅凭当前聊天记忆推断。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "当前可见信息里只有‘已保存过案情、刚才中断’，没有任何案件事实。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "接下来我会读取档案操作说明及当前工作区中的已保存案件，确认后继续。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "我会只在本工作区的案情归档位置定位已保存案件，不读取其他来源。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "先确认中断前已保存到哪一步，再从已保存档案恢复。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已确认当前工作区只有一个已保存案件。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已读取并恢复已保存案情，当前继续依据档案处理。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "前面已经按‘待核实、待补证据’保存，本轮没有新增事实。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这条追讨记录已经保存为待核实事实，本轮没有新增内容，不重复追加。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这条和上一条内容一致，档案里已经按待核实记录了，我不重复写入。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这份是候选稿，不是已提交或可提交的正式材料。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这条内容我已经记录过，不再重复；先作为待补充数据挂在档案里。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "这些证据线索已经保存，重复内容不再重复记载；候选文书标明待补证位置。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "我已读取之前保存的案情档案，下面按档案记录继续。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "档案工具说明：临时清理 warning 不反转已提交结果。\n"
                "ARGUMENTS: 虚构用户消息\n"
                "我已读取工作区里保存的案情档案，下面按档案记录继续。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "成功响应中的确定性结果才能形成正式记录。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "临时清理 warning 不反转已提交结果。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已读取本工作区档案，下面是已记录事实。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已从当前工作区保存的案情档案接续，现有记录如下。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已从当前工作区保存的案情档案接续，现有事实如下。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "已从当前工作区的案情档案接续，中断前的记录如下。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "公司拖欠三个月工资（已按你的更正记录，此前‘两个月’的说法作废）。"
            )
        )
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "当前已记录事实\n- 在职且正常上班。"
            )
        )

        class ClaimingRuntime:
            def __init__(self, **kwargs):
                self.command = ("fake-agent",)

            def set_runtime_context(self, context):
                return None

            def probe_version(self, *, workspace):
                return "fake-agent 0.test"

            def turn(self, *, workspace, message):
                return SimpleNamespace(
                    text="已保存到案情档案。",
                    session_id="claiming",
                    events=(),
                    command=("fake-agent",),
                    duration_seconds=0.0,
                )

            def close(self):
                return None

        with TemporaryDirectory() as temp:
            root = Path(temp)
            journey = _minimal_journey(root / "journey.yaml")
            with (
                patch("scripts.model_led_agent_eval.CliAgentRuntime", ClaimingRuntime),
                patch(
                    "scripts.model_led_agent_eval.probe_capabilities",
                    return_value=_capability_report(),
                ),
            ):
                result = run_journey(
                    journey,
                    platform="codex",
                    source_root=ROOT,
                    temp_root=root / "runs",
                    platform_command=["fake-agent"],
                )

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_category, "SUT/Skill")
        self.assertIn("false_persistence_claim", result.failure_message)

    def test_claude_restart_read_receipt_unblocks_recovery_and_degradation_turn(self):
        result, evidence, selected = self._run_claude_restart_read_receipt()
        self.assertTrue(result.passed, result.failure_message)
        self.assertEqual(result.turns, 5)
        self.assertEqual(selected, ["initial_facts", "correction", "degradation"])
        restore_turn = evidence["transcript"][3]
        self.assertEqual(
            restore_turn["persistence_claim"]["verification_basis"],
            "same_case_current_revision_archive_read_receipt",
        )
        self.assertTrue(
            _has_successful_case_archive_read(restore_turn["runtime_event_summary"])
        )
        self.assertIn(
            "archive_recovered_after_restart", restore_turn["milestones_observed"]
        )

    def test_saved_case_recovery_reference_reaches_degradation_without_new_write(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "我已从当前工作区保存的案情接续：公司拖欠三个月工资。"
            "具体月份及适用地区待确认。"
        )
        self.assertTrue(result.passed, result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction", "degradation"])
        restore_turn = evidence["transcript"][3]
        self.assertTrue(_has_successful_case_archive_read(restore_turn["runtime_event_summary"]))
        self.assertIn("archive_recovered_after_restart", restore_turn["milestones_observed"])

    def test_saved_case_reference_cannot_hide_same_sentence_new_write_claim(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "我已从当前工作区保存的案情接续，并已将用户新提供的工资流水保存到案情档案。"
            "公司拖欠三个月工资。"
        )
        self.assertFalse(result.passed)
        self.assertIn("false_persistence_claim", result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction"])
        self.assertFalse(evidence["transcript"][3]["persistence_claim"]["verified"])

    def test_correct_existing_correction_cannot_verify_a_new_write_claim(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "我已从当前工作区保存的案情接续：欠薪月数已更正为三个月。"
            "已将用户新提供的工资流水保存到案情档案。"
        )
        self.assertFalse(result.passed)
        self.assertIn("false_persistence_claim", result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction"])
        self.assertFalse(evidence["transcript"][3]["persistence_claim"]["verified"])

    def test_restart_read_receipt_does_not_prove_a_new_archive_write(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "已从当前工作区的案情档案接续。公司拖欠三个月工资。"
            "已将用户新提供的工资流水保存到案情档案。"
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.turns, 4)
        self.assertIn("false_persistence_claim", result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction"])
        self.assertFalse(evidence["transcript"][3]["persistence_claim"]["verified"])

    def test_restart_read_receipt_rejects_a_wrong_existing_summary(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "已从当前工作区的案情档案接续。"
            "公司拖欠三个月工资。"
            "当前已记录的情况：公司拖欠四个月工资。"
        )
        self.assertFalse(result.passed)
        self.assertIn("false_persistence_claim", result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction"])
        self.assertFalse(evidence["transcript"][3]["persistence_claim"]["verified"])

    def test_restart_read_receipt_rejects_mixed_true_summary_and_new_write(self):
        result, evidence, selected = self._run_claude_restart_read_receipt(
            "已从当前工作区的案情档案接续。"
            "当前已记录的情况：公司拖欠三个月工资。"
            "已将用户新提供的工资流水保存到案情档案。"
        )
        self.assertFalse(result.passed)
        self.assertIn("false_persistence_claim", result.failure_message)
        self.assertEqual(selected, ["initial_facts", "correction"])
        self.assertFalse(evidence["transcript"][3]["persistence_claim"]["verified"])

    def _run_claude_restart_read_receipt(self, restart_response=None):
        class SequenceResponder:
            def __init__(self):
                self.identity = ResponderIdentity(
                    mode="rule",
                    provider="test",
                    runtime_source="test-double",
                )
                self.selected = []

            def respond(self, request):
                fact_id = request.required_next_fact_id
                if fact_id is None:
                    fact_id = next(
                        fact.id
                        for fact in request.available_facts
                        if fact.used_count < fact.max_uses
                    )
                decision = ScenarioResponseDecision(
                    "answer", (fact_id,), "high", "direct_question"
                )
                request_hash = scenario_request_sha256(request)
                self.selected.append(fact_id)
                return ScenarioResponseResult(
                    decision=decision,
                    identity=self.identity,
                    request_sha256=request_hash,
                    attempt_count=1,
                    repair_count=0,
                    duration_seconds=0.0,
                    result_code="decision_received",
                    attempts=(
                        ScenarioAttempt(
                            attempt=1,
                            request_sha256=request_hash,
                            validation="passed",
                            action=decision.action,
                            selected_fact_ids=decision.selected_fact_ids,
                        ),
                    ),
                    trace={"request_sha256": request_hash},
                )

        class FakeClaudeRuntime:
            def __init__(self, **kwargs):
                self.turn_count = 0
                self.command = ("fake-claude",)
                self.case_id = None
                self.installed_skill_root = None

            def set_runtime_context(self, context):
                self.installed_skill_root = Path(context["installed_skill_root"])

            def probe_version(self, *, workspace):
                return "Claude Code 2.test"

            def turn(self, *, workspace, message):
                self.turn_count += 1
                archive = CaseArchive(workspace)
                events = []
                if self.turn_count == 1:
                    created = archive.create()
                    assert created["ok"], created
                    self.case_id = created["result"]["case_id"]
                    text = "已为你建立案情档案。你的劳动关系目前仍在持续吗？"
                elif self.turn_count == 2:
                    committed = archive.commit(
                        {
                            "case_id": self.case_id,
                            "expected_revision": 0,
                            "change_summary": "记录用户陈述",
                            "changes": [{
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": "用户陈述：公司拖欠两个月工资。",
                            }],
                        }
                    )
                    assert committed["ok"], committed
                    text = "已记录欠薪两个月，具体月份待核。"
                elif self.turn_count == 3:
                    committed = archive.commit(
                        {
                            "case_id": self.case_id,
                            "expected_revision": 1,
                            "change_summary": "更正欠薪月数",
                            "changes": [{
                                "operation": "replace",
                                "record_type": "fact",
                                "record_id": "F-001",
                                "reason": "用户更正此前表述",
                                "content_markdown": "用户更正：公司拖欠三个月工资。",
                            }],
                        }
                    )
                    assert committed["ok"], committed
                    text = "已更正为三个月，不再按两个月记录。"
                elif self.turn_count == 4:
                    current = archive.read(self.case_id)["result"]
                    executable = str(sys.executable).replace("\\", "/")
                    command = (
                        f'cd "{self.installed_skill_root}" && "{executable}" '
                        f'-B -X utf8 -m scripts.case_archive.cli --root '
                        f'"{workspace.resolve()}" read-current'
                    )
                    events = [
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [{
                                    "type": "tool_use",
                                    "id": "restart-read",
                                    "name": "Bash",
                                    "input": {"command": command},
                                }],
                            },
                        },
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [{
                                    "type": "tool_result",
                                    "tool_use_id": "restart-read",
                                    "is_error": False,
                                    "content": json.dumps({
                                        "contract_version": "case-archive-v1",
                                        "operation": "read",
                                        "ok": True,
                                        "errors": [],
                                        "result": {
                                            "case_id": self.case_id,
                                            "revision": current["revision"],
                                            "canonical_location": current[
                                                "canonical_location"
                                            ],
                                        },
                                    }),
                                }],
                            },
                        },
                    ]
                    text = (
                        "已从当前工作区的案情档案接续。当前已记录的情况："
                        "公司拖欠三个月工资。"
                    )
                    if restart_response is not None:
                        text = restart_response
                else:
                    text = (
                        "欠薪的具体月份仍待核实；一般性事实整理可以继续；"
                        "现行官方依据无法核验前，高风险定稿必须暂缓。"
                        "下一步请先确认劳动合同实际履行地。"
                    )
                return SimpleNamespace(
                    text=text,
                    session_id=f"claude-session-{self.turn_count}",
                    events=tuple(events),
                    command=self.command,
                    duration_seconds=0.0,
                )

            def close(self):
                return None

        with TemporaryDirectory() as temp:
            root = Path(temp)
            journey = load_journey(
                ROOT
                / "evals"
                / "model-led"
                / "codex-claude-interruption-correction-degradation.yaml"
            )
            responder = SequenceResponder()
            with (
                patch("scripts.model_led_agent_eval.CliAgentRuntime", FakeClaudeRuntime),
                patch(
                    "scripts.model_led_agent_eval.probe_capabilities",
                    return_value=_capability_report(
                        complete_document_pipeline_available=False
                    ),
                ),
            ):
                result = run_journey(
                    journey,
                    platform="claude-code",
                    source_root=ROOT,
                    temp_root=root / "runs",
                    platform_command=["fake-claude"],
                    responder=responder,
                )
            evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

        return result, evidence, responder.selected

    def test_delivery_receipt_records_are_not_persistence_claims(self):
        self.assertFalse(
            _probe_runtime_persistence_claim(
                "催告正文、欠薪明细、发送记录、到达/已读/签收记录、"
                "公司回复和付款记录"
            )
        )

    def test_existing_record_noun_phrase_does_not_claim_a_new_write(self):
        response = (
            "收到。这与已记录的情况一致：你还没拿到完整文件，公司也没有发解除通知；"
            "你持有的合同、流水、群聊和考勤尚未查看，因此目前不能判断文件条款或欠薪金额。"
        )
        self.assertFalse(_probe_runtime_persistence_claim(response))
        self.assertFalse(_probe_runtime_persistence_claim("请核对已保存的材料。"))
        self.assertTrue(_probe_runtime_persistence_claim("本轮新增事实已记录。"))
        self.assertTrue(_probe_runtime_persistence_claim("当前已记录的情况：公司已结清工资。"))
        self.assertTrue(_probe_runtime_persistence_claim(
            "这与已记录的情况一致，另已保存本轮新增事实。"
        ))
        self.assertTrue(_probe_runtime_persistence_claim(
            "已记录的情况如上。本轮新增事实已写入档案。"
        ))

    def test_existing_quoted_archive_status_is_not_a_false_persistence_claim(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create(
                {"jurisdiction": "全国", "initial_goal": "核对既有工作区状态"}
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录案件范围状态",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "地区待确认；当前范围状态待补充。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            response = '范围和“地区待确认”状态已记录。'
            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    '范围和“其他地区已确认”状态已记录。', workspace
                )
            )

    def test_existing_three_request_analysis_paraphrase_is_not_a_false_claim(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录尚待核实的具体信息",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": (
                                "**工资差额（2026 年 4—6 月）** 当前判断：信息不足。\n\n"
                                "**季度绩效奖金（具体季度待核）** 当前判断：信息不足。\n\n"
                                "**加班费（2026 年 4—6 月）** 当前判断：信息不足。"
                            ),
                        },
                        {
                            "operation": "append",
                            "record_type": "next_step",
                            "content_markdown": (
                                "用户再次说明：有一项具体信息目前说不准，需要核对材料后再确认；"
                                "该项先保留为待核，不据此推断其内容。"
                                "其他已经提供并确认的事实及本轮三项请求继续按现有口径处理。"
                                "具体所指字段尚未明确，相关未填字段均保持待核。"
                            ),
                        },
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            response = (
                "收到，已将这项信息保留为“待核”，不猜测内容；其他已知部分按现有事实继续处理。\n\n"
                "三项请求的条件化分析已记录。申请书候选稿、证据目录和提交前清单仍无法生成，"
                "因为文书运行环境拒绝写入。该问题不需要你补猜这项事实；"
                "输出权限恢复后才能交付文件。"
            )

            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )

            with TemporaryDirectory() as incomplete_temp:
                incomplete_workspace = Path(incomplete_temp)
                incomplete_archive = CaseArchive(incomplete_workspace)
                incomplete_created = incomplete_archive.create()
                self.assertTrue(incomplete_created["ok"], incomplete_created)
                incomplete_commit = incomplete_archive.commit(
                    {
                        "case_id": incomplete_created["result"]["case_id"],
                        "expected_revision": 0,
                        "change_summary": "只记录两项条件化分析",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "analysis",
                                "content_markdown": (
                                    "**工资差额（2026 年 4—6 月）** 当前判断：信息不足。\n\n"
                                    "**季度绩效奖金（具体季度待核）** 当前判断：信息不足。"
                                ),
                            }
                        ],
                    }
                )
                self.assertTrue(incomplete_commit["ok"], incomplete_commit)
                self.assertFalse(
                    _persistence_claim_references_existing_archive(
                        response, incomplete_workspace
                    )
                )

    def test_existing_three_request_analysis_output_paraphrase_is_not_a_false_claim(self):
        response = (
            '这些项目在前一轮已全部按“待核”记录，档案与候选稿也都停在同一状态，'
            "本轮没有新的事实或材料需要我改动。\n\n"
            '说明一下：“其他已知部分”已经推进到头了。三项请求（工资差额、本期季度绩效奖金、'
            "加班费）的金额、期间和举证都直接依赖您手上的材料，现有已知事实能支持的分析、"
            "候选稿、证据目录和核对清单都已产出，没有还能继续做的部分。"
        )

        def create_archive(workspace: Path, topics: tuple[str, ...]) -> None:
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            committed = archive.commit(
                {
                    "case_id": created["result"]["case_id"],
                    "expected_revision": 0,
                    "change_summary": "记录三项争议的条件化分析",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": (
                                f"**分析类型：{topic}**\n"
                                "- 事实状态：pending（关键输入缺失）\n"
                                "- 初步判断：信息不足\n"
                                "- 补强动作：待核对相关材料"
                            ),
                        }
                        for topic in topics
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            create_archive(
                workspace,
                ("工资差额", "季度绩效奖金", "加班工资"),
            )

            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )

        with TemporaryDirectory() as temp:
            incomplete_workspace = Path(temp)
            create_archive(
                incomplete_workspace,
                ("工资差额", "季度绩效奖金"),
            )

            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    response, incomplete_workspace
                )
            )

    def test_single_quoted_existing_archive_correction_is_not_a_false_claim(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
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
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户更正：相关期间为三个月（此前对两个月的表述有误）；"
                                "具体事项与起止时间待确认。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            response = (
                '更正已记录（档案里现在写的是"相关期间为三个月"，'
                "并标注了具体所指待确认）。"
            )
            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )

            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    '更正已记录（档案里现在写的是"相关期间为四个月"）。',
                    workspace,
                )
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    '本轮刚刚记录的更正：档案里现在写的是"相关期间为三个月"。',
                    workspace,
                )
            )

    def test_existing_archive_correction_value_without_quotes_is_not_a_false_claim(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录工资争议期间更正",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户更正：相关工资争议期间为三个月；"
                                "具体争议事项待澄清。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            response = (
                "已从当前工作区保存的案情接续：这是一起工资争议，"
                "相关期间已更正为三个月；具体争议事项尚未明确。"
            )
            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    "已从当前工作区保存的案情接续：相关期间已更正为四个月。",
                    workspace,
                )
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    "本轮刚刚记录：相关期间已更正为三个月。", workspace
                )
            )

    def test_restart_paraphrase_matches_an_existing_archive_correction(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录工资争议期间更正",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户更正：相关工资争议期间为三个月；"
                                "具体争议事项待澄清。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            response = (
                "我已从当前工作区保存的案情接续：这是一起工资争议；"
                "你更正过，相关期间是三个月而不是两个月，但档案尚未记明具体月份。"
            )

            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    "我已从当前工作区保存的案情接续：这是一起工资争议；"
                    "你更正过，相关期间是四个月而不是两个月。",
                    workspace,
                )
            )

    def test_ml02_existing_fact_list_does_not_require_another_archive_revision(self):
        response = (
            "你已有的合同、流水、群聊和考勤，以及尚未发过催告或解除通知，"
            "也已记录。"
        )
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create()
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录已知材料和通知状态",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户未发过催告或解除通知。",
                        },
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "劳动合同、工资银行流水、工作群聊和考勤记录。",
                        },
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            self.assertTrue(_probe_runtime_persistence_claim(response))
            self.assertTrue(
                _persistence_claim_references_existing_archive(response, workspace)
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    "合同、流水、虚构录音和考勤，也已记录。", workspace
                )
            )
            self.assertFalse(
                _persistence_claim_references_existing_archive(
                    "合同、流水、群聊和考勤，刚刚已记录。", workspace
                )
            )
            self.assertFalse(
                _existing_archive_fact_list(
                    response.rstrip("。"),
                    "劳动合同、工资银行流水、工作群聊和考勤记录。"
                    "用户未发过催告，公司已发解除通知。",
                )
            )

    def test_real_archive_write_satisfies_persistence_claim(self):
        class WritingRuntime:
            def __init__(self, **kwargs):
                self.command = ("fake-agent",)

            def set_runtime_context(self, context):
                return None

            def probe_version(self, *, workspace):
                return "fake-agent 0.test"

            def turn(self, *, workspace, message):
                created = CaseArchive(workspace).create()
                assert created["ok"], created
                return SimpleNamespace(
                    text="已保存到案情档案。",
                    session_id="writing",
                    events=(),
                    command=("fake-agent",),
                    duration_seconds=0.0,
                )

            def close(self):
                return None

        with TemporaryDirectory() as temp:
            root = Path(temp)
            journey = _minimal_journey(root / "journey.yaml")
            with (
                patch("scripts.model_led_agent_eval.CliAgentRuntime", WritingRuntime),
                patch(
                    "scripts.model_led_agent_eval.probe_capabilities",
                    return_value=_capability_report(),
                ),
            ):
                result = run_journey(
                    journey,
                    platform="codex",
                    source_root=ROOT,
                    temp_root=root / "runs",
                    platform_command=["fake-agent"],
                )

        self.assertTrue(result.passed, result.failure_message)
        self.assertTrue(result.observation["case_archive"])

    def test_word_probe_keeps_bounded_sanitized_diagnostics(self):
        with TemporaryDirectory() as temp:
            long_stderr = (
                r"ImportError: C:\Users\alice\private\site.py token=secret-value "
                + "x" * 2000
            )
            with patch(
                "scripts.platform_adapters.service.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=1,
                    stdout="stdout diagnostic",
                    stderr=long_stderr,
                ),
            ):
                report = _probe_word(Path(temp))

        self.assertEqual(report["failure_kind"], "module_load_failed")
        self.assertLessEqual(len(report["diagnostic"]["stderr"].encode("utf-8")), 1024)
        self.assertNotIn(r"C:\Users\alice", report["diagnostic"]["stderr"])
        self.assertNotIn("secret-value", report["diagnostic"]["stderr"])
        self.assertIn("<REDACTED>", report["diagnostic"]["stderr"])


if __name__ == "__main__":
    unittest.main()
