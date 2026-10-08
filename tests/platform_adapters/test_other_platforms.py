import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts.platform_adapters.service import (
    PlatformAdapterError,
    resolve_workbuddy_wheelhouse,
)
from scripts.version import display_version
from scripts.runtime_resources import RUNTIME_FILES
from tests.platform_adapters.test_cli import ROOT, run_cli


class OtherPlatformAdapterTests(unittest.TestCase):
    VERSION = display_version(ROOT)

    def _skip_if_no_workbuddy_wheelhouse(self):
        wheelhouse = resolve_workbuddy_wheelhouse(ROOT)
        if not wheelhouse.is_dir():
            self.skipTest("当前环境没有受管 WorkBuddy wheelhouse；按契约构建应安全失败")

    def test_workbuddy_runtime_layer_is_built_and_verified_with_target_python(self):
        self._skip_if_no_workbuddy_wheelhouse()
        target_python = (
            Path.home()
            / ".workbuddy"
            / "binaries"
            / "python"
            / "envs"
            / "arbibuddy"
            / "Scripts"
            / "python.exe"
        )
        if not target_python.is_file():
            self.skipTest("当前主机没有可只读探测的 WorkBuddy bundled Python")

        with TemporaryDirectory() as temp:
            package = Path(temp) / "arbibuddy-workbuddy-runtime.zip"
            code, output, error = run_cli(
                [
                    "build-artifact",
                    "--platform",
                    "workbuddy",
                    "--source",
                    str(ROOT),
                    "--output",
                    str(package),
                    "--runtime-python",
                    str(target_python),
                ]
            )
            self.assertEqual(code, 0, error or output)
            built = json.loads(output)
            self.assertTrue(built["renderer_dependencies_verified"], built)
            self.assertEqual(
                built["renderer_dependencies"]["python_abi"],
                "cp313",
            )
            self.assertTrue(built["renderer_dependencies"]["vendor_files"])

            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("arbibuddy.package.json"))
                runtime_manifest = json.loads(
                    archive.read("arbibuddy/workbuddy-runtime/runtime-manifest.json")
                )
            self.assertIn(
                "arbibuddy/workbuddy-runtime/site-packages/docx/__init__.py",
                names,
            )
            self.assertIn(
                "arbibuddy/workbuddy-runtime/site-packages/lxml/__init__.py",
                names,
                )
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.casefold().endswith((".pyc", ".pyo")) for name in names))
            self.assertEqual(runtime_manifest["python_abi"], "cp313")
            self.assertTrue(runtime_manifest["packages"])
            self.assertTrue(runtime_manifest["licenses"])
            self.assertIn("workbuddy-runtime/runtime-manifest.json", manifest["files"])

            code, output, error = run_cli(
                [
                    "verify-artifact",
                    "--platform",
                    "workbuddy",
                    "--artifact",
                    str(package),
                    "--runtime-python",
                    str(target_python),
                ]
            )
            self.assertEqual(code, 0, error or output)
            verified = json.loads(output)
            self.assertTrue(verified["renderer_dependencies_verified"], verified)
            self.assertTrue(verified["renderer_dependencies"]["isolated_smoke"]["verified"])

    def test_workbuddy_discovery_contract_requires_observable_lifecycle_stages(self):
        adapter = json.loads(
            (ROOT / "adapters" / "workbuddy" / "adapter.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            adapter["discovery_probe"]["kind"], "workbuddy-lifecycle-evidence"
        )
        self.assertEqual(adapter["discovery_probe"]["evidence_format"], "utf-8-json")
        self.assertEqual(
            adapter["discovery_probe"]["required_observations"],
            [
                "installed",
                "reload_observed",
                "registered",
                "exposed_to_model",
                "invoked",
            ],
        )
        self.assertTrue(adapter["discovery_probe"]["hot_reload_is_not_registration"])
        self.assertTrue(adapter["discovery_probe"]["fail_closed_when_missing"])

    def test_workbuddy_builds_and_verifies_an_auditable_local_skill_package(self):
        self._skip_if_no_workbuddy_wheelhouse()
        with TemporaryDirectory() as temp:
            package = Path(temp) / "arbibuddy-workbuddy.zip"
            code, output, error = run_cli(
                [
                    "build-artifact",
                    "--platform",
                    "workbuddy",
                    "--source",
                    str(ROOT),
                    "--output",
                    str(package),
                ]
            )
            self.assertEqual(code, 0, error or output)
            built = json.loads(output)
            self.assertEqual(built["format"], "deterministic-zip")
            self.assertEqual(built["skill_version"], self.VERSION)
            self.assertTrue(built["artifact_contract_valid"])
            self.assertFalse(built["client_import_observed"])

            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                packaged_skill = archive.read("arbibuddy/SKILL.md").decode("utf-8")
                portable_marker = json.loads(archive.read("arbibuddy/arbibuddy.runtime.json"))
                packaged_document_contract = archive.read(
                    "arbibuddy/references/document-render.md"
                ).decode("utf-8")
                manifest = json.loads(archive.read("arbibuddy.package.json"))
            self.assertIn("arbibuddy/SKILL.md", names)
            self.assertIn("arbibuddy/VERSION", names)
            self.assertIn("arbibuddy/references/document-render.md", names)
            self.assertIn("arbibuddy/scripts/case_archive/cli.py", names)
            self.assertIn("arbibuddy/scripts/amount_calculator/runtime_cli.py", names)
            self.assertIn("arbibuddy/scripts/documents/runtime_cli.py", names)
            core_names = {
                name.removeprefix("arbibuddy/") for name in names
                if name.startswith("arbibuddy/")
                and not name.startswith("arbibuddy/workbuddy-runtime/")
            }
            self.assertEqual(core_names, set(RUNTIME_FILES) | {"arbibuddy.runtime.json"})
            self.assertEqual(portable_marker["platform"], "workbuddy")
            self.assertEqual(portable_marker["runtime_identity"], built["runtime_identity"])
            for removed in (
                "references/workflows/main.md",
                "scripts/case_file/cli.py",
                "scripts/cli_orchestration/cli.py",
                "scripts/intake_orchestration/cli.py",
            ):
                with self.subTest(removed=removed):
                    self.assertNotIn("arbibuddy/" + removed, names)
            self.assertIn("arbibuddy.package.json", names)
            self.assertEqual(manifest["skill_version"], self.VERSION)
            for required in (
                "## 主路径",
                "## 场景路由",
                "案件来源门禁",
                "唯一案情档案",
                "amount.calculate-v1",
                "references/document-render.md",
                "references/model-led-case-archive.md",
                "references/platform-capabilities.md",
            ):
                with self.subTest(required=required):
                    self.assertIn(required, packaged_skill)
            self.assertIn("document.render-v1", packaged_document_contract)
            self.assertNotIn("WorkBuddy memory", packaged_skill)
            self.assertNotIn("run-package", packaged_skill)
            self.assertNotIn("confirm-snapshot", packaged_skill)

            code, output, error = run_cli(
                [
                    "verify-artifact",
                    "--platform",
                    "workbuddy",
                    "--artifact",
                    str(package),
                ]
            )
            self.assertEqual(code, 0, error or output)
            verified = json.loads(output)
            self.assertEqual(verified["skill_version"], self.VERSION)
            self.assertTrue(verified["integrity_verified"])
            self.assertTrue(verified["resources_complete"])
            self.assertEqual(verified["skill_name"], "arbibuddy")
            self.assertFalse(verified["runtime_discovered"])

            broken_package = Path(temp) / "arbibuddy-workbuddy-missing-docx-dependency.zip"
            with zipfile.ZipFile(package) as archive:
                entries = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if name
                    not in {
                        "arbibuddy/workbuddy-runtime/runtime-manifest.json",
                    }
                }
            manifest = json.loads(entries["arbibuddy.package.json"])
            manifest["files"].pop("workbuddy-runtime/runtime-manifest.json", None)
            entries["arbibuddy.package.json"] = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            with zipfile.ZipFile(broken_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)

            code, output, error = run_cli(
                [
                    "verify-artifact",
                    "--platform",
                    "workbuddy",
                    "--artifact",
                    str(broken_package),
                ]
            )
            self.assertEqual(code, 2, error or output)
            broken = json.loads(output)
            self.assertFalse(broken["renderer_dependencies_verified"])
            self.assertIn(
                "runtime-manifest.json",
                broken["renderer_dependencies"]["missing_files"],
            )

            missing_core_package = Path(temp) / "arbibuddy-workbuddy-missing-required-file.zip"
            with zipfile.ZipFile(package) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()
                           if name != "arbibuddy/requirements-documents.txt"}
            manifest = json.loads(entries["arbibuddy.package.json"])
            manifest["files"].pop("requirements-documents.txt")
            entries["arbibuddy.package.json"] = json.dumps(manifest).encode("utf-8")
            with zipfile.ZipFile(missing_core_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            code, output, error = run_cli([
                "verify-artifact", "--platform", "workbuddy", "--artifact", str(missing_core_package)
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertIn("RuntimeResourceError", error)

            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("arbibuddy/unregistered.txt", "not in manifest")
            code, _, error = run_cli(
                [
                    "verify-artifact",
                    "--platform",
                    "workbuddy",
                    "--artifact",
                    str(package),
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("未登记", error)











if __name__ == "__main__":
    unittest.main()
