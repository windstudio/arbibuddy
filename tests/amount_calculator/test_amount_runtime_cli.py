from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.amount_calculator.runtime_cli import main
from tests.amount_calculator.test_model_led_amount_contract import (
    ModelLedAmountContractTests,
)


class AmountRuntimeCliTests(unittest.TestCase):
    def test_calculate_consumes_managed_runtime_input(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            input_path = workspace / ".arbibuddy" / "runtime-input" / "amount.json"
            input_path.parent.mkdir(parents=True)
            input_path.write_text(
                json.dumps(ModelLedAmountContractTests._request(), ensure_ascii=False),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "calculate",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["contract_version"], "amount.calculate-v1")
            self.assertEqual(result["result"]["grand_total"], "102.02")

    def test_runtime_input_outside_managed_directory_is_not_read_or_deleted(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            input_path = workspace / "amount.json"
            input_path.write_text("{}", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "calculate",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertTrue(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertEqual(result["errors"][0]["code"], "invalid_runtime_input_path")


if __name__ == "__main__":
    unittest.main()
