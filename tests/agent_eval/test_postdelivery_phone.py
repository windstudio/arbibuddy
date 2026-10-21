"""Deterministic regression coverage for a post-delivery phone correction observer.

These fixtures use fictional archive records and the public document API. They
exercise observer evidence only; they do not stand in for a real Agent Eval or
WorkBuddy host journey.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from scripts.documents.public import create_delivery_set, render
from scripts.documents.view import managed_delivery_view
from scripts.model_led_agent_eval.postdelivery import (
    capture_delivery_snapshot,
    check_phone_correction,
)
from scripts.model_led_agent_eval import (
    JourneyOracle,
    JourneyPublicState,
    _build_scenario_response_request,
    _observe_workspace,
    load_journey,
)
from scripts.model_led_agent_eval.scenario_validation import (
    ScenarioResponseDecision,
    ScenarioResponseError,
    validate_scenario_response,
)
from scripts.platform_paths import path_for_io
from tests.documents.test_model_led_document_render import (
    _application_request,
    _create_archive,
    _evidence_request,
)


OLD_PHONE = "13800000000"
NEW_PHONE = "13900000000"
PHONE_JOURNEY = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "model-led"
    / "variants"
    / "ml01-postdelivery-phone-correction.yaml"
)
CANONICAL_ML01 = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "model-led"
    / "codex-claude-wage-bonus-overtime.yaml"
)


def _deliver_candidate(workspace: Path):
    """Create the initial fictional two-document candidate bundle."""

    archive, case_id, revision = _create_archive(workspace)
    selection = create_delivery_set(
        {
            "case_id": case_id,
            "archive_revision": revision,
            "mode": "candidate",
            "requested_document_types": [
                "arbitration_application",
                "evidence_catalog",
            ],
            "confirmation_refs": [],
        },
        workspace,
    )
    if not selection["ok"]:
        raise AssertionError(selection)
    set_id = selection["result"]["delivery_set_id"]
    application = _application_request(case_id, revision, mode="candidate")
    application["sections"][0]["full_text"] += f"联系电话：{OLD_PHONE}。"
    application["delivery_set_id"] = set_id
    evidence_catalog = _evidence_request(case_id, revision, mode="candidate")
    evidence_catalog["delivery_set_id"] = set_id
    for request in (application, evidence_catalog):
        response = render(request, workspace)
        if not response["ok"]:
            raise AssertionError(response)
    return archive, case_id, revision, set_id


def _correct_phone_and_rerender(workspace: Path, archive, case_id: str, revision: int):
    """Apply a fictional, phone-only archive correction and render both members."""

    corrected_revision = revision + 1
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": revision,
            "change_summary": "当事人仅更正联系电话",
            "changes": [
                {
                    "operation": "replace",
                    "record_type": "fact",
                    "record_id": "F-001",
                    "reason": "当事人明确更正仲裁申请书联系电话",
                    "content_markdown": (
                        "申请人：张三；身份证号：310000199001010000；"
                        "联系地址：上海市示例路1号；联系电话：13900000000。"
                    ),
                },
                {
                    "operation": "replace",
                    "record_type": "confirmation",
                    "record_id": "CONF-001",
                    "reason": "重新绑定当前档案版本并保留既有处理范围",
                    "content_markdown": (
                        "用户自然语言确认摘要：原处理范围保持不变，用户仅更正联系电话。\n"
                        f"绑定档案修订版本：{corrected_revision}\n"
                        "关键事实：劳动关系和工资差额已陈述。\n"
                        "权益范围：本次处理工资差额。\n"
                        "主要口径：以档案中的工资差额计算为准。\n"
                        "重要假设：未核实事项留待核对。\n"
                        "拟交付内容：仲裁申请书和证据目录。"
                    ),
                },
            ],
        }
    )
    if not committed["ok"]:
        raise AssertionError(committed)
    corrected_revision = committed["result"]["revision"]
    selection = create_delivery_set(
        {
            "case_id": case_id,
            "archive_revision": corrected_revision,
            "mode": "candidate",
            "requested_document_types": [
                "arbitration_application",
                "evidence_catalog",
            ],
            "confirmation_refs": [],
        },
        workspace,
    )
    if not selection["ok"]:
        raise AssertionError(selection)
    set_id = selection["result"]["delivery_set_id"]

    application = _application_request(
        case_id, corrected_revision, mode="candidate"
    )
    application["sections"][0]["full_text"] += f"联系电话：{NEW_PHONE}。"
    application["delivery_set_id"] = set_id
    evidence_catalog = _evidence_request(
        case_id, corrected_revision, mode="candidate"
    )
    evidence_catalog["delivery_set_id"] = set_id
    for request in (application, evidence_catalog):
        response = render(request, workspace)
        if not response["ok"]:
            raise AssertionError(response)
    return corrected_revision, set_id


def _replace_nested_text(value, old: str, new: str) -> bool:
    """Replace one matching string leaf in a copied snapshot structure."""

    if isinstance(value, dict):
        for key, item in value.items():
            if _replace_nested_text_in_mapping(value, key, item, old, new):
                return True
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str) and old in item:
                value[index] = item.replace(old, new, 1)
                return True
            if _replace_nested_text(item, old, new):
                return True
    return False


def _replace_nested_text_in_mapping(mapping, key, value, old: str, new: str) -> bool:
    if isinstance(value, str) and old in value:
        mapping[key] = value.replace(old, new, 1)
        return True
    return _replace_nested_text(value, old, new)


class PostDeliveryPhoneObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._fixture_temporary = TemporaryDirectory()
        cls._fixture_workspace = Path(cls._fixture_temporary.name)
        archive, case_id, revision, first_set_id = _deliver_candidate(
            cls._fixture_workspace
        )
        before = capture_delivery_snapshot(cls._fixture_workspace)
        if not before["verified"]:
            raise AssertionError(before)
        if (
            before["case_id"] != case_id
            or before["archive_revision"] != revision
            or before["delivery_set_id"] != first_set_id
            or set(before["ordered_blocks"])
            != {"arbitration_application", "evidence_catalog"}
            or len(before["files"]) != 4
        ):
            raise AssertionError(before)
        corrected_revision, corrected_set_id = _correct_phone_and_rerender(
            cls._fixture_workspace, archive, case_id, revision
        )
        after = capture_delivery_snapshot(cls._fixture_workspace)
        if not after["verified"]:
            raise AssertionError(after)
        if (
            after["case_id"] != case_id
            or after["archive_revision"] != corrected_revision
            or after["delivery_set_id"] != corrected_set_id
            or after["delivery_set_id"] == before["delivery_set_id"]
            or len(after["files"]) != 4
        ):
            raise AssertionError(after)
        for file in after["files"]:
            if (
                file["archive_revision"] != corrected_revision
                or file["delivery_set_id"] != corrected_set_id
                or not isinstance(file["size"], int)
                or file["size"] <= 0
                or re.fullmatch(r"[0-9a-f]{64}", file["sha256"]) is None
            ):
                raise AssertionError(file)
        cls._fixture_case_id = case_id
        cls._fixture_revision = revision
        cls._fixture_before = before
        cls._fixture_after = after

    @classmethod
    def tearDownClass(cls):
        cls._fixture_temporary.cleanup()

    def _real_before_after(self):
        return (
            self._fixture_workspace,
            deepcopy(self._fixture_before),
            deepcopy(self._fixture_after),
        )

    def test_public_rerender_passes_only_phone_change_with_current_two_member_view(self):
        _workspace, before, after = self._real_before_after()

        self.assertEqual(
            check_phone_correction(
                before, after, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            ),
            [],
        )

    def test_retained_backup_identity_and_shape_are_required(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        archive, case_id, revision, _set_id = _deliver_candidate(workspace)
        before = capture_delivery_snapshot(workspace)
        self.assertTrue(before["verified"], before)

        first_view = managed_delivery_view(workspace=workspace, case_id=case_id)
        self.assertFalse(first_view["stopped"], first_view)
        old_docx = path_for_io(first_view["presentation_files"][0]["path"])
        old_docx.write_bytes(old_docx.read_bytes() + b"external Office save")
        rejected_old = capture_delivery_snapshot(workspace)
        self.assertFalse(rejected_old["verified"], rejected_old)
        self.assertEqual(rejected_old["ordered_blocks"], {})
        self.assertEqual(rejected_old["files"], [])

        _corrected_revision, _new_set_id = _correct_phone_and_rerender(
            workspace, archive, case_id, revision
        )
        recovered = capture_delivery_snapshot(workspace)
        self.assertTrue(recovered["verified"], recovered)
        output_root = (
            workspace / ".arbibuddy" / "cases" / case_id / "output"
        )
        backups = [
            path
            for path in output_root.iterdir()
            if path.name.startswith(
                ".arbitration_application.committed-backup-"
            )
        ]
        self.assertEqual(len(backups), 1, backups)
        backup_manifest = next(backups[0].glob("*机器交付清单.json"))
        original_manifest = backup_manifest.read_bytes()

        for field, value in (
            ("case_id", "case-000000000000000000000000"),
            ("document_type", "evidence_catalog"),
        ):
            with self.subTest(field=field):
                invalid_manifest = json.loads(original_manifest.decode("utf-8"))
                invalid_manifest[field] = value
                backup_manifest.write_text(
                    json.dumps(invalid_manifest, ensure_ascii=False, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
                rejected_backup = capture_delivery_snapshot(workspace)
                self.assertFalse(rejected_backup["verified"], rejected_backup)
                self.assertEqual(rejected_backup["ordered_blocks"], {})
                self.assertEqual(rejected_backup["files"], [])
                rejected_view = managed_delivery_view(
                    workspace=workspace, case_id=case_id
                )
                self.assertTrue(rejected_view["stopped"], rejected_view)
                self.assertEqual(rejected_view["presentation_files"], [])
                backup_manifest.write_bytes(original_manifest)

        extra_file = backups[0] / "unexpected.txt"
        extra_file.write_text("unexpected retained file", encoding="utf-8")
        rejected_extra = capture_delivery_snapshot(workspace)
        self.assertFalse(rejected_extra["verified"], rejected_extra)
        self.assertEqual(rejected_extra["ordered_blocks"], {})
        self.assertEqual(rejected_extra["files"], [])
        rejected_extra_view = managed_delivery_view(
            workspace=workspace, case_id=case_id
        )
        self.assertTrue(rejected_extra_view["stopped"], rejected_extra_view)
        self.assertEqual(rejected_extra_view["presentation_files"], [])

    def test_rejects_unverified_wrong_case_and_non_advancing_revision(self):
        _workspace, before, after = self._real_before_after()

        unverified = deepcopy(after)
        unverified["verified"] = False
        self.assertTrue(
            check_phone_correction(
                before, unverified, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )
        wrong_case = deepcopy(after)
        wrong_case["case_id"] = "case-000000000000000000000000"
        self.assertTrue(
            check_phone_correction(
                before, wrong_case, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )
        for revision in (before["archive_revision"], before["archive_revision"] - 1):
            stale = deepcopy(after)
            stale["archive_revision"] = revision
            self.assertTrue(
                check_phone_correction(
                    before, stale, old_phone=OLD_PHONE, new_phone=NEW_PHONE
                ),
                f"must reject revision {revision}",
            )

    def test_rejects_missing_member_or_any_of_the_four_current_files(self):
        _workspace, before, after = self._real_before_after()

        missing_document = deepcopy(after)
        del missing_document["ordered_blocks"]["evidence_catalog"]
        self.assertTrue(
            check_phone_correction(
                before, missing_document, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )
        missing_file = deepcopy(after)
        missing_file["files"].pop()
        self.assertTrue(
            check_phone_correction(
                before, missing_file, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

    def test_rejects_missing_or_unchanged_phone_and_non_phone_text_changes(self):
        _workspace, before, after = self._real_before_after()

        no_initial_phone = deepcopy(before)
        self.assertTrue(
            _replace_nested_text(
                no_initial_phone["ordered_blocks"], OLD_PHONE, "13600000000"
            )
        )
        self.assertTrue(
            check_phone_correction(
                no_initial_phone, after, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

        unchanged_phone = deepcopy(after)
        self.assertTrue(
            _replace_nested_text(
                unchanged_phone["ordered_blocks"], NEW_PHONE, OLD_PHONE
            )
        )
        self.assertTrue(
            check_phone_correction(
                before, unchanged_phone, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

        archive_phone_unchanged = deepcopy(after)
        archive_phone_unchanged["archive_fact_text"] = before["archive_fact_text"]
        self.assertTrue(
            check_phone_correction(
                before,
                archive_phone_unchanged,
                old_phone=OLD_PHONE,
                new_phone=NEW_PHONE,
            )
        )

        changed_other_text = deepcopy(after)
        application = changed_other_text["ordered_blocks"]["arbitration_application"]
        target = next(
            block
            for block in application
            if block.get("kind") == "paragraph" and block.get("text")
        )
        target["text"] += "未授权的正文变化"
        self.assertTrue(
            check_phone_correction(
                before, changed_other_text, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

    def test_rejects_table_and_numbering_changes(self):
        _workspace, before, after = self._real_before_after()

        changed_table = deepcopy(after)
        catalog = changed_table["ordered_blocks"]["evidence_catalog"]
        table = next(block for block in catalog if block.get("kind") == "table")
        table["rows"][0][0][0]["text"] += "改动"
        self.assertTrue(
            check_phone_correction(
                before, changed_table, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

        numbered_before = before["ordered_blocks"]["arbitration_application"]
        numbered = next(
            block
            for block in numbered_before
            if block.get("kind") == "paragraph" and block.get("numbering")
        )
        changed_numbering = deepcopy(after)
        changed_app = changed_numbering["ordered_blocks"]["arbitration_application"]
        changed_numbered = next(
            block
            for block in changed_app
            if block.get("kind") == "paragraph" and block.get("numbering")
        )
        changed_numbered["numbering"] = deepcopy(numbered["numbering"])
        num_id = next(
            key for key in changed_numbered["numbering"] if key == "numId"
        )
        value_key = next(
            key
            for key in changed_numbered["numbering"][num_id]
            if key.endswith("}val")
        )
        changed_numbered["numbering"][num_id][value_key] = "999"
        self.assertTrue(
            check_phone_correction(
                before, changed_numbering, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

    def test_requires_new_set_identity_and_manifest_metadata_bound_to_revision(self):
        _workspace, before, after = self._real_before_after()

        reused_set = deepcopy(after)
        reused_set["delivery_set_id"] = before["delivery_set_id"]
        for file in reused_set["files"]:
            file["delivery_set_id"] = before["delivery_set_id"]
        self.assertTrue(
            check_phone_correction(
                before, reused_set, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

        stale_manifest = deepcopy(after)
        stale_manifest["files"][0]["archive_revision"] = before["archive_revision"]
        self.assertTrue(
            check_phone_correction(
                before, stale_manifest, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            )
        )

    def test_changed_canonical_file_is_rejected_instead_of_reused(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        _archive, _case_id, _revision, _set_id = _deliver_candidate(workspace)
        valid = capture_delivery_snapshot(workspace)
        self.assertTrue(valid["verified"], valid)

        view = managed_delivery_view(
            workspace=workspace, case_id=valid["case_id"]
        )
        self.assertFalse(view["stopped"], view)
        canonical_docx = path_for_io(view["presentation_files"][0]["path"])
        canonical_docx.write_bytes(canonical_docx.read_bytes() + b"external edit")

        rejected = capture_delivery_snapshot(workspace)
        self.assertFalse(rejected["verified"], rejected)
        self.assertEqual(rejected["ordered_blocks"], {})
        self.assertEqual(rejected["files"], [])

    def test_external_save_is_rejected_then_archive_based_rebuild_recovers(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        archive, case_id, revision, _set_id = _deliver_candidate(workspace)
        before = capture_delivery_snapshot(workspace)
        self.assertTrue(before["verified"], before)

        view = managed_delivery_view(workspace=workspace, case_id=case_id)
        self.assertFalse(view["stopped"], view)
        canonical_docx = path_for_io(view["presentation_files"][0]["path"])
        old_output_dir = canonical_docx.parent
        old_manifest_path = next(old_output_dir.glob("*机器交付清单.json"))
        original_manifest_bytes = old_manifest_path.read_bytes()
        original_manifest = json.loads(original_manifest_bytes.decode("utf-8"))
        original_document_sha256 = original_manifest["document"]["sha256"]
        canonical_docx.write_bytes(canonical_docx.read_bytes() + b"external Office save")
        externally_saved_docx_bytes = canonical_docx.read_bytes()
        rejected = capture_delivery_snapshot(workspace)
        self.assertFalse(rejected["verified"], rejected)
        self.assertEqual(rejected["ordered_blocks"], {})
        self.assertEqual(rejected["files"], [])

        corrected_revision, _new_set_id = _correct_phone_and_rerender(
            workspace, archive, case_id, revision
        )
        after = capture_delivery_snapshot(workspace)
        self.assertTrue(after["verified"], after)
        self.assertEqual(after["archive_revision"], corrected_revision)
        self.assertEqual(
            check_phone_correction(
                before, after, old_phone=OLD_PHONE, new_phone=NEW_PHONE
            ),
            [],
        )
        output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
        retained_backups = [
            path
            for path in output_root.iterdir()
            if path.name.startswith(
                ".arbitration_application.committed-backup-"
            )
        ]
        self.assertEqual(len(retained_backups), 1, retained_backups)
        retained_backup = retained_backups[0]
        retained_manifest = next(retained_backup.glob("*机器交付清单.json"))
        retained_document_name = json.loads(
            retained_manifest.read_text(encoding="utf-8")
        )["document"]["filename"]
        retained_document = retained_backup / retained_document_name

        def assert_external_generation_is_unchanged():
            self.assertEqual(
                retained_document.read_bytes(), externally_saved_docx_bytes
            )
            self.assertEqual(retained_manifest.read_bytes(), original_manifest_bytes)
            retained_manifest_data = json.loads(
                retained_manifest.read_text(encoding="utf-8")
            )
            self.assertEqual(
                retained_manifest_data["document"]["sha256"],
                original_document_sha256,
            )

        # A successful replacement must keep the externally edited prior
        # generation intact and must not rewrite its stale SHA into a new claim.
        assert_external_generation_is_unchanged()

        # Replaying both exact corrected requests must leave the retained user
        # save and its original manifest untouched.
        replay_application = _application_request(
            case_id, corrected_revision, mode="candidate"
        )
        replay_application["sections"][0]["full_text"] += (
            f"联系电话：{NEW_PHONE}。"
        )
        replay_application["delivery_set_id"] = after["delivery_set_id"]
        replay_catalog = _evidence_request(
            case_id, corrected_revision, mode="candidate"
        )
        replay_catalog["delivery_set_id"] = after["delivery_set_id"]
        for request in (replay_application, replay_catalog):
            replayed = render(request, workspace)
            self.assertTrue(replayed["ok"], replayed)
        replayed_snapshot = capture_delivery_snapshot(workspace)
        self.assertTrue(replayed_snapshot["verified"], replayed_snapshot)
        assert_external_generation_is_unchanged()

        current_view = managed_delivery_view(
            workspace=workspace, case_id=case_id
        )
        self.assertFalse(current_view["stopped"], current_view)
        current_docx = path_for_io(current_view["presentation_files"][0]["path"])
        current_docx.write_bytes(
            current_docx.read_bytes() + b"corrupt new canonical"
        )
        rejected_current = capture_delivery_snapshot(workspace)
        self.assertFalse(rejected_current["verified"], rejected_current)
        self.assertEqual(rejected_current["ordered_blocks"], {})
        self.assertEqual(rejected_current["files"], [])

    def test_phone_fact_is_hidden_until_verified_first_delivery_then_required(self):
        _workspace, before, _after = self._real_before_after()
        journey = load_journey(PHONE_JOURNEY)
        used_facts = {fact_id: 1 for fact_id in journey.initial_fact_ids}
        transcript = {
            "turn": 1,
            "user_message": journey.initial_user_message,
            "assistant_response": "首轮候选文书和清单已经生成。",
            "selected_fact_ids": list(journey.initial_fact_ids),
            "public_artifact_snapshot": {
                "verified": False,
                "ordered_blocks": {},
                "files": [],
            },
        }
        early_request = _build_scenario_response_request(
            journey,
            transcript["assistant_response"],
            [transcript],
            used_facts,
            0,
            turn=2,
        )
        phone_fact_id = journey.postdelivery_phone_correction["fact_id"]
        self.assertNotIn(
            phone_fact_id,
            {fact.id for fact in early_request.available_facts},
        )
        self.assertNotEqual(early_request.required_next_fact_id, phone_fact_id)
        self.assertFalse(early_request.public_delivery_observation["verified"])
        with self.assertRaises(ScenarioResponseError):
            validate_scenario_response(
                early_request,
                ScenarioResponseDecision(
                    action="answer",
                    selected_fact_ids=(phone_fact_id,),
                    confidence="high",
                    reason_code="correction",
                ),
            )

        transcript["public_artifact_snapshot"] = before
        before_confirmation_request = _build_scenario_response_request(
            journey,
            transcript["assistant_response"],
            [transcript],
            used_facts,
            0,
            turn=2,
        )
        self.assertTrue(
            before_confirmation_request.public_delivery_observation["verified"]
        )
        self.assertNotIn(
            phone_fact_id,
            {fact.id for fact in before_confirmation_request.available_facts},
        )
        self.assertNotEqual(
            before_confirmation_request.required_next_fact_id, phone_fact_id
        )

        responder_history = [
            {
                "turn": 1,
                "assistant_response": "请问是否扩大本轮范围、是否有材料补充？",
                "selected_fact_ids": list(journey.initial_fact_ids),
                "public_artifact_snapshot": {
                    "verified": False,
                    "ordered_blocks": {},
                    "files": [],
                },
            },
            {
                "turn": 2,
                "assistant_response": "请核对最终事实摘要。",
                "selected_fact_ids": [
                    "rights_scan_scope",
                    "final_supplement_none",
                ],
                "public_artifact_snapshot": {
                    "verified": False,
                    "ordered_blocks": {},
                    "files": [],
                },
            },
            {
                "turn": 3,
                "assistant_response": "仲裁申请书和证据目录候选稿已通过查看。",
                "selected_fact_ids": ["final_confirmation"],
                "public_artifact_snapshot": before,
            },
        ]
        used_after_confirmation = {
            **used_facts,
            "rights_scan_scope": 1,
            "final_supplement_none": 1,
            "final_confirmation": 1,
        }
        ready_request = _build_scenario_response_request(
            journey,
            responder_history[-1]["assistant_response"],
            responder_history,
            used_after_confirmation,
            0,
            turn=4,
        )
        self.assertIn(
            phone_fact_id,
            {fact.id for fact in ready_request.available_facts},
        )
        self.assertEqual(ready_request.required_next_fact_id, phone_fact_id)
        self.assertEqual(
            ready_request.public_delivery_observation,
            {
                "verified": True,
                "document_types": [
                    "arbitration_application",
                    "evidence_catalog",
                ],
            },
        )
        validate_scenario_response(
            ready_request,
            ScenarioResponseDecision(
                action="answer",
                selected_fact_ids=(phone_fact_id,),
                confidence="high",
                reason_code="correction",
            ),
        )

    def test_oracle_needs_a_valid_before_after_snapshot_not_only_final_files_or_reply(self):
        workspace, before, after = self._real_before_after()
        journey = load_journey(PHONE_JOURNEY)
        phone_fact_id = journey.postdelivery_phone_correction["fact_id"]
        correction_fact = next(
            fact for fact in journey.fact_pool if fact.id == phone_fact_id
        )
        first_transcript = {
            "turn": 1,
            "user_message": journey.initial_user_message,
            "assistant_response": "首轮仲裁申请书候选稿、证据目录和核对清单已交付。",
            "selected_fact_ids": list(journey.initial_fact_ids),
            "public_artifact_snapshot": before,
        }
        corrected_transcript = {
            "turn": 2,
            "user_message": correction_fact.text,
            "assistant_response": "已仅将申请人联系电话更正为13900000000，重新生成候选稿、证据目录和核对清单并完成查看。",
            "selected_fact_ids": [phone_fact_id],
            "public_artifact_snapshot": after,
        }
        observation = _observe_workspace(workspace)
        oracle = JourneyOracle(journey)
        valid_state = JourneyPublicState(
            journey=journey,
            transcripts=(first_transcript, corrected_transcript),
            sent_fact_ids=(*journey.initial_fact_ids, phone_fact_id),
            observation=observation,
            runtime_completed=True,
        )
        self.assertTrue(oracle.evaluate(valid_state).passed)

        no_first_snapshot = dict(first_transcript)
        no_first_snapshot["public_artifact_snapshot"] = {
            "verified": False,
            "ordered_blocks": {},
            "files": [],
        }
        final_files_without_baseline = JourneyPublicState(
            journey=journey,
            transcripts=(no_first_snapshot, corrected_transcript),
            sent_fact_ids=(*journey.initial_fact_ids, phone_fact_id),
            observation=observation,
            runtime_completed=True,
        )
        self.assertFalse(oracle.evaluate(final_files_without_baseline).passed)

        response_without_current_files = JourneyPublicState(
            journey=journey,
            transcripts=(first_transcript, corrected_transcript),
            sent_fact_ids=(*journey.initial_fact_ids, phone_fact_id),
            observation={},
            runtime_completed=True,
        )
        self.assertFalse(oracle.evaluate(response_without_current_files).passed)

    def test_canonical_ml01_without_opt_in_keeps_responder_input_unchanged(self):
        journey = load_journey(CANONICAL_ML01)
        self.assertIsNone(journey.postdelivery_phone_correction)
        legacy_without_opt_in = replace(journey, postdelivery_phone_correction=None)
        transcript = {
            "turn": 1,
            "assistant_response": "已准备仲裁申请书和证据目录。",
            "selected_fact_ids": [],
            "public_artifact_snapshot": {
                "verified": True,
                "ordered_blocks": {
                    "arbitration_application": [],
                    "evidence_catalog": [],
                },
                "files": [],
            },
        }
        request = _build_scenario_response_request(
            legacy_without_opt_in,
            transcript["assistant_response"],
            [transcript],
            {},
            0,
            turn=2,
        )
        self.assertIsNone(request.public_delivery_observation)
        self.assertNotIn("public_delivery_observation", request.prompt_payload())
        self.assertIsNone(request.required_next_fact_id)

    def test_equal_hash_for_an_unchanged_member_is_not_itself_a_failure(self):
        """A new revision/set can legitimately retain an unchanged DOCX member."""

        _workspace, before, after = self._real_before_after()
        before_catalog = next(
            file
            for file in before["files"]
            if file["document_type"] == "evidence_catalog" and file["kind"] == "document"
        )
        after_catalog = next(
            file
            for file in after["files"]
            if file["document_type"] == "evidence_catalog" and file["kind"] == "document"
        )

        # Model the legal unchanged-member case without requiring the renderer
        # to emit byte-identical ZIP metadata on every run. All other evidence
        # remains from real public snapshots; only the cross-revision digest
        # equality is varied because the observer must not require it to change.
        after_same_member_hash = deepcopy(after)
        target = next(
            file
            for file in after_same_member_hash["files"]
            if file["document_type"] == "evidence_catalog" and file["kind"] == "document"
        )
        target["sha256"] = before_catalog["sha256"]
        target["content_sha256"] = before_catalog.get(
            "content_sha256", before_catalog["sha256"]
        )
        self.assertEqual(
            check_phone_correction(
                before,
                after_same_member_hash,
                old_phone=OLD_PHONE,
                new_phone=NEW_PHONE,
            ),
            [],
            msg=(
                "unchanged evidence_catalog.docx may retain its digest across a "
                f"new revision (observed real digest equality: {before_catalog['sha256'] == after_catalog['sha256']})"
            ),
        )


if __name__ == "__main__":
    unittest.main()
