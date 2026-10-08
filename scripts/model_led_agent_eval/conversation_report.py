"""将 Journey 证据或 WorkBuddy 原生 transcript 转为可人工阅读的逐轮记录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


_USER_EVENTS = {"user_message", "user_turn", "user_round", "user_input"}
_ASSISTANT_EVENTS = {"assistant_message", "assistant_output", "model_output"}
_TOOL_EVENTS = {
    "function_call", "function_call_result", "function_result", "tool_call",
    "tool_use", "tool_result", "tool_response", "skill_call", "call_tool",
}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            return value["text"].strip()
        for key in ("content", "parts", "message"):
            result = _text(value.get(key))
            if result:
                return result
    return ""


def _block(role: str, content: object) -> list[str]:
    return [f"**{role}**", "", _text(content) or "（空消息）", ""]


def render_evidence_report(evidence: Mapping[str, Any], *, source: Path | None = None) -> str:
    """只展示用户可见对话和少量定位字段；完整机器证据仍在 JSON 中。"""

    journey = str(evidence.get("journey_id") or "未知")
    platform = str(evidence.get("platform") or "未知")
    attribution = evidence.get("attribution")
    status = attribution.get("category") if isinstance(attribution, Mapping) else "未知"
    lines = [
        f"# Journey 会话记录：{journey} / {platform}",
        "",
        f"- 场景：{journey}",
        f"- 客户端：{platform}",
        f"- 结果：{status}",
        f"- 开始：{evidence.get('started_at') or '未知'}",
        f"- 结束：{evidence.get('finished_at') or '未知'}",
    ]
    if source is not None:
        lines.append(f"- 原始证据：{source.resolve()}")
    lines.extend(["", "## 逐轮对话", ""])
    transcript = evidence.get("transcript")
    if not isinstance(transcript, list):
        transcript = []
    completed_turn_numbers: set[int] = set()
    for position, turn in enumerate(transcript, start=1):
        if not isinstance(turn, Mapping):
            continue
        number = turn.get("turn") if isinstance(turn.get("turn"), int) else position
        if isinstance(number, int) and not isinstance(number, bool):
            completed_turn_numbers.add(number)
        lines.extend([f"### 第 {number} 轮", ""])
        session = turn.get("session_id")
        if isinstance(session, str) and session:
            lines.extend([f"会话：{session}", ""])
        lines.extend(_block("用户", turn.get("user_message")))
        lines.extend(_block("助手", turn.get("assistant_response")))
        selected = turn.get("selected_fact_ids")
        if isinstance(selected, list) and selected:
            lines.extend([f"场景事实 ID：{', '.join(str(item) for item in selected)}", ""])
    if not transcript:
        lines.extend(["（没有完成的对话轮次）", ""])
    events = evidence.get("scenario_responder")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            turn_number = event.get("turn")
            if event.get("action") == "stop":
                lines.extend([
                    f"### 第 {turn_number if turn_number is not None else '?'} 轮未送达用户",
                    "",
                    f"场景回复器停止：{event.get('result_code') or event.get('status') or '未知'}",
                    "",
                ])
                continue
            if (
                event.get("status") != "accepted"
                or event.get("action") not in {"answer", "unavailable"}
                or not isinstance(turn_number, int)
                or isinstance(turn_number, bool)
                or turn_number in completed_turn_numbers
            ):
                continue
            lines.extend([f"### 第 {turn_number} 轮（助手响应未完成）", ""])
            rendered_message = event.get("rendered_message")
            if isinstance(rendered_message, str) and rendered_message.strip():
                lines.extend(_block("用户", rendered_message))
            else:
                lines.extend(["场景回复已接受，但证据未保留原文；请查看 evidence.json。", ""])
            selected = event.get("selected_fact_ids")
            if isinstance(selected, list) and selected:
                lines.extend([f"场景事实 ID：{', '.join(str(item) for item in selected)}", ""])
    if isinstance(attribution, Mapping) and attribution.get("message"):
        lines.extend(["## 失败说明", "", str(attribution["message"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def render_workbuddy_report(
    records: list[Mapping[str, Any]], *, journey_id: str, source: Path | None = None
) -> str:
    """按原生事件顺序展示 WorkBuddy 用户与助手消息，不推测缺失的轮次。"""

    lines = [
        f"# Journey 会话记录：{journey_id} / WorkBuddy",
        "",
        f"- 场景：{journey_id}",
        "- 客户端：WorkBuddy",
        "- 结果：待验收器判定",
    ]
    if source is not None:
        lines.append(f"- 原始记录：{source.resolve()}")
    lines.extend(["", "## 逐轮对话", ""])
    turn = 0
    session_number = 0
    last_session: str | None = None
    visible = 0
    for record in records:
        session = next(
            (record.get(key) for key in ("sessionId", "session_id", "session")
             if isinstance(record.get(key), str) and record.get(key)), None
        )
        event = next(
            (record.get(key) for key in ("type", "event", "kind")
             if isinstance(record.get(key), str)), ""
        ).casefold().replace("-", "_")
        if event in _TOOL_EVENTS:
            continue
        role = str(record.get("role") or "").casefold()
        if role == "user" or event in _USER_EVENTS:
            speaker = "用户"
        elif role in {"assistant", "model"} or event in _ASSISTANT_EVENTS:
            speaker = "助手"
        else:
            continue
        content = _text(record.get("content") or record.get("message") or record.get("text"))
        if not content:
            continue
        if session != last_session:
            session_number += 1
            last_session = session
            lines.extend([f"## 会话 {session_number}" + (f"（{session}）" if session else ""), ""])
        if speaker == "用户":
            turn += 1
            lines.extend([f"### 第 {turn} 轮", ""])
        elif turn == 0:
            lines.extend(["### 第 0 轮（缺少用户消息）", ""])
        lines.extend(_block(speaker, content))
        visible += 1
    if not visible:
        raise ValueError("transcript 中没有可展示的用户或助手消息")
    return "\n".join(lines).rstrip() + "\n"


def write_evidence_report(evidence: Mapping[str, Any], evidence_path: Path) -> Path:
    report_path = evidence_path.with_name("conversation.md")
    report_path.write_text(
        render_evidence_report(evidence, source=evidence_path), encoding="utf-8"
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成逐轮 Journey 会话记录")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--workbuddy-transcript", type=Path)
    parser.add_argument("--journey-id")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if bool(args.evidence) == bool(args.workbuddy_transcript):
        parser.error("必须且只能提供 --evidence 或 --workbuddy-transcript")
    if args.evidence:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            parser.error("evidence 顶层必须是对象")
        report = render_evidence_report(evidence, source=args.evidence)
    else:
        if not args.journey_id:
            parser.error("WorkBuddy 报告必须提供 --journey-id")
        records = [
            json.loads(line) for line in args.workbuddy_transcript.read_text(
                encoding="utf-8"
            ).splitlines() if line.strip()
        ]
        if any(not isinstance(item, dict) for item in records):
            parser.error("transcript 每行必须是对象")
        report = render_workbuddy_report(
            records, journey_id=args.journey_id, source=args.workbuddy_transcript
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
