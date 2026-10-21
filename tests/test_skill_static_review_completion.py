"""Completion regressions for concurrency, capacity, authority, and recovery."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import redirect_stdout
from io import StringIO
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.case_archive import CaseArchive
from scripts.amount_calculator.runtime_cli import main as amount_runtime_main
from scripts.documents.public import render
from scripts.documents.view import managed_delivery_view
from scripts.platform_adapters.service import (
    PlatformAdapterError,
    install_skill,
    update_skill,
    verify_install,
)
import scripts.platform_adapters.service as platform_service
from scripts.runtime_identity import workbuddy_trust_anchor_path
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as create_forced_case,
    forced_request,
)


ROOT = Path(__file__).resolve().parents[1]


_ARCHIVE_LOCK_WORKER = r"""
import json, sys, time
from pathlib import Path
from unittest.mock import patch
from scripts.case_archive import CaseArchive
from scripts.case_archive import service

workspace = Path(sys.argv[1])
case_id = sys.argv[2]
coord = Path(sys.argv[3])
role = sys.argv[4]
start = coord / "start"
(coord / ("ready-" + role)).write_text("ready", encoding="ascii")
deadline = time.monotonic() + 15
while not start.exists() and time.monotonic() < deadline:
    time.sleep(0.005)

original_parse = service._parse_archive
original_rmtree = service.shutil.rmtree
original_mkdir = Path.mkdir
original_write_text = Path.write_text

def wait_for(path, seconds=1.0):
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    return path.exists()

def fake_process_is_alive(_pid):
    (coord / ("stale-" + role)).write_text("seen", encoding="ascii")
    wait_for(coord / "stale-first")
    wait_for(coord / "stale-second")
    return False

def controlled_mkdir(path, *args, **kwargs):
    result = original_mkdir(path, *args, **kwargs)
    return result

def controlled_write_text(path, data, *args, **kwargs):
    result = original_write_text(path, data, *args, **kwargs)
    if role == "first" and path.name == "owner" and path.parent.name == ".archive.lock":
        (coord / "first-owner-written").write_text("ready", encoding="ascii")
    return result

def controlled_rmtree(path, *args, **kwargs):
    if role == "second" and Path(path).name == ".archive.lock":
        wait_for(coord / "first-owner-written")
        time.sleep(0.04)
    return original_rmtree(path, *args, **kwargs)

def synchronized_parse(current_case_id, archive_path):
    result = original_parse(current_case_id, archive_path)
    if isinstance(result, tuple) and result[1] == 0:
        (coord / ("read-" + role)).write_text("ready", encoding="ascii")
        wait_for(coord / "read-first")
        wait_for(coord / "read-second")
    return result

with (
    patch("scripts.case_archive.service._process_is_alive", side_effect=fake_process_is_alive),
    patch("scripts.case_archive.service._parse_archive", side_effect=synchronized_parse),
    patch("scripts.case_archive.service.shutil.rmtree", side_effect=controlled_rmtree),
    patch.object(Path, "mkdir", autospec=True, side_effect=controlled_mkdir),
    patch.object(Path, "write_text", autospec=True, side_effect=controlled_write_text),
):
    result = CaseArchive(workspace).commit({
        "case_id": case_id,
        "expected_revision": 0,
        "change_summary": "失效锁并发写入 " + role,
        "changes": [{
            "operation": "append",
            "record_type": "fact",
            "content_markdown": "**事实状态：用户陈述**\n跨进程保留：" + role,
        }],
    })
print(json.dumps({"role": role, "result": result}, ensure_ascii=False))
"""


def _runtime_layer_payload(version: str) -> dict[str, bytes]:
    files = {
        "workbuddy-runtime/site-packages/arbibuddy_test_runtime/__init__.py": (
            f"VERSION = {version!r}\n"
        ).encode("utf-8"),
        "workbuddy-runtime/site-packages/arbibuddy_test_runtime-1.0.dist-info/LICENSE": (
            "test license\n"
        ).encode("utf-8"),
    }
    manifest = {
        "schema_version": 1,
        "python_abi": "cp313",
        "platform_tag": "win_amd64",
        "packages": [{"distribution": "arbibuddy-test-runtime", "version": version}],
        "licenses": [
            "site-packages/arbibuddy_test_runtime-1.0.dist-info/LICENSE"
        ],
        "files": {
            relative.removeprefix("workbuddy-runtime/"): sha256(data).hexdigest()
            for relative, data in files.items()
        },
    }
    files["workbuddy-runtime/runtime-manifest.json"] = json.dumps(
        manifest, ensure_ascii=False
    ).encode("utf-8")
    return files


class SkillStaticReviewCompletionTests(unittest.TestCase):
    def _create_windows_junction(self, link: Path, target: Path) -> None:
        if os.name != "nt":
            self.skipTest("junction ancestor regression is Windows-specific")
        escaped_link = str(link).replace("'", "''")
        escaped_target = str(target).replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"New-Item -ItemType Junction -Path '{escaped_link}' "
            f"-Target '{escaped_target}' | Out-Null"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode != 0:
            self.skipTest(
                "当前 Windows 环境不能创建目录 junction："
                + (result.stderr or result.stdout).strip()
            )
        self.assertTrue(getattr(link, "is_junction", lambda: False)())

    def test_f05_windows_junction_workspace_ancestor_is_rejected_without_external_read(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            external = root / "external"
            workspace = root / "workspace"
            external.mkdir()
            workspace.mkdir()
            created = CaseArchive(external).create({"initial_goal": "被junction引用的案件"})
            self.assertTrue(created["ok"], created)
            case_id = str(created["result"]["case_id"])
            archive_path = Path(created["result"]["canonical_location"])
            before = archive_path.read_bytes()
            self._create_windows_junction(
                workspace / ".arbibuddy", external / ".arbibuddy"
            )

            result = CaseArchive(workspace).read(case_id)

            self.assertFalse(result["ok"], result)
            self.assertEqual(archive_path.read_bytes(), before)

    def test_f06_windows_junction_runtime_input_ancestor_is_rejected_and_preserved(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            external = root / "external"
            workspace = root / "workspace"
            external_runtime = external / ".arbibuddy" / "runtime-input"
            external_runtime.mkdir(parents=True)
            workspace.mkdir()
            external_file = external_runtime / "amount.json"
            external_file.write_text("{}", encoding="utf-8")
            before = external_file.read_bytes()
            self._create_windows_junction(
                workspace / ".arbibuddy", external / ".arbibuddy"
            )
            output = StringIO()

            with redirect_stdout(output):
                code = amount_runtime_main(
                    [
                        "--workspace",
                        str(workspace),
                        "calculate",
                        "--input",
                        str(workspace / ".arbibuddy" / "runtime-input" / "amount.json"),
                    ]
                )

            self.assertEqual(code, 2, output.getvalue())
            self.assertEqual(
                json.loads(output.getvalue())["errors"][0]["code"],
                "invalid_runtime_input_path",
            )
            self.assertTrue(external_file.is_file())
            self.assertEqual(external_file.read_bytes(), before)

    def test_f04_two_processes_contending_for_a_dead_lock_keep_both_successful_commits(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            workspace = root / "workspace"
            workspace.mkdir()
            created = CaseArchive(workspace).create()
            self.assertTrue(created["ok"], created)
            case_id = str(created["result"]["case_id"])
            archive_path = Path(created["result"]["canonical_location"])
            legacy_lock = archive_path.parent / ".archive.lock"
            legacy_lock.mkdir()
            (legacy_lock / "owner").write_text("99999999", encoding="ascii")
            coordination = root / "coordination"
            coordination.mkdir()
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        "-c",
                        _ARCHIVE_LOCK_WORKER,
                        str(workspace),
                        case_id,
                        str(coordination),
                        role,
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
                for role in ("first", "second")
            ]
            try:
                ready_deadline = time.monotonic() + 15
                while not all(
                    (coordination / f"ready-{role}").exists()
                    for role in ("first", "second")
                ):
                    if time.monotonic() >= ready_deadline:
                        self.fail("两个写入进程未在时限内就绪")
                    time.sleep(0.01)
                (coordination / "start").write_text("go", encoding="ascii")
                completed: list[dict[str, object]] = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, stderr)
                    completed.append(json.loads(stdout))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

            first_results = [item["result"] for item in completed]
            successes = [item for item in first_results if item["ok"]]
            conflicts = [
                item
                for item in first_results
                if not item["ok"]
                and any(
                    error["code"] == "revision_conflict"
                    for error in item.get("errors", [])
                )
            ]
            self.assertEqual(len(successes), 1, first_results)
            self.assertEqual(len(conflicts), 1, first_results)
            successful_role = next(
                item["role"] for item in completed if item["result"]["ok"]
            )
            conflicted_role = next(
                item["role"] for item in completed if not item["result"]["ok"]
            )
            current = CaseArchive(workspace).read(case_id)
            self.assertTrue(current["ok"], current)
            retried = CaseArchive(workspace).commit(
                {
                    "case_id": case_id,
                    "expected_revision": current["result"]["revision"],
                    "change_summary": "基于最新版本重试冲突写入",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n跨进程保留：" + conflicted_role
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(retried["ok"], retried)
            final = CaseArchive(workspace).read(case_id)
            self.assertTrue(final["ok"], final)
            self.assertEqual(final["result"]["revision"], 2)
            for role in (successful_role, conflicted_role):
                self.assertIn("跨进程保留：" + role, final["result"]["markdown"])

    def test_f18_record_and_batch_budgets_reject_without_changing_archive(self):
        with TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create()
            case_id = str(created["result"]["case_id"])
            path = Path(created["result"]["canonical_location"])
            before = path.read_bytes()

            oversized_record = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "单条记录超出预算",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "x" * (16 * 1024 + 1),
                        }
                    ],
                }
            )
            self.assertFalse(oversized_record["ok"], oversized_record)
            self.assertIn("capacity_exceeded", {error["code"] for error in oversized_record["errors"]})
            self.assertEqual(path.read_bytes(), before)

            oversized_count = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "变更条数超出预算",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": f"事实记录 {index}",
                        }
                        for index in range(65)
                    ],
                }
            )
            self.assertFalse(oversized_count["ok"], oversized_count)
            self.assertIn("capacity_exceeded", {error["code"] for error in oversized_count["errors"]})
            self.assertEqual(path.read_bytes(), before)

            oversized_batch = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "批次字节数超出预算",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": f"record-{index}-" + "x" * 13_200,
                        }
                        for index in range(5)
                    ],
                }
            )
            self.assertFalse(oversized_batch["ok"], oversized_batch)
            self.assertIn("capacity_exceeded", {error["code"] for error in oversized_batch["errors"]})
            self.assertEqual(path.read_bytes(), before)

    def test_f18_read_rejects_over_budget_archive_without_returning_partial_text(self):
        with TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create()
            case_id = str(created["result"]["case_id"])
            path = Path(created["result"]["canonical_location"])
            path.write_bytes(b"#" + b"x" * (128 * 1024))

            result = archive.read(case_id)

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["errors"][0]["code"], "archive_too_large")
            self.assertIsNone(result.get("result"))

    def test_f18_projected_archive_over_budget_is_rejected_before_file_replacement(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            case_id = str(created["result"]["case_id"])
            path = Path(created["result"]["canonical_location"])

            for batch_index in range(2):
                current = archive.read(case_id)
                self.assertTrue(current["ok"], current)
                accepted = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": current["result"]["revision"],
                        "change_summary": f"提交容量预算内的第{batch_index + 1}批记录",
                        "changes": [
                            {
                                "operation": "append",
                                "record_type": "fact",
                                "content_markdown": (
                                    f"容量记录-{batch_index}-{index}-" + "x" * 12_400
                                ),
                            }
                            for index in range(4)
                        ],
                    }
                )
                self.assertTrue(accepted["ok"], accepted)

            before = path.read_bytes()
            current = archive.read(case_id)
            rejected = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": current["result"]["revision"],
                    "change_summary": "拒绝会超过全文恢复容量的提交",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": f"超过容量的记录-{index}-" + "x" * 12_400,
                        }
                        for index in range(4)
                    ],
                }
            )

            self.assertFalse(rejected["ok"], rejected)
            self.assertIn("capacity_exceeded", {error["code"] for error in rejected["errors"]})
            self.assertEqual(path.read_bytes(), before)
            after = archive.read(case_id)
            self.assertEqual(after["result"]["revision"], current["result"]["revision"])

    def test_f19_domain_references_agree_with_the_authoritative_confirmation_rule(self):
        root = Path(__file__).resolve().parents[1]
        unified = (root / "references" / "rights-scan-and-unified-confirmation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("条件前提", unified)
        self.assertIn("事实等级", unified)
        self.assertIn("未核实假设", unified)
        for name in ("overtime-pay.md", "annual-leave-pay.md"):
            with self.subTest(reference=name):
                text = (root / "references" / "model-led" / name).read_text(
                    encoding="utf-8"
                )
                self.assertRegex(
                    text,
                    r"确认摘要.{0,30}条件前提.{0,30}事实等级.{0,50}统一确认",
                )
                self.assertNotRegex(
                    text,
                    r"(?:分析假设|重要假设).{0,16}(?:不能|不得|不可).{0,12}(?:确认|确认快照)",
                )

    @staticmethod
    def _confirmation_for_revision(revision: int) -> str:
        return (
            "高风险确认标识：planned_termination\n"
            "action_type：planned_termination\n"
            "action_scope：按当前事实准备被迫解除通知书，劳动者张三向示例公司发出，理由为未及时足额支付劳动报酬，使用欠薪流水，通知日期为2026年9月8日，解除生效日期为2026年9月20日，不包含自动发送或提交。\n"
            "risk_summary：解除理由、日期、程序、送达、时效和证据不足可能影响后果。\n"
            "alternatives_presented：先催告履行、补强证据、暂缓解除并保留历史索赔。\n"
            "user_choice：我明确选择按已说明范围继续准备被迫解除通知书。\n"
            "confirmed_at：2026-09-08\n"
            f"archive_revision：{revision}\n"
            "source_refs：F-001、F-002、F-003、F-004、CL-001、E-001、AUTH-001。"
        )

    def _render_forced_authority(self, authority_text: str) -> dict[str, object]:
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = create_forced_case(
                workspace, with_confirmation=True
            )
            next_revision = revision + 1
            updated = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "测试核验记录唯一控制字段",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "authority",
                            "record_id": "AUTH-001",
                            "content_markdown": authority_text,
                            "reason": "测试核验结论唯一性",
                        },
                        {
                            "operation": "replace",
                            "record_type": "confirmation",
                            "record_id": "CONF-001",
                            "content_markdown": self._confirmation_for_revision(
                                next_revision
                            ),
                            "reason": "重新绑定当前档案修订版本",
                        },
                    ],
                }
            )
            if not updated["ok"]:
                raise AssertionError(updated)
            return render(
                forced_request(
                    case_id,
                    next_revision,
                    confirmation_refs=["CONF-001"],
                    authority_refs=["AUTH-001"],
                ),
                workspace,
            )

    def test_f01_legacy_semicolon_delimited_single_verified_conclusion_remains_supported(self):
        result = self._render_forced_authority(
            "核验事项：劳动者解除相关现行规则；核验结论：已核验；适用地区：全国。"
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["delivery_state"], "final_ready")

    def test_f01_unavailable_state_and_duplicate_verified_conclusions_fail_closed(self):
        attempts = (
            "核验事项：劳动者解除相关现行规则；核验结论：已核验；核验状态：unavailable；适用地区：全国。",
            "核验事项：劳动者解除相关现行规则；核验结论：已核验；核验状态：verified；核验结论：已完成动态核验；适用地区：全国。",
        )
        for content in attempts:
            with self.subTest(content=content):
                result = self._render_forced_authority(content)
                self.assertFalse(result["ok"], result)
                self.assertIn(
                    "authority_stale",
                    {error["code"] for error in result["errors"]},
                )

    def test_f15_public_update_failure_after_anchor_write_restores_old_tree_and_identity(self):
        original_layer = _runtime_layer_payload("old")
        with (
            TemporaryDirectory() as temporary_root,
            patch(
                "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
                return_value=(
                    original_layer,
                    {"verified": True, "required_files": [], "missing_files": [], "missing_packages": []},
                ),
            ),
        ):
            root = Path(temporary_root)
            target = root / "WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            self.assertTrue(
                verify_install(
                    platform="workbuddy", skill_root=skill_root
                )["runtime_identity_verified"]
            )
            anchor = workbuddy_trust_anchor_path(target, skill_root)
            old_anchor = anchor.read_bytes()
            old_files = {
                path.relative_to(skill_root).as_posix(): sha256(path.read_bytes()).hexdigest()
                for path in skill_root.rglob("*")
                if path.is_file()
            }
            real_verify = platform_service.verify_install
            verification_calls = 0

            def fail_post_swap(*, platform: str, skill_root: Path):
                nonlocal verification_calls
                verification_calls += 1
                current = real_verify(platform=platform, skill_root=skill_root)
                if verification_calls == 2:
                    return {**current, "runtime_identity_verified": False}
                return current

            with (
                patch(
                    "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
                    return_value=(
                        _runtime_layer_payload("new"),
                        {"verified": True, "required_files": [], "missing_files": [], "missing_packages": []},
                    ),
                ),
                patch(
                    "scripts.platform_adapters.service.verify_install",
                    side_effect=fail_post_swap,
                ),
            ):
                with self.assertRaisesRegex(
                    PlatformAdapterError,
                    "workbuddy_update_verification_failed",
                ):
                    update_skill(
                        platform="workbuddy",
                        scope="project",
                        target_root=target,
                        source=ROOT,
                        mode="copy",
                    )

            self.assertEqual(verification_calls, 2)
            self.assertEqual(anchor.read_bytes(), old_anchor)
            self.assertEqual(
                {
                    path.relative_to(skill_root).as_posix(): sha256(path.read_bytes()).hexdigest()
                    for path in skill_root.rglob("*")
                    if path.is_file()
                },
                old_files,
            )
            restored = real_verify(platform="workbuddy", skill_root=skill_root)
            self.assertTrue(restored["runtime_identity_verified"], restored)

    def test_f15_reap_recovers_old_tree_receipt_and_anchor_after_interrupted_rollback(self):
        original_layer = _runtime_layer_payload("old")
        with (
            TemporaryDirectory() as temporary_root,
            patch(
                "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
                return_value=(
                    original_layer,
                    {"verified": True, "required_files": [], "missing_files": [], "missing_packages": []},
                ),
            ),
        ):
            root = Path(temporary_root)
            target = root / "WorkBuddy workspace"
            installed = install_skill(
                platform="workbuddy",
                scope="project",
                target_root=target,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            before_verification = verify_install(
                platform="workbuddy", skill_root=skill_root
            )
            self.assertTrue(before_verification["runtime_identity_verified"])
            anchor = workbuddy_trust_anchor_path(target, skill_root)
            old_anchor = anchor.read_bytes()
            old_tree = {
                path.relative_to(skill_root).as_posix(): sha256(path.read_bytes()).hexdigest()
                for path in skill_root.rglob("*")
                if path.is_file()
            }
            real_write_anchor = platform_service.write_workbuddy_trust_anchor
            real_replace = platform_service._replace_workbuddy_path
            replace_calls = 0

            def corrupt_new_anchor(**kwargs):
                written = real_write_anchor(**kwargs)
                Path(written).write_bytes(b"corrupt-new-anchor")
                return written

            def fail_rollback_move(source: Path, destination: Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 3:
                    raise PermissionError("模拟进程中断后的回滚移动失败")
                real_replace(source, destination)

            with (
                patch(
                    "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
                    return_value=(
                        _runtime_layer_payload("new"),
                        {"verified": True, "required_files": [], "missing_files": [], "missing_packages": []},
                    ),
                ),
                patch(
                    "scripts.platform_adapters.service.write_workbuddy_trust_anchor",
                    side_effect=corrupt_new_anchor,
                ),
                patch(
                    "scripts.platform_adapters.service._replace_workbuddy_path",
                    side_effect=fail_rollback_move,
                ),
            ):
                with self.assertRaisesRegex(PlatformAdapterError, "rollback_failed"):
                    update_skill(
                        platform="workbuddy",
                        scope="project",
                        target_root=target,
                        source=ROOT,
                        mode="copy",
                    )

            self.assertEqual(replace_calls, 3)
            transaction_roots = list(target.glob(".arbibuddy-workbuddy-update-*"))
            self.assertEqual(len(transaction_roots), 1)
            transaction_root = transaction_roots[0]
            self.assertTrue((transaction_root / "previous").is_dir())
            self.assertTrue((transaction_root / "previous-anchor.json").is_file())
            self.assertFalse(verify_install(platform="workbuddy", skill_root=skill_root)["runtime_identity_verified"])

            recovered = platform_service.reap_workbuddy_cleanup(
                platform="workbuddy", target_root=target, scope="project"
            )

            self.assertTrue(recovered["cleaned"], recovered)
            self.assertEqual(recovered["warnings"], [])
            self.assertEqual(list(target.glob(".arbibuddy-workbuddy-update-*")), [])
            self.assertEqual(anchor.read_bytes(), old_anchor)
            self.assertEqual(
                {
                    path.relative_to(skill_root).as_posix(): sha256(path.read_bytes()).hexdigest()
                    for path in skill_root.rglob("*")
                    if path.is_file()
                },
                old_tree,
            )
            after = verify_install(platform="workbuddy", skill_root=skill_root)
            self.assertTrue(after["runtime_identity_verified"], after)


if __name__ == "__main__":
    unittest.main()
