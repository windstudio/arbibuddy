from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.release.skills_distribution import build_skills_repository
from scripts.runtime_identity import (
    DISTRIBUTION_MARKER_NAME, RuntimeIdentityModule, runtime_identity_digest,
)
from scripts.runtime_resources import RUNTIME_FILES


ROOT = Path(__file__).resolve().parents[2]


class SkillsDistributionTests(unittest.TestCase):
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
            self.assertEqual(actual, set(RUNTIME_FILES) | {DISTRIBUTION_MARKER_NAME})
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
            (skill / "arbibuddy.install.json").write_text("{}", encoding="utf-8")
            result = RuntimeIdentityModule().preflight(skill)
            self.assertFalse(result["verified"])
            self.assertIn("runtime_identity_marker_invalid", result["failures"])
            self.assertNotEqual(result.get("mode"), "distribution")

    def test_extra_git_checkout_cannot_bypass_distribution_file_set(self):
        with TemporaryDirectory() as temp:
            skill = self._build(temp)
            for command in (
                ["git", "init", "--quiet"], ["git", "add", "VERSION"],
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


if __name__ == "__main__":
    unittest.main()
