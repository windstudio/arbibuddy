"""WorkBuddy-safe public transport for model-authored document requests.

The document API remains ``scripts.documents.public``.  This module only
transports one JSON request to that API without source introspection or a
persistent request copy in the case workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

MAX_RUNTIME_INPUT_BYTES = 2 * 1024 * 1024
_OPERATIONS = ("describe", "delivery-set", "render", "view")
RUNTIME_BOOTSTRAP_ENV = "ARBIBUDDY_DOCUMENT_RUNTIME_BOOTSTRAPPED"
_RUNTIME_ROOT_NAME = "workbuddy-runtime"
_RUNTIME_MANIFEST_NAME = "runtime-manifest.json"


def _runtime_platform_tag(*, system: str | None = None, machine: str | None = None) -> str:
    system_name = sys.platform if system is None else system
    machine_name = platform.machine() if machine is None else machine
    folded_machine = machine_name.casefold()
    if system_name == "win32" and folded_machine in {"amd64", "x86_64"}:
        return "win_amd64"
    return f"{system_name}_{folded_machine}"


def _skill_root(value: str | Path | None = None) -> Path:
    return (
        Path(value).expanduser().resolve()
        if value is not None
        else Path(__file__).resolve().parents[2]
    )


def _packaged_runtime_target(skill_root: Path) -> tuple[str, str] | None:
    manifest_path = skill_root / _RUNTIME_ROOT_NAME / _RUNTIME_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    abi = value.get("python_abi")
    platform_tag = value.get("platform_tag")
    if not isinstance(abi, str) or not abi.strip():
        return None
    if not isinstance(platform_tag, str) or not platform_tag.strip():
        return None
    return abi.strip(), platform_tag.strip()


def _current_runtime_target() -> tuple[str, str]:
    return (
        f"cp{sys.version_info.major}{sys.version_info.minor}",
        _runtime_platform_tag(),
    )


def _workbuddy_root(skill_root: Path) -> Path:
    for candidate in (skill_root, *skill_root.parents):
        if candidate.name.casefold() == ".workbuddy":
            return candidate.resolve()
    return (Path.home() / ".workbuddy").resolve()


def _discover_workbuddy_python(skill_root: Path) -> Path | None:
    workbuddy_root = _workbuddy_root(skill_root)
    candidates = (
        workbuddy_root
        / "binaries"
        / "python"
        / "envs"
        / "arbibuddy"
        / "Scripts"
        / "python.exe",
        workbuddy_root
        / "binaries"
        / "python"
        / "envs"
        / "default"
        / "Scripts"
        / "python.exe",
    )
    for candidate in candidates:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved.is_relative_to(workbuddy_root):
                return resolved
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _probe_workbuddy_python(python: Path) -> dict[str, str] | None:
    probe = (
        "import json, platform, sys; "
        "print(json.dumps({'python_abi': f'cp{sys.version_info.major}{sys.version_info.minor}', "
        "'platform_tag': ('win_amd64' if sys.platform == 'win32' and "
        "platform.machine().casefold() in {'amd64', 'x86_64'} else "
        "f'{sys.platform}_{platform.machine().casefold()}')}))"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    try:
        completed = subprocess.run(
            [str(python), "-S", "-c", probe],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    abi = value.get("python_abi")
    platform_tag = value.get("platform_tag")
    if not isinstance(abi, str) or not isinstance(platform_tag, str):
        return None
    return {"python_abi": abi, "platform_tag": platform_tag}


def bootstrap_document_runtime(
    argv: Sequence[str] | None = None,
    *,
    skill_root: str | Path | None = None,
) -> bool:
    """Replace an ABI-mismatched host interpreter with WorkBuddy's managed one."""

    root = _skill_root(skill_root)
    expected = _packaged_runtime_target(root)
    if expected is None or expected == _current_runtime_target():
        return False
    if os.environ.get(RUNTIME_BOOTSTRAP_ENV) == "1":
        return False
    target = _discover_workbuddy_python(root)
    if target is None:
        return False
    probed = _probe_workbuddy_python(target)
    if probed != {
        "python_abi": expected[0],
        "platform_tag": expected[1],
    }:
        return False
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    os.environ[RUNTIME_BOOTSTRAP_ENV] = "1"
    try:
        os.execv(
            str(target),
            [str(target), "-m", "scripts.documents.runtime_cli", *raw_argv],
        )
    except OSError:
        os.environ.pop(RUNTIME_BOOTSTRAP_ENV, None)
        return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ArbiBuddy WorkBuddy 文书公共 transport"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("describe", help="读取公开文书契约")
    for operation in ("delivery-set", "render", "view"):
        command = commands.add_parser(operation)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--json", dest="request_json")
        source.add_argument("--input", type=Path)
    return parser


def _error(code: str, message: str, *, received: object = None) -> dict[str, Any]:
    return {
        "contract_version": "document.runtime-transport-v1",
        "operation": "document.runtime-transport",
        "ok": False,
        "result": None,
        "errors": [
            {
                "code": code,
                "path": "request",
                "message": message,
                "expected": "public document request JSON",
                "received": received,
                "recoverable": True,
            }
        ],
        "warnings": [],
    }


def _workspace_root(value: Path) -> Path:
    root = value.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("--workspace 必须是已存在的工作区目录")
    return root


def _consume_runtime_input(path: Path, workspace: Path) -> str:
    runtime_root_raw = workspace / ".arbibuddy" / "runtime-input"
    if runtime_root_raw.is_symlink() or not runtime_root_raw.is_dir():
        raise ValueError("invalid_runtime_input_path")
    runtime_root = runtime_root_raw.resolve()
    if not runtime_root.is_relative_to(workspace.resolve()):
        raise ValueError("invalid_runtime_input_path")
    if any(part == ".." for part in Path(str(path)).parts):
        raise ValueError("invalid_runtime_input_path")
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    if candidate.is_symlink():
        raise ValueError("invalid_runtime_input_path")
    candidate = candidate.resolve()
    if (
        candidate.parent != runtime_root
        or candidate.suffix.casefold() != ".json"
        or not candidate.is_file()
        or not candidate.is_relative_to(workspace.resolve())
    ):
        raise ValueError("invalid_runtime_input_path")
    size = candidate.stat().st_size
    if size > MAX_RUNTIME_INPUT_BYTES:
        raise OverflowError("runtime_input_too_large")
    payload = candidate.read_text(encoding="utf-8-sig")
    candidate.unlink()
    return payload


def _request(args: argparse.Namespace, workspace: Path) -> Mapping[str, Any]:
    if args.request_json is not None:
        raw = args.request_json
    else:
        raw = _consume_runtime_input(args.input, workspace)
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise TypeError("request_must_be_object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if bootstrap_document_runtime(raw_argv):
        return 0
    from scripts.cli_encoding import configure_utf8_stdio
    from scripts.documents.public import create_delivery_set, describe_contract, render

    configure_utf8_stdio()
    try:
        args = _parser().parse_args(raw_argv)
        workspace = _workspace_root(args.workspace)
        if args.operation == "describe":
            result: dict[str, Any] = {
                "contract_version": "document.runtime-transport-v1",
                "operation": "document.runtime-transport.describe",
                "ok": True,
                "result": describe_contract(),
                "errors": [],
                "warnings": [],
            }
        elif args.operation in {"delivery-set", "render"}:
            request = _request(args, workspace)
            result = (
                create_delivery_set(request, workspace)
                if args.operation == "delivery-set"
                else render(request, workspace)
            )
        else:
            request = _request(args, workspace)
            case_id = request.get("case_id")
            expected_delivery_set_id = request.get("delivery_set_id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("case_id_required")
            if expected_delivery_set_id is not None and not isinstance(
                expected_delivery_set_id, str
            ):
                raise ValueError("delivery_set_id_invalid")
            # Imported lazily to keep the public renderer free of an adapter
            # dependency while exposing one executable WorkBuddy exit path.
            from scripts.documents.view import managed_delivery_view

            result = managed_delivery_view(workspace=workspace, case_id=case_id)
            observed_delivery_set = result.get("delivery_set")
            observed_delivery_set_id = (
                observed_delivery_set.get("delivery_set_id")
                if isinstance(observed_delivery_set, Mapping)
                else None
            )
            if (
                expected_delivery_set_id is not None
                and observed_delivery_set_id != expected_delivery_set_id
            ):
                result = {
                    "schema_version": 1,
                    "case_id": case_id,
                    "delivery_state": "unavailable",
                    "stopped": True,
                    "error_code": "delivery_set_identity_mismatch",
                    "reason": "受管视图与请求的 delivery set 不一致",
                    "next_action": "rebuild_managed_delivery_set",
                    "presentation_files": [],
                    "present_files_arguments": {
                        "cwd": str(workspace),
                        "files": [],
                    },
                    "delivery_set": observed_delivery_set,
                }
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2
    except ValueError as error:
        code = (
            "invalid_runtime_input_path"
            if str(error) == "invalid_runtime_input_path"
            else "invalid_runtime_request"
        )
        result = _error(code, "文书请求无法读取", received=error.__class__.__name__)
    except OverflowError as error:
        result = _error(
            "runtime_input_too_large",
            "文书请求超过公开 transport 的大小上限",
            received=str(error),
        )
    except (OSError, TypeError, json.JSONDecodeError) as error:
        result = _error(
            "invalid_runtime_request",
            "文书请求无法解析",
            received=error.__class__.__name__,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.operation == "view":
        return 0 if result.get("stopped") is False else 2
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "MAX_RUNTIME_INPUT_BYTES",
    "RUNTIME_BOOTSTRAP_ENV",
    "bootstrap_document_runtime",
    "main",
]
