from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile
from scripts.runtime_resources import RUNTIME_FILES

from scripts.platform_adapters.service import (
    _package_payload,
    _write_deterministic_zip,
)


class ArtifactSnapshotTests(unittest.TestCase):
    def test_manifest_and_zip_use_the_same_immutable_source_snapshot(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            for relative in RUNTIME_FILES:
                resource = source / relative
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_bytes(b"before")
            skill = source / "SKILL.md"
            payload = _package_payload(source)
            skill.write_text("after", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "platform": "workbuddy",
                "skill": "arbibuddy",
                "core_contract_version": 5,
                "files": {
                    relative: hashlib.sha256(data).hexdigest()
                    for relative, data in payload.items()
                },
            }
            output = root / "artifact.zip"

            _write_deterministic_zip(output, payload, manifest)

            with zipfile.ZipFile(output) as archive:
                archived = archive.read("arbibuddy/SKILL.md")
                archived_manifest = json.loads(archive.read("arbibuddy.package.json"))
            self.assertEqual(archived, b"before")
            self.assertEqual(
                archived_manifest["files"]["SKILL.md"],
                hashlib.sha256(archived).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
