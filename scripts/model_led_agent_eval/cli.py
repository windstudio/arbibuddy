"""显式运行 Codex/Claude Code 模型主导 Agent Journey。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from scripts.cli_encoding import configure_utf8_stdio

from . import (
    DEFAULT_SIMULATOR_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_PER_TURN_SECONDS,
    JourneyError,
    JourneyProgress,
    SUPPORTED_PLATFORMS,
    _current_shanghai_date,
    load_journey,
    run_journey_with_retries,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arbibuddy-model-led-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="运行一条模型主导 Journey")
    run.add_argument("journey", type=Path)
    _common(run)
    suite = subparsers.add_parser("suite", help="运行三个 Journey 的完整平台套件")
    suite.add_argument("suite", type=Path)
    suite.add_argument(
        "--platform",
        choices=(*SUPPORTED_PLATFORMS, "all"),
        default="all",
    )
    suite.add_argument("--codex-command", nargs="+")
    suite.add_argument("--claude-code-command", nargs="+")
    suite.add_argument("--codex-model")
    suite.add_argument("--claude-model")
    _common(suite, include_platform=False)
    return parser


def _common(parser: argparse.ArgumentParser, *, include_platform: bool = True) -> None:
    if include_platform:
        parser.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
        parser.add_argument("--platform-command", nargs="+")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument(
        "--timeout-per-turn",
        type=int,
        default=DEFAULT_TIMEOUT_PER_TURN_SECONDS,
        help="模型 A 每轮上限（秒），默认 1200；模型 B 单次调用另计",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="失败后重跑次数；用于区分模型/环境波动，默认 1",
    )
    parser.add_argument("--model")
    parser.add_argument(
        "--responder",
        choices=("model", "rule", "replay"),
        default="model",
        help="ScenarioResponder 模式；rule 仅为显式 legacy 基线",
    )
    parser.add_argument(
        "--simulator-provider",
        choices=("codex-sdk",),
        default="codex-sdk",
    )
    parser.add_argument("--simulator-model")
    parser.add_argument(
        "--simulator-codex-bin",
        type=Path,
        help="显式覆盖模型 B 的 Codex runtime；省略时使用 SDK pinned runtime",
    )
    parser.add_argument(
        "--simulator-timeout",
        type=int,
        default=DEFAULT_SIMULATOR_TIMEOUT_SECONDS,
        help="模型 B 单次调用上限（秒），默认 300",
    )
    parser.add_argument("--simulator-replay", type=Path)
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        help="固定相对日期场景的基准日（YYYY-MM-DD）；默认运行日，证据记录实际日期",
    )
    parser.add_argument("--keep-workspace-on-failure", action="store_true")
    parser.add_argument(
        "--retain-deliveries",
        action="store_true",
        help="虚构场景中保留已验证 DOCX 与清单副本，供宿主展示核验",
    )
    parser.add_argument("--json", action="store_true")


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    args = _parser().parse_args(argv)
    source = args.source.resolve()
    temp_root = (
        args.temp_root.resolve()
        if args.temp_root is not None
        else source.parent / ".arbibuddy-model-led-eval"
    )
    try:
        scenario_as_of_date = args.as_of_date or _current_shanghai_date()
        if args.command == "run":
            journey = load_journey(
                _resolve_path(args.journey, source), as_of_date=scenario_as_of_date
            )
            results = [
                run_journey_with_retries(
                    journey,
                    platform=args.platform,
                    source_root=source,
                    temp_root=temp_root,
                    platform_command=args.platform_command,
                    model=args.model,
                    responder_mode=args.responder,
                    simulator_provider=args.simulator_provider,
                    simulator_model=args.simulator_model,
                    simulator_codex_bin=args.simulator_codex_bin,
                    simulator_timeout_seconds=args.simulator_timeout,
                    simulator_replay=_replay_path(args.simulator_replay, source),
                    timeout_per_turn_seconds=args.timeout_per_turn,
                    keep_workspace_on_failure=args.keep_workspace_on_failure,
                    retain_deliveries=args.retain_deliveries,
                    on_progress=None if args.json else _print_progress,
                    retries=args.retries,
                )
            ]
        else:
            suite = _load_suite(_resolve_path(args.suite, source))
            platforms = (
                list(SUPPORTED_PLATFORMS)
                if args.platform == "all"
                else [args.platform]
            )
            results = []
            for platform in platforms:
                command = (
                    args.codex_command
                    if platform == "codex"
                    else args.claude_code_command
                )
                for journey_path in suite:
                    journey = load_journey(
                        _resolve_path(journey_path, source), as_of_date=scenario_as_of_date
                    )
                    if not args.json:
                        print(
                            f"[{platform}][{journey.id}] START elapsed=0.0s",
                            flush=True,
                        )
                    results.append(
                        run_journey_with_retries(
                            journey,
                            platform=platform,
                            source_root=source,
                            temp_root=temp_root,
                            platform_command=command,
                            model=_suite_model(args, platform),
                            responder_mode=args.responder,
                            simulator_provider=args.simulator_provider,
                            simulator_model=args.simulator_model,
                            simulator_codex_bin=args.simulator_codex_bin,
                            simulator_timeout_seconds=args.simulator_timeout,
                            simulator_replay=_replay_path(args.simulator_replay, source),
                            timeout_per_turn_seconds=args.timeout_per_turn,
                            keep_workspace_on_failure=args.keep_workspace_on_failure,
                            retain_deliveries=args.retain_deliveries,
                            on_progress=None if args.json else _print_progress,
                            retries=args.retries,
                        )
                    )
        payload = [_result_payload(result) for result in results]
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for result in results:
                state = "PASS" if result.passed else "FAIL"
                print(
                    f"[{result.platform}][{result.journey_id}] {state} "
                    f"turns={result.turns} elapsed={result.duration_seconds:.1f}s "
                    f"evidence={result.evidence_path} "
                    f"conversation={result.evidence_path.with_name('conversation.md')}",
                    flush=True,
                )
                if not result.passed:
                    print(
                        f"failure_category={result.failure_category} "
                        f"failure={result.failure_message}",
                        flush=True,
                    )
        return 0 if all(result.passed for result in results) else 1
    except (JourneyError, OSError, ValueError) as error:
        print(f"模型主导 Agent Eval 失败：{error}", file=sys.stderr)
        return 2


def _resolve_path(path: Path, source: Path) -> Path:
    if path.is_absolute():
        return path
    candidate = Path.cwd() / path
    if candidate.is_file():
        return candidate
    return source / path


def _replay_path(path: Path | None, source: Path) -> Path | None:
    if path is None:
        return None
    return _resolve_path(path, source)


def _suite_model(args: argparse.Namespace, platform: str) -> str | None:
    """为 suite 按平台选择模型，避免一个 --model 同时代表两个客户端。"""

    codex_model = getattr(args, "codex_model", None)
    claude_model = getattr(args, "claude_model", None)
    overrides = {"codex": codex_model, "claude-code": claude_model}
    if args.platform == "all":
        if args.model is not None:
            raise JourneyError(
                "suite --platform all 不接受共享 --model；请使用 --codex-model 和/或 --claude-model"
            )
        return overrides[platform]
    selected_override = overrides[platform]
    other_platform = "claude-code" if platform == "codex" else "codex"
    other_override = overrides[other_platform]
    if args.model is not None and selected_override is not None:
        raise JourneyError(
            "suite 单平台不能同时使用 --model 与对应的平台模型覆盖"
        )
    if args.model is not None and other_override is not None:
        raise JourneyError("suite 指定的平台模型覆盖与 --model 不匹配")
    if other_override is not None:
        raise JourneyError("suite 不能为未选择的平台指定模型覆盖")
    return selected_override if selected_override is not None else args.model


def _load_suite(path: Path) -> list[Path]:
    try:
        import yaml
    except ImportError as error:
        raise JourneyError("读取模型主导 Suite 需要 PyYAML") from error
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise JourneyError(f"Suite 无法读取：{path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise JourneyError("Suite schema_version 必须是 1")
    journeys = value.get("journeys")
    if not isinstance(journeys, list) or not journeys or not all(
        isinstance(item, str) and item.strip() for item in journeys
    ):
        raise JourneyError("Suite journeys 必须是非空路径列表")
    return [Path(item) for item in journeys]


def _print_progress(progress: JourneyProgress) -> None:
    print(
        f"[{progress.platform}][{progress.journey_id}] "
        f"TURN {progress.turn:02d} {progress.phase} "
        f"elapsed={progress.elapsed_seconds:.1f}s",
        flush=True,
    )


def _result_payload(result) -> dict[str, Any]:
    return {
        "journey_id": result.journey_id,
        "platform": result.platform,
        "passed": result.passed,
        "failure_category": result.failure_category,
        "failure_message": result.failure_message,
        "turns": result.turns,
        "duration_seconds": result.duration_seconds,
        "observation": result.observation,
        "evidence_path": str(result.evidence_path),
        "conversation_path": str(result.evidence_path.with_name("conversation.md")),
    }


if __name__ == "__main__":
    raise SystemExit(main())
