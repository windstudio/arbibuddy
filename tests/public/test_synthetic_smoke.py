"""New synthetic checks; these do not replay historical acceptance cases."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.amount_calculator.public import calculate
from scripts.case_archive.service import CaseArchive

FIXTURE = Path(__file__).parent / "fixtures" / "arithmetic-rounding.json"

class PublicSyntheticSmoke(unittest.TestCase):
    def test_independent_rounding_then_sum(self):
        result = calculate(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertTrue(result["ok"], result)
        self.assertEqual([line["result"] for line in result["result"]["line_items"]], ["431.24", "32.79"])
        self.assertEqual(result["result"]["grand_total"], "464.03")

    def test_incompatible_unit_is_rejected(self):
        request = deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))
        request["scenarios"][0]["inputs"]["line_items"][0]["quantity"]["unit"] = "hour"
        result = calculate(request)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["errors"], result)

    def test_synthetic_archive_can_be_created_and_read(self):
        with tempfile.TemporaryDirectory(prefix="arbibuddy-public-synthetic-") as workspace:
            archive = CaseArchive(workspace)
            created = archive.create({"case_label":"公开合成档案", "jurisdiction":"待核实", "initial_goal":"验证档案创建和读取，不涉及真实案件。"})
            self.assertTrue(created["ok"], created)
            read = archive.read(created["result"]["case_id"])
            self.assertTrue(read["ok"], read)
            self.assertIn("公开合成档案", read["result"]["markdown"])
