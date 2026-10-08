from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json

from scripts.runtime_identity import (
    RUNTIME_IDENTITY_SCHEMA_VERSION,
    RuntimeIdentityModule,
)
from scripts.platform_adapters.service import install_skill, verify_install
from scripts.version import display_version


ROOT = Path(__file__).resolve().parents[2]


class RuntimeIdentityModuleTests(unittest.TestCase):
    def test_other_platforms_reject_workbuddy_import_metadata(self):
        for platform in ("codex", "claude-code"):
            with self.subTest(platform=platform), TemporaryDirectory() as temp:
                installed = install_skill(platform=platform, scope="project", target_root=Path(temp), source=ROOT, mode="copy")
                skill_root = Path(installed["skill_root"])
                (skill_root / "_user_meta.json").write_text(
                    json.dumps({"name": "arbibuddy", "installedAt": 1790922044674, "source": "userImport"}),
                    encoding="utf-8",
                )
                self.assertFalse(RuntimeIdentityModule().preflight(skill_root)["verified"])

    def test_copy_with_git_cannot_hide_install_resource_set_failure(self):
        with TemporaryDirectory() as temp:
            installed = install_skill(platform="codex", scope="project", target_root=Path(temp), source=ROOT, mode="copy")
            skill_root = Path(installed["skill_root"])
            for command in (
                ["git", "init", "--quiet"],
                ["git", "add", "VERSION"],
                ["git", "-c", "user.name=Ticket12 fixture", "-c", "user.email=ticket12@example.invalid", "commit", "--quiet", "-m", "synthetic source identity"],
            ):
                subprocess.run(command, cwd=skill_root, check=True, capture_output=True, timeout=15)
            result = RuntimeIdentityModule().preflight(skill_root)
            self.assertFalse(result["verified"], result)
            self.assertIn("installed_resource_set_mismatch", result["failures"])

    def test_invalid_source_link_receipt_stops_before_resolved_source_fallback(self):
        from scripts import runtime_identity
        from unittest.mock import patch

        with TemporaryDirectory() as temp:
            try:
                installed = install_skill(platform="codex", scope="project", target_root=Path(temp), source=ROOT, mode="link")
            except OSError as error:
                self.skipTest(f"当前平台不能创建源码链接：{error}")
            skill_root = Path(installed["skill_root"])
            receipt_path = skill_root.parent / "arbibuddy.install.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["runtime_identity"]["installation_manifest_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            linked_module = skill_root / "scripts/runtime_identity.py"
            with patch.object(runtime_identity, "__file__", str(linked_module)):
                result = RuntimeIdentityModule().preflight()
            self.assertFalse(result["verified"], result)
            self.assertIn("runtime_identity_marker_mismatch", result["failures"])

    def test_copy_install_rejects_extra_execution_files_without_rewriting_case(self):
        from scripts.case_archive import service as archive_service
        from scripts.case_archive import CaseArchive
        from unittest.mock import patch

        for platform in ("codex", "claude-code"):
            with self.subTest(platform=platform), TemporaryDirectory() as temp:
                workspace = Path(temp) / "case-workspace"
                archive = CaseArchive(workspace)
                created = archive.create({"initial_goal": "保护已有用户档案"})
                self.assertTrue(created["ok"], created)
                case_id = created["result"]["case_id"]
                before = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
                installed = install_skill(platform=platform, scope="project", target_root=Path(temp) / "project", source=ROOT, mode="copy")
                skill_root = Path(installed["skill_root"])
                self.assertTrue(RuntimeIdentityModule().preflight(skill_root)["verified"])
                extra = skill_root / "scripts/core_workflow/cli.py"
                extra.parent.mkdir(parents=True)
                extra.write_text("# harmless extra execution fixture\n", encoding="utf-8")
                self.assertFalse(verify_install(platform=platform, skill_root=skill_root)["runtime_identity_verified"])
                preflight = RuntimeIdentityModule().preflight(skill_root)
                self.assertIn("installed_resource_set_mismatch", preflight["failures"])
                with patch.object(archive_service._RUNTIME_IDENTITY, "preflight", return_value=preflight):
                    self.assertFalse(archive.read(case_id)["ok"])
                self.assertEqual(before, {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file()})

    def test_current_identity_is_path_free_and_contains_shared_contract_digests(self):
        identity = RuntimeIdentityModule().generate(ROOT)

        self.assertEqual(identity["schema_version"], RUNTIME_IDENTITY_SCHEMA_VERSION)
        self.assertEqual(identity["skill_version"], display_version(ROOT))
        self.assertIn("core_contract_version", identity)
        self.assertIn("installation_manifest_sha256", identity)
        self.assertIn("resource_sha256", identity)
        self.assertIn("artifact_manifest_sha256", identity)
        self.assertNotIn(str(ROOT), str(identity))
        self.assertNotIn("\\\\?\\", str(identity))

    def test_copy_installation_and_link_installation_compare_to_the_same_identity(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            RuntimeIdentityModule().install_snapshot(ROOT, source, mode="copy")
            copy_root = root / "copy"
            link_root = root / "link"
            RuntimeIdentityModule().install_snapshot(source, copy_root, mode="copy")
            try:
                RuntimeIdentityModule().install_snapshot(source, link_root, mode="link")
            except OSError as error:
                self.skipTest(f"当前平台不支持目录 link：{error}")
            expected = RuntimeIdentityModule().generate(source)

            self.assertTrue(
                RuntimeIdentityModule().compare(expected, copy_root)["verified"]
            )
            self.assertTrue(
                RuntimeIdentityModule().compare(expected, link_root)["verified"]
            )

    def test_single_file_change_and_link_source_change_are_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            RuntimeIdentityModule().install_snapshot(ROOT, source, mode="copy")
            copy_root = root / "copy"
            source_link = root / "source-link"
            RuntimeIdentityModule().install_snapshot(source, copy_root, mode="copy")
            try:
                RuntimeIdentityModule().install_snapshot(source, source_link, mode="link")
            except OSError as error:
                self.skipTest(f"当前平台不支持目录 link：{error}")
            expected = RuntimeIdentityModule().generate(source)

            (copy_root / "SKILL.md").write_text("tampered", encoding="utf-8")
            self.assertFalse(
                RuntimeIdentityModule().compare(expected, copy_root)["verified"]
            )

            # The link points at the source snapshot; changing its source must
            # invalidate the same identity without touching the repository.
            if (source_link / "SKILL.md").is_file():
                original = (source / "SKILL.md").read_text(encoding="utf-8")
                try:
                    (source / "SKILL.md").write_text(original + "\nchanged", encoding="utf-8")
                    self.assertFalse(
                        RuntimeIdentityModule().compare(expected, source_link)["verified"]
                    )
                finally:
                    (source / "SKILL.md").write_text(original, encoding="utf-8")



    def test_old_receipt_and_tampered_installation_fail_before_runtime_use(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            report = install_skill(
                platform="codex",
                scope="project",
                target_root=project,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(report["skill_root"])
            receipt_path = skill_root.parent / "arbibuddy.install.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["schema_version"] = 1
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            old = verify_install(platform="codex", skill_root=skill_root)
            self.assertFalse(old["discovery_contract_valid"])
            self.assertFalse(old["runtime_identity_verified"])

            receipt["schema_version"] = 2
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            (skill_root / "SKILL.md").write_text("tampered", encoding="utf-8")
            tampered = verify_install(platform="codex", skill_root=skill_root)
            self.assertFalse(tampered["runtime_identity_verified"])
            self.assertIn("installation_manifest", tampered["runtime_identity_verification"]["failures"])

    def test_existing_but_stale_marker_is_reported_as_mismatch_not_missing(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            report = install_skill(
                platform="codex",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(report["skill_root"])
            receipt_path = skill_root.parent / "arbibuddy.install.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["runtime_identity"]["skill_version"] = "v0.0.0-stale"
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

            result = RuntimeIdentityModule().preflight(skill_root)

            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_mismatch", result["failures"])
            self.assertNotIn("runtime_identity_marker_missing", result["failures"])

    def test_missing_installation_marker_is_not_reported_as_mismatch(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "arbibuddy"
            RuntimeIdentityModule().install_snapshot(ROOT, target, mode="copy")

            result = RuntimeIdentityModule().preflight(target)

            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_missing", result["failures"])
            self.assertNotIn("runtime_identity_marker_mismatch", result["failures"])
            self.assertNotIn("runtime_identity_marker_invalid", result["failures"])

    def test_invalid_installation_marker_is_not_reported_as_missing_or_mismatch(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "arbibuddy"
            RuntimeIdentityModule().install_snapshot(ROOT, target, mode="copy")
            (target.parent / "arbibuddy.install.json").write_text(
                "{not-json",
                encoding="utf-8",
            )

            result = RuntimeIdentityModule().preflight(target)

            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_invalid", result["failures"])
            self.assertNotIn("runtime_identity_marker_missing", result["failures"])
            self.assertNotIn("runtime_identity_marker_mismatch", result["failures"])


if __name__ == "__main__":
    unittest.main()
