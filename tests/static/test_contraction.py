from __future__ import annotations

import ast
import json
import re
from pathlib import Path
import tempfile
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.view import managed_delivery_view
from scripts.platform_adapters.service import SUPPORTED_PLATFORMS
from scripts.runtime_identity import RuntimeIdentityModule
from scripts.runtime_resources import RUNTIME_FILES, RuntimeResourceError, runtime_paths

ROOT = Path(__file__).resolve().parents[2]


class ContractionTests(unittest.TestCase):
    def test_packaged_markdown_links_remain_available_in_the_runtime(self):
        for relative in RUNTIME_FILES:
            if not relative.endswith(".md"):
                continue
            content = (ROOT / relative).read_text(encoding="utf-8")
            for link in re.findall(r"\]\(([^)]+)\)", content):
                if "://" in link or link.startswith("#"):
                    continue
                target = (ROOT / relative).parent / link.split("#")[0]
                with self.subTest(source=relative, link=link):
                    self.assertTrue(target.resolve().is_relative_to(ROOT))
                    self.assertIn(target.resolve().relative_to(ROOT).as_posix(), RUNTIME_FILES)
                    self.assertTrue(target.is_file())

    def test_runtime_whitelist_is_complete_and_excludes_development(self):
        paths = runtime_paths(ROOT)
        self.assertEqual(len(paths), len(set(RUNTIME_FILES)))
        self.assertEqual({p.relative_to(ROOT).as_posix() for p in paths}, set(RUNTIME_FILES))
        self.assertEqual(set(SUPPORTED_PLATFORMS), {"codex", "claude-code", "workbuddy"})
        self.assertTrue(all(not p.startswith(("tests/", "evals/", "docs/", ".scratch/")) for p in RUNTIME_FILES))
        self.assertTrue(all("model_led_agent_eval" not in p and "test_suites" not in p and "workbuddy_acceptance" not in p for p in RUNTIME_FILES))

    def test_all_runtime_imports_including_dynamic_imports_are_closed(self):
        modules = {p[:-3].replace("/", ".") for p in RUNTIME_FILES if p.endswith(".py")}
        modules |= {m.removesuffix(".__init__") for m in modules if m.endswith(".__init__")}
        for relative in RUNTIME_FILES:
            if not relative.endswith(".py"):
                continue
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            package = relative[:-3].replace("/", ".").rsplit(".", 1)[0]
            dependencies = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    dependencies.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        prefix = package.split(".")[:len(package.split(".")) - node.level + 1]
                        dependencies.append(".".join(prefix + ([node.module] if node.module else [])))
                        if node.module is None:
                            dependencies.extend(".".join(prefix + [alias.name]) for alias in node.names)
                    elif node.module:
                        dependencies.append(node.module)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "import_module":
                    self.assertTrue(node.args and isinstance(node.args[0], ast.Constant), relative)
                    dependencies.append(node.args[0].value)
            for dependency in dependencies:
                if dependency.startswith("scripts"):
                    self.assertIn(dependency, modules, (relative, dependency))

    def test_recorded_deleted_paths_are_absent_without_replacement_wrappers(self):
        record = json.loads((ROOT / "docs/agents/ticket12-contraction-inventory.json").read_text(encoding="utf-8"))
        for entry in record["entries"]:
            with self.subTest(path=entry["path"]):
                self.assertFalse((ROOT / entry["path"]).exists())
                self.assertTrue(entry["reason"] and entry["replacement"])

    def test_missing_required_file_cannot_be_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshot"
            RuntimeIdentityModule().install_snapshot(ROOT, snapshot, mode="copy")
            (snapshot / "references/model-led/wage-analysis.md").unlink()
            with self.assertRaises(RuntimeResourceError):
                runtime_paths(snapshot)

    def test_supported_existing_archive_read_and_view_do_not_rewrite_case(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create({"initial_goal": "继续已保存的案情"})
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            before = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
            self.assertTrue(archive.read(case_id)["ok"])
            self.assertTrue(managed_delivery_view(workspace=workspace, case_id=case_id)["stopped"])
            after = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_unsupported_existing_archive_is_preserved_without_conversion(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            old = workspace / ".arbibuddy/cases/historical-case/案情档案.md"
            old.parent.mkdir(parents=True)
            old.write_bytes("历史用户档案：仅保护原件，不自动转换。".encode("utf-8"))
            before = old.read_bytes()
            self.assertFalse(CaseArchive(workspace).read("historical-case")["ok"])
            self.assertTrue(managed_delivery_view(workspace=workspace, case_id="historical-case")["stopped"])
            self.assertEqual(old.read_bytes(), before)
            self.assertEqual([p for p in workspace.rglob("*") if p.is_file()], [old])


if __name__ == "__main__":
    unittest.main()
