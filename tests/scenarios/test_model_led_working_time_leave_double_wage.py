from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.amount_calculator.public import AmountCalculator
from scripts.case_archive import CaseArchive


ROOT = Path(__file__).resolve().parents[2]


class _NaturalLanguageWorkingTimeLeaveDoubleWageModel:
    """Scenario adapter observes the model-led public seam only."""

    def __init__(self, workspace_root: Path):
        self.archive = CaseArchive(workspace_root)
        self.calculator = AmountCalculator()
        self.case_id: str | None = None

    def respond(self, user_message: str) -> dict[str, object]:
        if self.case_id is None:
            created = self.archive.create(
                {"jurisdiction": "上海", "initial_goal": "分析加班、休假和未签合同权益"}
            )
            assert created["ok"], created
            self.case_id = created["result"]["case_id"]
            committed = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": 0,
                    "change_summary": "记录自然语言交叉案情与共享工资基数",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：证据支持事实**\n"
                                "用户在上海持续接受公司排班和工作安排，月工资基数为12000元。"
                            ),
                        },
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "2025年末至2026年初有工作日延时、休息日和法定休假日工作；"
                                "2025年度有3天年休假未休；工作满一个月后连续8个月未签书面合同。"
                            ),
                        },
                        {
                            "operation": "append",
                            "record_type": "evidence",
                            "content_markdown": (
                                "考勤、排班、工资流水和工作群记录分别支持工时、工资基数、"
                                "劳动管理、未休年休假与书面合同状态。"
                            ),
                        },
                    ],
                }
            )
            assert committed["ok"], committed
            return {
                "assistant": "我已记录案情。接下来请确认是否同时分析加班、未休年休假和未签合同二倍工资，并把其他带薪假单独核对路径。",
                "case_id": self.case_id,
            }

        if "确认" in user_message:
            current = self.archive.read(self.case_id)
            assert current["ok"], current
            committed = self.archive.commit(
                {
                    "case_id": self.case_id,
                    "expected_revision": current["result"]["revision"],
                    "change_summary": "记录一次自然语言范围与口径确认",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "用户确认分析范围包括加班、未休年休假和未签合同二倍工资；"
                                "共享工资基数为12000元/月，但每项主张按各自期间、单位和证据分别计算；"
                                "其他带薪假只做休假权/工资待遇/行政路径分流，不默认折现。"
                            ),
                        }
                    ],
                }
            )
            assert committed["ok"], committed
            return {"assistant": "已记录确认。我会分别分析各项主张并排除重复获偿。", "case_id": self.case_id}

        current = self.archive.read(self.case_id)
        assert current["ok"], current
        overtime = self.calculator.calculate(self._overtime_request())
        annual_leave = self.calculator.calculate(self._annual_leave_request())
        unsigned = self.calculator.calculate(self._unsigned_request())
        for calculation in (overtime, annual_leave, unsigned):
            assert calculation["ok"], calculation
        committed = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": current["result"]["revision"],
                "change_summary": "写回四项模型主导分析的交叉影响与可复算金额",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "analysis",
                        "content_markdown": (
                            "主张分析卡：劳动关系事实支持未签合同二倍工资的分析前提；"
                            "同一12000元/月工资基数分别影响加班与年休假计算，但不把同一收入重复计入。\n"
                            "其他带薪假路由：休假权、工资或待遇差额、行政待遇分别识别；"
                            "产假待遇（如有）与用人单位工资差额不合并，未休年休假不按本路由统一折现。\n"
                            "方案比较：加班、年休假和未签合同主张分别保留期间、证据和公式；"
                            "互斥或重复获偿项目不进入同一合计。"
                        ),
                    },
                    {
                        "operation": "append",
                        "record_type": "calculation",
                        "content_markdown": self._calculation_markdown(
                            overtime, annual_leave, unsigned
                        ),
                    },
                ],
            }
        )
        assert committed["ok"], committed
        return {
            "assistant": (
                "我已分别完成加班、未休年休假和未签合同二倍工资的条件化分析与金额复算；"
                "其他带薪假保持独立路由，未把同一收入重复获偿。"
            ),
            "case_id": self.case_id,
            "calculations": (overtime, annual_leave, unsigned),
        }

    @staticmethod
    def _base_request(formula_id: str, scenario: dict[str, object]) -> dict[str, object]:
        return {
            "calculation_label": f"交叉主张：{formula_id}",
            "formula_id": formula_id,
            "formula_version": "1.0.0",
            "legal_basis_summary": "模型选择口径，计算器只执行数学。",
            "source_refs": ["F-001", "F-002", "E-001"],
            "aggregation_policy": "single_scenario",
            "scenarios": [scenario],
            "rounding_policy": {
                "id": "money-independent-unit-half-up-v1",
                "quantum": "0.01",
                "mode": "ROUND_HALF_UP",
                "aggregation": "round_each_line_item_then_sum",
            },
        }

    @classmethod
    def _overtime_request(cls) -> dict[str, object]:
        return cls._base_request(
            "overtime_pay",
            {
                "scenario_id": "overtime",
                "assumptions": ["标准工时、用人单位安排和未补休事实已分别确认。"],
                "period": {
                    "start_date": "2025-12-31",
                    "end_date": "2026-01-02",
                    "semantics": "explicit_period",
                },
                "inputs": {
                    "line_items": [
                        {
                            "line_id": "workday",
                            "basis": {"value": "75", "unit": "yuan_per_hour"},
                            "quantity": {"value": "2", "unit": "hour"},
                            "multiplier": {"value": "1.5", "unit": "ratio"},
                            "paid": {"value": "0", "unit": "yuan"},
                        },
                        {
                            "line_id": "rest-day",
                            "basis": {"value": "600", "unit": "yuan_per_day"},
                            "quantity": {"value": "1", "unit": "day"},
                            "multiplier": {"value": "2", "unit": "ratio"},
                            "paid": {"value": "0", "unit": "yuan"},
                        },
                        {
                            "line_id": "holiday",
                            "basis": {"value": "600", "unit": "yuan_per_day"},
                            "quantity": {"value": "1", "unit": "day"},
                            "multiplier": {"value": "3", "unit": "ratio"},
                            "paid": {"value": "0", "unit": "yuan"},
                        },
                    ]
                },
            },
        )

    @classmethod
    def _annual_leave_request(cls) -> dict[str, object]:
        return cls._base_request(
            "annual_leave_pay",
            {
                "scenario_id": "annual-leave",
                "assumptions": ["2025年度3天未休，月平均工资已剔除加班工资。"],
                "period": {
                    "start_date": "2025-01-01",
                    "end_date": "2025-12-31",
                    "semantics": "calendar_days",
                },
                "inputs": {
                    "line_items": [
                        {
                            "line_id": "2025",
                            "basis": {"value": "12000", "unit": "yuan_per_month"},
                            "divisor": {"value": "21.75", "unit": "count"},
                            "quantity": {"value": "3", "unit": "day"},
                            "multiplier": {"value": "2", "unit": "ratio"},
                            "paid": {"value": "0", "unit": "yuan"},
                        }
                    ]
                },
            },
        )

    @classmethod
    def _unsigned_request(cls) -> dict[str, object]:
        return cls._base_request(
            "unsigned_contract_double_wage",
            {
                "scenario_id": "unsigned-contract",
                "assumptions": ["首次用工满一个月后连续8个月未签书面合同，正常工资已付。"],
                "period": {
                    "start_date": "2025-05-01",
                    "end_date": "2025-12-31",
                    "semantics": "completed_months",
                },
                "inputs": {
                    "line_items": [
                        {
                            "line_id": "2025-05-to-2025-12",
                            "basis": {"value": "12000", "unit": "yuan_per_month"},
                            "quantity": {"value": "8", "unit": "month"},
                            "multiplier": {"value": "1", "unit": "ratio"},
                            "paid": {"value": "0", "unit": "yuan"},
                        }
                    ]
                },
            },
        )

    @staticmethod
    def _calculation_markdown(*calculations: dict[str, object]) -> str:
        parts: list[str] = []
        for calculation in calculations:
            payload = calculation["result"]
            parts.append(
                f"公式：{payload['formula_id']}@{payload['formula_version']}；"
                f"分项与舍入后合计：{payload['grand_total']}元；"
                f"来源：{'、'.join(payload['source_refs'])}。"
            )
        return "\n".join(parts) + "\n聚合边界：不同主张和互斥情景不自动合并。"


class ModelLedWorkingTimeLeaveDoubleWageScenarioTests(unittest.TestCase):
    def test_references_and_migration_are_complete_for_the_four_capabilities(self):
        reference_dir = ROOT / "references" / "model-led"
        required_sections = (
            "能力边界",
            "最低必要事实与停止条件",
            "事实、证据与分析假设",
            "主张分析卡与方案比较",
            "金额与 amount.calculate",
            "动态核验与降级",
            "交叉影响与重复获偿",
            "四类最小行为样本",
        )
        for name in (
            "overtime-pay.md",
            "paid-leave-routing.md",
            "annual-leave-pay.md",
            "unsigned-contract-double-wage.md",
        ):
            content = (reference_dir / name).read_text(encoding="utf-8")
            for section in required_sections:
                self.assertIn(section, content, f"{name}: {section}")
            for sample in ("成立样本", "不成立样本", "信息不足样本", "事实冲突样本"):
                self.assertIn(sample, content, f"{name}: {sample}")
            self.assertNotIn("unclassified", content.lower())

        migration = (ROOT / "docs" / "agents" / "domain-migration.md").read_text(encoding="utf-8")
        for disposition in ("preserve", "revise", "calculator", "obsolete", "unresolved"):
            self.assertIn(disposition, migration)
        for source in (
            "overtime-pay",
            "paid-leave-routing",
            "annual-leave-pay",
            "unsigned-contract-double-wage",
        ):
            self.assertIn(source, migration)
        self.assertIn("05 号工单", migration)
        self.assertNotIn("未分类", migration)

    def test_natural_language_cross_claim_closure_keeps_shared_facts_and_routes_distinct(self):
        with self.subTest("public seam"), TemporaryDirectory() as temp:
            model = _NaturalLanguageWorkingTimeLeaveDoubleWageModel(Path(temp))
            first = model.respond(
                "我在上海工作，月工资12000元，跨年有加班、年休假没休，也没有签合同。"
            )
            confirmed = model.respond("确认这些范围，请分析并把其他带薪假单独处理。")
            analyzed = model.respond("请分析并计算当前主张。")

            self.assertIn("case_id", first)
            self.assertIn("已记录确认", confirmed["assistant"])
            self.assertEqual(
                [calculation["result"]["formula_id"] for calculation in analyzed["calculations"]],
                ["overtime_pay", "annual_leave_pay", "unsigned_contract_double_wage"],
            )
            self.assertEqual(
                [calculation["result"]["grand_total"] for calculation in analyzed["calculations"]],
                ["3225.00", "3310.34", "96000.00"],
            )
            self.assertNotIn("JSON", analyzed["assistant"])
            self.assertNotIn("record_id", analyzed["assistant"])

            archive = model.archive.read(model.case_id)
            self.assertTrue(archive["ok"], archive)
            markdown = archive["result"]["markdown"]
            for phrase in (
                "同一12000元/月工资基数分别影响加班与年休假计算",
                "劳动关系事实支持未签合同二倍工资",
                "未休年休假不按本路由统一折现",
                "不把同一收入重复计入",
                "聚合边界：不同主张和互斥情景不自动合并",
            ):
                self.assertIn(phrase, markdown)


if __name__ == "__main__":
    unittest.main()
