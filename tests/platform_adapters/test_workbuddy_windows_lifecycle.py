from __future__ import annotations

import json
from hashlib import sha256
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import scripts.platform_adapters.service as platform_service
from scripts.runtime_identity import workbuddy_trust_anchor_path
from scripts.platform_paths import public_path
from scripts.platform_adapters.service import (
    PlatformAdapterError,
    identify_installation,
    install_skill,
    reap_workbuddy_cleanup,
    resolve_workbuddy_wheelhouse,
    uninstall_skill,
    update_skill,
    verify_install,
    verify_platform_discovery,
)
from tests.platform_adapters.test_cli import run_cli


ROOT = Path(__file__).resolve().parents[2]


class WorkBuddyWindowsLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        wheelhouse = resolve_workbuddy_wheelhouse(ROOT)
        if not wheelhouse.is_dir():
            self.skipTest(
                "缺少锁定 WorkBuddy wheelhouse；真实生命周期需在受管构建输入环境运行"
            )

    def test_workbuddy_cli_exposes_install_identify_discovery_and_uninstall(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            code, output, error = run_cli(
                [
                    "install",
                    "--platform",
                    "workbuddy",
                    "--scope",
                    "project",
                    "--target-root",
                    str(target),
                    "--source",
                    str(ROOT),
                    "--mode",
                    "copy",
                ]
            )
            self.assertEqual(code, 0, error)
            installed = json.loads(output)
            skill_root = Path(installed["skill_root"])

            code, output, error = run_cli(
                [
                    "identify",
                    "--platform",
                    "workbuddy",
                    "--skill-root",
                    str(skill_root),
                ]
            )
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["verified"])

            receipt = json.loads(
                Path(installed["receipt"]).read_text(encoding="utf-8")
            )
            evidence_path = root / "lifecycle.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "workbuddy",
                        "skill": "arbibuddy",
                        "skill_root": str(skill_root),
                        "runtime_identity_sha256": receipt[
                            "runtime_identity_sha256"
                        ],
                        "observations": {
                            "installed": True,
                            "reload_observed": True,
                            "registered": True,
                            "exposed_to_model": True,
                            "invoked": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            code, output, error = run_cli(
                [
                    "verify-platform",
                    "--platform",
                    "workbuddy",
                    "--skill-root",
                    str(skill_root),
                    "--lifecycle-evidence",
                    str(evidence_path),
                ]
            )
            self.assertEqual(code, 0, error)
            discovery = json.loads(output)
            self.assertTrue(discovery["runtime_discovered"])
            self.assertEqual(discovery["level"], "runtime-lifecycle-evidence")

            code, output, error = run_cli(
                [
                    "uninstall",
                    "--platform",
                    "workbuddy",
                    "--scope",
                    "project",
                    "--target-root",
                    str(target),
                ]
            )
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["uninstalled"])

            code, output, error = run_cli(
                [
                    "cleanup",
                    "--platform",
                    "workbuddy",
                    "--scope",
                    "project",
                    "--target-root",
                    str(target),
                ]
            )
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["already_clean"])

    def test_workbuddy_install_identify_update_and_uninstall_use_one_utf8_identity(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / ("中文 空格" * 20) / "WorkBuddy 用户工作区"

            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            self.assertTrue(installed["verified"])
            skill_root = Path(installed["skill_root"])
            self.assertNotIn("\\\\?\\", str(skill_root))

            with self.assertRaisesRegex(
                PlatformAdapterError, "workbuddy_uninstall_scope_violation"
            ):
                uninstall_skill(
                    platform="workbuddy",
                    scope="project",
                    target_root=target,
                    skill_root=root / "outside" / "arbibuddy",
                )

            receipt_path = Path(installed["receipt"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["platform"], "workbuddy")
            self.assertEqual(receipt["skill_root"], str(skill_root))
            self.assertNotIn("\\\\?\\", receipt_path.read_text(encoding="utf-8"))

            verification = verify_install(
                platform="workbuddy", skill_root=skill_root
            )
            self.assertTrue(verification["discovery_contract_valid"])
            self.assertTrue(verification["runtime_identity_verified"])

            identified = identify_installation(
                platform="workbuddy", skill_root=skill_root
            )
            self.assertEqual(identified["skill_version"], verification["skill_version"])
            self.assertEqual(
                identified["runtime_identity_sha256"],
                receipt["runtime_identity_sha256"],
            )

            stale = skill_root / "obsolete-resource.txt"
            stale.write_text("必须在覆盖更新中消失", encoding="utf-8")
            self.assertFalse(verify_install(
                platform="workbuddy", skill_root=skill_root
            )["runtime_identity_verified"])
            updated = update_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            self.assertTrue(updated["updated"])
            self.assertTrue(updated["verified"])
            self.assertFalse(stale.exists())
            repeated = update_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            self.assertTrue(repeated["updated"])
            self.assertTrue(repeated["verified"])

            removed = uninstall_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
            )
            self.assertTrue(removed["uninstalled"])
            self.assertFalse(skill_root.exists())

            replay = uninstall_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
            )
            self.assertTrue(replay["already_absent"])

    def test_post_commit_cleanup_failure_is_a_warning_and_retry_is_idempotent(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "中文 WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])

            with patch(
                "scripts.platform_adapters.service._cleanup_workbuddy_transaction",
                side_effect=PermissionError("模拟提交后暂存清理权限失败"),
            ):
                report = update_skill(
                    platform="workbuddy",
                    scope="project",
                    target_root=target,
                    source=ROOT,
                    mode="copy",
                )

            self.assertTrue(report["updated"])
            self.assertTrue(report["verified"])
            self.assertEqual(report["cleanup"]["state"], "warning")
            self.assertIn("post_commit_cleanup", report["cleanup"]["code"])
            self.assertTrue(verify_install(
                platform="workbuddy", skill_root=skill_root
            )["runtime_identity_verified"])

            recovered = reap_workbuddy_cleanup(
                platform="workbuddy", target_root=target, scope="project"
            )
            self.assertEqual(recovered["warnings"], [])
            self.assertTrue(recovered["cleaned"])

            replay = reap_workbuddy_cleanup(
                platform="workbuddy", target_root=target, scope="project"
            )
            self.assertTrue(replay["already_clean"])

    def test_update_extra_files_cannot_bypass_core_receipt_anchor_or_vendor_failures(self):
        with TemporaryDirectory() as temp:
            root = public_path(Path(temp).resolve())
            target = root / "WorkBuddy workspace"
            installed = install_skill(platform="workbuddy", scope="project",
                                      target_root=target, source=ROOT, mode="copy")
            skill_root = Path(installed["skill_root"])
            extra = skill_root / "obsolete-resource.txt"
            extra.write_text("待维护换树清除", encoding="utf-8")
            anchor = public_path(workbuddy_trust_anchor_path(target, skill_root))
            for path in (
                skill_root / "VERSION", Path(installed["receipt"]), anchor,
                skill_root / "workbuddy-runtime/site-packages/docx/__init__.py",
                skill_root / "workbuddy-runtime/site-packages/unregistered.py",
            ):
                with self.subTest(path=path.relative_to(root).as_posix()):
                    original = path.read_bytes() if path.exists() else None
                    original_mode = path.stat().st_mode if path.exists() else None
                    if original_mode is not None:
                        path.chmod(stat.S_IREAD | stat.S_IWRITE)
                    path.write_bytes(b"invalid or unregistered")
                    before = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
                              for p in root.rglob("*") if p.is_file()}
                    try:
                        with self.assertRaisesRegex(PlatformAdapterError, "workbuddy_update_receipt_invalid"):
                            update_skill(platform="workbuddy", scope="project",
                                         target_root=target, source=ROOT, mode="copy")
                        after = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
                                 for p in root.rglob("*") if p.is_file()}
                        self.assertEqual(before, after)
                    finally:
                        if original is None:
                            path.unlink()
                        else:
                            path.write_bytes(original)
                            path.chmod(original_mode)

    def test_update_extra_file_exception_still_rejects_file_and_directory_links(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            installed = install_skill(platform="workbuddy", scope="project",
                                      target_root=target, source=ROOT, mode="copy")
            skill_root = Path(installed["skill_root"])
            sentinel = root / "outside.txt"
            sentinel.write_bytes(b"untouched outside installation")
            extra = skill_root / "obsolete-resource.txt"
            extra.write_bytes(b"extra file")
            anchor = public_path(workbuddy_trust_anchor_path(target, skill_root))
            anchor_before = anchor.read_bytes()
            for directory in (False, True):
                with self.subTest(directory=directory):
                    link = skill_root / "unregistered-link"
                    try:
                        link.symlink_to(root if directory else sentinel, target_is_directory=directory)
                    except OSError as error:
                        self.skipTest(f"当前环境不能创建测试链接：{error}")
                    try:
                        with self.assertRaisesRegex(PlatformAdapterError, "workbuddy_update_receipt_invalid"):
                            update_skill(platform="workbuddy", scope="project",
                                         target_root=target, source=ROOT, mode="copy")
                        self.assertTrue(link.is_symlink())
                        self.assertEqual(sentinel.read_bytes(), b"untouched outside installation")
                        self.assertEqual(extra.read_bytes(), b"extra file")
                        self.assertEqual(anchor.read_bytes(), anchor_before)
                    finally:
                        link.unlink()

    def test_permission_and_file_in_use_failures_are_public_and_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            with patch(
                "scripts.platform_adapters.service._write_package_payload",
                side_effect=PermissionError("模拟无权限"),
            ):
                with self.assertRaisesRegex(PlatformAdapterError, "permission_denied"):
                    install_skill(
                        platform="workbuddy",
                        scope="project",
                        target_root=target,
                        source=ROOT,
                        mode="copy",
                    )

            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            with patch(
                "scripts.platform_adapters.service._replace_workbuddy_path",
                side_effect=PermissionError("模拟文件占用"),
            ):
                with self.assertRaisesRegex(PlatformAdapterError, "file_in_use"):
                    update_skill(
                        platform="workbuddy",
                        scope="project",
                        target_root=target,
                        source=ROOT,
                        mode="copy",
                    )

            self.assertTrue(
                verify_install(
                    platform="workbuddy",
                    skill_root=Path(installed["skill_root"]),
                )["runtime_identity_verified"]
            )

    def test_update_failure_during_tree_commit_restores_the_previous_install(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            extra = skill_root / "obsolete-resource.txt"
            extra.write_bytes(b"preserve on rollback")
            before = {p.relative_to(skill_root).as_posix(): sha256(p.read_bytes()).hexdigest()
                      for p in skill_root.rglob("*") if p.is_file()}
            anchor = public_path(workbuddy_trust_anchor_path(target, skill_root))
            anchor_before = anchor.read_bytes()
            original_replace = platform_service._replace_workbuddy_path
            calls = 0

            def fail_new_tree_commit(source: Path, destination: Path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError("模拟文件占用")
                return original_replace(source, destination)

            with patch(
                "scripts.platform_adapters.service._replace_workbuddy_path",
                side_effect=fail_new_tree_commit,
            ):
                with self.assertRaisesRegex(PlatformAdapterError, "file_in_use"):
                    update_skill(
                        platform="workbuddy",
                        scope="project",
                        target_root=target,
                        source=ROOT,
                        mode="copy",
                    )

            restored = verify_install(
                platform="workbuddy",
                skill_root=Path(installed["skill_root"]),
            )
            self.assertTrue(restored["discovery_contract_valid"])
            self.assertFalse(restored["runtime_identity_verified"])
            after = {p.relative_to(skill_root).as_posix(): sha256(p.read_bytes()).hexdigest()
                     for p in skill_root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(anchor.read_bytes(), anchor_before)
            extra.unlink()
            self.assertTrue(verify_install(platform="workbuddy", skill_root=skill_root)["runtime_identity_verified"])

    def test_cleanup_recovers_an_interrupted_update_before_reaping_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            transaction = platform_service._workbuddy_transaction_root(
                target, "update"
            )
            rollback_skill = transaction / "previous"
            transaction.mkdir(parents=True)
            platform_service._replace_workbuddy_path(skill_root, rollback_skill)

            recovered = reap_workbuddy_cleanup(
                platform="workbuddy", target_root=target, scope="project"
            )
            self.assertTrue(recovered["cleaned"])
            self.assertEqual(recovered["warnings"], [])
            self.assertTrue(
                verify_install(
                    platform="workbuddy", skill_root=skill_root
                )["runtime_identity_verified"]
            )

    def test_discovery_requires_identity_bound_lifecycle_evidence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            receipt = json.loads(
                Path(installed["receipt"]).read_text(encoding="utf-8")
            )
            missing = verify_platform_discovery(
                platform="workbuddy",
                skill_root=skill_root,
                platform_command=[],
            )
            self.assertFalse(missing["verified"])
            self.assertIn("lifecycle_evidence_missing", missing["reason"])
            evidence_path = root / "生命周期 evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "workbuddy",
                        "skill": "arbibuddy",
                        "skill_root": str(skill_root),
                        "runtime_identity_sha256": receipt[
                            "runtime_identity_sha256"
                        ],
                        "observations": {
                            "installed": True,
                            "reload_observed": True,
                            "registered": True,
                            "exposed_to_model": True,
                            "invoked": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            verified = verify_platform_discovery(
                platform="workbuddy",
                skill_root=skill_root,
                platform_command=[],
                lifecycle_evidence=evidence_path,
            )
            self.assertTrue(verified["verified"])
            self.assertTrue(verified["runtime_discovered"])
            self.assertEqual(verified["level"], "runtime-lifecycle-evidence")

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["observations"]["invoked"] = False
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
            )
            rejected = verify_platform_discovery(
                platform="workbuddy",
                skill_root=skill_root,
                platform_command=[],
                lifecycle_evidence=evidence_path,
            )
            self.assertFalse(rejected["verified"])
            self.assertIn("invoked", rejected["missing_observations"])


if __name__ == "__main__":
    unittest.main()
