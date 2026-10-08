from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.platform_adapters.cli import _parser
from scripts.platform_adapters.service import (
    PlatformAdapterError,
    install_skill,
    update_skill,
    verify_install,
)


ROOT = Path(__file__).resolve().parents[2]


class PlatformSurfaceTests(unittest.TestCase):
    def test_platform_adapter_exposes_only_current_commands(self):
        parser = _parser()
        choices = set(parser._subparsers._group_actions[0].choices)
        self.assertEqual(
            choices,
            {
                "evaluate",
                "install",
                "update",
                "uninstall",
                "inspect-source",
                "build-artifact",
                "verify-artifact",
                "verify-core",
                "verify-install",
                "identify",
                "uninstall",
                "cleanup",
                "verify-platform",
                "probe",
                "present-files",
            },
        )

    def test_adapter_metadata_matches_current_installation_contracts(self):
        expected_keys = {
            "codex": {
                "schema_version",
                "platform",
                "project_discovery_path",
                "user_discovery_path",
                "required_metadata",
                "discovery_probe",
                "runtime_permissions",
                "installer",
                "uninstaller",
                "verifier",
                "official_documentation",
            },
            "claude-code": {
                "schema_version",
                "platform",
                "project_discovery_path",
                "user_discovery_path",
                "required_metadata",
                "discovery_probe",
                "installer",
                "uninstaller",
                "verifier",
                "official_documentation",
            },
            "workbuddy": {
                "schema_version",
                "platform",
                "project_discovery_path",
                "user_discovery_path",
                "required_metadata",
                "discovery_probe",
                "installation",
                "official_documentation",
            },
        }
        for platform, expected in expected_keys.items():
            path = ROOT / "adapters" / platform / "adapter.json"
            with self.subTest(path=path.name):
                metadata = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(metadata), expected)

    def test_update_replaces_the_complete_skill_tree(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            stale = skill_root / "obsolete-resource.txt"
            stale.write_text("must not survive replacement", encoding="utf-8")

            report = update_skill(
                platform="codex",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )

            self.assertEqual(
                set(report),
                {
                    "platform",
                    "scope",
                    "mode",
                    "skill_root",
                    "receipt",
                    "skill_version",
                    "updated",
                    "verified",
                },
            )
            self.assertTrue(report["updated"])
            self.assertTrue(report["verified"])
            self.assertFalse(stale.exists())
            self.assertEqual(
                (skill_root / "VERSION").read_text(encoding="utf-8").strip(),
                (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            )
            self.assertTrue(verify_install(platform="codex", skill_root=skill_root)["runtime_identity_verified"])

    def test_update_rejects_link_mode_before_touching_existing_install(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            before = (skill_root / "VERSION").read_bytes()

            with self.assertRaisesRegex(PlatformAdapterError, "update.*copy"):
                update_skill(
                    platform="codex",
                    scope="project",
                    target_root=target,
                    source=ROOT,
                    mode="link",
                )

            self.assertEqual((skill_root / "VERSION").read_bytes(), before)
            self.assertTrue(verify_install(platform="codex", skill_root=skill_root)["runtime_identity_verified"])


if __name__ == "__main__":
    unittest.main()
