from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.amount_calculator.public import AmountCalculator


ROOT = Path(__file__).resolve().parents[2]


class ModelLedAmountBoundaryTests(unittest.TestCase):
    @staticmethod
    def _invalid_items_request() -> dict[str, object]:
        return {
            "items": [
                {
                    "calculation_id": "CAL-001",
                    "label": "工资差额",
                    "mode": "exact",
                    "period": "2026年3月",
                    "basis": 1000,
                    "paid": 0,
                    "quantity": 1,
                    "multiplier": 1,
                }
            ]
        }

    def test_public_amount_calculate_rejects_unknown_items_shape(self):
        result = AmountCalculator().calculate(self._invalid_items_request())

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["contract_version"], "amount.calculate-v1")
        self.assertEqual(result["operation"], "amount.calculate")
        self.assertIn("unknown_field", {error["code"] for error in result["errors"]})
        self.assertIn("missing_input", {error["code"] for error in result["errors"]})




if __name__ == "__main__":
    unittest.main()
