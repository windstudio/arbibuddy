"""WorkBuddy-safe transport for the public ``amount.calculate`` contract.

The calculator API remains :mod:`scripts.amount_calculator.public`.  This
module only carries one JSON request to that API and consumes any file-backed
request before calculation so the case workspace keeps no second fact source.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from scripts.amount_calculator.public import calculate, describe_contract
from scripts.cli_encoding import configure_utf8_stdio


MAX_RUNTIME_INPUT_BYTES = 2 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ArbiBuddy WorkBuddy 金额计算公共 transport"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("describe", help="读取公开金额计算契约")
    command = commands.add_parser("calculate", help="执行 amount.calculate-v1")
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", dest="request_json")
    source.add_argument("--input", type=Path)
    return parser


def _error(code: str, message: str, *, received: object = None) -> dict[str, Any]:
    return {
        "contract_version": "amount.runtime-transport-v1",
        "operation": "amount.runtime-transport",
        "ok": False,
        "result": None,
        "errors": [
            {
                "code": code,
                "path": "request",
                "message": message,
                "expected": "amount.calculate-v1 request JSON",
                "received": received,
                "recoverable": True,
            }
        ],
        "warnings": [],
    }


def _workspace_root(value: Path) -> Path:
    root = value.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("workspace_not_found")
    return root


def _consume_runtime_input(path: Path, workspace: Path) -> str:
    runtime_root = (workspace / ".arbibuddy" / "runtime-input").resolve()
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.resolve()
    if (
        candidate.parent != runtime_root
        or candidate.suffix.casefold() != ".json"
        or not candidate.is_file()
    ):
        raise ValueError("invalid_runtime_input_path")
    if candidate.stat().st_size > MAX_RUNTIME_INPUT_BYTES:
        raise OverflowError("runtime_input_too_large")
    payload = candidate.read_text(encoding="utf-8-sig")
    candidate.unlink()
    try:
        runtime_root.rmdir()
    except OSError:
        pass
    return payload


def _request(args: argparse.Namespace, workspace: Path) -> Mapping[str, Any]:
    raw = (
        args.request_json
        if args.request_json is not None
        else _consume_runtime_input(args.input, workspace)
    )
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise TypeError("request_must_be_object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        workspace = _workspace_root(args.workspace)
        if args.operation == "describe":
            result: dict[str, Any] = {
                "contract_version": "amount.runtime-transport-v1",
                "operation": "amount.runtime-transport.describe",
                "ok": True,
                "result": describe_contract(),
                "errors": [],
                "warnings": [],
            }
        else:
            result = calculate(_request(args, workspace))
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2
    except ValueError as error:
        code = (
            "invalid_runtime_input_path"
            if str(error) == "invalid_runtime_input_path"
            else "invalid_runtime_request"
        )
        result = _error(code, "金额请求无法读取", received=error.__class__.__name__)
    except OverflowError as error:
        result = _error(
            "runtime_input_too_large",
            "金额请求超过公开 transport 的大小上限",
            received=str(error),
        )
    except (OSError, TypeError, json.JSONDecodeError) as error:
        result = _error(
            "invalid_runtime_request",
            "金额请求无法解析",
            received=error.__class__.__name__,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["MAX_RUNTIME_INPUT_BYTES", "main"]
