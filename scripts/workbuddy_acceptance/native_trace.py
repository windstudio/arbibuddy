"""只读适配 WorkBuddy 原生 ``{spans, trace}`` 轨迹。

WorkBuddy 的 SDK 轨迹不是仓库旧版验收器使用的 JSONL。这个模块只把验收
需要的结构化事件投影为内部记录，不保存或回显聊天正文、完整命令和工具
结果原文；重复的 ``traceId + spanId`` 只计一次。
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MAX_NATIVE_TRACE_FILE_BYTES = 20 * 1024 * 1024
_FUNCTION_EVENTS = frozenset({
    "function",
    "function_call",
    "function_span",
    "tool",
    "tool_call",
})
_USER_EVENTS = frozenset({
    "user_turn",
    "user_message",
    "user_round",
    "turn_boundary",
})
_TURN_END_EVENTS = frozenset({
    "turn_end",
    "end_turn",
    "user_turn_end",
    "round_end",
    "turn_completed",
    "response_completed",
    "run_completed",
    "message_stop",
    "assistant_turn_end",
})
_RENDER_NAMES = frozenset({
    "document.render",
    "document.render-v1",
    "scripts.documents.public.render",
})


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _fold(value: object) -> str:
    return _text(value).casefold().replace("-", "_").replace(" ", "_")


def _first(mapping: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _trace_id(top_trace: Mapping[str, Any], span: Mapping[str, Any]) -> str | None:
    value = _first(span, "traceId", "trace_id", "traceID")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = _first(top_trace, "traceId", "trace_id", "traceID")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _session_id(top_trace: Mapping[str, Any], span: Mapping[str, Any]) -> str | None:
    value = _first(span, "sessionId", "session_id", "session")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = _first(top_trace, "sessionId", "session_id", "session")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _span_id(span: Mapping[str, Any]) -> str | None:
    value = _first(span, "spanId", "span_id", "spanID", "id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parent_id(span: Mapping[str, Any]) -> str | None:
    value = _first(span, "parentId", "parent_id", "parentSpanId", "parent_span_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _timestamp(span: Mapping[str, Any]) -> str | int | float:
    value = _first(
        span,
        "startedAt",
        "startTime",
        "start_time",
        "timestamp",
        "time",
    )
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        return value
    return ""


def _decode_payload(
    value: object,
    *,
    field: str,
    diagnostics: list[dict[str, Any]],
    source: Path,
) -> object:
    if isinstance(value, (Mapping, list)):
        return value
    if value is None:
        return None
    if not isinstance(value, str):
        diagnostics.append({
            "code": "native_trace_tool_payload_unparseable",
            "field": field,
            "source": source.name,
        })
        return {"_native_payload_unparseable": True, "field": field}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        diagnostics.append({
            "code": "native_trace_tool_payload_unparseable",
            "field": field,
            "source": source.name,
        })
        return {"_native_payload_unparseable": True, "field": field}


def _tool_name(span: Mapping[str, Any]) -> str | None:
    value = _first(span, "toolName", "tool_name", "tool", "functionName", "function_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _span_kind(span: Mapping[str, Any]) -> str:
    value = _first(span, "kind", "spanType", "span_type", "type", "event", "name")
    return _fold(value)


def _event_records(
    *,
    span: Mapping[str, Any],
    top_trace: Mapping[str, Any],
    source: Path,
    diagnostics: list[dict[str, Any]],
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    trace_id = _trace_id(top_trace, span)
    session_id = _session_id(top_trace, span)
    span_id = _span_id(span)
    tool_name = _tool_name(span)
    kind = _span_kind(span)
    if not trace_id or not span_id or not session_id:
        diagnostics.append({
            "code": "native_trace_span_identity_missing",
            "source": source.name,
        })
        return session_id, span_id, []

    parent_id = _parent_id(span)
    timestamp = _timestamp(span)
    status = _first(span, "status", "state", "outcome")
    result: list[dict[str, Any]] = []
    input_present = "toolInput" in span or "tool_input" in span or "input" in span
    output_present = "toolOutput" in span or "tool_output" in span or "output" in span
    if tool_name is not None and (kind in _FUNCTION_EVENTS or input_present or output_present):
        arguments = _decode_payload(
            _first(span, "toolInput", "tool_input", "input"),
            field="toolInput",
            diagnostics=diagnostics,
            source=source,
        )
        result.append({
            "sessionId": session_id,
            "traceId": trace_id,
            "spanId": span_id,
            "parentId": parent_id,
            "timestamp": timestamp,
            "type": "function_call",
            "event": "function_call",
            "id": span_id,
            "name": tool_name,
            "tool_name": tool_name,
            "arguments": arguments if isinstance(arguments, Mapping) else {},
            "status": status,
            "native_trace": True,
        })
        if output_present:
            output = _decode_payload(
                _first(span, "toolOutput", "tool_output", "output"),
                field="toolOutput",
                diagnostics=diagnostics,
                source=source,
            )
            result.append({
                "sessionId": session_id,
                "traceId": trace_id,
                "spanId": span_id,
                "parentId": parent_id,
                "timestamp": _first(span, "endedAt", "endTime", "end_time") or timestamp,
                "type": "function_call_result",
                "event": "function_call_result",
                "call_id": span_id,
                "name": tool_name,
                "result": output,
                "status": status,
                "native_trace": True,
            })
        return session_id, span_id, result

    event = _fold(_first(span, "event", "type", "kind", "name"))
    if event in _USER_EVENTS or event in _TURN_END_EVENTS:
        result.append({
            "sessionId": session_id,
            "traceId": trace_id,
            "spanId": span_id,
            "parentId": parent_id,
            "timestamp": timestamp,
            "type": event,
            "event": event,
            "native_trace": True,
        })
    return session_id, span_id, result


def _sort_key(record: Mapping[str, Any]) -> tuple[str, int, str, str, str]:
    timestamp = record.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        normalized_time = f"{timestamp:030.6f}"
    else:
        normalized_time = str(timestamp or "")
    event = str(record.get("event") or "")
    event_rank = 0 if event == "function_call" else 1
    return (
        normalized_time,
        event_rank,
        str(record.get("parentId") or ""),
        str(record.get("spanId") or ""),
        str(record.get("call_id") or record.get("id") or ""),
    )


def _iter_trace_files(root: Path, include_files: Sequence[str] | None) -> list[Path]:
    root = root.resolve()
    if include_files is not None:
        result = []
        for name in include_files:
            if not isinstance(name, str) or not name.strip():
                continue
            raw_candidate = root / name
            if raw_candidate.is_symlink():
                continue
            candidate = raw_candidate.resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if (
                not candidate.is_symlink()
                and candidate.is_file()
                and candidate.suffix.casefold() == ".json"
            ):
                result.append(candidate)
        return sorted(dict.fromkeys(result), key=lambda path: path.as_posix().casefold())
    return sorted(
        (
            path
            for path in root.rglob("*.json")
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(root)
        ),
        key=lambda path: path.as_posix().casefold(),
    )


def _count_session(records: Iterable[Mapping[str, Any]], session_id: str) -> dict[str, Any]:
    selected = [item for item in records if item.get("sessionId") == session_id]
    calls = [item for item in selected if item.get("event") == "function_call"]
    names = [str(item.get("tool_name") or "").casefold() for item in calls]
    present_counts = []
    for item in calls:
        if item.get("tool_name") == "present_files":
            arguments = item.get("arguments")
            files = arguments.get("files") if isinstance(arguments, Mapping) else None
            if isinstance(files, list):
                present_counts.append(len(files))
    return {
        "session_id": session_id,
        "record_count": len(selected),
        "function_calls": len(calls),
        "skill_calls": sum(name in {"skill", "skill(arbibuddy)"} for name in names),
        "render_calls": sum(name in _RENDER_NAMES for name in names),
        "present_files_calls": names.count("present_files"),
        "present_file_counts": present_counts,
        "case_archive_operations": sum("casearchive" in name or "case_archive" in name for name in names),
    }


def adapt_native_traces(
    trace_root: str | Path,
    *,
    session_ids: Sequence[str] = (),
    include_files: Sequence[str] | None = None,
) -> dict[str, Any]:
    """读取并规范化原生轨迹，返回可供验收器消费的内部证据。

    解析失败不会被当成空输入。返回的 ``diagnostic_codes`` 是稳定的、不会
    包含命令参数或聊天正文的诊断；调用方应在存在诊断时 fail closed。
    """

    root = Path(trace_root).expanduser().resolve()
    diagnostics: list[dict[str, Any]] = []
    requested = [item for item in session_ids if isinstance(item, str) and item.strip()]
    if any(not isinstance(item, str) or not item.strip() for item in session_ids):
        diagnostics.append({"code": "native_trace_session_invalid"})
    records: list[dict[str, Any]] = []
    trace_files: list[dict[str, Any]] = []
    seen_spans: set[tuple[str, str]] = set()
    duplicate_spans_removed = 0
    if not root.is_dir():
        diagnostics.append({"code": "native_trace_root_missing"})
    if include_files is not None and root.is_dir():
        for name in include_files:
            if not isinstance(name, str) or not name.strip():
                diagnostics.append({"code": "native_trace_file_invalid"})
                continue
            raw_candidate = root / name
            candidate = raw_candidate.resolve()
            if (
                raw_candidate.is_symlink()
                or not candidate.is_relative_to(root)
                or not candidate.is_file()
                or candidate.suffix.casefold() != ".json"
            ):
                diagnostics.append({"code": "native_trace_file_missing"})
    for path in _iter_trace_files(root, include_files) if root.is_dir() else []:
        relative = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            diagnostics.append({"code": "native_trace_file_unreadable", "source": relative})
            continue
        if size > MAX_NATIVE_TRACE_FILE_BYTES:
            diagnostics.append({"code": "native_trace_file_too_large", "source": relative})
            continue
        try:
            raw_bytes = path.read_bytes()
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            diagnostics.append({"code": "native_trace_file_invalid", "source": relative})
            continue
        if not isinstance(payload, Mapping):
            diagnostics.append({"code": "native_trace_schema_invalid", "source": relative})
            continue
        if set(payload) != {"spans", "trace"}:
            diagnostics.append({"code": "native_trace_schema_invalid", "source": relative})
            continue
        top_trace = payload.get("trace")
        spans = payload.get("spans")
        if not isinstance(top_trace, Mapping) or not isinstance(spans, list):
            diagnostics.append({"code": "native_trace_schema_invalid", "source": relative})
            continue
        file_span_count = 0
        file_sessions: set[str] = set()
        for raw_span in spans:
            if not isinstance(raw_span, Mapping):
                diagnostics.append({"code": "native_trace_span_invalid", "source": relative})
                continue
            candidate_session = _session_id(top_trace, raw_span)
            if requested and candidate_session not in requested:
                continue
            current_session, current_span, projected = _event_records(
                span=raw_span,
                top_trace=top_trace,
                source=path,
                diagnostics=diagnostics,
            )
            if requested and current_session is None:
                continue
            if current_session is None or current_span is None:
                continue
            file_sessions.add(current_session)
            key = (
                str(_trace_id(top_trace, raw_span)),
                str(current_span),
            )
            if key in seen_spans:
                duplicate_spans_removed += 1
                continue
            seen_spans.add(key)
            records.extend(projected)
            file_span_count += 1
        if file_sessions:
            trace_files.append({
                "path": relative,
                "sha256": sha256(raw_bytes).hexdigest(),
                "span_count": file_span_count,
                "session_ids": sorted(file_sessions),
            })

    records.sort(key=_sort_key)
    found_sessions = sorted(
        {str(item.get("sessionId")) for item in records if item.get("sessionId")},
        key=lambda value: (requested.index(value) if value in requested else len(requested), value),
    )
    if requested:
        missing = [item for item in requested if item not in found_sessions]
        if missing:
            diagnostics.append({"code": "native_trace_session_missing", "count": len(missing)})
    if root.is_dir() and not trace_files:
        diagnostics.append({"code": "native_trace_files_missing"})
    if root.is_dir() and not records:
        diagnostics.append({"code": "native_trace_events_missing"})
    selected_sessions = requested or found_sessions
    sessions = [_count_session(records, session_id) for session_id in selected_sessions]
    summary = {
        "record_count": len(records),
        "function_calls": sum(item["function_calls"] for item in sessions),
        "skill_calls": sum(item["skill_calls"] for item in sessions),
        "render_calls": sum(item["render_calls"] for item in sessions),
        "present_files_calls": sum(item["present_files_calls"] for item in sessions),
        "present_file_counts": [
            count
            for item in sessions
            for count in item["present_file_counts"]
        ],
        "duplicate_spans_removed": duplicate_spans_removed,
    }
    return {
        "schema_version": 1,
        "trace_root": str(root),
        "session_ids": list(selected_sessions),
        "trace_files": trace_files,
        "records": records,
        "sessions": sessions,
        "summary": summary,
        "diagnostics": diagnostics,
        "diagnostic_codes": sorted({item["code"] for item in diagnostics}),
        "valid": not diagnostics,
    }


__all__ = [
    "MAX_NATIVE_TRACE_FILE_BYTES",
    "adapt_native_traces",
]
