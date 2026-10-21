from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import create_delivery_set, render
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as _new_forced_case,
    forced_request as _forced_request,
)
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings
from tests.documents.model_led_demand_forced_fixtures import visible_text
from tests.documents.test_model_led_document_render import (
    _application_request,
    _evidence_request,
)


def _new_case(
    workspace: Path,
    *,
    confirmation_revision_lines: tuple[str, ...] | None = None,
    salary_fact: str = "劳动合同约定月工资标准：15000元。",
    extra_calculation: str | None = None,
) -> tuple[CaseArchive, str, int, str, str]:
    archive = CaseArchive(workspace)
    created = archive.create({"jurisdiction": "上海", "initial_goal": "准备仲裁申请书"})
    assert created["ok"], created
    case_id = created["result"]["case_id"]

    revision_lines = confirmation_revision_lines
    if revision_lines is None:
        revision_lines = ("- 绑定档案修订版本：1",)
    confirmation = "\n".join(
        (
            "**完整摘要自然语言确认（用户已确认）**",
            *revision_lines,
            "- 用户自然语言确认：确认当前事实、请求范围和拟交付内容，可以准备仲裁申请书。",
            "- 关键事实：劳动关系和合同工资标准按当前档案记录。",
            "- 权益范围：本次处理工资差额请求。",
            "- 主要口径：请求金额以确定性计算结果为准。",
            "- 重要假设：未核事项不作为确定事实。",
            "- 拟交付内容：仲裁申请书和证据目录。",
        )
    )
    changes: list[dict[str, str]] = [
        {"operation": "append", "record_type": "fact", "content_markdown": "申请人：张三；身份证号：310000199001010000；联系地址：上海市示例路1号；联系电话：13800000000。"},
        {"operation": "append", "record_type": "fact", "content_markdown": "被申请人：示例公司；住所：上海市示例路2号；法定代表人：李四。"},
        {"operation": "append", "record_type": "fact", "content_markdown": "申请日期：2026年9月7日；签名：张三。"},
        {"operation": "append", "record_type": "fact", "content_markdown": "文书标题：劳动人事争议仲裁申请书。"},
        {"operation": "append", "record_type": "fact", "content_markdown": "文书标题：劳动人事争议仲裁答辩书。"},
        {"operation": "append", "record_type": "fact", "content_markdown": "文书标题：证据目录。"},
        {"operation": "append", "record_type": "fact", "content_markdown": salary_fact},
        {"operation": "append", "record_type": "claim", "content_markdown": "仲裁请求：支付工资差额。"},
        {"operation": "append", "record_type": "calculation", "content_markdown": "确定性计算结果：6000.00元。"},
    ]
    if extra_calculation is not None:
        changes.append(
            {"operation": "append", "record_type": "calculation", "content_markdown": extra_calculation}
        )
    changes.extend(
        (
            {"operation": "append", "record_type": "evidence", "content_markdown": "证据名称：工资流水；证据内容：银行流水；证明目的：证明工资差额。"},
            {"operation": "append", "record_type": "confirmation", "content_markdown": confirmation},
        )
    )
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": 0,
            "change_summary": "记录申请书所需的事实、请求、计算和当前摘要确认",
            "changes": changes,
        }
    )
    assert committed["ok"], committed
    generated = committed["result"]["generated_record_ids"]
    fact_ids = [item for item in generated if item.startswith("F-")]
    return (
        archive,
        case_id,
        committed["result"]["revision"],
        next(item for item in generated if item.startswith("CONF-")),
        fact_ids[-1],
    )


def _make_set(
    case_id: str,
    revision: int,
    workspace: Path,
    *,
    mode: str = "external_final",
    refs: list[str] | None = None,
    templates: list[str] | None = None,
) -> dict[str, object]:
    return create_delivery_set(
        {
            "case_id": case_id,
            "archive_revision": revision,
            "mode": mode,
            "requested_templates": templates or ["labor-arbitration-application"],
            "confirmation_refs": [] if refs is None else refs,
        },
        workspace,
    )


def _application_with_fact_amount(
    case_id: str,
    revision: int,
    salary_fact_ref: str,
    delivery_set_id: str,
    *,
    fact_text: str = "双方劳动关系存续，合同约定月工资15000元。",
    fact_section_id: str = "facts_and_reasons",
    fact_occurrence: str = "合同约定月工资15000元",
    fact_amount_value: str = "15000元",
) -> dict[str, object]:
    request = _application_request(case_id, revision)
    request["mode"] = "external_final"
    request["confirmation_refs"] = []
    request["delivery_set_id"] = delivery_set_id
    next(item for item in request["sections"] if item["section_id"] == fact_section_id)["full_text"] = fact_text
    request["locked_bindings"].append(
        {
            "kind": "fact_amount",
            "archive_record_ref": salary_fact_ref,
            "rendered_value": fact_amount_value,
            "occurrences": [{"section_id": fact_section_id, "text": fact_occurrence}],
        }
    )
    return with_occurrence_bindings(request)


class ConfirmationAndFactAmountRecoveryTests(unittest.TestCase):
    def test_unified_confirmation_revision_accepts_common_markdown_forms(self):
        valid_lines = (
            ("绑定档案修订版本：1",),
            ("**绑定档案修订版本：1**",),
            ("**绑定档案修订版本**：1",),
            ("- 绑定档案修订版本：1",),
            ("绑定档案修订版本: 1",),
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for index, line in enumerate(valid_lines):
                with self.subTest(line=line):
                    workspace = root / f"valid-{index}"
                    workspace.mkdir()
                    _, case_id, revision, _, _ = _new_case(
                        workspace, confirmation_revision_lines=line
                    )
                    result = _make_set(case_id, revision, workspace, mode="candidate")
                    self.assertTrue(result["ok"], result)

    def test_ambiguous_or_malformed_confirmation_revision_cannot_create_delivery_set(self):
        invalid_lines = (
            ("绑定档案修订版本：1", "绑定档案修订版本：2"),
            ("绑定档案修订版本：1（提交后）",),
            ("绑定档案修订版本：第一版",),
            ("绑定档案修订版本：-1",),
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for index, lines in enumerate(invalid_lines):
                with self.subTest(lines=lines):
                    workspace = root / f"invalid-{index}"
                    workspace.mkdir()
                    _, case_id, revision, _, _ = _new_case(
                        workspace, confirmation_revision_lines=lines
                    )
                    result = _make_set(case_id, revision, workspace, mode="candidate")
                    self.assertFalse(result["ok"], result)
                    self.assertFalse(
                        (workspace / ".arbibuddy" / "cases" / case_id / "delivery-set-v1.json").exists()
                    )

    def test_delivery_set_validates_each_explicit_confirmation_reference(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)

            current_workspace = root / "current"
            current_workspace.mkdir()
            _, current_case, current_revision, current_confirmation, _ = _new_case(current_workspace)
            current = _make_set(
                current_case,
                current_revision,
                current_workspace,
                refs=[current_confirmation],
            )
            self.assertTrue(current["ok"], current)
            current_request = _application_request(current_case, current_revision)
            current_request["confirmation_refs"] = [current_confirmation]
            current_request["delivery_set_id"] = current["result"]["delivery_set_id"]
            current_render = render(current_request, current_workspace)
            self.assertTrue(current_render["ok"], current_render)

            missing_workspace = root / "missing-version"
            missing_workspace.mkdir()
            _, missing_case, missing_revision, missing_confirmation, _ = _new_case(
                missing_workspace, confirmation_revision_lines=()
            )
            missing = _make_set(
                missing_case,
                missing_revision,
                missing_workspace,
                refs=[missing_confirmation],
            )
            self.assertFalse(missing["ok"], missing)
            self.assertIn("confirmation_missing", {item["code"] for item in missing["errors"]})

            stale_workspace = root / "stale"
            stale_workspace.mkdir()
            archive, stale_case, revision, confirmation, _ = _new_case(stale_workspace)
            changed = archive.commit(
                {
                    "case_id": stale_case,
                    "expected_revision": revision,
                    "change_summary": "追加普通分析，旧摘要确认随之过期",
                    "changes": [{"operation": "append", "record_type": "analysis", "content_markdown": "当前分析继续核对请求金额。"}],
                }
            )
            self.assertTrue(changed["ok"], changed)
            stale = _make_set(
                stale_case,
                changed["result"]["revision"],
                stale_workspace,
                refs=[confirmation],
            )
            self.assertFalse(stale["ok"], stale)
            self.assertIn("confirmation_stale", {item["code"] for item in stale["errors"]})

            nonexistent = _make_set(
                stale_case,
                changed["result"]["revision"],
                stale_workspace,
                mode="candidate",
                refs=["CONF-999"],
            )
            self.assertFalse(nonexistent["ok"], nonexistent)

            for index, (reference_kind, expected_code) in enumerate(
                (("non-confirmation", "confirmation_missing"),
                 ("duplicate", "binding_mismatch"))
            ):
                with self.subTest(reference_kind=reference_kind):
                    workspace = root / f"invalid-refs-{index}"
                    workspace.mkdir()
                    _, case_id, revision, confirmation, _ = _new_case(workspace)
                    actual_refs = (
                        ["F-001"]
                        if reference_kind == "non-confirmation"
                        else [confirmation, confirmation]
                    )
                    rejected = _make_set(case_id, revision, workspace, refs=actual_refs)
                    self.assertFalse(rejected["ok"], rejected)
                    self.assertIn(expected_code, {item["code"] for item in rejected["errors"]})
                    self.assertFalse(
                        (workspace / ".arbibuddy" / "cases" / case_id / "delivery-set-v1.json").exists()
                    )

            null_workspace = root / "null-refs"
            null_workspace.mkdir()
            _, null_case, null_revision, _, _ = _new_case(null_workspace)
            null_refs = create_delivery_set(
                {
                    "case_id": null_case,
                    "archive_revision": null_revision,
                    "mode": "candidate",
                    "requested_templates": ["labor-arbitration-application"],
                    "confirmation_refs": None,
                },
                null_workspace,
            )
            self.assertFalse(null_refs["ok"], null_refs)
            self.assertIn("invalid_confirmation_refs", {item["code"] for item in null_refs["errors"]})
            self.assertFalse(
                (null_workspace / ".arbibuddy" / "cases" / null_case / "delivery-set-v1.json").exists()
            )

    def test_high_risk_candidate_keeps_stale_confirmation_warning_in_delivery_set(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            candidate_workspace = root / "candidate"
            candidate_workspace.mkdir()
            archive, case_id, revision, confirmation_id = _new_forced_case(
                candidate_workspace, with_confirmation=True
            )
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "追加普通分析，需保留候选文书的旧确认提示",
                    "changes": [{"operation": "append", "record_type": "analysis", "content_markdown": "继续核对解除路径与证据。"}],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current_revision = changed["result"]["revision"]
            candidate_set = create_delivery_set(
                {
                    "case_id": case_id,
                    "archive_revision": current_revision,
                    "mode": "candidate",
                    "requested_templates": ["forced-termination-notice"],
                    "confirmation_refs": [confirmation_id],
                },
                candidate_workspace,
            )
            self.assertTrue(candidate_set["ok"], candidate_set)
            self.assertIn(
                "confirmation_stale",
                {warning["code"] for warning in candidate_set["warnings"]},
            )
            candidate_request = _forced_request(
                case_id,
                current_revision,
                mode="candidate",
                confirmation_refs=[confirmation_id],
                authority_refs=["AUTH-001"],
            )
            candidate_request["delivery_set_id"] = candidate_set["result"]["delivery_set_id"]
            candidate_render = render(candidate_request, candidate_workspace)
            self.assertTrue(candidate_render["ok"], candidate_render)
            self.assertIn(
                "confirmation_stale",
                {warning["code"] for warning in candidate_render["warnings"]},
            )

            final_workspace = root / "external-final"
            final_workspace.mkdir()
            final_archive, final_case_id, final_revision, final_confirmation = _new_forced_case(
                final_workspace, with_confirmation=True
            )
            final_changed = final_archive.commit(
                {
                    "case_id": final_case_id,
                    "expected_revision": final_revision,
                    "change_summary": "同一后续修订使外发确认过期",
                    "changes": [{"operation": "append", "record_type": "analysis", "content_markdown": "继续核对解除路径与证据。"}],
                }
            )
            self.assertTrue(final_changed["ok"], final_changed)
            final_set = create_delivery_set(
                {
                    "case_id": final_case_id,
                    "archive_revision": final_changed["result"]["revision"],
                    "mode": "external_final",
                    "requested_templates": ["forced-termination-notice"],
                    "confirmation_refs": [final_confirmation],
                },
                final_workspace,
            )
            self.assertFalse(final_set["ok"], final_set)
            self.assertIn("confirmation_stale", {item["code"] for item in final_set["errors"]})

    def test_external_final_requires_current_unified_confirmation_even_with_empty_refs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)

            current_workspace = root / "current"
            current_workspace.mkdir()
            _, case_id, revision, _, _ = _new_case(current_workspace)
            current = _make_set(case_id, revision, current_workspace, refs=[])
            self.assertTrue(current["ok"], current)

            stale_workspace = root / "stale"
            stale_workspace.mkdir()
            archive, stale_case, stale_revision, _, _ = _new_case(stale_workspace)
            changed = archive.commit(
                {
                    "case_id": stale_case,
                    "expected_revision": stale_revision,
                    "change_summary": "只追加普通分析和计算",
                    "changes": [
                        {"operation": "append", "record_type": "analysis", "content_markdown": "仍按工资差额准备候选稿。"},
                        {"operation": "append", "record_type": "calculation", "content_markdown": "新增独立计算结果：1000元。"},
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            stale = _make_set(
                stale_case,
                changed["result"]["revision"],
                stale_workspace,
                refs=[],
            )
            self.assertFalse(stale["ok"], stale)

    def test_candidate_without_refs_can_render_after_analysis_and_calculation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _, _ = _new_case(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "普通分析与独立计算后继续准备候选稿",
                    "changes": [
                        {"operation": "append", "record_type": "analysis", "content_markdown": "请求范围按工资差额继续核对。"},
                        {"operation": "append", "record_type": "calculation", "content_markdown": "新增独立计算结果：1000元。"},
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current_revision = changed["result"]["revision"]
            control = _make_set(
                case_id,
                current_revision,
                workspace,
                mode="candidate",
                refs=[],
            )
            self.assertTrue(control["ok"], control)
            request = _application_request(case_id, current_revision, mode="candidate")
            request["confirmation_refs"] = []
            request["delivery_set_id"] = control["result"]["delivery_set_id"]
            rendered = render(request, workspace)
            self.assertTrue(rendered["ok"], rendered)
            self.assertEqual(rendered["result"]["delivery_state"], "candidate_ready")

    def test_external_application_uses_fact_amount_for_salary_and_cal_for_request(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _, salary_fact_ref = _new_case(workspace)
            control = _make_set(case_id, revision, workspace, refs=[])
            self.assertTrue(control["ok"], control)
            request = _application_with_fact_amount(
                case_id,
                revision,
                salary_fact_ref,
                control["result"]["delivery_set_id"],
            )

            rendered = render(request, workspace)

            self.assertTrue(rendered["ok"], rendered)
            document = Path(rendered["result"]["canonical_docx"])
            text = visible_text(document)
            self.assertIn("合同约定月工资15000元", text)
            self.assertIn("请求支付工资差额6000.00元", text)
            self.assertEqual(
                sum(binding["kind"] == "amount" for binding in request["locked_bindings"]),
                1,
            )
            archive_markdown = archive.read(case_id)["result"]["markdown"]
            self.assertNotIn("CAL-002", archive_markdown)

    def test_fact_amount_is_rejected_in_request_or_when_source_is_unsupported(self):
        scenarios = (
            {
                "name": "request-section",
                "salary_fact": "劳动合同约定月工资标准：15000元。",
                "extra_calculation": None,
                "fact_text": "请求支付工资差额6000.00元，并按合同工资标准15000元核算。",
                "fact_section_id": "requests",
                "fact_occurrence": "合同工资标准15000元",
                "source": "salary",
                "value": "15000元",
            },
            {
                "name": "mismatched-value",
                "salary_fact": "劳动合同约定月工资标准：15000元。",
                "extra_calculation": None,
                "fact_text": "双方劳动关系存续，合同约定月工资15000元。",
                "fact_section_id": "facts_and_reasons",
                "fact_occurrence": "合同约定月工资15000元",
                "source": "salary",
                "value": "16000元",
            },
            {
                "name": "assumption-source",
                "salary_fact": "事实状态：分析假设；劳动合同约定月工资标准15000元，尚未核实。",
                "extra_calculation": None,
                "fact_text": "双方劳动关系存续，合同约定月工资15000元。",
                "fact_section_id": "facts_and_reasons",
                "fact_occurrence": "合同约定月工资15000元",
                "source": "salary",
                "value": "15000元",
            },
            {
                "name": "wrong-record-type",
                "salary_fact": "劳动合同约定月工资标准：15000元。",
                "extra_calculation": "另一来源金额：15000元。",
                "fact_text": "双方劳动关系存续，合同约定月工资15000元。",
                "fact_section_id": "facts_and_reasons",
                "fact_occurrence": "合同约定月工资15000元",
                "source": "calculation",
                "value": "15000元",
            },
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for scenario in scenarios:
                with self.subTest(scenario=scenario["name"]):
                    workspace = root / scenario["name"]
                    workspace.mkdir()
                    archive, case_id, revision, _, salary_fact_ref = _new_case(
                        workspace,
                        salary_fact=scenario["salary_fact"],
                        extra_calculation=scenario["extra_calculation"],
                    )
                    control = _make_set(case_id, revision, workspace, refs=[])
                    self.assertTrue(control["ok"], control)
                    source_ref = (
                        "CAL-002" if scenario["source"] == "calculation" else salary_fact_ref
                    )
                    request = _application_with_fact_amount(
                        case_id,
                        revision,
                        source_ref,
                        control["result"]["delivery_set_id"],
                        fact_text=scenario["fact_text"],
                        fact_section_id=scenario["fact_section_id"],
                        fact_occurrence=scenario["fact_occurrence"],
                        fact_amount_value=scenario["value"],
                    )
                    rendered = render(request, workspace)
                    self.assertFalse(rendered["ok"], rendered)
                    self.assertIn("binding_mismatch", {item["code"] for item in rendered["errors"]})
                    self.assertEqual(archive.read(case_id)["result"]["revision"], revision)

    def test_evidence_catalog_can_locate_every_fact_amount_occurrence(self):
        for index, heading in enumerate(("五列表格", "证据列表", "证据目录表格")):
            with self.subTest(heading=heading), TemporaryDirectory() as temp:
                workspace = Path(temp)
                _, case_id, revision, _, salary_fact_ref = _new_case(workspace)
                control = _make_set(
                    case_id,
                    revision,
                    workspace,
                    refs=[],
                    templates=["evidence-catalog"],
                )
                self.assertTrue(control["ok"], control)
                request = _evidence_request(case_id, revision)
                request["confirmation_refs"] = []
                request["delivery_set_id"] = control["result"]["delivery_set_id"]
                request["sections"][1]["heading"] = heading
                table_text = (
                    "| 编号 | 证据名称 | 证据内容 | 证明目的 | 页码 |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| 1 | 工资流水 | 银行流水；合同约定月工资15000元，再述合同工资标准15000元 | 证明工资差额 |  |"
                )
                request["sections"][1]["full_text"] = table_text
                request["locked_bindings"].append(
                    {
                        "kind": "fact_amount",
                        "archive_record_ref": salary_fact_ref,
                        "rendered_value": "15000元",
                        "occurrences": [
                            {"section_id": "table", "text": "合同约定月工资15000元"},
                            {"section_id": "table", "text": "合同工资标准15000元"},
                        ],
                    }
                )

                rendered = render(request, workspace)

                self.assertTrue(rendered["ok"], rendered)
                text = visible_text(Path(rendered["result"]["canonical_docx"]))
                self.assertEqual(text.count("15000元"), 2)


if __name__ == "__main__":
    unittest.main()
