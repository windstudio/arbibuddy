from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedEmploymentRoutingWageContractTests(unittest.TestCase):
    def test_skill_routes_the_three_model_led_references_and_amount_tool(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for reference in (
            "references/model-led/employment-relationship.md",
            "references/model-led/dispute-routing.md",
            "references/model-led/wage-analysis.md",
            "references/amount-calculate.md",
        ):
            self.assertIn(reference, skill)
        self.assertIn("amount.calculate", skill)
        self.assertIn("scripts.amount_calculator.runtime_cli calculate", skill)
        self.assertNotIn("调用专项分析器", skill)

    def test_amount_reference_is_self_describing_and_separates_model_from_arithmetic(self):
        reference = (ROOT / "references/amount-calculate.md").read_text(encoding="utf-8")
        for required in (
            "formula_id",
            "formula_version",
            "legal_basis_summary",
            "source_refs",
            "scenarios",
            "rounding_policy",
            "ROUND_HALF_UP",
            "yuan_per_month",
            "yuan_per_day",
            "unit_mismatch",
            "输入不足",
            "聚合",
            "不判断法律",
            "不自动选择最大金额",
            "CaseArchive.commit",
        ):
            self.assertIn(required, reference)
        self.assertNotIn("PublicTurn", reference)
        self.assertNotIn("advance/status", reference)
        self.assertIn("scripts.amount_calculator.runtime_cli", reference)
        self.assertIn(".arbibuddy/runtime-input/", reference)


if __name__ == "__main__":
    unittest.main()
