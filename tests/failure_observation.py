from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


STATUS_READ_TIMEOUT_SECONDS = 30


def _status_read_timeout_seconds() -> int:
    configured = os.environ.get("ARBIBUDDY_STATUS_READ_TIMEOUT_SECONDS")
    if configured is None:
        return STATUS_READ_TIMEOUT_SECONDS
    try:
        value = int(configured)
    except ValueError:
        return STATUS_READ_TIMEOUT_SECONDS
    return value if value > 0 else STATUS_READ_TIMEOUT_SECONDS


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    else:
        process.kill()


def emit_failure_observation(
    injection: str,
    *,
    case_root: Path,
    case_id: str,
    operation_log: list[dict[str, Any]] | None = None,
    public_action: str | None = None,
    expected_state: dict[str, Any] | None = None,
) -> None:
    raw = os.environ.get("ARBIBUDDY_FAILURE_OBSERVATION")
    if not raw:
        return
    installed_root = Path(
        os.environ.get("ARBIBUDDY_INSTALLED_ROOT", Path(__file__).resolve().parents[1])
    )
    process = subprocess.Popen(
        [
            sys.executable, "-B", "-X", "utf8", "-m",
            "scripts.core_workflow.cli", "--root", str(case_root),
            "status", case_id,
        ],
        cwd=installed_root,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        timeout_seconds = _status_read_timeout_seconds()
        stdout, stderr = process.communicate(
            timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as error:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate(timeout=10)
        raise AssertionError(
            f"故障后公共状态读取超时（{timeout_seconds}s）："
            f"{stderr or stdout}"
        ) from error
    if process.returncode != 0:
        raise AssertionError(f"故障后公共状态读取失败：{stderr or stdout}")
    status = json.loads(stdout)
    deliveries = status.get("deliveries")
    if not isinstance(deliveries, list):
        raise AssertionError("故障后公共状态未返回受管交付清单")
    state_envelope = {
        "phase": status.get("phase"),
        "completed": status.get("phase") == "client_visible_complete",
        "deliveries": deliveries,
        "recoverable": status.get("resumable") is True,
        "next_user_action": status.get("next_user_action"),
    }
    for key, value in (expected_state or {}).items():
        if state_envelope.get(key) != value:
            raise AssertionError(
                f"故障后公共状态 {key}={state_envelope.get(key)!r}，"
                f"期望 {value!r}"
            )
    trace_path = os.environ.get("ARBIBUDDY_PUBLIC_OPERATION_TRACE")
    trace: list[dict[str, Any]] = []
    if trace_path and Path(trace_path).is_file():
        for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if (
                isinstance(item, dict)
                and item.get("case_id") == case_id
                and item.get("case_root") == str(case_root.resolve())
            ):
                trace.append(item)
    action_records = [
        item for item in trace
        if public_action is not None and item.get("action") == public_action
    ]
    if public_action is not None and not action_records:
        raise AssertionError(f"未观测到核心公共动作原始轨迹：{public_action}")
    path = Path(raw)
    path.write_text(json.dumps({
        "schema_version": 2,
        "injection": injection,
        "case_id": case_id,
        "case_root": str(case_root.resolve()),
        "public_action": public_action,
        "failed_transaction_id": (
            action_records[-1].get("transaction_id") if action_records else None
        ),
        "public_operation_trace": trace,
        "state_envelope": state_envelope,
        "managed_delivery_manifest": deliveries,
        "operation_log": [
            *(operation_log or []),
            {
                "operation": "public_status_after_failure",
                "process_id": process.pid,
                "exit_code": process.returncode,
            },
        ],
    }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
