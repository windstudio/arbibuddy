from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Sequence

from scripts.cli_encoding import configure_utf8_stdio
from scripts.test_suites.paths import assert_external_root
from scripts.platform_adapters.service import (
    WORKBUDDY_RUNTIME_LOCK_RELATIVE,
    WORKBUDDY_WHEELHOUSE_ENV,
    resolve_workbuddy_wheelhouse,
)
from scripts.platform_paths import public_path
from scripts.runtime_temp import runtime_temporary_directory

@dataclass(frozen=True)
class TestGroup:
    name: str
    timeout_seconds: int
    start_dir: str | None = None
    pattern: str = "test_*.py"
    test_modules: tuple[str, ...] = ()


FAST_GROUPS = (
    TestGroup("amount_calculator", 300, pattern="test_model_led*.py", test_modules=(
        "tests.amount_calculator.test_model_led_amount_contract",
        "tests.amount_calculator.test_model_led_amount_boundary",
        "tests.amount_calculator.test_model_led_working_time_leave_double_wage",
        "tests.amount_calculator.test_amount_runtime_cli",
    )),
    TestGroup("case_archive", 300, test_modules=("tests.case_file.test_model_led_archive",)),
    TestGroup("legal_verification", 300),
    TestGroup("scenarios", 300, pattern="test_model_led*.py"),
    TestGroup("static", 300, pattern="test_model_led*.py"),
    TestGroup("contraction", 300, test_modules=("tests.static.test_contraction",)),
    TestGroup("test_suites", 300),
    TestGroup("network", 300, test_modules=("tests.test_network_diagnostics",)),
    TestGroup("model_led_agent_eval", 300, test_modules=(
        "tests.agent_eval.test_artifact_oracle",
        "tests.agent_eval.test_issue10_ml02_regressions",
        "tests.agent_eval.test_issue10_observer_failure_regressions",
        "tests.agent_eval.test_journey_conversation_report",
        "tests.agent_eval.test_ml01_confirmation_lifecycle",
        "tests.agent_eval.test_ml01_failed_responder_replays",
        "tests.agent_eval.test_ml02_unverified_contribution_fact",
        "tests.agent_eval.test_model_led_harness",
        "tests.agent_eval.test_model_led_journey_remediation",
        "tests.agent_eval.test_model_led_oracle_fixtures",
        "tests.agent_eval.test_scenario_responder",
        "tests.agent_eval.test_two_batch_remediation",
    )),
)

SLOW_GROUPS = (
    TestGroup("documents_core", 900, test_modules=(
        "tests.documents.test_model_led_document_render",
        "tests.documents.test_model_led_demand_forced_render",
        "tests.documents.test_model_led_program_documents_render",
    )),
    TestGroup("documents_support", 900, test_modules=(
        "tests.documents.test_delivery_set", "tests.documents.test_candidate_dependency_reuse",
        "tests.documents.test_document_runtime_cli", "tests.documents.test_document_runtime_vendor_paths",
        "tests.documents.test_codex_ml01_render_recovery", "tests.documents.test_public_docx_layout",
        "tests.documents.test_public_layout_feedback",
        "tests.documents.test_public_overwrite_recovery",
        "tests.documents.test_rc14_delivery_transaction", "tests.documents.test_rc14_ooxml_audit",
    )),
    TestGroup("platform_adapters", 900),
    TestGroup("runtime_identity", 300),
    TestGroup("release", 300),
)

Runner = Callable[..., subprocess.CompletedProcess[object]]


def _terminate_process_tree(process: subprocess.Popen[object]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    else:
        process.kill()


def _run_with_tree_timeout(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[object]:
    """Run one test domain and kill its descendants when the budget expires."""

    process = subprocess.Popen(argv, cwd=cwd, env=env)
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        raise
    completed = subprocess.CompletedProcess(argv, returncode)
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, argv)
    return completed


def _ensure_writable_temp_root(temp_root: Path) -> Path:
    root = temp_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with runtime_temporary_directory(
            root, prefix=f".arbibuddy-test-probe-{os.getpid()}-"
        ) as child:
            probe = child / "write-probe"
            probe.write_text("ok", encoding="ascii")
    except OSError as error:
        raise ValueError(
            f"临时根目录不可稳定创建、写入或清理子目录：{root}"
        ) from error
    return root


def _temp_environment(temp_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    value = str(temp_root.resolve())
    environment["TEMP"] = value
    environment["TMP"] = value
    environment["TMPDIR"] = value
    environment["ARBIBUDDY_TEST_TEMP_ROOT"] = value
    return environment


def _group_environment(
    group: TestGroup,
    *,
    group_temp: Path,
    repo_root: Path,
    workbuddy_wheelhouse: Path | None = None,
) -> dict[str, str]:
    environment = _temp_environment(group_temp)
    # Keep Python's generic TemporaryDirectory paths one level above the
    # isolated Runtime root.  Deep fixture names can otherwise exceed
    # Windows' legacy path boundary before the test-only cleanup guard runs.
    process_temp_root = group_temp.parent
    for variable in ("TEMP", "TMP", "TMPDIR"):
        environment[variable] = str(process_temp_root)
    harness_site = repo_root / "tests" / "sitecustomize.py"
    if harness_site.is_file():
        existing_pythonpath = environment.get("PYTHONPATH", "")
        pythonpath_entries = [str(harness_site.parent)]
        if existing_pythonpath:
            pythonpath_entries.append(existing_pythonpath)
        environment["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    resolved_wheelhouse: Path | None = None
    if (
        workbuddy_wheelhouse is not None
        or (repo_root / WORKBUDDY_RUNTIME_LOCK_RELATIVE).is_file()
    ):
        resolved_wheelhouse = resolve_workbuddy_wheelhouse(
            repo_root,
            wheelhouse=workbuddy_wheelhouse,
        )
        environment[WORKBUDDY_WHEELHOUSE_ENV] = str(resolved_wheelhouse)
    return environment


def _default_temp_root(repo_root: Path) -> Path:
    candidate = Path(tempfile.gettempdir()).expanduser().resolve() / "arbibuddy-test-suites"
    return assert_external_root(
        repo_root,
        candidate,
        label="测试套件默认临时根目录",
    )


def _groups(profile: str) -> tuple[TestGroup, ...]:
    if profile == "fast":
        return FAST_GROUPS
    if profile == "slow":
        return SLOW_GROUPS
    if profile == "full":
        return FAST_GROUPS + SLOW_GROUPS
    raise ValueError(f"未知测试层级：{profile}")


def _command(group: TestGroup) -> list[str]:
    if group.test_modules:
        return [
            sys.executable,
            "-m",
            "unittest",
            *group.test_modules,
            "-q",
            "-b",
            "--durations",
            "20",
        ]
    return [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        group.start_dir or f"tests/{group.name}",
        "-p",
        group.pattern,
        "-q",
        "-b",
        "--durations",
        "20",
    ]


def run_profile(
    profile: str,
    *,
    runner: Runner = _run_with_tree_timeout,
    repo_root: Path | None = None,
    temp_root: Path | None = None,
    workbuddy_wheelhouse: Path | None = None,
) -> int:
    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        configured_temp_root = (
            _ensure_writable_temp_root(temp_root) if temp_root is not None else None
        )
    except ValueError as error:
        print(f"[ENV-FAIL] {error}", flush=True)
        return 1
    failures: list[str] = []
    try:
        temp_anchor = configured_temp_root or _default_temp_root(root)
    except ValueError as error:
        print(f"[ENV-FAIL] {error}", flush=True)
        return 1
    for group in _groups(profile):
        started = monotonic()
        print(
            f"[START] {group.name}（预算 {group.timeout_seconds}s）",
            flush=True,
        )
        group_scope = runtime_temporary_directory(
            temp_anchor, prefix=f"arbibuddy-{group.name}-"
        )
        group_path = group_scope.__enter__()
        completed = None
        try:
            environment = _group_environment(
                group,
                group_temp=public_path(group_path),
                repo_root=root,
                workbuddy_wheelhouse=workbuddy_wheelhouse,
            )
            completed = runner(
                _command(group),
                cwd=root,
                timeout=group.timeout_seconds,
                check=False,
                **({"env": environment} if environment is not None else {}),
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{group.name}: TIMEOUT")
            print(
                f"[TIMEOUT] {group.name}（{monotonic() - started:.1f}s）",
                flush=True,
            )
            continue
        except (OSError, ValueError) as error:
            failures.append(f"{group.name}: ENV({error.__class__.__name__})")
            print(f"[ENV-FAIL] {group.name}：{error}", flush=True)
            continue
        finally:
            try:
                group_scope.__exit__(None, None, None)
            except OSError as error:
                failures.append(
                    f"{group.name}: CLEANUP({error.__class__.__name__})"
                )
                print(f"[CLEANUP-FAIL] {group.name}：{error}", flush=True)
        if completed is None:
            continue
        if completed.returncode:
            failures.append(f"{group.name}: FAIL({completed.returncode})")
            state = "FAIL"
        else:
            state = "PASS"
        print(f"[{state}] {group.name}（{monotonic() - started:.1f}s）", flush=True)

    if failures:
        print("[SUMMARY] " + "；".join(failures), flush=True)
        return 1
    print(f"[SUMMARY] {profile} 全部通过", flush=True)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按测试域运行 ArbiBuddy 测试套件")
    parser.add_argument("profile", choices=("fast", "slow", "full"))
    parser.add_argument(
        "--temp-root",
        type=Path,
        help="明确可写且独立的临时根目录；每个测试域使用其下的独立目录",
    )
    parser.add_argument(
        "--workbuddy-wheelhouse",
        "--wheelhouse",
        dest="workbuddy_wheelhouse",
        type=Path,
        help="WorkBuddy 锁定 wheelhouse；缺失时相关测试按契约失败或跳过，不联网下载",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    args = _parser().parse_args(argv)
    return run_profile(
        args.profile,
        temp_root=args.temp_root,
        workbuddy_wheelhouse=args.workbuddy_wheelhouse,
    )


if __name__ == "__main__":
    raise SystemExit(main())
