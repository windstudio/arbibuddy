from __future__ import annotations

from pathlib import Path
import json
import unittest

from scripts.legal_verification import LegalVerificationTracer


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_LEGACY_REGISTRATIONS = {
    "references/legal/annual-leave-pay-rules.v1.json",
    "references/legal/dispute-routing-rules.v1.json",
    "references/legal/employment-relationship-rules.v1.json",
    "references/legal/enforcement-rules.v1.json",
    "references/legal/forced-termination-rules.v1.json",
    "references/legal/overtime-rules.v1.json",
    "references/legal/paid-leave-routing-rules.v1.json",
    "references/legal/property-preservation-rules.v1.json",
    "references/legal/shanghai-cancellation-restriction-rules.v1.json",
    "references/legal/unlawful-termination-rules.v1.json",
    "references/legal/unsigned-contract-double-wage-rules.v1.json",
    "references/legal/wage-floor-rules.v1.json",
    "references/legal/wage-rules.v1.json",
}


class SourceRegistryCleanupTests(unittest.TestCase):
    def test_all_current_source_registrations_have_a_replayable_disposition_and_evidence(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.load_source_cleanup()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation"], "load_source_cleanup")
        registrations = result["result"]["registrations"]
        self.assertEqual(len(registrations), 13)
        self.assertEqual(
            {item["legacy_file"] for item in registrations},
            EXPECTED_LEGACY_REGISTRATIONS,
        )
        self.assertEqual(
            {item["disposition"] for item in registrations},
            {"valid", "replace", "merge"},
        )
        for item in registrations:
            with self.subTest(legacy_file=item["legacy_file"]):
                self.assertTrue((ROOT / item["legacy_file"]).is_file())
                self.assertTrue(item["reason"])
                self.assertTrue(item["evidence"])
                self.assertTrue(item["successor"])
                self.assertNotEqual(item["disposition"], "unclassified")

    def test_source_cleanup_rejects_an_unclassified_registration_without_evidence(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.validate_source_cleanup(
            {
                "schema_version": "source-cleanup-v1",
                "registrations": [
                    {
                        "legacy_file": "references/legal/example.json",
                        "disposition": "unclassified",
                        "reason": "",
                        "evidence": [],
                        "successor": "",
                    }
                ],
            }
        )

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("registrations[0].disposition", paths)
        self.assertIn("registrations[0].evidence", paths)
        self.assertIn("registrations[0].successor", paths)

    def test_source_cleanup_rejects_an_evidence_pointer_that_cannot_be_replayed(self):
        tracer = LegalVerificationTracer(ROOT)
        manifest = json.loads(
            (ROOT / "references" / "legal" / "source-cleanup.v1.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["registrations"][0]["evidence"] = [
            "references/legal/does-not-exist.json#rules"
        ]

        result = tracer.validate_source_cleanup(manifest)

        self.assertFalse(result["ok"], result)
        self.assertIn(
            "registrations[0].evidence[0]",
            {error["path"] for error in result["errors"]},
        )

    def test_source_cleanup_rejects_evidence_without_the_legacy_registration_or_canonical_successor(self):
        tracer = LegalVerificationTracer(ROOT)
        manifest = json.loads(
            (ROOT / "references" / "legal" / "source-cleanup.v1.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["registrations"][0]["evidence"][0] = "SKILL.md#ArbiBuddy"
        manifest["registrations"][0]["successor"] = "SKILL.md#ArbiBuddy"

        result = tracer.validate_source_cleanup(manifest)

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("registrations[0].evidence", paths)
        self.assertIn("registrations[0].successor[0]", paths)


if __name__ == "__main__":
    unittest.main()
