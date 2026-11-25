"""Independent Harness fixtures; these are not real-model Journey evidence."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from scripts.model_led_agent_eval import (
    JourneyError, JourneyOracle, JourneyPublicState, _build_scenario_response_request,
    _normalize_managed_view_receipt, _observe_workspace, load_journey, run_journey,
)
from scripts.model_led_agent_eval.artifact_oracle import inspect_public_artifacts
from scripts.model_led_agent_eval.brief_journey import check_brief_journey
from scripts.model_led_agent_eval.postdelivery import capture_delivery_snapshot
from scripts.model_led_agent_eval.scenario_responder import ScenarioResponseDecision, validate_scenario_response
from scripts.platform_adapters.service import PlatformAdapterError
from tests.agent_eval.public_delivery_fixture import write_public_contract_delivery

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "evals/model-led/codex-claude-lawyer-case-brief.yaml"
EDIT_SCENARIO = ROOT / "evals/model-led/variants/ml04-postdelivery-edits.yaml"
COMPLEX_SCENARIO = ROOT / "evals/model-led/variants/ml04-complex-hearing.yaml"
PLANNED_SCENARIO = ROOT / "evals/model-led/variants/ml04-planned-arbitration.yaml"
TYPE = "lawyer_case_brief"


def fixture_view(workspace):
    case, paths = write_public_contract_delivery(workspace, document_type=TYPE)
    archive = workspace / ".arbibuddy/cases" / case / "案情档案.md"
    archive.write_bytes(archive.read_text(encoding="utf-8").encode("utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["delivery_set_id"] = "set-brief-fixture"
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    summary = {key: manifest[key] for key in ("case_id", "archive_revision", "document_type", "mode", "delivery_state", "validation_summary")}
    view = {"case_id": case, "stopped": False, "delivery_state": "final_ready", "current_archive_revision": 3,
        "delivery_set": {"contract_version": "document.delivery-set-v1", "delivery_set_id": "set-brief-fixture",
            "archive_revision": 3, "expected_document_types": [TYPE], "completed_document_types": [TYPE], "missing_document_types": []},
        "delivery_summaries": [summary],
        "presentation_files": [{"delivery_label": TYPE, "kind": "document", "order": 1,
            "path": str(paths["document"]), "sha256": sha256(paths["document"].read_bytes()).hexdigest(), "state": "final_ready"}]}
    return view, paths


class BriefJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.view, self.paths = fixture_view(self.root)
        self.journey = load_journey(SCENARIO)

    def snapshot(self):
        with patch("scripts.documents.view.managed_delivery_view", return_value=self.view):
            return capture_delivery_snapshot(self.root, document_types={TYPE})

    def change_paragraph(self, snapshot, old, new):
        for block in snapshot["ordered_blocks"][TYPE]:
            block["text"] = block["text"].replace(old, new)

    def test_runner_retains_verified_brief_before_workspace_cleanup(self):
        # Exercise the runner's real Oracle, copying and cleanup, not just the
        # copy helper: a passing brief used to be deleted despite this option.
        for retain, tamper in ((True, False), (False, False), (True, True)):
            with self.subTest(retain=retain, tamper=tamper):
                written = {}

                def turn(*, workspace, message):
                    view, paths = fixture_view(workspace)
                    written.update(view=view, paths=paths, workspace=workspace)
                    if tamper:
                        paths["checklist"].write_text("被篡改的清单", encoding="utf-8")
                    return SimpleNamespace(text="简报已生成。", session_id="fixture",
                        duration_seconds=0, command=["fixture"], events=[])

                report = {"capabilities": {}, "document_capabilities": {},
                    "complete_document_pipeline_available": True,
                    "platform_version_probe": {}, "tested_at": "fixture",
                    "attachment_presentation": {}}
                with (
                    patch("scripts.model_led_agent_eval.CliAgentRuntime.turn", side_effect=turn),
                    patch("scripts.model_led_agent_eval.CliAgentRuntime.probe_version", return_value="fixture"),
                    patch("scripts.model_led_agent_eval.probe_capabilities", return_value=report),
                    patch("scripts.documents.view.managed_delivery_view", side_effect=lambda **kwargs: written["view"]),
                ):
                    result = run_journey(self.journey, platform="codex", source_root=ROOT,
                        temp_root=self.root / f"runs-{retain}-{tamper}",
                        platform_command=["fixture"], responder_mode="rule",
                        retain_deliveries=retain)
                evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
                self.assertEqual(result.passed, not tamper, result.failure_message)
                self.assertFalse(written["workspace"].exists())
                retained = evidence["retained_deliveries"]
                if retain and not tamper:
                    self.assertEqual(evidence["oracle"]["completion_path"], "brief_delivered")
                    self.assertEqual({item["kind"] for item in retained}, {"docx", "checklist"})
                    for item in retained:
                        data = Path(item["path"]).read_bytes()
                        self.assertEqual(len(data), item["size"])
                        self.assertEqual(sha256(data).hexdigest(), item["sha256"])
                    self.assertFalse(any("案情档案" in item["path"] for item in retained))
                else:
                    self.assertEqual(retained, [])

    def test_runner_preserves_setup_failure_as_harness_evidence(self):
        with patch("scripts.model_led_agent_eval.install_skill",
                   side_effect=PlatformAdapterError("模拟安装校验失败")):
            result = run_journey(self.journey, platform="codex", source_root=ROOT, temp_root=self.root / "setup-failure",
                                 platform_command=["fixture"], responder_mode="rule")

        self.assertFalse(result.passed)
        self.assertEqual(result.turns, 0)
        self.assertEqual(result.failure_category, "Harness")
        evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["attribution"]["message"], "模拟安装校验失败")
        self.assertFalse((result.evidence_path.parent / "workspace").exists())

    def test_independent_observer_accepts_brief_without_closing_but_requires_internal_checklist(self):
        observed = inspect_public_artifacts(self.root)
        self.assertEqual(observed["invalid_delivery_count"], 0)
        self.assertEqual(len(observed["deliveries"]), 1)
        snapshot = self.snapshot()
        self.assertTrue(snapshot["verified"], snapshot)
        self.paths["checklist"].unlink()
        self.assertEqual(inspect_public_artifacts(self.root)["invalid_delivery_count"], 1)
        self.assertFalse(self.snapshot()["verified"])

    def test_tampered_checklist_and_stale_archive_are_not_accepted(self):
        for mutation in ("digest", "revision"):
            with self.subTest(mutation=mutation):
                self.view, self.paths = fixture_view(self.root)
                if mutation == "digest":
                    self.paths["checklist"].write_text("伪造清单", encoding="utf-8")
                else:
                    path = self.root / ".arbibuddy/cases" / self.view["case_id"] / "案情档案.md"
                    path.write_text(path.read_text(encoding="utf-8").replace("档案修订版本：3", "档案修订版本：4"), encoding="utf-8")
                self.assertFalse(self.snapshot()["verified"])

    def test_receipt_requires_exactly_one_word_and_rejects_extra_checklist_or_bad_identity(self):
        request = {"case_id": self.view["case_id"], "delivery_set_id": "set-brief-fixture"}
        self.assertIsNotNone(_normalize_managed_view_receipt(self.view, request=request, workspace=self.root))
        for mutation in ("extra", "kind", "hash", "order", "case"):
            view = deepcopy(self.view)
            if mutation == "extra":
                view["presentation_files"].append(deepcopy(view["presentation_files"][0]))
            elif mutation == "case":
                view["case_id"] = "case-" + "0" * 24
            else:
                key = {"kind": "kind", "hash": "sha256", "order": "order"}[mutation]
                view["presentation_files"][0][key] = "invalid"
            with self.subTest(mutation=mutation):
                self.assertIsNone(_normalize_managed_view_receipt(view, request=request, workspace=self.root))

    def test_oracle_requires_first_turn_delivery_not_later_confirmation(self):
        snapshot = self.snapshot()
        transcript = {"turn": 1, "selected_fact_ids": ["brief_request"], "public_artifact_snapshot": snapshot}
        state = JourneyPublicState(self.journey, transcripts=(transcript,), sent_fact_ids=("brief_request",), observation=_observe_workspace(self.root))
        self.assertTrue(JourneyOracle(self.journey).evaluate(state).passed)
        delayed = ({"turn": 1, "public_artifact_snapshot": {"verified": False}}, {**transcript, "turn": 2})
        self.assertFalse(JourneyOracle(self.journey).evaluate(replace(state, transcripts=delayed)).passed)
        self.assertFalse(JourneyOracle(self.journey).evaluate(replace(state, runtime_completed=False)).passed)

    def test_arbitration_stage_accepts_equivalent_wording_but_not_omission(self):
        snapshot = self.snapshot()
        self.change_paragraph(snapshot, "已申请仲裁", "已申请劳动仲裁")
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        self.assertEqual(check_brief_journey(self.journey.lawyer_case_brief, turns), [])
        self.change_paragraph(snapshot, "已申请劳动仲裁", "仲裁阶段待核")
        self.assertIn("opening_groups:3", check_brief_journey(self.journey.lawyer_case_brief, turns))

    def test_checks_section_assignment_and_allows_factual_uncertainty(self):
        snapshot = self.snapshot()
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        self.change_paragraph(snapshot, "尚不明确。", "尚不明确，仍未核实。")
        spec = self.journey.lawyer_case_brief
        self.assertEqual(check_brief_journey(spec, turns), [])
        self.change_paragraph(snapshot, "拟与律师讨论的问题", "面谈优先核对")
        self.assertIn("missing_brief_section:拟与律师讨论的问题", check_brief_journey(spec, turns))
        self.change_paragraph(snapshot, "面谈优先核对", "拟与律师讨论的问题")
        self.change_paragraph(snapshot, "工资差额应如何认定？", "3月差额应如何认定？")
        self.assertEqual(check_brief_journey(spec, turns), [])
        self.change_paragraph(snapshot, "3月差额应如何认定？", "建议律师核实工资差额。")
        self.assertIn("forbidden_content:建议律师核实", check_brief_journey(spec, turns))

    def test_content_mutations_cannot_pass_by_chat_claims(self):
        good = self.snapshot()
        self.assertEqual(check_brief_journey(self.journey.lawyer_case_brief, [{"turn": 1, "public_artifact_snapshot": good}]), [])
        snapshot = deepcopy(good)
        self.change_paragraph(snapshot, "当事人现估3月工资差额", "当事人现估2026年3月工资差额")
        self.assertIn("forbidden_content:2026年3月", check_brief_journey(
            self.journey.lawyer_case_brief, [{"turn": 1, "public_artifact_snapshot": snapshot}]))
        for wrong in ("E03", "F-001", "13800001234", "18000元", "在场人与发出方式均记不清"):
            snapshot = deepcopy(good)
            snapshot["ordered_blocks"][TYPE].append({"text": wrong})
            self.assertTrue(check_brief_journey(self.journey.lawyer_case_brief, [{"turn": 1, "public_artifact_snapshot": snapshot}]))
        snapshot = deepcopy(good)
        self.change_paragraph(snapshot, "现估3月工资差额8000元", "现估3月工资差额10000元")
        self.change_paragraph(snapshot, "3月工资差额请求10000元", "3月工资差额请求8000元")
        errors = check_brief_journey(self.journey.lawyer_case_brief, [{"turn": 1, "public_artifact_snapshot": snapshot}])
        self.assertIn("amount_attribution:8000", errors)
        self.assertIn("amount_attribution:10000", errors)

    def test_internal_content_check_distinguishes_public_urls_and_local_paths(self):
        snapshot = self.snapshot()
        spec = self.journey.lawyer_case_brief
        url = "https://example.org/material"
        self.change_paragraph(snapshot, "返岗微信截图、仲裁申请书均未提供。",
                              f"返岗微信截图、仲裁申请书均未提供。另有公开链接 {url}。")
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        self.assertEqual(check_brief_journey(spec, turns), [])
        self.change_paragraph(snapshot, url, "C:/workspace/material.png")
        self.assertIn("internal_content_leak", check_brief_journey(spec, turns))

    def edit_transcripts(self):
        before = self.snapshot()
        before["archive_fact_text"] = "姓名：林晓\n入职日期：2024年3月1日"
        before["ordered_blocks"][TYPE].insert(-1, {"text": "林晓，2024年3月1日入职。"})
        anonymous = deepcopy(before)
        anonymous["ordered_blocks"][TYPE][-2]["text"] = "当事人，2024年3月1日入职。"
        corrected = deepcopy(anonymous)
        corrected["archive_revision"] += 1
        corrected["delivery_set_id"] = "set-corrected"
        for item in corrected["files"]:
            item["archive_revision"] = corrected["archive_revision"]
            item["delivery_set_id"] = corrected["delivery_set_id"]
        corrected["archive_fact_text"] = "姓名：林晓\n入职日期：2024年2月29日"
        corrected["ordered_blocks"][TYPE][-2]["text"] = "当事人，2024年2月29日入职。"
        return [{"turn": i + 1, "selected_fact_ids": [fact], "public_artifact_snapshot": snapshot}
            for i, (fact, snapshot) in enumerate(zip(("brief_request", "anonymize", "correct_date"), (before, anonymous, corrected)))]

    def test_edit_sequence_preserves_facts_for_anonymization_and_updates_correction(self):
        spec = load_journey(EDIT_SCENARIO).lawyer_case_brief
        turns = self.edit_transcripts()
        self.assertEqual(check_brief_journey(spec, turns), [])
        for mutation in ("anonymous_archive", "stale_date", "old_revision", "wrong_case", "no_initial"):
            changed = deepcopy(turns)
            if mutation == "anonymous_archive":
                changed[1]["public_artifact_snapshot"]["archive_fact_text"] = "姓名：当事人"
            elif mutation == "stale_date":
                changed[2]["public_artifact_snapshot"]["archive_fact_text"] = turns[0]["public_artifact_snapshot"]["archive_fact_text"]
            elif mutation == "old_revision":
                changed[2]["public_artifact_snapshot"]["archive_revision"] = 3
            elif mutation == "wrong_case":
                changed[2]["public_artifact_snapshot"]["case_id"] = "other"
            else:
                changed[0]["public_artifact_snapshot"]["verified"] = False
            with self.subTest(mutation=mutation):
                self.assertTrue(check_brief_journey(spec, changed))

    def test_responder_only_sees_edit_after_verified_delivery_and_in_order(self):
        journey = load_journey(EDIT_SCENARIO)
        used = {"brief_request": 1}
        turns = self.edit_transcripts()
        candidate = deepcopy(turns[:1])
        candidate[0]["public_artifact_snapshot"]["delivery_state"] = "candidate_ready"
        for history, expected in (([], None), (candidate, None), (turns[:1], "anonymize"), (turns[:2], "correct_date")):
            counts = {**used, **({"anonymize": 1} if len(history) == 2 else {})}
            request = _build_scenario_response_request(journey, "请查看文档", history, counts, 0)
            self.assertEqual(request.required_next_fact_id, expected)
            edit_ids = {f.id for f in request.available_facts} & {"anonymize", "correct_date"}
            self.assertEqual(edit_ids, {expected} if expected else set())
            self.assertEqual(request.public_delivery_observation["verified"], expected is not None)
            if expected is not None:
                decision = ScenarioResponseDecision("answer", (expected,), "high", "direct_question")
                self.assertEqual(validate_scenario_response(request, decision).decision.selected_fact_ids, (expected,))

    def test_all_brief_scenarios_load_and_reject_bad_contract(self):
        suite = yaml.safe_load((ROOT / "evals/suites/model-led-lawyer-case-brief.yaml").read_text(encoding="utf-8"))
        for path in suite["journeys"]:
            self.assertIsNotNone(load_journey(ROOT / path).lawyer_case_brief)
        data = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
        for mutation in ("unknown", "turn", "groups"):
            changed = deepcopy(data)
            key, value = {"unknown": ("typo", True), "turn": ("first_delivery_turn", True), "groups": ("content_groups", [])}[mutation]
            changed["expect"]["lawyer_case_brief"][key] = value
            path = self.root / "bad.yaml"
            path.write_text(yaml.safe_dump(changed, allow_unicode=True), encoding="utf-8")
            with self.assertRaises(JourneyError):
                load_journey(path)

    def test_real_public_renderer_is_observed_and_receipt_normalized(self):
        from scripts.case_archive import CaseArchive
        from scripts.documents.public import create_delivery_set, describe_contract, render
        from scripts.documents.view import managed_delivery_view
        root = self.root / "rendered"
        root.mkdir()
        example = describe_contract("render", document_type=TYPE)["examples"]["worked_example"]
        archive = CaseArchive(root)
        created = archive.create(example["create"])
        self.assertTrue(created["ok"], created)
        case_id = created["result"]["case_id"]
        committed = archive.commit({**example["commit"], "case_id": case_id})
        self.assertTrue(committed["ok"], committed)
        revision = committed["result"]["revision"]
        selected = create_delivery_set({**example["delivery_set"], "case_id": case_id, "archive_revision": revision}, root)
        self.assertTrue(selected["ok"], selected)
        set_id = selected["result"]["delivery_set_id"]
        result = render({**example["render_requests"][0], "case_id": case_id,
                         "archive_revision": revision, "delivery_set_id": set_id}, root)
        self.assertTrue(result["ok"], result)
        snapshot = capture_delivery_snapshot(root, document_types={TYPE})
        self.assertTrue(snapshot["verified"], snapshot)
        view = managed_delivery_view(workspace=root, case_id=case_id)
        self.assertIsNotNone(_normalize_managed_view_receipt(view,
            request={"case_id": case_id, "delivery_set_id": set_id}, workspace=root))

    def test_complex_brief_requires_hearing_in_overview_and_multiple_dispute_headings(self):
        spec = load_journey(COMPLEX_SCENARIO).lawyer_case_brief
        # Isolate the layout checks from the case-specific factual assertions.
        layout_spec = {**spec, "section_expectations": {}, "amount_attributions": [],
                       "content_groups": [["案情简报"]], "opening_groups": [["2026年5月8日"]]}
        snapshot = self.snapshot()
        snapshot["ordered_blocks"][TYPE] = [
            {"text": text, "style_id": style} for style, text in (
                (None, "案情简报"), (None, "供律师咨询参考"),
                ("Heading1", "一、案件概要"), (None, "2026年5月8日开庭。"),
                ("Heading1", "二、主要争议事项"), ("Heading2", "（一）工资差额"), (None, "工资口径存在差异。"),
                ("Heading2", "（二）离岗和返岗"), (None, "当事人未返岗。"),
                (None, "编制说明：供律师阅案。"),
            )
        ]
        def errors(value):
            return check_brief_journey(layout_spec, [{"turn": 1, "public_artifact_snapshot": value}])
        self.assertEqual(errors(snapshot), [])
        for mutation in ("forced_break", "hearing_not_in_opening", "missing_dispute_level", "misnumbered"):
            changed = deepcopy(snapshot)
            blocks = changed["ordered_blocks"][TYPE]
            if mutation == "forced_break":
                changed["page_break_count"] = 1
            elif mutation == "hearing_not_in_opening":
                blocks[3]["text"] = "本案主要涉及工资差额。"
                blocks[6]["text"] += "2026年5月8日开庭。"
            elif mutation == "missing_dispute_level":
                blocks[7]["style_id"] = None
            else:
                blocks[7]["text"] = "（三）离岗和返岗"
            with self.subTest(mutation=mutation):
                self.assertTrue(errors(changed))

    def test_simple_brief_rejects_an_explicit_continuation(self):
        snapshot = self.snapshot()
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        spec = self.journey.lawyer_case_brief
        self.assertEqual(check_brief_journey(spec, turns), [])
        snapshot["page_break_count"] = 1
        self.assertIn("brief_structure", check_brief_journey(spec, turns))

    def test_planned_arbitration_preserves_unknown_notice_status_and_actual_chronology(self):
        spec = load_journey(PLANNED_SCENARIO).lawyer_case_brief
        snapshot = self.snapshot()
        blocks = [
            (None, "案情简报"), (None, "供律师咨询参考"),
            ("Heading1", "一、案件概要"), (None, "白屿设备有限公司欠薪与排班争议。"),
            ("Heading1", "二、基本案情"), ("Heading2", "（一）关键事件时间线"),
            (None, "2025 年 1 月起工资迟发。"), (None, "2025-05 调整排班。"),
            ("Heading1", "三、主要争议事项"),
            ("Heading2", "（一）欠薪"), (None, "当事人称仍欠工资7200元。"),
            ("Heading2", "（二）排班"), (None, "当事人称排班发生调整。"),
            ("Heading1", "四、争议处理进展"),
            (None, "原计划于2026年4月20日申请仲裁，是否提交及后续通知状态未核实。"),
            ("Heading1", "五、证据及材料情况"), (None, "往来邮件、劳动合同本次未收到。"),
            ("Heading1", "六、拟与律师讨论的问题"), (None, "现有工资和排班问题如何判断？"),
            (None, "编制说明：根据当事人陈述整理。"),
        ]
        snapshot["ordered_blocks"][TYPE] = [{"style_id": style, "text": text} for style, text in blocks]
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        self.assertEqual(check_brief_journey(spec, turns), [])
        timeline = snapshot["ordered_blocks"][TYPE]
        timeline[6], timeline[7] = timeline[7], timeline[6]
        self.assertEqual(check_brief_journey(spec, turns), ["brief_section_order:关键事件时间线"])
        timeline[6], timeline[7] = timeline[7], timeline[6]
        self.change_paragraph(snapshot, "后续通知状态未核实。", "后续进展未核实，尚未收到受理、开庭通知。")
        self.assertIn("forbidden_content:尚未收到受理", check_brief_journey(spec, turns))

    def test_brief_requires_new_title_and_subtitle_before_the_case_information(self):
        spec = self.journey.lawyer_case_brief
        for field in ("案情简报", "供律师咨询参考"):
            snapshot = self.snapshot()
            self.change_paragraph(snapshot, field, "")
            with self.subTest(field=field):
                self.assertIn("brief_structure", check_brief_journey(spec, [
                    {"turn": 1, "public_artifact_snapshot": snapshot}
                ]))

    def test_archive_evidence_rejects_unattributed_absence_of_records(self):
        archive = self.root / ".arbibuddy/cases" / self.view["case_id"] / "案情档案.md"
        text = archive.read_text(encoding="utf-8")
        archive.write_bytes(text.replace("### [E-001] 返岗微信\n",
            "### [E-001] 返岗微信\n当事人称无录音或书面记录。\n工资流水未提供。\n").encode("utf-8"))
        snapshot = self.snapshot()
        self.assertTrue(snapshot["verified"], snapshot)
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        spec = self.journey.lawyer_case_brief
        failures = check_brief_journey(spec, turns)
        self.assertIn("forbidden_archive_evidence:无录音或书面记录", failures)
        self.assertIn("forbidden_archive_evidence:工资流水", failures)
        snapshot.pop("archive_evidence_text")
        self.assertIn("archive_evidence_not_observed", check_brief_journey(spec, turns))

    def test_compact_materials_reject_guessed_records_and_missing_recordings(self):
        snapshot = self.snapshot()
        turns = [{"turn": 1, "public_artifact_snapshot": snapshot}]
        spec = self.journey.lawyer_case_brief
        self.assertEqual(check_brief_journey(spec, turns), [])
        self.change_paragraph(snapshot, "返岗微信截图、仲裁申请书均未提供。",
                              "返岗微信截图、仲裁申请书均未提供。工资支付记录等未阅，口头通知无录音或书面材料。")
        errors = check_brief_journey(spec, turns)
        self.assertIn("brief_section_forbidden:证据及材料情况:工资支付记录", errors)
        self.assertIn("brief_section_forbidden:证据及材料情况:无录音或书面材料", errors)

    def test_mixed_view_keeps_other_document_checklist_and_global_order(self):
        view = deepcopy(self.view)
        case, paths = write_public_contract_delivery(self.root, document_type="evidence_catalog")
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        view["delivery_summaries"].append({key: manifest[key] for key in
            ("case_id", "archive_revision", "document_type", "mode", "delivery_state", "validation_summary")})
        for key in ("expected_document_types", "completed_document_types"):
            view["delivery_set"][key].append("evidence_catalog")
        for index, kind in enumerate(("document", "checklist"), 2):
            view["presentation_files"].append({"delivery_label": "evidence_catalog", "kind": kind,
                "order": index, "path": str(paths[kind]), "sha256": sha256(paths[kind].read_bytes()).hexdigest(), "state": "final_ready"})
        request = {"case_id": case, "delivery_set_id": "set-brief-fixture"}
        self.assertIsNotNone(_normalize_managed_view_receipt(view, request=request, workspace=self.root))
        view["presentation_files"].pop()
        self.assertIsNone(_normalize_managed_view_receipt(view, request=request, workspace=self.root))


if __name__ == "__main__":
    unittest.main()
