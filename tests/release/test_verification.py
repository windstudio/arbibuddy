from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from scripts.release.verification import REQUIRED_CHECKS, release_root, validate_release_destination, verify_evidence
from scripts.runtime_identity import RuntimeIdentityModule

ROOT = Path(__file__).resolve().parents[2]


class ReleaseEvidenceTests(unittest.TestCase):
    def _release_tools(self, project):
        """载入迁移后的真实发布模块，不依赖当前仓库路径或调用目录。"""
        module_path = project / "scripts/release/verification.py"
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_bytes((ROOT / "scripts/release/verification.py").read_bytes())
        spec = importlib.util.spec_from_file_location("relocated_release_verification", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

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

    def test_relocated_tools_anchor_relative_destination_and_candidate_version(self):
        with TemporaryDirectory() as temp:
            project = Path(temp) / "moved project"
            tools = self._release_tools(project)
            candidate = Path(temp) / "public-source"
            candidate.mkdir()
            (candidate / "VERSION").write_text("2.3.4\n", encoding="utf-8")
            expected = project / "dist/2.3.4"
            self.assertEqual(tools.release_root(candidate), expected.resolve())
            self.assertEqual(tools.validate_release_destination(candidate, Path("dist/2.3.4")), expected.resolve())
            for wrong in (candidate / "dist/2.3.4", project / "dist/2.3.5", Path("../dist/2.3.4")):
                with self.subTest(path=wrong), self.assertRaises(ValueError):
                    tools.validate_release_destination(candidate, wrong)
            self.assertFalse(expected.exists())

    def test_linked_worktree_uses_main_checkout_after_repository_move(self):
        with TemporaryDirectory() as temp:
            project = Path(temp) / "main project"
            self._release_tools(project)
            (project / "VERSION").write_text("2.3.4\n", encoding="utf-8")
            for args in (("init", "-q"), ("add", "."),
                         ("-c", "user.name=ArbiBuddy Tests", "-c", "user.email=tests@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic release fixture")):
                subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
            # 移动主仓库后新建关联 worktree，模拟另一台电脑上的新项目位置。
            moved = Path(temp) / "new location"
            project.rename(moved)
            worktree = Path(temp) / "linked worktree"
            subprocess.run(["git", "worktree", "add", "--detach", str(worktree)],
                           cwd=moved, check=True, capture_output=True)
            tools = self._release_tools(worktree)
            expected = moved / "dist/2.3.4"
            self.assertEqual(tools.release_root(worktree), expected.resolve())
            self.assertEqual(tools.validate_release_destination(worktree, Path("dist/2.3.4")), expected.resolve())
            with self.assertRaises(ValueError):
                tools.validate_release_destination(worktree, worktree / "dist/2.3.4")
            self.assertFalse(expected.exists())

    def test_broken_worktree_metadata_does_not_become_a_formal_root(self):
        with TemporaryDirectory() as temp:
            project = Path(temp) / "broken worktree"
            tools = self._release_tools(project)
            (project / "VERSION").write_text("2.3.4\n", encoding="utf-8")
            for content in ("invalid metadata\n", "gitdir: missing/worktrees/test\n"):
                (project / ".git").write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaisesRegex(ValueError, "Git 元数据"):
                    tools.release_root(project)


if __name__ == "__main__":
    unittest.main()
