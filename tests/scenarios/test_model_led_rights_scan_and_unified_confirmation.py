from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from scripts.case_archive import CaseArchive


RIGHTS_CATALOG = (
    ("wage_arrears", "拖欠工资"),
    ("unilateral_pay_cut", "单方降薪工资差额"),
    ("sales_commission", "销售提成"),
    ("project_bonus", "项目奖金"),
    ("performance_bonus", "绩效奖金"),
    ("annual_bonus_or_thirteenth_salary", "年终奖金或十三薪"),
    ("workday_overtime", "工作日延时加班工资"),
    ("rest_day_overtime", "休息日加班工资"),
    ("statutory_holiday_overtime", "法定休假日加班工资"),
    ("statutory_allowance", "法定津贴补贴差额"),
    ("unused_annual_leave", "未休年休假工资"),
    ("other_paid_leave", "其他法定带薪假权利或工资待遇"),
    ("social_insurance", "社会保险"),
    ("housing_fund", "住房公积金"),
    ("unsigned_contract_double_wage", "未签书面劳动合同二倍工资"),
    ("termination_remedies", "离职、辞退相关补偿"),
)

_CLAIM_BLOCK = re.compile(
    r"^### \[(?P<record_id>CL-\d{3,})\] 主张与权益\n\n"
    r"(?P<body>.*?)(?=^### \[|^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_CONFIRMATION_BLOCK = re.compile(
    r"^### \[CONF-\d{3,}\] 确认记录\n\n"
    r"(?P<body>.*?)(?=^### \[|^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _claims(markdown: str) -> list[tuple[str, str]]:
    return [(match.group("record_id"), match.group("body")) for match in _CLAIM_BLOCK.finditer(markdown)]


def _claim_by_category(markdown: str, category_id: str) -> tuple[str, str]:
    for record_id, body in _claims(markdown):
        if f"权益类别：{category_id}" in body:
            return record_id, body
    raise AssertionError(f"missing claim category: {category_id}")


class _NaturalLanguageRightsScenarioModel:
    """Scenario adapter: model chooses the scan and uses only archive atomic writes."""

    def __init__(self, workspace_root: Path):
        self.archive = CaseArchive(workspace_root)
        self.case_id: str | None = None
        self._reconfirmation_scope: str | None = None

    def _read(self) -> dict[str, object]:
        assert self.case_id is not None
        result = self.archive.read(self.case_id)
        assert result["ok"], result
        return result["result"]

    def _commit(self, changes: list[dict[str, object]], summary: str) -> dict[str, object]:
        current = self._read()
        assert self.case_id is not None
        result = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": current["revision"],
                "change_summary": summary,
                "changes": changes,
            }
        )
        assert result["ok"], result
        return result["result"]

    def start_with_raised_wage_claim(self) -> None:
        created = self.archive.create({"initial_goal": "梳理劳动争议范围"})
        self.assert_ok(created)
        self.case_id = created["result"]["case_id"]
        self._commit(
            [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "**事实状态：用户陈述**\n"
                        "用户表示 2026 年 2 月工资尚未支付。"
                    ),
                }
            ],
            "记录用户已提出的工资事实",
        )
        self._commit(
            [
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": (
                        "权益类别：wage_arrears\n"
                        "权益状态：mentioned\n"
                        "事实依据：F-001\n"
                        "说明：用户已明确提出工资未支付问题。"
                    ),
                }
            ],
            "记录已提出的权益",
        )

    @staticmethod
    def assert_ok(result: dict[str, object]) -> None:
        assert result["ok"], result

    def scan_rights(self) -> dict[str, object]:
        current = self._read()
        markdown = current["markdown"]
        if "权益扫描事件：model-led-rights-scan-v1" in markdown:
            return {
                "assistant": "前面的权益核对已经完成；如果后来出现重要新事实，我只会针对受影响的范围重新核对。",
                "offered_category_ids": [],
                "already_scanned": True,
            }

        raised = {
            category_id
            for _, body in _claims(markdown)
            for category_id, _ in RIGHTS_CATALOG
            if f"权益类别：{category_id}" in body
        }
        offered = [item for item in RIGHTS_CATALOG if item[0] not in raised]
        changes: list[dict[str, object]] = [
            {
                "operation": "append",
                "record_type": "claim",
                "content_markdown": (
                    f"权益类别：{category_id}\n"
                    "权益状态：potential\n"
                    "说明：主动核对发现的可能方向；只是候选，不代表违法或已经成立。"
                ),
            }
            for category_id, _ in offered
        ]
        changes.append(
            {
                "operation": "append",
                "record_type": "analysis",
                "content_markdown": (
                    "权益扫描事件：model-led-rights-scan-v1\n"
                    "扫描范围：已排除档案中已经提出的类别；其余 16 类目录均已核对。\n"
                    "候选性质：扫描结果需要事实、证据和用户范围选择，不是违法认定。"
                ),
            }
        )
        self._commit(changes, "记录模型主动发现的权益候选")
        labels = "、".join(label for _, label in offered)
        return {
            "assistant": (
                "目前事实已经足以做一次权益核对。除你已经提到的事项外，"
                f"还可能涉及：{labels}。这些只是需要核实的可能方向，不代表已经认定违法或一定成立；"
                "请告诉我哪些可能相关，也可以说暂时不确定，好吗？"
            ),
            "offered_category_ids": [category_id for category_id, _ in offered],
            "already_scanned": False,
        }

    def final_supplement_prompt(self) -> dict[str, object]:
        markdown = self._read()["markdown"]
        if "最终开放式补充事件：" in markdown:
            return {
                "assistant": "这次开放补充已经完成；没有新增重要事实时，我不会反复要求你再补一轮。",
                "needs_user_answer": False,
            }
        return {
            "assistant": "在我收拢当前范围前，还有没有其他重要事实、材料或你特别希望处理的事项？你也可以直接说没有。",
            "needs_user_answer": True,
        }

    def answer_final_supplement(self, answer: str) -> dict[str, object]:
        if not answer.strip():
            raise AssertionError("the scenario requires a natural-language answer")
        changes: list[dict[str, object]] = [
            {
                "operation": "append",
                "record_type": "fact",
                "content_markdown": (
                    "**事实状态：用户陈述**\n"
                    f"最终开放式补充：{answer.strip()}"
                ),
            },
            {
                "operation": "append",
                "record_type": "analysis",
                "content_markdown": (
                    "最终开放式补充事件：已完成\n"
                    "最终开放式补充事件：无新事实循环"
                    if "没有" in answer or "无" in answer
                    else "最终开放式补充事件：已完成\n最终开放式补充事件：包含用户新增事实"
                ),
            },
        ]
        result = self._commit(changes, "记录一次最终开放式补充")
        return {
            "assistant": "我已记下这次最终补充；接下来会只按有事实依据且由你决定的范围继续。",
            "revision": result["revision"],
        }

    def record_material_new_fact(self, category_id: str, answer: str) -> dict[str, object]:
        """Model reopens only the affected scope after a material new fact."""

        label = dict(RIGHTS_CATALOG)[category_id]
        result = self._commit(
            [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "**事实状态：用户陈述**\n"
                        f"最终补充后的新事实：{answer.strip()}"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "analysis",
                    "content_markdown": (
                        f"最终开放式补充事件：实质变化重新开放：{category_id}"
                    ),
                },
            ],
            "记录最终补充后影响范围的新事实",
        )
        return {
            "assistant": (
                f"最终补充中的这条新事实可能影响{label}的处理范围。"
                f"我只针对{label}再请你说明：这项内容是否纳入当前分析？"
            ),
            "revision": result["revision"],
        }

    def resolve(self, category_id: str, status: str, reason: str) -> dict[str, object]:
        current = self._read()
        record_id, _ = _claim_by_category(current["markdown"], category_id)
        description = {
            "selected": "纳入本次分析",
            "pending": "暂缓，待补事实或以后再处理",
            "excluded": "当前不处理；这不等于永久放弃实体权利",
            "potential": "保留为候选，等待新的事实或范围决定",
        }[status]
        result = self._commit(
            [
                {
                    "operation": "replace",
                    "record_type": "claim",
                    "record_id": record_id,
                    "content_markdown": (
                        f"权益类别：{category_id}\n"
                        f"权益状态：{status}\n"
                        f"范围说明：{description}\n"
                        f"用户选择理由：{reason}"
                    ),
                    "reason": "用户修正当前权益处理范围",
                }
            ],
            "记录用户对权益范围的选择",
        )
        return {"record_id": record_id, "revision": result["revision"]}

    def confirmation_prompt(self) -> str:
        markdown = self._read()["markdown"]
        # The test adapter deliberately turns archive control values into user language.
        claims = _claims(markdown)
        labels_by_status = {
            status: [
                label
                for category_id, label in RIGHTS_CATALOG
                for _, body in claims
                if f"权益类别：{category_id}" in body and f"权益状态：{status}" in body
            ]
            for status in ("selected", "pending", "excluded")
        }
        return (
            "请确认我是否准确理解了这次工作：关键事实是工资存在未支付情况；"
            f"本次纳入处理的是{ '、'.join(labels_by_status['selected']) or '目前没有明确纳入的项目'}，"
            f"暂缓的是{ '、'.join(labels_by_status['pending']) or '目前没有暂缓项目'}，"
            f"当前不处理的是{ '、'.join(labels_by_status['excluded']) or '目前没有明确排除的项目'}；"
            "主要口径是按已说明的期间、工资基数和适用规则分别列出；"
            "重要假设是尚未核实的部分不会当作确定事实；"
            "拟交付内容是条件化分析、证据缺口和下一步建议。"
            "这些范围选择不等于永久放弃实体权利。若有任何不准确，请直接指出；"
            "如果准确，请用你自己的话告诉我可以按这个范围继续，好吗？"
        )

    def reconfirmation_prompt(self, category_id: str) -> str:
        """Model asks only about the scope changed by the latest material fact."""

        self._reconfirmation_scope = category_id
        label = dict(RIGHTS_CATALOG)[category_id]
        return (
            f"刚才修正的事实只影响{label}。"
            f"我只需要重新确认这一项是否按修正后的内容纳入当前分析，可以吗？"
        )

    def confirm(self, answer: str) -> dict[str, object]:
        current = self._read()
        markdown = current["markdown"]
        confirmation_blocks = list(_CONFIRMATION_BLOCK.finditer(markdown))
        stale_confirmation = False
        if confirmation_blocks:
            bound = re.findall(r"绑定档案修订版本：(?P<revision>\d+)", confirmation_blocks[-1].group("body"))
            if not bound or int(bound[-1]) != current["revision"]:
                stale_confirmation = True
                if "修正后的" not in answer and "更新后的" not in answer:
                    return {
                        "assistant": "刚才已有事实被修正，之前的确认只覆盖修正前的内容；我会只请你重新确认受影响的范围。",
                        "confirmed": False,
                        "stale": True,
                    }

        normalized = answer.strip()
        if any(marker in normalized for marker in ("不确定", "再看看", "说不清")):
            return {
                "assistant": "我还不能确定你是否同意这份范围。请告诉我需要修改哪一项，或者直接说可以按当前范围继续，好吗？",
                "confirmed": False,
                "ambiguous": True,
            }
        if any(marker in normalized for marker in ("拒绝", "不同意", "不确认")):
            return {
                "assistant": "好的，我不会把这次内容当作已确认范围；你可以继续补充或先暂缓处理。",
                "confirmed": False,
                "refused": True,
            }
        if not any(marker in normalized for marker in ("确认", "同意", "可以按", "可以继续")):
            return {
                "assistant": "我还不能确定你的意思。请直接告诉我是否可以按刚才的范围继续，或指出需要修改的内容，好吗？",
                "confirmed": False,
                "ambiguous": True,
            }

        bound_revision = int(current["revision"]) + 1
        if self._reconfirmation_scope is not None:
            scope_label = dict(RIGHTS_CATALOG)[self._reconfirmation_scope]
            rights_scope = (
                f"本次重新确认范围：{scope_label}；"
                "未受影响的范围沿用此前确认。"
            )
        else:
            rights_scope = "权益范围：记录中所有 selected、pending、excluded 的当前范围。"
        self._commit(
            [
                {
                    "operation": "append",
                    "record_type": "confirmation",
                    "content_markdown": (
                        f"自然语言确认记录：用户确认按当前内容继续。\n"
                        f"绑定档案修订版本：{bound_revision}\n"
                        "关键事实：工资存在未支付情况。\n"
                        f"{rights_scope}\n"
                        "主要口径：期间、工资基数和适用规则按当前分析说明；不确定处使用条件情景。\n"
                        "重要假设：未核实推断不作为确定事实。\n"
                        "拟交付内容：条件化分析、证据缺口和下一步建议。"
                    ),
                }
            ],
            "记录一次普通自然语言确认",
        )
        self._reconfirmation_scope = None
        return {
            "assistant": "我已理解你的确认，会按刚才说明的事实、范围、口径、假设和拟交付内容继续。",
            "confirmed": True,
            "bound_revision": bound_revision,
            "reconfirmed": stale_confirmation,
        }

    def continue_after_confirmation(self) -> str:
        current = self._read()
        blocks = list(_CONFIRMATION_BLOCK.finditer(current["markdown"]))
        if not blocks:
            return "当前还没有完成范围确认，我会先把需要确认的内容说明清楚。"
        bound = int(re.findall(r"绑定档案修订版本：(\d+)", blocks[-1].group("body"))[-1])
        if bound != current["revision"]:
            return "已有重要内容发生变化，我会先请你重新确认受影响范围。"
        return "确认内容仍然有效，我可以继续进行分析、计算或准备候选材料，不需要再次确认同一范围。"


class ModelLedRightsScanAndConfirmationScenarioTests(unittest.TestCase):
    def test_complete_scan_excludes_raised_category_and_records_candidates_as_unproven(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageRightsScenarioModel(Path(temporary_root))
            model.start_with_raised_wage_claim()

            scanned = model.scan_rights()
            self.assertEqual(len(scanned["offered_category_ids"]), 15)
            self.assertNotIn("wage_arrears", scanned["offered_category_ids"])
            self.assertEqual(
                set(scanned["offered_category_ids"]),
                {category_id for category_id, _ in RIGHTS_CATALOG} - {"wage_arrears"},
            )
            self.assertEqual(scanned["assistant"].count("？"), 1)
            self.assertIn("只是需要核实的可能方向", scanned["assistant"])
            self.assertNotIn("违法清单", scanned["assistant"])
            self.assertNotIn("potential", scanned["assistant"])

            archive = model._read()["markdown"]
            self.assertEqual(
                {
                    category_id
                    for _, body in _claims(archive)
                    for category_id, _ in RIGHTS_CATALOG
                    if f"权益类别：{category_id}" in body
                },
                {category_id for category_id, _ in RIGHTS_CATALOG},
            )
            self.assertEqual(archive.count("权益扫描事件：model-led-rights-scan-v1"), 1)
            self.assertEqual(
                sum("权益状态：potential" in body for _, body in _claims(archive)),
                15,
            )

    def test_selected_pending_and_excluded_are_editable_and_pending_is_not_dropped(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageRightsScenarioModel(Path(temporary_root))
            model.start_with_raised_wage_claim()
            model.scan_rights()

            model.resolve("sales_commission", "selected", "用户希望一并核实提成记录")
            model.resolve("social_insurance", "pending", "暂时缺少缴费记录")
            model.resolve("housing_fund", "excluded", "用户当前明确不处理该事项")
            archive = model._read()["markdown"]
            self.assertIn("权益状态：selected", _claim_by_category(archive, "sales_commission")[1])
            self.assertIn("权益状态：pending", _claim_by_category(archive, "social_insurance")[1])
            self.assertIn("权益状态：excluded", _claim_by_category(archive, "housing_fund")[1])
            self.assertIn("不等于永久放弃实体权利", _claim_by_category(archive, "housing_fund")[1])

            model.resolve("social_insurance", "selected", "用户补齐材料后希望纳入分析")
            model.resolve("housing_fund", "potential", "用户补充了新的公积金事实")
            archive = model._read()["markdown"]
            self.assertIn("权益状态：selected", _claim_by_category(archive, "social_insurance")[1])
            self.assertIn("权益状态：potential", _claim_by_category(archive, "housing_fund")[1])
            self.assertEqual(len(_claims(archive)), 16)

    def test_final_supplement_is_once_without_change_and_reopens_only_for_new_fact(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageRightsScenarioModel(Path(temporary_root))
            model.start_with_raised_wage_claim()
            model.scan_rights()

            first = model.final_supplement_prompt()
            self.assertTrue(first["needs_user_answer"])
            self.assertEqual(first["assistant"].count("？"), 1)
            recorded = model.answer_final_supplement("目前没有其他重要事实。")
            self.assertNotIn("再补一轮", recorded["assistant"])
            second = model.final_supplement_prompt()
            self.assertFalse(second["needs_user_answer"])
            self.assertEqual(model._read()["markdown"].count("最终开放式补充事件：已完成"), 1)

            # A material new fact is a different observation, not a silent no-change loop.
            new_fact = model.record_material_new_fact(
                "sales_commission", "还有一笔销售提成可能没有支付。"
            )
            self.assertIn("最终补充", new_fact["assistant"])
            self.assertEqual(new_fact["assistant"].count("？"), 1)
            self.assertIn("销售提成", new_fact["assistant"])
            self.assertEqual(model._read()["markdown"].count("最终开放式补充事件：已完成"), 1)
            self.assertEqual(model._read()["markdown"].count("最终开放式补充事件：实质变化重新开放"), 1)

    def test_one_natural_language_confirmation_binds_revision_and_is_invalidated_by_fact_change(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageRightsScenarioModel(Path(temporary_root))
            model.start_with_raised_wage_claim()
            model.scan_rights()
            model.answer_final_supplement("目前没有其他重要事实。")
            model.resolve("wage_arrears", "selected", "用户希望处理已提出的工资问题")
            model.resolve("social_insurance", "pending", "暂时等待缴费记录")
            model.resolve("housing_fund", "excluded", "用户当前不处理，但不等于永久放弃")

            prompt = model.confirmation_prompt()
            self.assertEqual(prompt.count("？"), 1)
            for hidden in ("selected", "pending", "excluded", "JSON", "CONF-", "record_id"):
                self.assertNotIn(hidden, prompt)
            for visible in ("关键事实", "本次纳入处理", "暂缓", "当前不处理", "主要口径", "重要假设", "拟交付内容"):
                self.assertIn(visible, prompt)

            ambiguous = model.confirm("大概可以吧")
            self.assertFalse(ambiguous["confirmed"])
            self.assertEqual(ambiguous["assistant"].count("？"), 1)
            refused = model.confirm("我不同意，先不要按这个范围")
            self.assertFalse(refused["confirmed"])
            self.assertEqual(len(_CONFIRMATION_BLOCK.findall(model._read()["markdown"])), 0)

            before = model._read()["revision"]
            confirmed = model.confirm("我确认，可以按这个范围继续")
            self.assertTrue(confirmed["confirmed"])
            self.assertEqual(confirmed["bound_revision"], before + 1)
            archive = model._read()
            self.assertEqual(archive["revision"], before + 1)
            confirmation = _CONFIRMATION_BLOCK.findall(archive["markdown"])[-1]
            self.assertIn(f"绑定档案修订版本：{before + 1}", confirmation)
            self.assertIn("不需要再次确认同一范围", model.continue_after_confirmation())

            fact_record = model._commit(
                [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": (
                            "**事实状态：用户陈述**\n"
                            "用户修正：工资实际只拖欠半个月。"
                        ),
                    }
                ],
                "记录用户修正的工资事实",
            )
            self.assertGreater(fact_record["revision"], before + 1)
            stale = model.confirm("确认")
            self.assertTrue(stale["stale"])
            self.assertFalse(stale["confirmed"])
            self.assertIn("受影响的范围", stale["assistant"])
            self.assertEqual(stale["assistant"].count("？"), 0)

            scoped_reconfirmation = model.reconfirmation_prompt("wage_arrears")
            self.assertEqual(scoped_reconfirmation.count("？"), 1)
            self.assertIn("拖欠工资", scoped_reconfirmation)
            self.assertNotIn("社会保险", scoped_reconfirmation)
            self.assertNotIn("住房公积金", scoped_reconfirmation)
            reconfirm = model.confirm("我确认，可以按修正后的内容继续")
            self.assertTrue(reconfirm["confirmed"])
            self.assertGreater(reconfirm["bound_revision"], confirmed["bound_revision"])

    def test_user_visible_flow_never_exposes_archive_controls_or_repeats_confirmation_gate(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageRightsScenarioModel(Path(temporary_root))
            model.start_with_raised_wage_claim()
            visible = [model.scan_rights()["assistant"]]
            visible.append(model.final_supplement_prompt()["assistant"])
            visible.append(model.answer_final_supplement("目前没有其他重要事实。")["assistant"])
            model.resolve("wage_arrears", "selected", "用户希望处理")
            visible.append(model.confirmation_prompt())
            visible.append(model.confirm("我确认，可以按这个范围继续")["assistant"])
            visible.append(model.continue_after_confirmation())

            for message in visible:
                self.assertNotIn("JSON", message)
                self.assertNotIn("revision", message)
                self.assertNotIn("record_id", message)
                self.assertNotIn("CONF-", message)
                self.assertNotIn("selected", message)
                self.assertNotIn("pending", message)
                self.assertNotIn("excluded", message)
            self.assertIn("不需要再次确认同一范围", visible[-1])


if __name__ == "__main__":
    unittest.main()
