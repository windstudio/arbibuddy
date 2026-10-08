"""WorkBuddy-safe transport for the public legal-verification contract.

The agent still owns browsing and legal judgment.  This module only validates
one model observation and, for ``record``, commits the resulting authority
record through the public :class:`scripts.case_archive.CaseArchive` seam.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from scripts.case_archive import CaseArchive
from scripts.cli_encoding import configure_utf8_stdio
from scripts.legal_verification import LegalVerificationTracer


MAX_RUNTIME_INPUT_BYTES = 2 * 1024 * 1024
_TRANSPORT_VERSION = "legal-verification.runtime-transport-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ArbiBuddy WorkBuddy 法律核验公共 transport"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("describe", help="读取公开动态核验 transport 契约")
    for operation in ("assess", "record"):
        command = commands.add_parser(operation)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--json", dest="request_json")
        source.add_argument("--input", type=Path)
    return parser


def _error(code: str, message: str, *, received: object = None) -> dict[str, Any]:
    return {
        "contract_version": _TRANSPORT_VERSION,
        "operation": "legal-verification.runtime-transport",
        "ok": False,
        "result": None,
        "errors": [
            {
                "code": code,
                "path": "request",
                "message": message,
                "expected": "public legal-verification request JSON",
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


def _describe() -> dict[str, Any]:
    return {
        "contract_version": _TRANSPORT_VERSION,
        "operation": "legal-verification.runtime-transport.describe",
        "ok": True,
        "result": {
            "operations": ["assess", "record"],
            "network_access": False,
            "assess_request": "legal-verification-v2 observation",
            "record_request": {
                "required": ["case_id", "expected_revision", "observation"],
                "optional": ["change_summary"],
            },
        },
        "errors": [],
        "warnings": [],
    }


def _record(
    tracer: LegalVerificationTracer,
    workspace: Path,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = {"case_id", "expected_revision", "observation", "change_summary"}
    if set(request) - allowed:
        raise TypeError("record_request_has_unknown_fields")
    case_id = request.get("case_id")
    expected_revision = request.get("expected_revision")
    observation = request.get("observation")
    change_summary = request.get("change_summary", "记录一次最小法律核验结果")
    if not isinstance(case_id, str) or not case_id.strip():
        raise TypeError("case_id_required")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise TypeError("expected_revision_invalid")
    if not isinstance(observation, Mapping):
        raise TypeError("observation_required")
    if not isinstance(change_summary, str) or not change_summary.strip():
        raise TypeError("change_summary_invalid")
    return tracer.record(
        CaseArchive(workspace),
        case_id=case_id,
        expected_revision=expected_revision,
        observation=observation,
        change_summary=change_summary,
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        workspace = _workspace_root(args.workspace)
        if args.operation == "describe":
            result = _describe()
        else:
            tracer = LegalVerificationTracer(Path(__file__).resolve().parents[2])
            request = _request(args, workspace)
            result = (
                tracer.assess(request)
                if args.operation == "assess"
                else _record(tracer, workspace, request)
            )
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2
    except ValueError as error:
        code = (
            "invalid_runtime_input_path"
            if str(error) == "invalid_runtime_input_path"
            else "invalid_runtime_request"
        )
        result = _error(code, "法律核验请求无法读取", received=error.__class__.__name__)
    except OverflowError as error:
        result = _error(
            "runtime_input_too_large",
            "法律核验请求超过公开 transport 的大小上限",
            received=str(error),
        )
    except (OSError, TypeError, json.JSONDecodeError) as error:
        result = _error(
            "invalid_runtime_request",
            "法律核验请求无法解析",
            received=error.__class__.__name__,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["MAX_RUNTIME_INPUT_BYTES", "main"]
