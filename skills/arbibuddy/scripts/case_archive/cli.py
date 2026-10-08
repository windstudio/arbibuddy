from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Sequence

from .service import CaseArchive


MAX_RUNTIME_INPUT_BYTES = 2 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ArbiBuddy 模型主导案情档案工具（仅 create/read/commit）"
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="当前工作区根目录；案件目录由工具自行派生",
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    create = commands.add_parser("create", help="创建最小案情档案")
    create.add_argument("--case-label")
    create.add_argument("--jurisdiction")
    create.add_argument("--initial-goal")

    read = commands.add_parser("read", help="读取完整权威案情档案")
    read.add_argument("case_id")

    commands.add_parser(
        "read-current",
        help="恢复工作区中唯一的当前案件；不需要先扫描目录取得 case_id",
    )

    commit = commands.add_parser("commit", help="原子提交一批档案变更")
    source = commit.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--json",
        dest="request_json",
        help="单次 commit 请求 JSON；不会写入案件目录",
    )
    source.add_argument(
        "--input",
        type=Path,
        help="单次 commit 请求 JSON 文件；工具不会将其复制到案件目录",
    )
    return parser


def _runtime_input_path(value: Path, workspace_root: Path) -> Path:
    runtime_input_root_raw = workspace_root / ".arbibuddy" / "runtime-input"
    if runtime_input_root_raw.is_symlink() or not runtime_input_root_raw.is_dir():
        raise ValueError("invalid_runtime_input_path")
    runtime_input_root = runtime_input_root_raw.resolve()
    if not runtime_input_root.is_relative_to(workspace_root.resolve()):
        raise ValueError("invalid_runtime_input_path")
    raw_text = str(value)
    if any(part == ".." for part in Path(raw_text).parts):
        raise ValueError("invalid_runtime_input_path")
    candidate = value.expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    if candidate.is_symlink():
        raise ValueError("invalid_runtime_input_path")
    resolved = candidate.resolve()
    if (
        resolved.parent != runtime_input_root
        or resolved.suffix.casefold() != ".json"
        or not resolved.is_file()
        or not resolved.is_relative_to(workspace_root.resolve())
    ):
        raise ValueError("invalid_runtime_input_path")
    return resolved


def _consume_runtime_input(value: Path, workspace_root: Path) -> str:
    candidate = _runtime_input_path(value, workspace_root)
    size = candidate.stat().st_size
    if size > MAX_RUNTIME_INPUT_BYTES:
        raise OverflowError("runtime_input_too_large")
    payload = candidate.read_text(encoding="utf-8-sig")
    candidate.unlink()
    return payload


def _commit_request(args: argparse.Namespace, workspace_root: Path) -> dict:
    if args.request_json is not None:
        return json.loads(args.request_json)
    return json.loads(_consume_runtime_input(args.input, workspace_root))


def _validate_workspace_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("--root 必须是已存在的工作区目录")
    installed_skill_root = Path(__file__).resolve().parents[2]
    if resolved == installed_skill_root or resolved.is_relative_to(installed_skill_root):
        raise ValueError("--root 不得位于 Skill 安装目录内")
    return resolved


def _operation_hint(argv: Sequence[str]) -> str | None:
    options_with_values = {
        "--root",
        "--case-label",
        "--jurisdiction",
        "--initial-goal",
        "--json",
        "--input",
    }
    skip_value = False
    for token in argv:
        if skip_value:
            skip_value = False
            continue
        if token in options_with_values:
            skip_value = True
            continue
        if token.startswith("--root="):
            continue
        if token in {"create", "read", "read-current", "commit"}:
            return token
    return None


def _input_error(operation: str, error: Exception) -> dict:
    error_text = str(error)
    if error_text == "invalid_runtime_input_path":
        code = "invalid_runtime_input_path"
        message = "commit 输入必须是当前工作区 runtime-input 下的直接 JSON 文件"
    elif error_text == "runtime_input_too_large":
        code = "runtime_input_too_large"
        message = "commit 输入超过公开 transport 的大小上限"
    else:
        code = "invalid_change"
        message = "工具输入无法解析"
    return {
        "contract_version": "case-archive-v1",
        "operation": operation,
        "ok": False,
        "errors": [
            {
                "code": code,
                "path": "request",
                "message": message,
                "expected": "valid operation input",
                "received": error.__class__.__name__,
                "recoverable": True,
            }
        ],
        "warnings": [],
    }


def _read_current(tool: CaseArchive, root: Path) -> dict:
    cases_root = (root / ".arbibuddy" / "cases").resolve()
    candidates: list[str] = []
    if cases_root.is_dir():
        for path in cases_root.iterdir():
            if (
                path.is_dir()
                and not path.is_symlink()
                and re.fullmatch(r"case-[0-9a-f]{24}", path.name)
                and (path / "案情档案.md").is_file()
            ):
                candidates.append(path.name)
    candidates.sort()
    if len(candidates) == 1:
        return tool.read(candidates[0])
    code = "current_case_missing" if not candidates else "current_case_ambiguous"
    message = (
        "当前工作区没有可恢复的案件"
        if not candidates
        else "当前工作区存在多个案件，无法自动选择"
    )
    return {
        "contract_version": "case-archive-v1",
        "operation": "read",
        "ok": False,
        "result": None,
        "errors": [
            {
                "code": code,
                "path": "workspace",
                "message": message,
                "expected": "exactly one current case",
                "received": len(candidates),
                "recoverable": True,
            }
        ],
        "warnings": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    operation = _operation_hint(raw_argv)
    try:
        args = _parser().parse_args(raw_argv)
        operation = args.operation
        workspace_root = _validate_workspace_root(args.root)
        tool = CaseArchive(workspace_root)
        if args.operation == "create":
            request = {
                key: value
                for key, value in {
                    "case_label": args.case_label,
                    "jurisdiction": args.jurisdiction,
                    "initial_goal": args.initial_goal,
                }.items()
                if value is not None
            }
            result = tool.create(request)
        elif args.operation == "read":
            result = tool.read(args.case_id)
        elif args.operation == "read-current":
            result = _read_current(tool, workspace_root)
        else:
            result = tool.commit(_commit_request(args, workspace_root))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok") is True else 2
    except SystemExit as error:
        if error.code == 0:
            return 0
        if operation is None:
            return int(error.code) if isinstance(error.code, int) else 2
        result = _input_error(operation, error)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        if operation is None:
            return 2
        result = _input_error(operation, error)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
