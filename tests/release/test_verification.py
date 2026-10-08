from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.release.verification import REQUIRED_CHECKS, release_root, validate_release_destination, verify_evidence
from scripts.runtime_identity import RuntimeIdentityModule

ROOT = Path(__file__).resolve().parents[2]


class ReleaseEvidenceTests(unittest.TestCase):
    def _record(self, root):
        report = root / "report.md"
        report.write_text("隔离测试证据；不代表正式发布。", encoding="utf-8")
        return {"contract_version": "model-led-release-evidence-v1", "runtime_identity": RuntimeIdentityModule().generate(ROOT), "checks": {name: {"status": "passed", "report": "report.md", "sha256": sha256(report.read_bytes()).hexdigest()} for name in REQUIRED_CHECKS}}

    def test_evidence_verification_is_read_only_and_identity_bound(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            record = root / "evidence.json"
            record.write_text(json.dumps(self._record(root)), encoding="utf-8")
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertEqual(verify_evidence(ROOT, record)["boundary"], "evidence_files_and_identity_only")
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_missing_failed_stale_and_unbounded_evidence_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            good = self._record(root)
            variants = []
            for change in ("missing", "failed", "notes", "digest", "escape", "identity"):
                record = deepcopy(good)
                if change == "missing":
                    del record["checks"]["tools"]
                elif change == "identity":
                    record["runtime_identity"]["skill_version"] = "different"
                else:
                    field, value = {"failed": ("status", "failed"), "notes": ("status", "accepted_with_notes"), "digest": ("sha256", "0" * 64), "escape": ("report", "../outside.md")}[change]
                    record["checks"]["tools"][field] = value
                variants.append((change, record))
            for label, record in variants:
                with self.subTest(label=label):
                    path = root / "evidence.json"
                    path.write_text(json.dumps(record), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        verify_evidence(ROOT, path)

    def test_formal_destination_has_one_version_bound_project_root(self):
        expected = release_root(ROOT)
        self.assertEqual(validate_release_destination(ROOT, expected), expected.resolve())
        for wrong in (ROOT / "dist", ROOT / "dist/1.2.6-rc39", expected.parent / "wrong-version"):
            with self.subTest(path=wrong), self.assertRaises(ValueError):
                validate_release_destination(ROOT, wrong)


if __name__ == "__main__":
    unittest.main()
