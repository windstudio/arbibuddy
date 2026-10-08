from __future__ import annotations

from pathlib import Path
import unittest

from scripts.legal_verification import LegalVerificationTracer


ROOT = Path(__file__).resolve().parents[2]


class LegalBaselineContractTests(unittest.TestCase):
    def test_published_baseline_exposes_complete_metadata_without_treating_url_as_proof(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.load_baseline()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation"], "load_baseline")
        self.assertEqual(result["result"]["schema_version"], "legal-baseline-v1")
        self.assertGreaterEqual(len(result["result"]["rules"]), 1)
        for rule in result["result"]["rules"]:
            with self.subTest(rule_id=rule["rule_id"]):
                self.assertTrue(rule["name"])
                self.assertTrue(rule["issuing_authority"])
                self.assertTrue(rule["version"])
                self.assertEqual(rule["status"], "effective")
                self.assertTrue(rule["jurisdiction"])
                self.assertTrue(rule["official_url"].startswith("https://"))
                self.assertRegex(rule["last_verified_on"], r"^2026-\d{2}-\d{2}$")
                self.assertRegex(rule["review_due_on"], r"^2026-\d{2}-\d{2}$|^2027-\d{2}-\d{2}$")
                self.assertTrue(rule["verification_basis"])
                self.assertNotEqual(rule.get("verified_by_url_only"), True)

    def test_incomplete_baseline_is_rejected_even_when_its_url_is_present(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.validate_baseline(
            {
                "schema_version": "legal-baseline-v1",
                "rules": [
                    {
                        "rule_id": "missing-metadata",
                        "name": "某项规则",
                        "official_url": "https://example.test/rule",
                    }
                ],
            }
        )

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("rules[0].issuing_authority", paths)
        self.assertIn("rules[0].last_verified_on", paths)
        self.assertIn("rules[0].verification_basis", paths)


if __name__ == "__main__":
    unittest.main()
