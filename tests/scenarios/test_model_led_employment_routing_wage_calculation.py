from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.amount_calculator.public import AmountCalculator
from scripts.case_archive import CaseArchive


ROOT = Path(__file__).resolve().parents[2]


class _NaturalLanguageEmploymentRoutingWageModel:
    """Scenario-only model adapter exercising the Skill's public behavior."""

    def __init__(self, workspace_root: Path):
        self.archive = CaseArchive(workspace_root)
        self.calculator = AmountCalculator()
        self.case_id: str | None = None
        self.confirmed = False

    def respond(self, user_message: str) -> dict[str, object]:
        if self.case_id is None:
            created = self.archive.create(
                {"jurisdiction": "上海", "initial_goal": "梳理劳动关系与工资差额"}
            )
            assert created["ok"], created
            self.case_id = created["result"]["case_id"]
            initial = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户自然语言描述的事实与共享证据",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：证据支持事实**\n"
                                "公司通过工作群安排客服班次并按月支付固定报酬。"
                            ),
                        },
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户称 2026 年 3 月和 4 月每月只收到 9000 元。"
                            ),
                        },
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": (
                                "银行流水同时用于证明劳动报酬支付、工资差额和期间。"
                            ),
                        },
                    ],
                }
            )
            assert initial["ok"], initial
            return {
                "assistant": "我已记录这段案情。接下来你希望我先分析劳动关系、争议路径和工资差额吗？",
                "case_id": self.case_id,
            }

        if "确认" in user_message and "请分析" not in user_message:
            current = self.archive.read(self.case_id)
            assert current["ok"], current
            confirmation = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": current["result"]["revision"],
                    "change_summary": "记录一次统一自然语言确认",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "统一确认：用户确认当前事实、劳动关系/争议路径/工资差额范围、"
                                "按月工资差额的主要口径、待核实扣款假设和本次分析交付。"
                                "引用：F-001、F-002、E-001。"
                            ),
                        }
                    ],
                }
            )
            assert confirmation["ok"], confirmation
            self.confirmed = True
            return {
                "assistant": "我已记录这次统一确认，接下来会按确认范围完成分析和金额复算。",
                "case_id": self.case_id,
            }

        if "分析" not in user_message:
            return {
                "assistant": "我会继续依据案情档案整理事实；你可以补充新的证据或明确希望先看的主张。",
                "case_id": self.case_id,
            }

        if not self.confirmed:
            return {
                "assistant": "在分析和计算前，请先确认当前事实、范围、主要口径和拟交付内容。",
                "case_id": self.case_id,
            }

        current = self.archive.read(self.case_id)
        assert current["ok"], current
        calculated = self.calculator.calculate(self._calculation_request())
        assert calculated["ok"], calculated
        writeback = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": current["result"]["revision"],
                "change_summary": "写回模型分析与确定性金额结果",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "analysis",
                        "content_markdown": self._analysis_markdown(),
                    },
                    {
                        "operation": "append",
                        "record_type": "calculation",
                        "content_markdown": self._calculation_markdown(calculated),
                    },
                ],
            }
        )
        assert writeback["ok"], writeback
        return {
            "assistant": (
                "我已按当前事实完成条件化分析：劳动关系和工资差额有一定支持，"
                "主位口径的可复算工资差额为 6000.00 元；扣款性质仍需核实，"
                "与同一收入重复获偿的方案不纳入合计。"
            ),
            "case_id": self.case_id,
            "calculation": calculated,
        }

    @staticmethod
    def _analysis_markdown() -> str:
        return "\n".join(
            (
                "分析类型：employment-relationship + dispute-routing + wage-analysis",
                "事实状态：事实 F-001 为证据支持事实；事实 F-002 为用户陈述；无分析假设冒充事实。",
                "劳动关系主张分析卡：初步判断=有一定支持；成立事实=主体、劳动管理、有报酬劳动、业务组成。",
                "劳动关系证据：E-001 同时证明工资支付和劳动关系；证据缺口=完整考勤记录。",
                "争议路径：社保缴费事项=行政处理/征缴或投诉核查（administrative_handling）；如主张待遇损失则 conditional_cross_route。",
                "工资主张：当前事实不足以把经营困难抗辩视为成立；该抗辩保持待核查。",
                "方案比较：primary=已证明的工资差额；fallback=扣款性质核实后再调整；not_recommended=与同一收入重复获偿的方案。",
                "方案约束：先排除互斥和重复获偿，再保留对劳动者更有利且有证据基础的主位方案。",
                "来源引用：F-001、F-002、E-001。",
            )
        )

    @staticmethod
    def _calculation_request() -> dict[str, object]:
        return {
            "calculation_label": "2026年3月至4月工资差额",
            "formula_id": "wage_difference",
            "formula_version": "1.0.0",
            "legal_basis_summary": "模型选择按月工资差额口径；计算器只执行数学。",
            "source_refs": ["F-001", "F-002", "E-001"],
            "aggregation_policy": "single_scenario",
            "scenarios": [
                {
                    "scenario_id": "primary",
                    "assumptions": ["每月约定工资 12000 元，已付两个月合计 18000 元。"],
                    "period": {
                        "start_date": "2026-03-01",
                        "end_date": "2026-04-30",
                        "semantics": "completed_months",
                    },
                    "inputs": {
                        "line_items": [
                            {
                                "line_id": "2026-03-04",
                                "basis": {"value": "12000", "unit": "yuan_per_month"},
                                "quantity": {"value": "2", "unit": "month"},
                                "multiplier": {"value": "1", "unit": "ratio"},
                                "paid": {"value": "18000", "unit": "yuan"},
                            }
                        ]
                    },
                }
            ],
            "rounding_policy": {
                "id": "money-independent-unit-half-up-v1",
                "quantum": "0.01",
                "mode": "ROUND_HALF_UP",
                "aggregation": "round_each_line_item_then_sum",
            },
        }

    @staticmethod
    def _calculation_markdown(calculated: dict[str, object]) -> str:
        result = calculated["result"]
        line_item = result["line_items"][0]
        scenario_total = result["scenario_totals"][0]["total"]
        return "\n".join(
            (
                "计算名称：2026年3月至4月工资差额",
                "公式标识：wage_difference",
                "公式版本：1.0.0",
                "法律口径说明：模型选择按月工资差额口径；计算器只执行数学。",
                "来源引用：F-001、F-002、E-001、A-001",
                "情景：primary",
                "舍入规则：money-independent-unit-half-up-v1；ROUND_HALF_UP；独立分项先舍入再汇总。",
                f"公式：{line_item['formula']}",
                f"分项结果：{line_item['result']}元；合计：{scenario_total}元。",
            )
        )


class ModelLedEmploymentRoutingWageCalculationTests(unittest.TestCase):
    def test_employment_relationship_reference_exposes_conditional_analysis_card(self):
        reference = (ROOT / "references/model-led/employment-relationship.md").read_text(
            encoding="utf-8"
        )

        for required in (
            "employment-relationship",
            "成立样本",
            "不成立样本",
            "信息不足样本",
            "事实冲突样本",
            "主张分析卡",
            "用户陈述",
            "证据支持事实",
            "分析假设",
            "证据缺口",
            "对方可能抗辩",
            "初步判断",
            "不由计算器选择",
            "全国基线",
            "上海增强",
            "法源强度",
            "最后核验日",
            "历史回归",
        ):
            self.assertIn(required, reference)

    def test_dispute_routing_reference_keeps_administrative_and_complex_routes_distinct(self):
        reference = (ROOT / "references/model-led/dispute-routing.md").read_text(
            encoding="utf-8"
        )

        for required in (
            "dispute-routing",
            "administrative_handling",
            "conditional_cross_route",
            "professional_review",
            "pending_verification",
            "社会保险",
            "住房公积金",
            "工伤、职业病、竞业限制、劳务派遣、股权激励、涉外用工",
            "不生成金额",
            "事实冲突样本",
            "全国基线",
            "上海增强",
            "最后核验日",
            "历史回归",
        ):
            self.assertIn(required, reference)
        self.assertNotIn("自动选择最大金额", reference)

    def test_wage_reference_covers_shared_evidence_and_primary_fallback_comparison(self):
        reference = (ROOT / "references/model-led/wage-analysis.md").read_text(
            encoding="utf-8"
        )

        for required in (
            "wage-analysis",
            "拖欠工资",
            "绩效奖金",
            "十三薪",
            "complete",
            "pending",
            "conflict",
            "evidence_roles",
            "同一证据",
            "互斥",
            "重复获偿",
            "primary",
            "fallback",
            "not_recommended",
            "成立样本",
            "不成立样本",
            "信息不足样本",
            "事实冲突样本",
            "经营困难",
            "全国基线",
            "上海增强",
            "最后核验日",
            "历史回归",
        ):
            self.assertIn(required, reference)

    def test_model_observation_samples_cover_four_states_and_cross_claim_boundaries(self):
        observations = {
            "employment": [
                {"sample": "成立", "judgment": "倾向支持", "fact_status": "evidence_supported", "evidence": ["E-001"]},
                {"sample": "不成立", "judgment": "倾向不支持", "fact_status": "evidence_supported", "evidence": ["E-002"]},
                {"sample": "信息不足", "judgment": "信息不足", "fact_status": "user_statement", "evidence": []},
                {"sample": "事实冲突", "judgment": "信息不足", "fact_status": "conflict", "evidence": ["E-003"]},
            ],
            "routing": [
                {"sample": "成立", "route_status": "administrative_handling", "amount": False},
                {"sample": "不成立", "route_status": "pending_verification", "amount": False},
                {"sample": "信息不足", "route_status": "pending_verification", "amount": False},
                {"sample": "事实冲突", "route_status": "conditional_cross_route", "amount": False},
            ],
            "wage": [
                {"sample": "成立", "status": "complete", "recommendation": "primary"},
                {"sample": "不成立", "status": "complete", "recommendation": "not_recommended"},
                {"sample": "信息不足", "status": "pending", "recommendation": "fallback"},
                {"sample": "事实冲突", "status": "conflict", "recommendation": "fallback"},
            ],
        }

        self.assertEqual(
            {item["judgment"] for item in observations["employment"]},
            {"倾向支持", "倾向不支持", "信息不足"},
        )
        self.assertEqual(
            {item["route_status"] for item in observations["routing"]},
            {"administrative_handling", "pending_verification", "conditional_cross_route"},
        )
        self.assertEqual(
            {item["status"] for item in observations["wage"]},
            {"complete", "pending", "conflict"},
        )
        self.assertTrue(all(not item["amount"] for item in observations["routing"]))
        self.assertEqual(
            {item["recommendation"] for item in observations["wage"]},
            {"primary", "fallback", "not_recommended"},
        )
        shared_evidence = {"employment": "E-001", "wage": "E-001"}
        self.assertEqual(set(shared_evidence.values()), {"E-001"})
        self.assertIn("not_recommended", {item["recommendation"] for item in observations["wage"]})

    def test_natural_language_analysis_calculation_and_archive_writeback_is_one_closure(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageEmploymentRoutingWageModel(Path(temporary_root))

            first = model.respond(
                "公司通过工作群安排客服班次，按月发工资但最近两个月少发了。"
            )
            confirmation = model.respond(
                "我确认当前事实、分析范围、主要口径和这次拟交付内容。"
            )
            second = model.respond("请分析劳动关系、争议路径和工资差额，并说明主位与备选。")

            self.assertEqual(first["assistant"].count("？"), 1)
            self.assertEqual(confirmation["assistant"].count("？"), 0)
            self.assertIn("统一确认", confirmation["assistant"])
            self.assertEqual(second["assistant"].count("？"), 0)
            self.assertIn("6000.00 元", second["assistant"])
            for response in (first["assistant"], confirmation["assistant"], second["assistant"]):
                self.assertNotIn("JSON", response)
                self.assertNotIn("revision", response)
                self.assertNotIn("F-001", response)
                self.assertNotIn("CAL-001", response)
                self.assertNotIn("commit", response)

            assert model.case_id is not None
            final_archive = model.archive.read(model.case_id)
            self.assertTrue(final_archive["ok"], final_archive)
            markdown = final_archive["result"]["markdown"]
            self.assertIn("初步判断=有一定支持", markdown)
            self.assertIn("行政处理/征缴", markdown)
            self.assertIn("公式标识：wage_difference", markdown)
            self.assertIn("合计：6000.00元", markdown)
            self.assertEqual(markdown.count("### [E-001]"), 1)
            self.assertEqual(markdown.count("### [CONF-001]"), 1)
            self.assertEqual(markdown.count("### [A-001]"), 1)
            self.assertEqual(markdown.count("### [CAL-001]"), 1)
            case_files = list(
                (Path(temporary_root) / ".arbibuddy" / "cases" / model.case_id).rglob("*.json")
            )
            self.assertEqual(case_files, [])
            self.assertEqual(final_archive["result"]["revision"], 3)
            self.assertNotIn("最优方案", markdown)
            self.assertNotIn("胜诉金额", markdown)

    def test_migration_table_has_no_unclassified_source_or_scenario(self):
        table = (ROOT / "docs/agents/domain-migration.md").read_text(
            encoding="utf-8"
        )
        allowed = {"preserve", "revise", "calculator", "obsolete", "unresolved"}
        rows = [line for line in table.splitlines() if line.startswith("|") and "---" not in line]
        self.assertGreater(len(rows), 15)
        dispositions = set()
        for row in rows[1:]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertGreaterEqual(len(cells), 4, row)
            if cells[2] == "处置":
                continue
            self.assertTrue(cells[2], row)
            dispositions.add(cells[2])
        self.assertTrue(dispositions <= allowed, dispositions)
        self.assertNotIn("未分类", table)
        self.assertNotIn("TBD", table)
        self.assertTrue(allowed <= dispositions)


if __name__ == "__main__":
    unittest.main()
