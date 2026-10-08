from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.cli_encoding import configure_utf8_stdio
from scripts.workbuddy_acceptance.service import (
    audit_workspace,
    build_no_delivery_contract,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验收 WorkBuddy 真实客户端文书交付事务")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("build-no-delivery-contract",),
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--journey")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit-log", type=Path)
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument(
        "--transcript-jsonl",
        type=Path,
        help="只读诊断结构化工具调用元数据，并检查助手公开文本的内部实现泄漏；不回显聊天正文",
    )
    parser.add_argument(
        "--client-log",
        type=Path,
        help="只读取客户端 Skill 生命周期标记和宿主噪声分类；不回显日志正文",
    )
    parser.add_argument(
        "--require-memory-isolation",
        action="store_true",
        help="要求宿主提供 memory 事前禁写或案件隔离证明；缺失时 fail closed",
    )
    parser.add_argument(
        "--workbuddy-trace-root",
        type=Path,
        help="只读读取 WorkBuddy 原生 {spans, trace} JSON 轨迹根目录",
    )
    parser.add_argument(
        "--journey-contract",
        type=Path,
        help="ML03-v2 原生 trace Journey contract；等同于 --no-delivery-contract",
    )
    parser.add_argument(
        "--allow-no-delivery",
        action="store_true",
        help="仅用于有 ML03-v2 contract、且没有文书输出/调用的模型主导 Journey",
    )
    parser.add_argument(
        "--no-delivery-contract",
        type=Path,
        help="--allow-no-delivery 使用的结构化 Journey contract JSON",
    )
    parser.add_argument(
        "--expected-delivery",
        action="append",
        default=[],
        help="声明本次诊断预期文书；必须与公开交付集合一致",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "build-no-delivery-contract":
        if not args.journey or not args.case_id or not args.output:
            raise SystemExit(
                "build-no-delivery-contract 需要 --journey、--case-id 和 --output，"
                "并提供 --transcript-jsonl；ML03-v2 原生 trace 还需 --workbuddy-trace-root"
            )
        if not args.transcript_jsonl:
            raise SystemExit(
                "build-no-delivery-contract 必须提供 --transcript-jsonl"
            )
        if args.workbuddy_trace_root and not args.session_id:
            raise SystemExit(
                "ML03-v2 原生 trace contract 还必须提供两个 --session-id"
            )
        contract = build_no_delivery_contract(
            journey=args.journey,
            case_id=args.case_id,
            transcript_jsonl=args.transcript_jsonl,
            output=args.output,
            workbuddy_trace_root=args.workbuddy_trace_root,
            session_ids=tuple(args.session_id),
        )
        print(json.dumps({
            "contract": contract,
            "contract_path": str(args.output.resolve()),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.workspace is None or args.case_id is None:
        raise SystemExit("验收命令需要 --workspace 和 --case-id")
    session_id = args.session_id[0] if args.session_id else None
    report = audit_workspace(
        workspace=args.workspace,
        case_id=args.case_id,
        expected_deliveries=tuple(args.expected_delivery),
        audit_log=args.audit_log,
        session_id=session_id,
        transcript_jsonl=args.transcript_jsonl,
        client_log=args.client_log,
        workbuddy_trace_root=args.workbuddy_trace_root,
        session_ids=tuple(args.session_id),
        require_memory_isolation=args.require_memory_isolation,
        allow_no_delivery=args.allow_no_delivery,
        no_delivery_contract=args.no_delivery_contract,
        journey_contract=args.journey_contract,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
