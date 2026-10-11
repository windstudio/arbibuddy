from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.release.skills_distribution import build_skills_repository, validate_distribution_limits
from scripts.runtime_identity import (
    DISTRIBUTION_MARKER_NAME, RuntimeIdentityModule, runtime_identity_digest,
    runtime_identity_failure_message, runtime_identity_failure_next_action,
    build_distribution_marker, measure_runtime_identity,
)
from scripts.runtime_resources import RUNTIME_FILES, runtime_resource_names


ROOT = Path(__file__).resolve().parents[2]


class SkillsDistributionTests(unittest.TestCase):
    def _skillhub_import(self, skill: Path) -> None:
        # 平台会过滤无扩展名文件；发布/安装分别加入这些非执行文件。
        for name in ("VERSION", "LICENSE"):
            (skill / name).unlink(missing_ok=True)
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        (skill / "_meta.json").write_text(json.dumps({
            "ownerId": "614631", "publishedAt": 1791643169644,
            "slug": "arbibuddy", "version": version,
        }), encoding="utf-8")
        (skill / "_skillhub_meta.json").write_text(json.dumps({
            "slug": "arbibuddy", "name": "arbibuddy", "version": version,
            "installedAt": 1791687584191, "source": "skillhub",
            "namespace": "user_eabd9039", "canonicalName": "@user_eabd9039/arbibuddy",
            "iconSource": "https://example.invalid/icon.png",
            "iconLocalPath": str(skill / "_icon.png"),
            "iconDownloadedFrom": "https://example.invalid/icon.png",
            "labels": {"requires_api_key": "false"},
        }), encoding="utf-8")
        (skill / "_icon.png").write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGr0AAAAASUVORK5CYII="
        ))

    def test_skillhub_published_zip_metadata_without_client_files_is_valid(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            self._skillhub_import(skill)
            (skill / "_skillhub_meta.json").unlink()
            (skill / "_icon.png").unlink()
            self.assertTrue(RuntimeIdentityModule().preflight(skill)["verified"])

    def test_skillhub_metadata_does_not_allow_drift_or_unknown_files(self):
        with TemporaryDirectory() as temp:
            original = self._build(temp)
            self._skillhub_import(original)
            for kind in ("script", "nested", "version", "duplicate", "boolean", "oversize",
                         "icon", "directory", "namespace", "unknown-key", "missing-published",
                         "missing-version", "dual-version", "license", "reference"):
                with self.subTest(kind=kind):
                    skill = Path(temp) / kind
                    shutil.copytree(original, skill)
                    meta = skill / "_meta.json"
                    if kind == "script":
                        (skill / "_meta.py").write_text("# unexpected execution file", encoding="utf-8")
                    elif kind == "nested":
                        (skill / "scripts/_meta.json").write_bytes(meta.read_bytes())
                    elif kind == "duplicate":
                        meta.write_text('{"slug":"arbibuddy","slug":"arbibuddy"}', encoding="utf-8")
                    elif kind == "oversize":
                        meta.write_bytes(b" " * (16 * 1024 + 1))
                    elif kind == "icon":
                        (skill / "_icon.png").write_text("not a PNG", encoding="utf-8")
                    elif kind == "directory":
                        meta.unlink()
                        meta.mkdir()
                    elif kind == "missing-published":
                        meta.unlink()
                    elif kind == "missing-version":
                        (skill / "VERSION.txt").unlink()
                    elif kind == "dual-version":
                        (skill / "VERSION").write_bytes((skill / "VERSION.txt").read_bytes())
                    elif kind in {"license", "reference"}:
                        path = skill / ("LICENSE.txt" if kind == "license" else "references/amount-calculate.md")
                        path.write_bytes(path.read_bytes() + b"\nmodified")
                    elif kind == "namespace":
                        path = skill / "_skillhub_meta.json"
                        value = json.loads(path.read_text(encoding="utf-8"))
                        value["canonicalName"] = "@another/arbibuddy"
                        path.write_text(json.dumps(value), encoding="utf-8")
                    else:
                        value = json.loads(meta.read_text(encoding="utf-8"))
                        if kind == "version":
                            value["version"] = "1.2.6"
                        elif kind == "boolean":
                            value["publishedAt"] = True
                        else:
                            value["execute"] = "untrusted"
                        meta.write_text(json.dumps(value), encoding="utf-8")
                    result = RuntimeIdentityModule().preflight(skill)
                    self.assertFalse(result["verified"], result)

    def test_skillhub_limits_and_missing_entry_are_rejected_before_delivery(self):
        with TemporaryDirectory() as temp:
            skill = Path(temp)
            with self.assertRaisesRegex(ValueError, "SKILL.md"):
                validate_distribution_limits(skill)
            (skill / "SKILL.md").write_bytes(b"")
            for index in range(199):
                (skill / f"{index}.txt").write_bytes(b"")
            self.assertEqual(validate_distribution_limits(skill)["distribution_file_count"], 200)
            (skill / "extra.txt").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "上限"):
                validate_distribution_limits(skill)
            (skill / "extra.txt").unlink()
            with (skill / "SKILL.md").open("wb") as stream:
                stream.truncate(10_000_000)
            self.assertEqual(validate_distribution_limits(skill)["distribution_total_bytes"], 10_000_000)
            with (skill / "SKILL.md").open("ab") as stream:
                stream.write(b"x")
            with self.assertRaisesRegex(ValueError, "上限"):
                validate_distribution_limits(skill)

    def test_skillhub_metadata_symlink_is_rejected(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            self._skillhub_import(skill)
            meta = skill / "_meta.json"
            outside = Path(temp) / "outside.json"
            outside.write_bytes(meta.read_bytes())
            meta.unlink()
            try:
                meta.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"当前环境不能创建符号链接：{error}")
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"], result)
            self.assertIn("installed_resource_set_mismatch", result["failures"])

    def test_legacy_distribution_layout_still_verifies(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            marker = json.loads((skill / DISTRIBUTION_MARKER_NAME).read_text(encoding="utf-8"))
            (skill / "VERSION.txt").rename(skill / "VERSION")
            (skill / "LICENSE.txt").rename(skill / "LICENSE")
            identity = measure_runtime_identity(skill, source_commit=marker["runtime_identity"]["source_commit"])
            (skill / DISTRIBUTION_MARKER_NAME).write_bytes(build_distribution_marker(identity))
            self.assertTrue(RuntimeIdentityModule().preflight(skill)["verified"])

    def test_skillhub_filtered_import_creates_and_restores_archive(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            self._skillhub_import(skill)
            workspace = Path(temp) / "case"
            workspace.mkdir()
            result = self._command(skill, "-m", "scripts.case_archive.cli", "--root", str(workspace), "create")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            restored = self._command(skill, "-m", "scripts.case_archive.cli", "--root", str(workspace), "read-current")
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertTrue(json.loads(restored.stdout)["ok"])

    def _build(self, temp: str) -> Path:
        output = Path(temp) / "repository"
        result = build_skills_repository(ROOT, output)
        self.assertTrue(result["verified"], result)
        return output / "skills" / "arbibuddy"

    def _command(self, skill: Path, *args: str) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", *args], cwd=skill,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env=environment,
        )

    def test_only_runtime_resources_and_marker_are_shipped(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            actual = {p.relative_to(skill).as_posix() for p in skill.rglob("*") if p.is_file()}
            self.assertEqual(actual, set(runtime_resource_names(skill)) | {DISTRIBUTION_MARKER_NAME})
            self.assertTrue((skill / "VERSION.txt").is_file())
            self.assertTrue((skill / "LICENSE.txt").is_file())
            self.assertFalse((skill / "VERSION").exists())
            self.assertFalse((skill / "LICENSE").exists())
            self.assertIn("](LICENSE.txt)", (skill / "README.md").read_text(encoding="utf-8"))
            self.assertFalse((skill.parents[1] / "SKILL.md").exists())
            self.assertFalse((skill / ".git").exists())
            self.assertFalse((skill / "tests").exists())
            self.assertTrue(RuntimeIdentityModule().preflight(skill)["verified"])

    def test_copied_install_creates_and_restores_archive_in_fresh_process(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            installed = Path(temp) / "project/.agents/skills/arbibuddy"
            shutil.copytree(skill, installed)
            workspace = Path(temp) / "case"
            workspace.mkdir()
            created = self._command(installed, "-m", "scripts.case_archive.cli",
                                    "--root", str(workspace), "create", "--initial-goal", "虚构安装验证")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            result = json.loads(created.stdout)
            self.assertTrue(result["ok"], result)
            restored = self._command(installed, "-m", "scripts.case_archive.cli",
                                     "--root", str(workspace), "read-current")
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertTrue(json.loads(restored.stdout)["ok"])
            before = {p.relative_to(workspace): p.read_bytes()
                      for p in workspace.rglob("*") if p.is_file()}
            (installed / DISTRIBUTION_MARKER_NAME).write_text("{}", encoding="utf-8")
            stopped = self._command(installed, "-m", "scripts.case_archive.cli",
                                    "--root", str(workspace), "read-current")
            self.assertNotEqual(stopped.returncode, 0)
            self.assertFalse(json.loads(stopped.stdout)["ok"])
            after = {p.relative_to(workspace): p.read_bytes()
                     for p in workspace.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_installed_document_transport_renders_and_views_complete_bundle(self):
        from tests.documents.test_model_led_document_render import (
            _application_request, _create_archive, _evidence_request,
        )
        from zipfile import ZipFile

        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            self._skillhub_import(skill)
            workspace = Path(temp) / "case"
            workspace.mkdir()
            _, case_id, revision = _create_archive(workspace)

            def document(operation: str, *args: str) -> dict:
                completed = self._command(skill, "-m", "scripts.documents.runtime_cli",
                                          "--workspace", str(workspace), operation, *args)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                return json.loads(completed.stdout)

            delivery = document("delivery-set", "--json", json.dumps({
                "case_id": case_id, "archive_revision": revision, "mode": "candidate",
                "requested_templates": ["labor-arbitration-application"], "confirmation_refs": [],
            }))
            self.assertTrue(delivery["ok"], delivery)
            for factory in (_application_request, _evidence_request):
                request = factory(case_id, revision, mode="candidate")
                request["delivery_set_id"] = delivery["result"]["delivery_set_id"]
                rendered = document("render", "--json", json.dumps(request, ensure_ascii=False))
                self.assertTrue(rendered["ok"], rendered)
            view = document("view", "--json", json.dumps({"case_id": case_id}))
            self.assertFalse(view["stopped"], view)
            files = view["present_files_arguments"]["files"]
            self.assertEqual(len(files), 4)
            documents = list(workspace.rglob("*.docx"))
            self.assertEqual(len(documents), 2)
            for path in documents:
                with ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertIn("word/document.xml", archive.namelist())

    def test_missing_marker_does_not_accept_bare_copy(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            (skill / DISTRIBUTION_MARKER_NAME).unlink()
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_missing", result["failures"])

    def test_modified_noncritical_resource_and_extra_script_are_rejected(self):
        for relative in ("references/amount-formulas.v1.json", "scripts/unregistered.py"):
            with self.subTest(relative=relative), TemporaryDirectory() as temp:
                skill = self._build(temp)
                path = skill / relative
                path.write_bytes(path.read_bytes() + b"\n" if path.exists() else b"# fixture\n")
                result = RuntimeIdentityModule().preflight(skill)
                self.assertFalse(result["verified"], result)
                self.assertIn("distribution_marker_mismatch", result["failures"])

    def test_invalid_markers_are_rejected_without_source_fallback(self):
        for kind in ("json", "duplicate", "digest", "absolute", "commit", "boolean"):
            with self.subTest(kind=kind), TemporaryDirectory() as temp:
                skill = self._build(temp)
                marker = skill / DISTRIBUTION_MARKER_NAME
                value = json.loads(marker.read_text(encoding="utf-8"))
                if kind == "json":
                    marker.write_text("{", encoding="utf-8")
                elif kind == "duplicate":
                    marker.write_text('{"skill":"arbibuddy","skill":"arbibuddy"}', encoding="utf-8")
                else:
                    if kind == "digest":
                        value["runtime_identity_sha256"] = "0" * 64
                    elif kind == "absolute":
                        value["runtime_identity"]["source_commit"] = "C:/private/source"
                    elif kind == "commit":
                        value["runtime_identity"]["source_commit"] = "unavailable"
                        value["runtime_identity_sha256"] = runtime_identity_digest(value["runtime_identity"])
                    else:
                        value["schema_version"] = True
                    marker.write_text(json.dumps(value), encoding="utf-8")
                result = RuntimeIdentityModule().preflight(skill)
                self.assertFalse(result["verified"])
                self.assertIn("distribution_marker_invalid", result["failures"])

    def test_invalid_install_receipt_has_priority_over_valid_distribution(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            self._skillhub_import(skill)
            (skill / "arbibuddy.install.json").write_text("{}", encoding="utf-8")
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_invalid", result["failures"])
            self.assertNotEqual(result.get("mode"), "distribution")

    def test_stale_sibling_receipt_reports_channel_conflict_and_explicit_repair_restores_archive(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            installed = Path(temp) / "project/.agents/skills/arbibuddy"
            shutil.copytree(skill, installed)
            marker = json.loads((installed / DISTRIBUTION_MARKER_NAME).read_text(encoding="utf-8"))
            old_identity = dict(marker["runtime_identity"], skill_version="v1.2.6-rc8")
            receipt = installed.parent / "arbibuddy.install.json"
            receipt.write_text(json.dumps({
                "schema_version": 2, "platform": "codex", "mode": "copy",
                "runtime_identity": old_identity,
            }), encoding="utf-8")
            workspace = Path(temp) / "case"
            workspace.mkdir()
            result = RuntimeIdentityModule().preflight(installed)
            self.assertFalse(result["verified"], result)
            self.assertIn("installation_channel_conflict", result["failures"])
            self.assertIn("旧受管安装回执", runtime_identity_failure_message(result))
            self.assertEqual(runtime_identity_failure_next_action(result),
                             "resolve-installation-channel-conflict-and-restart")
            stopped = self._command(installed, "-m", "scripts.case_archive.cli",
                                    "--root", str(workspace), "create")
            self.assertNotEqual(stopped.returncode, 0)
            envelope = json.loads(stopped.stdout)
            self.assertEqual(envelope["errors"][0]["code"], "installed_skill_modified")
            self.assertIn("安装通道冲突", envelope["errors"][0]["message"])
            self.assertFalse((workspace / ".arbibuddy/cases").exists())

            # Maintenance explicitly preserves the obsolete receipt after independently
            # verifying the distribution; runtime preflight must never do this itself.
            original = receipt.read_bytes()
            backup = receipt.with_name("arbibuddy.install.json.pre-skills.bak")
            receipt.rename(backup)
            self.assertEqual(backup.read_bytes(), original)
            self.assertTrue(RuntimeIdentityModule().preflight(installed)["verified"])
            created = self._command(installed, "-m", "scripts.case_archive.cli",
                                    "--root", str(workspace), "create")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            self.assertTrue(json.loads(created.stdout)["ok"])
            restored = self._command(installed, "-m", "scripts.case_archive.cli",
                                     "--root", str(workspace), "read-current")
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertTrue(json.loads(restored.stdout)["ok"])

    def test_stale_sibling_receipt_with_tampered_distribution_is_not_a_channel_conflict(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            (skill.parent / "arbibuddy.install.json").write_text("{}", encoding="utf-8")
            (skill / "SKILL.md").write_text("tampered", encoding="utf-8")
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"], result)
            self.assertNotIn("installation_channel_conflict", result["failures"])

    def test_distribution_failure_recovery_does_not_request_a_signed_workbuddy_package(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            (skill / DISTRIBUTION_MARKER_NAME).write_text("{}", encoding="utf-8")
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"])
            self.assertIn("skills CLI", runtime_identity_failure_message(result))
            self.assertNotIn("验签包", runtime_identity_failure_message(result))

    def test_extra_git_checkout_cannot_bypass_distribution_file_set(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            for command in (
                ["git", "init", "--quiet"], ["git", "add", "VERSION.txt"],
                ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--quiet", "-m", "Fixture"],
            ):
                subprocess.run(command, cwd=skill, check=True, capture_output=True, timeout=15)
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"])
            self.assertIn("installed_resource_set_mismatch", result["failures"])

    def test_builder_refuses_existing_or_source_internal_destination(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "existing"
            output.mkdir()
            sentinel = output / "user.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_skills_repository(ROOT, output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        with self.assertRaises(ValueError):
            build_skills_repository(ROOT, ROOT / "skills-output")

    def test_builder_does_not_publish_an_oversized_candidate(self):
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            output = Path(temp) / "repository"
            with patch("scripts.release.skills_distribution.SKILLHUB_MAX_FILES", 1):
                with self.assertRaisesRegex(ValueError, "上限"):
                    build_skills_repository(ROOT, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
