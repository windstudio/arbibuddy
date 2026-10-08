from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.case_archive.service import REQUIRED_SECTIONS
from scripts.documents.public import render as public_render
from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as _create_case,
    demand_request as _demand_request,
    forced_request as _forced_request,
    visible_text as _visible_text,
)


class ModelLedDemandAndForcedRenderTests(unittest.TestCase):
    def test_candidate_salary_fact_is_preserved_in_demand_and_forced_notice(self):
        for factory, heading in ((_demand_request, "劳动关系说明"), (_forced_request, "解除理由")):
            with self.subTest(heading=heading), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision, _ = _create_case(workspace)
                saved = archive.commit({
                    "case_id": case_id, "expected_revision": revision,
                    "change_summary": "记录用户陈述的月薪",
                    "changes": [{"operation": "append", "record_type": "fact",
                                 "content_markdown": "用户陈述：约定月薪15000元，原件及工资构成待核。"}],
                })
                self.assertTrue(saved["ok"], saved)
                request = factory(case_id, saved["result"]["revision"], mode="candidate")
                section = next(item for item in request["sections"] if item["heading"] == heading)
                section["full_text"] += "据本人陈述，约定月薪15000元，原件及工资构成待核。"
                request["locked_bindings"].append({
                    "kind": "fact_amount", "archive_record_ref": saved["result"]["generated_record_ids"][0],
                    "rendered_value": "15000元",
                    "occurrences": [{"section_id": section["section_id"], "text": "约定月薪15000元"}],
                })
                result = public_render(request, workspace)
                self.assertTrue(result["ok"], result)
                text = _visible_text(Path(result["result"]["canonical_docx"]))
                self.assertIn("约定月薪15000元", text)
                self.assertIn("据本人陈述", text)

    def test_workbuddy_public_entry_renders_10_section_archive_delivery_loop(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = _create_case(workspace)

            self.assertEqual(len(REQUIRED_SECTIONS), 10)
            archived = archive.read(case_id)
            self.assertTrue(archived["ok"], archived)
            demand_result = public_render(
                _demand_request(case_id, revision),
                workspace,
            )

            self._assert_public_delivery(
                demand_result,
                title="劳动用工义务催告函",
                revision=revision,
            )

            forced_workspace = workspace / "forced"
            _archive, forced_case_id, forced_revision, confirmation_id = _create_case(
                forced_workspace,
                with_confirmation=True,
            )
            assert confirmation_id is not None
            forced_result = public_render(
                _forced_request(
                    forced_case_id,
                    forced_revision,
                    confirmation_refs=[confirmation_id],
                    authority_refs=["AUTH-001"],
                ),
                forced_workspace,
            )
            self._assert_public_delivery(
                forced_result,
                title="被迫解除劳动合同通知书",
                revision=forced_revision,
            )

    def _assert_public_delivery(
        self,
        result: dict[str, object],
        *,
        title: str,
        revision: int,
    ) -> None:
        self.assertTrue(result["ok"], result)
        payload = result["result"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["delivery_state"], "final_ready")
        self.assertEqual(payload["archive_revision"], revision)
        document = Path(str(payload["canonical_docx"]))
        checklist = Path(str(payload["verification_checklist"]))
        manifest = Path(str(payload["machine_manifest"]))
        self.assertTrue(document.is_file())
        self.assertTrue(checklist.is_file())
        self.assertTrue(manifest.is_file())
        visible = _visible_text(document)
        self.assertIn(title, visible)
        self.assertIn("张三", visible)
        self.assertIn("示例公司", visible)
        self.assertIn("6000.00元", visible)
        for user_text in (
            visible,
            checklist.read_text(encoding="utf-8"),
        ):
            self.assertNotRegex(
                user_text,
                r"(?<![A-Za-z0-9_])(?:F|CL|E|A|CAL|AUTH|R|CONF|N|SC|OR|DR)-\d{3,}(?![A-Za-z0-9_])",
            )
            self.assertNotIn("case_id", user_text)
        self.assertIn("仅供核对，请勿外发", checklist.read_text(encoding="utf-8"))
        machine_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(machine_manifest["contract_version"], "document.render-v1")
        self.assertEqual(machine_manifest["operation"], "document.render")
        with ZipFile(document) as package:
            self.assertIn("[Content_Types].xml", package.namelist())
            self.assertIn("word/document.xml", package.namelist())
            self.assertIn("word/styles.xml", package.namelist())

    def test_public_entry_rejects_legacy_14_section_archive(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(workspace)
            archive_path = (
                workspace / ".arbibuddy" / "cases" / case_id / "案情档案.md"
            )
            legacy_sections = "\n".join(
                f"## 旧迁移章节 {index}" for index in range(1, 5)
            )
            archive_path.write_text(
                archive_path.read_text(encoding="utf-8")
                + "\n"
                + legacy_sections
                + "\n",
                encoding="utf-8",
            )

            archived = _archive.read(case_id)
            result = public_render(
                _demand_request(case_id, revision),
                workspace,
            )

            self.assertFalse(archived["ok"], archived)
            self.assertFalse(result["ok"], result)
            self.assertIn("archive_corrupt", {item["code"] for item in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_public_contract_exposes_both_08_document_types(self):
        with TemporaryDirectory() as temporary_root:
            contract = DocumentRenderer(Path(temporary_root)).describe()

        self.assertEqual(contract["entrypoint"], "scripts.documents.public.render")
        self.assertEqual(contract["archive_contract"], "case-archive-v1")

        self.assertEqual(
            set(contract["input"]["document_types"]),
            {
                "employment_obligation_demand_letter",
                "forced_termination_notice",
                "arbitration_application",
                "arbitration_defense",
                "evidence_catalog",
                "company_deregistration_restriction_request_shanghai",
                "property_preservation_application",
                "enforcement_application",
            },
        )
        self.assertIn("confirmation_missing", contract["errors"]["codes"])
        self.assertIn("authority_required", contract["errors"]["codes"])
        self.assertIn("document_type_conflict", contract["errors"]["codes"])

    def test_demand_candidate_and_external_final_are_real_model_authored_docx(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(workspace)
            renderer = DocumentRenderer(workspace)
            candidate = _demand_request(case_id, revision, mode="candidate")
            candidate["sections"][2]["full_text"] += " 联系电话：【待补：劳动者联系电话】。"
            candidate["placeholders"] = [
                {"placeholder_id": "employee_phone", "text": "【待补：劳动者联系电话】"}
            ]

            candidate_result = renderer.render(candidate)

            self.assertTrue(candidate_result["ok"], candidate_result)
            self.assertEqual(candidate_result["result"]["delivery_state"], "candidate_ready")
            self.assertIn("劳动用工义务催告函", _visible_text(Path(candidate_result["result"]["canonical_docx"])))

            candidate["mode"] = "external_final"
            promoted = renderer.render(candidate)
            self.assertFalse(promoted["ok"], promoted)
            self.assertIn("placeholder_in_final", {item["code"] for item in promoted["errors"]})

            final_result = renderer.render(_demand_request(case_id, revision))
            self.assertTrue(final_result["ok"], final_result)
            self.assertEqual(final_result["result"]["delivery_state"], "final_ready")
            final_path = Path(final_result["result"]["canonical_docx"])
            self.assertTrue(final_path.is_file())
            self.assertIn("6000.00元", _visible_text(final_path))
            self.assertEqual(len(list(final_path.parent.glob("*.docx"))), 1)

    def test_demand_rejects_termination_notice_semantics_but_allows_explicit_boundary(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(workspace)
            renderer = DocumentRenderer(workspace)

            for prohibited_text in (
                "请于期限届满前支付欠款，否则逾期即解除劳动合同。",
                "请于期限届满前支付欠款，逾期后解除劳动合同。",
                "请于期限届满前支付欠款，逾期不支付就解除劳动合同。",
                "请于期限届满前履行义务，未按期履行则解除劳动合同。",
                "本函作为解除通知书发送。",
                "本函作为解除劳动关系通知书发送。",
                "本函作为终止劳动合同通知书发送。",
                "本函记载劳动关系终止。",
                "本函用于解除劳动合同。",
                "本函用于终止劳动合同。",
                "本函记载劳动关系解除。",
                "本函记载劳动合同终止。",
                "逾期未按时支付将解除劳动合同。",
                "未按期支付工资导致解除劳动合同。",
                "未按时履行义务即终止劳动关系。",
                "本函用于生效裁判履行催告。",
            ):
                with self.subTest(prohibited_text=prohibited_text):
                    prohibited = _demand_request(case_id, revision, mode="candidate")
                    prohibited["sections"][2]["full_text"] = prohibited_text
                    rejected = renderer.render(prohibited)

                    self.assertFalse(rejected["ok"], rejected)
                    self.assertIn(
                        "document_type_conflict",
                        {item["code"] for item in rejected["errors"]},
                    )
            boundary = _demand_request(case_id, revision, mode="candidate")
            accepted = renderer.render(boundary)
            self.assertTrue(accepted["ok"], accepted)

    def test_candidate_missing_lock_requires_a_used_placeholder(self):
        for document_type, request_factory, with_confirmation in (
            ("employment_obligation_demand_letter", _demand_request, False),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = _create_case(
                    workspace, with_confirmation=with_confirmation
                )
                kwargs = (
                    {"confirmation_refs": [confirmation_id], "authority_refs": ["AUTH-001"]}
                    if with_confirmation
                    else {"authority_refs": []}
                )
                request = request_factory(case_id, revision, mode="candidate", **kwargs)
                request["locked_bindings"] = [
                    binding
                    for binding in request["locked_bindings"]
                    if binding["kind"] != "signature"
                ]
                missing = DocumentRenderer(workspace).render(request)
                self.assertFalse(missing["ok"], missing)
                self.assertIn("missing_input", {item["code"] for item in missing["errors"]})

                request["sections"][0]["full_text"] += " 联系方式：【待补：联系电话】。"
                request["placeholders"] = [
                    {"placeholder_id": "phone", "text": "【待补：联系电话】"}
                ]
                unrelated = DocumentRenderer(workspace).render(request)
                self.assertFalse(unrelated["ok"], unrelated)
                self.assertIn("missing_input", {item["code"] for item in unrelated["errors"]})

                request["sections"][0]["full_text"] = request["sections"][0]["full_text"].replace(
                    " 联系方式：【待补：联系电话】。", ""
                )
                request["sections"][-1]["full_text"] += " 签名：【待补：签名】。"
                request["placeholders"] = [
                    {"placeholder_id": "signature", "text": "【待补：签名】"}
                ]
                candidate = DocumentRenderer(workspace).render(request)
                self.assertTrue(candidate["ok"], candidate)

    def test_forced_candidate_accepts_employee_name_placeholder_in_closing(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(workspace)
            request = _forced_request(case_id, revision, mode="candidate")
            request["locked_bindings"] = [
                binding for binding in request["locked_bindings"]
                if binding["kind"] not in {"party", "date", "signature"}
            ]
            request["sections"][0]["full_text"] = "致：【待补：用人单位名称】"
            request["sections"][1]["full_text"] = (
                "本人现通知贵单位解除劳动合同，最后工作日为【待补：最后工作日】。"
            )
            request["sections"][-1]["full_text"] = (
                "通知人：【待补：劳动者姓名】\n日期：【待补：签署日期】"
            )
            request["placeholders"] = [
                {"placeholder_id": name, "text": text}
                for name, text in (
                    ("employer", "【待补：用人单位名称】"),
                    ("last_day", "【待补：最后工作日】"),
                    ("employee", "【待补：劳动者姓名】"),
                    ("sign_date", "【待补：签署日期】"),
                )
            ]

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "candidate_ready")

    def test_forced_candidate_is_available_but_external_final_requires_confirmation(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(workspace)
            renderer = DocumentRenderer(workspace)

            candidate = renderer.render(
                _forced_request(case_id, revision, mode="candidate")
            )
            self.assertTrue(candidate["ok"], candidate)
            self.assertEqual(candidate["result"]["delivery_state"], "candidate_ready")

            final = renderer.render(_forced_request(case_id, revision))
            self.assertFalse(final["ok"], final)
            codes = {item["code"] for item in final["errors"]}
            self.assertIn("confirmation_missing", codes)
            self.assertIn("authority_required", codes)
            output = workspace / ".arbibuddy" / "cases" / case_id / "output" / "forced_termination_notice"
            self.assertFalse((output / "《被迫解除劳动合同通知书》.docx").exists())
            self.assertTrue((output / "《被迫解除劳动合同通知书》（候选草稿）.docx").is_file())

    def test_both_document_types_reject_final_placeholders_and_internal_content(self):
        for document_type, request_factory, with_confirmation in (
            (
                "employment_obligation_demand_letter",
                _demand_request,
                False,
            ),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type):
                with TemporaryDirectory() as temporary_root:
                    workspace = Path(temporary_root)
                    _archive, case_id, revision, confirmation_id = _create_case(
                        workspace, with_confirmation=with_confirmation
                    )
                    confirmation_refs = [confirmation_id] if confirmation_id else None
                    authority_refs = ["AUTH-001"] if with_confirmation else None
                    request = request_factory(
                        case_id,
                        revision,
                        mode="candidate",
                        **(
                            {
                                "confirmation_refs": confirmation_refs,
                                "authority_refs": authority_refs,
                            }
                            if document_type == "forced_termination_notice"
                            else {"authority_refs": []}
                        ),
                    )
                    request["sections"][0]["full_text"] += " 联系方式：【待补：联系电话】。"
                    request["placeholders"] = [
                        {"placeholder_id": "phone", "text": "【待补：联系电话】"}
                    ]
                    renderer = DocumentRenderer(workspace)
                    candidate = renderer.render(request)
                    self.assertTrue(candidate["ok"], candidate)

                    request["mode"] = "external_final"
                    promoted = renderer.render(request)
                    self.assertFalse(promoted["ok"], promoted)
                    self.assertIn(
                        "placeholder_in_final",
                        {item["code"] for item in promoted["errors"]},
                    )

                    leaky = request_factory(
                        case_id,
                        revision,
                        mode="candidate",
                        **(
                            {
                                "confirmation_refs": confirmation_refs,
                                "authority_refs": authority_refs,
                            }
                            if document_type == "forced_termination_notice"
                            else {"authority_refs": []}
                        ),
                    )
                    leaky["sections"][0]["full_text"] = "收件人：示例公司（内部记录 F-001）。"
                    leaked = renderer.render(leaky)
                    self.assertFalse(leaked["ok"], leaked)
                    self.assertIn(
                        "internal_content_leak",
                        {item["code"] for item in leaked["errors"]},
                    )

    def test_forced_external_final_consumes_current_confirmation_and_authority(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, confirmation_id = _create_case(
                workspace, with_confirmation=True
            )
            assert confirmation_id is not None
            request = _forced_request(
                case_id,
                revision,
                confirmation_refs=[confirmation_id],
                authority_refs=["AUTH-001"],
            )

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "final_ready")
            text = _visible_text(Path(result["result"]["canonical_docx"]))
            self.assertIn("被迫解除劳动合同通知书", text)
            self.assertIn("未及时足额支付劳动报酬", text)
            self.assertNotIn("CONF-", text)

    def test_forced_confirmation_scope_sources_and_authority_are_bound_to_this_notice(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, confirmation_id = _create_case(
                workspace, with_confirmation=True
            )
            assert confirmation_id is not None
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录本次通知新增的解除证据和确认范围",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": "第二份欠薪材料｜工资明细｜补充证明解除理由。",
                        },
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "高风险确认标识：planned_termination\n"
                                "action_type：planned_termination\n"
                                "action_scope：按当前事实准备被迫解除劳动合同通知书，劳动者张三向示例公司发出，理由为未及时足额支付劳动报酬，使用欠薪流水，通知日期为2026年9月8日，解除生效日期为2026年9月20日。\n"
                                "risk_summary：解除理由和送达事实可能影响后果。\n"
                                "alternatives_presented：先催告履行或暂缓解除。\n"
                                "user_choice：我明确选择按已说明范围继续准备被迫解除劳动合同通知书。\n"
                                "confirmed_at：2026-09-08\n"
                                f"archive_revision：{revision + 1}\n"
                                "source_refs：F-001、F-002、F-003、F-004、CL-001、E-001、AUTH-001。"
                            ),
                        },
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current_revision = int(changed["result"]["revision"])
            current_confirmation = str(changed["result"]["generated_record_ids"][1])
            request = _forced_request(
                case_id,
                current_revision,
                confirmation_refs=[current_confirmation],
                authority_refs=["AUTH-001"],
            )
            request["locked_bindings"][4] = {
                "kind": "evidence",
                "archive_record_ref": "E-002",
                "rendered_value": "第二份欠薪材料｜工资明细｜补充证明解除理由",
            }
            request["sections"][2]["full_text"] = "因贵单位未及时足额支付劳动报酬，理由由第二份欠薪材料进一步支持。"
            missing_source = DocumentRenderer(workspace).render(request)
            self.assertFalse(missing_source["ok"], missing_source)
            self.assertIn(
                "confirmation_missing",
                {item["code"] for item in missing_source["errors"]},
            )

            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": current_revision,
                    "change_summary": "替换为缺少适用范围的现行规则记录",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "authority",
                            "record_id": "AUTH-001",
                            "content_markdown": "核验结论：已核验；来源：官方规则。",
                            "reason": "测试现行规则适用范围门禁",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current = _forced_request(
                case_id,
                int(changed["result"]["revision"]),
                confirmation_refs=[current_confirmation],
                authority_refs=["AUTH-001"],
            )
            stale = DocumentRenderer(workspace).render(current)
            self.assertFalse(stale["ok"], stale)
            self.assertIn("authority_stale", {item["code"] for item in stale["errors"]})

    def test_confirmation_stale_after_archive_revision_change_does_not_replace_canonical(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, confirmation_id = _create_case(
                workspace, with_confirmation=True
            )
            assert confirmation_id is not None
            renderer = DocumentRenderer(workspace)
            valid = renderer.render(
                _forced_request(
                    case_id,
                    revision,
                    confirmation_refs=[confirmation_id],
                    authority_refs=["AUTH-001"],
                )
            )
            self.assertTrue(valid["ok"], valid)
            canonical = Path(valid["result"]["canonical_docx"])
            before = canonical.read_bytes()

            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录确认后新增的送达事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户补充了送达时间，待结合材料核对。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            stale_request = _forced_request(
                case_id,
                int(changed["result"]["revision"]),
                confirmation_refs=[confirmation_id],
                authority_refs=["AUTH-001"],
            )
            stale = renderer.render(stale_request)

            self.assertFalse(stale["ok"], stale)
            self.assertEqual(
                sum(item["code"] == "confirmation_stale" for item in stale["errors"]),
                1,
            )
            self.assertEqual(canonical.read_bytes(), before)
            self.assertEqual(len(list(canonical.parent.glob("*.docx"))), 1)

    def test_stale_dynamic_authority_allows_candidate_but_blocks_external_final(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(
                workspace, authority_status="过期"
            )
            renderer = DocumentRenderer(workspace)
            candidate = renderer.render(
                _forced_request(
                    case_id,
                    revision,
                    mode="candidate",
                    authority_refs=["AUTH-001"],
                )
            )
            self.assertTrue(candidate["ok"], candidate)
            self.assertEqual(candidate["result"]["delivery_state"], "candidate_ready")
            self.assertTrue(any(item.get("code") == "authority_stale" for item in candidate["warnings"]))

            final = renderer.render(
                _forced_request(
                    case_id,
                    revision,
                    authority_refs=["AUTH-001"],
                )
            )
            self.assertFalse(final["ok"], final)
            self.assertIn("confirmation_missing", {item["code"] for item in final["errors"]})
            self.assertIn("authority_stale", {item["code"] for item in final["errors"]})

    def test_forced_confirmation_rejects_wrong_action_and_missing_source_refs(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = _create_case(workspace)
            confirmation = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录不匹配的解除确认以验证窄门禁",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "高风险确认标识：employer_termination\n"
                                "action_type：employer_termination\n"
                                "action_scope：按当前事实准备用人单位解除动作。\n"
                                "risk_summary：需要核对解除理由、日期和送达。\n"
                                "alternatives_presented：先补证据或暂缓。\n"
                                "user_choice：我明确选择按已说明范围继续。\n"
                                "confirmed_at：2026-09-08\n"
                                f"archive_revision：{revision + 1}\n"
                                "source_refs：无。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(confirmation["ok"], confirmation)
            current_revision = int(confirmation["result"]["revision"])
            confirmation_id = str(confirmation["result"]["generated_record_ids"][0])

            result = DocumentRenderer(workspace).render(
                _forced_request(
                    case_id,
                    current_revision,
                    confirmation_refs=[confirmation_id],
                    authority_refs=["AUTH-001"],
                )
            )

            self.assertFalse(result["ok"], result)
            errors = result["errors"]
            self.assertGreaterEqual(
                sum(item["code"] == "confirmation_missing" for item in errors),
                2,
            )
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_forced_dynamic_authority_conflict_and_blocked_variants_keep_candidate_only(self):
        for authority_status in ("冲突", "受阻"):
            with self.subTest(authority_status=authority_status):
                with TemporaryDirectory() as temporary_root:
                    workspace = Path(temporary_root)
                    _archive, case_id, revision, _ = _create_case(
                        workspace, authority_status=authority_status
                    )
                    renderer = DocumentRenderer(workspace)
                    candidate = renderer.render(
                        _forced_request(
                            case_id,
                            revision,
                            mode="candidate",
                            authority_refs=["AUTH-001"],
                        )
                    )
                    self.assertTrue(candidate["ok"], candidate)
                    self.assertTrue(
                        any(item["code"] == "authority_stale" for item in candidate["warnings"])
                    )
                    self.assertEqual(
                        renderer.render(
                            _forced_request(
                                case_id,
                                revision,
                                mode="candidate",
                                authority_refs=["AUTH-001"],
                            )
                        ),
                        candidate,
                    )
                    final = renderer.render(
                        _forced_request(
                            case_id,
                            revision,
                            authority_refs=["AUTH-001"],
                        )
                    )
                    self.assertFalse(final["ok"], final)
                    self.assertIn(
                        "authority_stale",
                        {item["code"] for item in final["errors"]},
                    )

    def test_forced_fact_conflict_and_amount_binding_are_aggregated(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = _create_case(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录被迫解除理由冲突以验证外发门禁",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "claim",
                            "record_id": "CL-001",
                            "content_markdown": "事实冲突：解除理由存在两种说法。",
                            "reason": "测试被迫解除事实冲突门禁",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            request = _forced_request(
                case_id,
                int(changed["result"]["revision"]),
                authority_refs=["AUTH-001"],
            )
            request["sections"][3]["full_text"] = (
                "请结算经济补偿7000.00元并办理解除证明。"
            )

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            codes = {item["code"] for item in result["errors"]}
            self.assertIn("fact_conflict", codes)
            self.assertIn("binding_mismatch", codes)
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_rendered_currency_cannot_bypass_amount_binding_for_either_document_type(self):
        for document_type, request_factory, with_confirmation in (
            (
                "employment_obligation_demand_letter",
                _demand_request,
                False,
            ),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type):
                with TemporaryDirectory() as temporary_root:
                    workspace = Path(temporary_root)
                    _archive, case_id, revision, confirmation_id = _create_case(
                        workspace, with_confirmation=with_confirmation
                    )
                    request = request_factory(
                        case_id,
                        revision,
                        mode="external_final",
                        **(
                            {
                                "confirmation_refs": [confirmation_id],
                                "authority_refs": ["AUTH-001"],
                            }
                            if document_type == "forced_termination_notice"
                            else {"authority_refs": []}
                        ),
                    )
                    request["locked_bindings"] = [
                        binding
                        for binding in request["locked_bindings"]
                        if binding["kind"] != "amount"
                    ]

                    result = DocumentRenderer(workspace).render(request)

                    self.assertFalse(result["ok"], result)
                    self.assertIn(
                        "missing_input",
                        {item["code"] for item in result["errors"]},
                    )

    def test_fact_conflict_and_amount_binding_are_aggregated_before_publication(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = _create_case(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录冲突事实以验证绑定门禁",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "claim",
                            "record_id": "CL-001",
                            "content_markdown": "事实冲突：义务和解除理由存在两种说法。",
                            "reason": "测试事实冲突门禁",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            request = _demand_request(case_id, int(changed["result"]["revision"]))
            request["sections"][2]["full_text"] = "请支付2026年8月工资差额7000.00元。"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            codes = {item["code"] for item in result["errors"]}
            self.assertIn("fact_conflict", codes)
            self.assertIn("binding_mismatch", codes)
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_both_new_document_types_have_audited_ooxml_and_separate_checklist(self):
        for document_type, request_factory, with_confirmation in (
            ("employment_obligation_demand_letter", _demand_request, False),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = _create_case(
                    workspace, with_confirmation=with_confirmation
                )
                kwargs = (
                    {"confirmation_refs": [confirmation_id], "authority_refs": ["AUTH-001"]}
                    if with_confirmation
                    else {"authority_refs": []}
                )
                result = DocumentRenderer(workspace).render(
                    request_factory(case_id, revision, **kwargs)
                )
                self.assertTrue(result["ok"], result)
                docx = Path(result["result"]["canonical_docx"])
                checklist = Path(result["result"]["verification_checklist"])
                with ZipFile(docx) as package:
                    names = set(package.namelist())
                    document_xml = package.read("word/document.xml").decode("utf-8")
                    footer_xml = package.read("word/footer1.xml").decode("utf-8")
                self.assertIn("word/styles.xml", names)
                self.assertIn("word/numbering.xml", names)
                self.assertIn("word/footer1.xml", names)
                self.assertIn("<w:pgSz", document_xml)
                self.assertIn("<w:pgMar", document_xml)
                self.assertIn("PAGE", footer_xml)
                self.assertIn("NUMPAGES", footer_xml)
                self.assertNotIn("CONF-", checklist.read_text(encoding="utf-8"))
                self.assertNotIn("AUTH-", checklist.read_text(encoding="utf-8"))

    def test_replay_of_both_new_document_types_is_idempotent(self):
        for document_type, request_factory, with_confirmation in (
            ("employment_obligation_demand_letter", _demand_request, False),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = _create_case(
                    workspace, with_confirmation=with_confirmation
                )
                kwargs = (
                    {"confirmation_refs": [confirmation_id], "authority_refs": ["AUTH-001"]}
                    if with_confirmation
                    else {"authority_refs": []}
                )
                request = request_factory(case_id, revision, **kwargs)
                renderer = DocumentRenderer(workspace)

                first = renderer.render(request)
                replay = renderer.render(request)

                self.assertTrue(first["ok"], first)
                self.assertEqual(replay, first)
                output = Path(first["result"]["canonical_docx"]).parent
                self.assertEqual(len(list(output.glob("*.docx"))), 1)
                self.assertEqual(len(list(output.glob("*.txt"))), 1)
                self.assertEqual(len(list(output.glob("*.json"))), 1)

    def test_success_requires_canonical_artifacts_after_publication_returns(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(
                workspace, with_confirmation=False
            )
            request = _demand_request(case_id, revision, authority_refs=[])
            renderer = DocumentRenderer(workspace)

            # A publisher that reports success without promoting the staging tree
            # must not allow document.render-v1 to report a false success.
            with patch(
                "scripts.documents.public.commit_generation_staging"
            ) as commit:
                result = renderer.render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "publication_conflict",
                {item["code"] for item in result["errors"]},
            )

    def test_first_render_without_existing_output_keeps_all_canonical_artifacts(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _ = _create_case(
                workspace, with_confirmation=False
            )
            request = _demand_request(case_id, revision, authority_refs=[])

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            for key in ("canonical_docx", "verification_checklist", "machine_manifest"):
                path = Path(result["result"][key])
                self.assertTrue(path.is_file(), key)
            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
            self.assertTrue(output_root.is_dir())

    def test_new_document_delivery_has_separate_manifest_and_rolls_back_publish_failure(self):
        for document_type, request_factory, with_confirmation in (
            ("employment_obligation_demand_letter", _demand_request, False),
            ("forced_termination_notice", _forced_request, True),
        ):
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, confirmation_id = _create_case(
                    workspace, with_confirmation=with_confirmation
                )
                kwargs = (
                    {"confirmation_refs": [confirmation_id], "authority_refs": ["AUTH-001"]}
                    if with_confirmation
                    else {"authority_refs": []}
                )
                request = request_factory(case_id, revision, **kwargs)
                renderer = DocumentRenderer(workspace)

                with patch(
                    "scripts.documents.public.commit_generation_staging",
                    side_effect=OSError("injected publication failure"),
                ):
                    failed = renderer.render(request)
                self.assertFalse(failed["ok"], failed)
                self.assertIn("io_failure", {item["code"] for item in failed["errors"]})
                output = workspace / ".arbibuddy" / "cases" / case_id / "output"
                self.assertFalse(output.exists())

                successful = renderer.render(request)
                self.assertTrue(successful["ok"], successful)
                result = successful["result"]
                manifest = Path(result["machine_manifest"])
                checklist = Path(result["verification_checklist"])
                self.assertEqual(
                    {path.name for path in manifest.parent.iterdir()},
                    {manifest.name, checklist.name, Path(result["canonical_docx"]).name},
                )
                self.assertNotIn("F-001", checklist.read_text(encoding="utf-8"))
                self.assertNotIn("sha256", checklist.read_text(encoding="utf-8"))
                self.assertEqual(
                    json.loads(manifest.read_text(encoding="utf-8"))["document_type"],
                    document_type,
                )


if __name__ == "__main__":
    unittest.main()
