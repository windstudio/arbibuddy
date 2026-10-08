from __future__ import annotations

import unittest

from scripts.amount_calculator.public import AmountCalculator


class ModelLedWorkingTimeLeaveDoubleWageAmountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = AmountCalculator()

    @staticmethod
    def _request(*, formula_id: str, scenarios: list[dict[str, object]], aggregation_policy: str = "single_scenario") -> dict[str, object]:
        return {
            "calculation_label": f"{formula_id} 交叉计算",
            "formula_id": formula_id,
            "formula_version": "1.0.0",
            "legal_basis_summary": "模型已经选择法律口径；计算器只执行显式数学。",
            "source_refs": ["F-WAGE-001", "E-WAGE-001"],
            "aggregation_policy": aggregation_policy,
            "scenarios": scenarios,
            "rounding_policy": {
                "id": "money-independent-unit-half-up-v1",
                "quantum": "0.01",
                "mode": "ROUND_HALF_UP",
                "aggregation": "round_each_line_item_then_sum",
            },
        }

    def test_contract_exposes_dedicated_formulas_and_shared_paid_leave_math(self):
        contract = self.calculator.describe()

        self.assertIn("wage_difference", contract["formulas"])
        self.assertIn("divisor", contract["input"]["line_item_shapes"]["daily_rate_difference"])
        for formula_id in (
            "overtime_pay",
            "annual_leave_pay",
            "unsigned_contract_double_wage",
        ):
            self.assertIn(formula_id, contract["formulas"])
        self.assertIn("daily_rate_difference", {
            formula["kind"] for formula in contract["formulas"].values()
        })

    def test_overtime_supports_hour_day_segments_cap_and_line_rounding(self):
        result = self.calculator.calculate(
            self._request(
                formula_id="overtime_pay",
                scenarios=[
                    {
                        "scenario_id": "cross-month-year",
                        "assumptions": ["工作日、休息日和法定休假日分别由模型确认。"],
                        "period": {
                            "start_date": "2025-12-31",
                            "end_date": "2026-01-02",
                            "semantics": "explicit_period",
                        },
                        "inputs": {
                            "line_items": [
                                {
                                    "line_id": "workday-hours",
                                    "basis": {"value": "100.005", "unit": "yuan_per_hour"},
                                    "quantity": {"value": "1", "unit": "hour"},
                                    "multiplier": {"value": "1.5", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                },
                                {
                                    "line_id": "rest-day",
                                    "basis": {"value": "600", "unit": "yuan_per_day"},
                                    "quantity": {"value": "1", "unit": "day"},
                                    "multiplier": {"value": "2", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                    "cap": {"value": "1000", "unit": "yuan"},
                                },
                                {
                                    "line_id": "statutory-holiday-zero",
                                    "basis": {"value": "600", "unit": "yuan_per_day"},
                                    "quantity": {"value": "0", "unit": "day"},
                                    "multiplier": {"value": "3", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                },
                            ]
                        },
                    }
                ],
            )
        )

        self.assertTrue(result["ok"], result)
        payload = result["result"]
        self.assertEqual(
            [item["result"] for item in payload["line_items"]],
            ["150.01", "1000.00", "0.00"],
        )
        self.assertEqual(payload["scenario_totals"], [{"scenario_id": "cross-month-year", "total": "1150.01"}])
        self.assertEqual(payload["grand_total"], "1150.01")

    def test_annual_leave_uses_explicit_daily_divisor_and_unsigned_contract_is_monthly(self):
        annual = self.calculator.calculate(
            self._request(
                formula_id="annual_leave_pay",
                scenarios=[
                    {
                        "scenario_id": "annual-leave-2025",
                        "assumptions": ["未休天数和剔除加班工资后的月平均工资已确认。"],
                        "period": {
                            "start_date": "2025-01-01",
                            "end_date": "2025-12-31",
                            "semantics": "calendar_days",
                        },
                        "inputs": {
                            "line_items": [
                                {
                                    "line_id": "PER-001",
                                    "basis": {"value": "12000", "unit": "yuan_per_month"},
                                    "divisor": {"value": "21.75", "unit": "count"},
                                    "quantity": {"value": "3", "unit": "day"},
                                    "multiplier": {"value": "2", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                }
                            ]
                        },
                    }
                ],
            )
        )
        unsigned = self.calculator.calculate(
            self._request(
                formula_id="unsigned_contract_double_wage",
                scenarios=[
                    {
                        "scenario_id": "unsigned-eight-months",
                        "assumptions": ["成立期间、工资基数和已付金额由模型分别确认。"],
                        "period": {
                            "start_date": "2025-05-01",
                            "end_date": "2026-01-01",
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
                    }
                ],
            )
        )

        self.assertTrue(annual["ok"], annual)
        self.assertEqual(annual["result"]["line_items"][0]["result"], "3310.34")
        self.assertEqual(annual["result"]["grand_total"], "3310.34")
        self.assertTrue(unsigned["ok"], unsigned)
        self.assertEqual(unsigned["result"]["line_items"][0]["result"], "96000.00")
        self.assertEqual(unsigned["result"]["grand_total"], "96000.00")

    def test_annual_leave_division_by_zero_and_unit_mismatch_are_aggregated(self):
        request = self._request(
            formula_id="annual_leave_pay",
            scenarios=[
                {
                    "scenario_id": "invalid-inputs",
                    "assumptions": [],
                    "period": {
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                        "semantics": "calendar_days",
                    },
                    "inputs": {
                        "line_items": [
                            {
                                "line_id": "bad",
                                "basis": {"value": "12000", "unit": "yuan_per_month"},
                                "divisor": {"value": "0", "unit": "count"},
                                "quantity": {"value": "3", "unit": "month"},
                                "multiplier": {"value": "2", "unit": "ratio"},
                                "paid": {"value": "0", "unit": "yuan"},
                            }
                        ]
                    },
                }
            ],
        )

        result = self.calculator.calculate(request)

        self.assertFalse(result["ok"], result)
        self.assertIn("division_by_zero", {error["code"] for error in result["errors"]})
        self.assertIn("unit_mismatch", {error["code"] for error in result["errors"]})

    def test_annual_leave_mutually_exclusive_period_inputs_are_not_summed(self):
        result = self.calculator.calculate(
            self._request(
                formula_id="annual_leave_pay",
                aggregation_policy="mutually_exclusive",
                scenarios=[
                    {
                        "scenario_id": "three-days",
                        "assumptions": ["已休天数存在第一种事实口径。"],
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
                    {
                        "scenario_id": "four-days",
                        "assumptions": ["已休天数存在第二种事实口径。"],
                        "period": {
                            "start_date": "2025-01-01",
                            "end_date": "2025-12-31",
                            "semantics": "calendar_days",
                        },
                        "inputs": {
                            "line_items": [
                                {
                                    "line_id": "2025",
                                    "basis": {"value": "15000", "unit": "yuan_per_month"},
                                    "divisor": {"value": "21.75", "unit": "count"},
                                    "quantity": {"value": "4", "unit": "day"},
                                    "multiplier": {"value": "2", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                }
                            ]
                        },
                    },
                ],
            )
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["result"]["scenario_totals"],
            [
                {"scenario_id": "three-days", "total": "3310.34"},
                {"scenario_id": "four-days", "total": "5517.24"},
            ],
        )
        self.assertIsNone(result["result"]["grand_total"])
        self.assertEqual(
            result["result"]["grand_total_status"],
            "not_aggregated_mutually_exclusive",
        )


if __name__ == "__main__":
    unittest.main()
