from __future__ import annotations

import unittest

from scripts.amount_calculator.public import AmountCalculator


class ModelLedAmountContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = AmountCalculator()

    @staticmethod
    def _request(*, aggregation_policy: str = "single_scenario", scenarios=None):
        return {
            "calculation_label": "2026年3月至4月工资差额",
            "formula_id": "wage_difference",
            "formula_version": "1.0.0",
            "legal_basis_summary": "模型已选择工资差额口径；计算器只执行明确输入的数学公式。",
            "source_refs": ["F-001", "E-001"],
            "aggregation_policy": aggregation_policy,
            "scenarios": scenarios
            or [
                {
                    "scenario_id": "primary",
                    "assumptions": ["两个月适用同一工资基数。"],
                    "period": {
                        "start_date": "2026-03-01",
                        "end_date": "2026-04-30",
                        "semantics": "completed_months",
                    },
                    "inputs": {
                        "line_items": [
                            {
                                "line_id": "2026-03",
                                "basis": {"value": "100.005", "unit": "yuan_per_month"},
                                "quantity": {"value": "1", "unit": "month"},
                                "multiplier": {"value": "1", "unit": "ratio"},
                                "paid": {"value": "0", "unit": "yuan"},
                            },
                            {
                                "line_id": "2026-04",
                                "basis": {"value": "2.005", "unit": "yuan_per_month"},
                                "quantity": {"value": "1", "unit": "month"},
                                "multiplier": {"value": "1", "unit": "ratio"},
                                "paid": {"value": "0", "unit": "yuan"},
                            },
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

    def test_contract_describes_fields_units_math_errors_and_recovery(self):
        contract = self.calculator.describe()

        self.assertEqual(contract["contract_version"], "amount.calculate-v1")
        self.assertEqual(contract["operation"], "amount.calculate")
        self.assertTrue(
            any(
                item["name"] == "formula_id" and item["required"]
                for item in contract["input"]["fields"]
            )
        )
        for field in (
            "formula_version",
            "legal_basis_summary",
            "source_refs",
            "scenarios",
            "rounding_policy",
        ):
            self.assertTrue(
                any(item["name"] == field and item["required"] for item in contract["input"]["fields"]),
                field,
            )
        self.assertEqual(
            set(contract["input"]["units"]),
            {
                "yuan",
                "yuan_per_month",
                "yuan_per_day",
                "yuan_per_hour",
                "month",
                "day",
                "hour",
                "ratio",
                "count",
            },
        )
        self.assertIn("wage_difference", contract["formulas"])
        self.assertIn("variable_remuneration", contract["formulas"])
        self.assertTrue(
            any("ROUND_HALF_UP" in item for item in contract["math_conventions"])
        )
        self.assertIn("errors", contract)
        self.assertIn("recoverable", contract["errors"])
        self.assertIn("grand_total_status", contract["output"]["fields"])
        self.assertTrue(
            {
                "unknown_field",
                "invalid_text",
                "invalid_object",
                "duplicate_scenario",
            }
            <= set(contract["errors"]["codes"])
        )

    def test_wage_difference_rounds_each_line_item_before_summing(self):
        result = self.calculator.calculate(self._request())

        self.assertTrue(result["ok"], result)
        payload = result["result"]
        self.assertEqual(payload["formula_id"], "wage_difference")
        self.assertEqual(payload["formula_version"], "1.0.0")
        self.assertEqual([item["result"] for item in payload["line_items"]], ["100.01", "2.01"])
        self.assertEqual(payload["scenario_totals"], [{"scenario_id": "primary", "total": "102.02"}])
        self.assertEqual(payload["grand_total"], "102.02")
        self.assertEqual(payload["rounding_policy"]["mode"], "ROUND_HALF_UP")
        self.assertEqual(payload["intermediate_trace"][0]["raw_result"], "100.005")
        self.assertIn("yuan_per_month", payload["normalized_inputs"]["units"])
        self.assertNotIn("最优", payload)
        self.assertNotIn("胜诉", payload)

    def test_mutually_exclusive_scenarios_are_not_maxed_or_implicitly_summed(self):
        scenarios = self._request()["scenarios"]
        alternative = {
            **scenarios[0],
            "scenario_id": "fallback",
            "inputs": {
                "line_items": [
                    {
                        **scenarios[0]["inputs"]["line_items"][0],
                        "line_id": "alternative",
                        "basis": {"value": "120", "unit": "yuan_per_month"},
                    }
                ]
            },
        }
        result = self.calculator.calculate(
            self._request(
                aggregation_policy="mutually_exclusive",
                scenarios=[scenarios[0], alternative],
            )
        )

        self.assertTrue(result["ok"], result)
        payload = result["result"]
        self.assertEqual(
            payload["scenario_totals"],
            [
                {"scenario_id": "primary", "total": "102.02"},
                {"scenario_id": "fallback", "total": "120.00"},
            ],
        )
        self.assertIsNone(payload["grand_total"])
        self.assertEqual(payload["grand_total_status"], "not_aggregated_mutually_exclusive")
        self.assertNotIn("selected_scenario", payload)

    def test_variable_remuneration_and_zero_value_keep_explicit_units_and_cap(self):
        request = self._request()
        request.update(
            {
                "calculation_label": "季度奖金条件情景",
                "formula_id": "variable_remuneration",
                "scenarios": [
                    {
                        "scenario_id": "bonus-primary",
                        "assumptions": ["模型已确认按一件计数单位核算。"],
                        "period": {
                            "start_date": "2026-04-01",
                            "end_date": "2026-06-30",
                            "semantics": "event_count",
                        },
                        "inputs": {
                            "line_items": [
                                {
                                    "line_id": "bonus",
                                    "basis": {"value": "0", "unit": "yuan"},
                                    "quantity": {"value": "1", "unit": "count"},
                                    "multiplier": {"value": "1", "unit": "ratio"},
                                    "paid": {"value": "0", "unit": "yuan"},
                                    "cap": {"value": "100", "unit": "yuan"},
                                }
                            ]
                        },
                    }
                ],
            }
        )

        result = self.calculator.calculate(request)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["line_items"][0]["result"], "0.00")
        self.assertFalse(result["result"]["line_items"][0]["cap_applied"])
        self.assertEqual(result["result"]["grand_total"], "0.00")

    def test_independent_amount_total_is_explicitly_rounded_without_legal_aggregation(self):
        request = self._request()
        request.update(
            {
                "calculation_label": "独立欠薪分项",
                "formula_id": "wage_arrears_total",
                "scenarios": [
                    {
                        "scenario_id": "arrears",
                        "assumptions": ["模型已分别确认两个期间的欠付金额。"],
                        "period": {
                            "start_date": "2026-03-01",
                            "end_date": "2026-04-30",
                            "semantics": "explicit_period",
                        },
                        "inputs": {
                            "line_items": [
                                {
                                    "line_id": "march",
                                    "amount": {"value": "1.005", "unit": "yuan"},
                                },
                                {
                                    "line_id": "april",
                                    "amount": {"value": "2.005", "unit": "yuan"},
                                },
                            ]
                        },
                    }
                ],
            }
        )

        result = self.calculator.calculate(request)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [item["result"] for item in result["result"]["line_items"]],
            ["1.01", "2.01"],
        )
        self.assertEqual(result["result"]["grand_total"], "3.02")
        self.assertNotIn("recommendation", result["result"])

    def test_validation_returns_all_recoverable_errors_with_precise_paths(self):
        invalid = self._request()
        invalid["scenarios"][0]["inputs"]["line_items"][0]["basis"] = {
            "value": "-1",
            "unit": "yuan_per_month",
        }
        invalid["scenarios"][0]["inputs"]["line_items"][0]["quantity"]["unit"] = "day"
        invalid["scenarios"].append(
            {
                "scenario_id": "missing-input",
                "assumptions": [],
                "period": invalid["scenarios"][0]["period"],
                "inputs": {"line_items": [{"line_id": "bad"}]},
            }
        )

        result = self.calculator.calculate(invalid)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["operation"], "amount.calculate")
        self.assertGreaterEqual(len(result["errors"]), 4)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("scenarios[0].inputs.line_items[0].basis.value", paths)
        self.assertIn("scenarios[0].inputs.line_items[0].quantity.unit", paths)
        self.assertIn("scenarios[1].inputs.line_items[0].basis", paths)
        self.assertIn("scenarios[1].inputs.line_items[0].quantity", paths)
        for error in result["errors"]:
            self.assertTrue(
                {"code", "path", "message", "expected", "received", "recoverable"}
                <= set(error)
            )

    def test_non_string_aggregation_policy_returns_a_recoverable_error(self):
        request = self._request()
        request["aggregation_policy"] = []

        result = self.calculator.calculate(request)

        self.assertFalse(result["ok"], result)
        self.assertIn(
            "constraint_conflict",
            {error["code"] for error in result["errors"]},
        )


if __name__ == "__main__":
    unittest.main()
