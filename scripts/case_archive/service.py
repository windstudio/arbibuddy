from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from threading import RLock, Lock
import time
from typing import Any, Mapping
from scripts.platform_paths import absolute_path_for_io, public_path
from scripts.runtime_identity import RuntimeIdentityModule, runtime_identity_failure_message


CONTRACT_VERSION = "case-archive-v1"
ARCHIVE_FILENAME = "案情档案.md"
CASE_ID_PATTERN = re.compile(r"^case-[a-f0-9]{24}$")
REVISION_PATTERN = re.compile(r"^- 档案修订版本：(?P<revision>\d+)$", re.MULTILINE)
CASE_ID_LINE_PATTERN = re.compile(
    r"^- 案件编号：(?P<case_id>case-[a-f0-9]{24})$", re.MULTILINE
)
UPDATED_AT_PATTERN = re.compile(
    r"^- 最后更新：(?P<updated_at>[^\r\n]+)$", re.MULTILINE
)
RECORD_HEADING_PATTERN = re.compile(
    r"^### \[(?P<record_id>[A-Z][A-Z0-9_]*-\d{3,})\] [^\r\n]+$",
    re.MULTILINE,
)
RECORD_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:F|CL|E|A|CAL|AUTH|R|CONF|N)-\d{3,}(?![A-Za-z0-9_])"
)
COMMIT_MARKER_PATTERN = re.compile(
    r"^<!-- ARBIBUDDY:COMMIT key=(?P<key>[0-9a-f]{64}) "
    r"previous=(?P<previous>\d+) revision=(?P<revision>\d+) "
    r"applied=(?P<applied>[a-z]+\|[a-z_]+\|[A-Z0-9_-]+(?:,[a-z]+\|[a-z_]+\|[A-Z0-9_-]+)*) -->$",
    re.MULTILINE,
)

REQUIRED_SECTIONS = (
    "## 案件元数据",
    "## 事实",
    "## 主张与权益",
    "## 证据材料",
    "## 分析与假设",
    "## 计算结果",
    "## 法律核验",
    "## 风险与确认",
    "## 下一步",
    "## 更新记录",
)

_CREATE_KEYS = frozenset({"case_label", "jurisdiction", "initial_goal"})
_PII_KEYS = frozenset(
    {
        "name",
        "applicant_name",
        "worker_name",
        "employee_name",
        "employer_name",
        "company_name",
        "id_card",
        "id_number",
        "phone",
        "mobile",
        "address",
        "姓名",
        "身份证号",
        "手机号",
        "电话",
        "地址",
        "单位全称",
    }
)

RECORD_TYPE_PREFIXES = {
    "fact": "F",
    "claim": "CL",
    "evidence": "E",
    "analysis": "A",
    "calculation": "CAL",
    "authority": "AUTH",
    "risk": "R",
    "confirmation": "CONF",
    "next_step": "N",
}
RECORD_TYPE_SECTIONS = {
    "fact": "## 事实",
    "claim": "## 主张与权益",
    "evidence": "## 证据材料",
    "analysis": "## 分析与假设",
    "calculation": "## 计算结果",
    "authority": "## 法律核验",
    "risk": "## 风险与确认",
    "confirmation": "## 风险与确认",
    "next_step": "## 下一步",
}
RECORD_TYPE_LABELS = {
    "fact": "事实",
    "claim": "主张与权益",
    "evidence": "证据材料",
    "analysis": "分析与假设",
    "calculation": "计算结果",
    "authority": "法律核验",
    "risk": "风险记录",
    "confirmation": "确认记录",
    "next_step": "下一步",
}
RECORD_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-\d{3,}$")
_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = Lock()
_RUNTIME_IDENTITY = RuntimeIdentityModule()
_USER_VISIBLE_SUMMARIES = {
    "create": "案情档案已创建",
    "read": "案情档案已读取",
    "commit": "案情档案已更新",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(
    code: str,
    *,
    path: str,
    message: str,
    expected: Any = None,
    received: Any = None,
    recoverable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "expected": expected,
        "received": received,
        "recoverable": recoverable,
    }


def _failure(operation: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "ok": False,
        "errors": errors,
        "warnings": [],
    }


def _success(
    operation: str,
    result: dict[str, Any],
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    public_result = dict(result)
    public_result["user_visible_summary"] = _USER_VISIBLE_SUMMARIES.get(
        operation,
        "案情档案操作已完成",
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "ok": True,
        "result": public_result,
        "errors": [],
        "warnings": warnings or [],
    }


def _runtime_integrity_failure(operation: str) -> dict[str, Any] | None:
    """Stop public archive operations before any write when an install drifts."""

    try:
        preflight = _RUNTIME_IDENTITY.preflight()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        preflight = {
            "verified": False,
            "failures": [f"runtime_identity_preflight_error:{type(error).__name__}"],
        }
    if preflight.get("verified") is True:
        return None
    return _failure(
        operation,
        [
            _error(
                "installed_skill_modified",
                path="runtime",
                message=runtime_identity_failure_message(preflight),
                expected="verified installed Skill identity",
                received=preflight.get("failures", ["runtime_identity_preflight_failed"]),
                recoverable=False,
            )
        ],
    )


def _display_value(value: Any, *, fallback: str = "未填写") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    return text


def _validate_create_request(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(request, Mapping):
        return [
            _error(
                "invalid_change",
                path="create",
                message="创建输入必须是对象",
                expected="object",
                received=type(request).__name__,
            )
        ]
    errors: list[dict[str, Any]] = []
    unknown = sorted(set(request) - _CREATE_KEYS)
    for key in unknown:
        errors.append(
            _error(
                "invalid_change",
                path=f"create.{key}",
                message="创建请求包含未公开字段",
                expected=sorted(_CREATE_KEYS),
                received=request[key],
            )
        )
    for key, value in request.items():
        if key not in _CREATE_KEYS:
            continue
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            errors.append(
                _error(
                    "invalid_change",
                    path=f"create.{key}",
                    message="可选字段必须是非空文本",
                    expected="non-empty string or null",
                    received=value,
                )
            )
            continue
        if "\n" in value or "\r" in value:
            errors.append(
                _error(
                    "invalid_change",
                    path=f"create.{key}",
                    message="创建字段不得包含换行",
                    expected="single-line string",
                    received=value,
                )
            )
    for key in sorted(set(request) & _PII_KEYS):
        errors.append(
            _error(
                "invalid_change",
                path=f"create.{key}",
                message="create 不接收个人或单位主体信息",
                expected="case_label, jurisdiction, initial_goal only",
                received=request[key],
            )
        )
    return errors


def _render_initial_markdown(
    case_id: str,
    request: Mapping[str, Any],
    *,
    created_at: str,
) -> str:
    return "\n".join(
        (
            "# 案情档案",
            "",
            "## 案件元数据",
            "",
            f"- 案件编号：{case_id}",
            "- 档案修订版本：0",
            f"- 创建时间：{created_at}",
            f"- 最后更新：{created_at}",
            f"- 案件标签：{_display_value(request.get('case_label'))}",
            f"- 适用地区：{_display_value(request.get('jurisdiction'))}",
            f"- 初始目标：{_display_value(request.get('initial_goal'))}",
            "",
            "## 事实",
            "",
            "### 用户陈述",
            "",
            "暂无记录。",
            "",
            "### 证据支持事实",
            "",
            "暂无记录。",
            "",
            "### 分析假设",
            "",
            "暂无记录。",
            "",
            "## 主张与权益",
            "",
            "暂无记录。",
            "",
            "## 证据材料",
            "",
            "暂无记录。",
            "",
            "## 分析与假设",
            "",
            "暂无记录。",
            "",
            "## 计算结果",
            "",
            "暂无记录。",
            "",
            "## 法律核验",
            "",
            "暂无记录。",
            "",
            "## 风险与确认",
            "",
            "暂无记录。",
            "",
            "## 下一步",
            "",
            "由模型根据档案和用户目标决定下一步。",
            "",
            "## 更新记录",
            "",
            f"- {created_at}：创建案情档案。",
            "",
        )
    )


def _case_sections(markdown: str) -> list[str]:
    return re.findall(r"^## [^\r\n]+$", markdown, re.MULTILINE)


def _parse_archive(case_id: str, archive_path: Path) -> tuple[str, int, str] | list[dict[str, Any]]:
    try:
        markdown = archive_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            _error(
                "case_not_found",
                path="case_id",
                message="案情档案不存在",
                expected="existing case archive",
                received=case_id,
            )
        ]
    except PermissionError:
        return [
            _error(
                "permission_denied",
                path="case_id",
                message="没有读取案情档案的权限",
                recoverable=True,
            )
        ]
    except UnicodeError as error:
        return [
            _error(
                "archive_corrupt",
                path="archive.markdown",
                message="案情档案不是有效的 UTF-8 Markdown",
                expected="UTF-8 Markdown",
                received=error.__class__.__name__,
                recoverable=True,
            )
        ]
    except OSError as error:
        return [
            _error(
                "io_failure",
                path="case_id",
                message="读取案情档案失败",
                expected="readable UTF-8 Markdown",
                received=error.__class__.__name__,
            )
        ]

    errors: list[dict[str, Any]] = []
    if not markdown.endswith("\n"):
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.markdown",
                message="案情档案必须以换行结束",
                expected="trailing newline",
                received="missing",
                recoverable=True,
            )
        )
    if not markdown.startswith("# 案情档案\n"):
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.markdown",
                message="案情档案标题缺失或损坏",
                expected="# 案情档案",
                received=markdown.splitlines()[0] if markdown.splitlines() else "",
                recoverable=True,
            )
        )
    if _case_sections(markdown) != list(REQUIRED_SECTIONS):
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.sections",
                message="案情档案章节缺失、重复或顺序损坏",
                expected=list(REQUIRED_SECTIONS),
                received=_case_sections(markdown),
                recoverable=True,
            )
        )
    case_match = CASE_ID_LINE_PATTERN.search(markdown)
    if case_match is None or case_match.group("case_id") != case_id:
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.case_id",
                message="案情档案案件编号与请求不一致",
                expected=case_id,
                received=case_match.group("case_id") if case_match else None,
                recoverable=True,
            )
        )
    revision_match = REVISION_PATTERN.search(markdown)
    updated_match = UPDATED_AT_PATTERN.search(markdown)
    if revision_match is None:
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.revision",
                message="案情档案修订版本缺失",
                expected="non-negative integer",
                recoverable=True,
            )
        )
    if updated_match is None:
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.updated_at",
                message="案情档案更新时间缺失",
                expected="ISO 8601 timestamp",
                recoverable=True,
            )
        )
    elif updated_match is not None:
        try:
            datetime.fromisoformat(updated_match.group("updated_at"))
        except ValueError:
            errors.append(
                _error(
                    "archive_corrupt",
                    path="archive.updated_at",
                    message="案情档案更新时间不是有效的 ISO 8601 时间",
                    expected="ISO 8601 timestamp",
                    received=updated_match.group("updated_at"),
                    recoverable=True,
                )
            )
    record_ids = [match.group("record_id") for match in RECORD_HEADING_PATTERN.finditer(markdown)]
    duplicates = sorted({item for item in record_ids if record_ids.count(item) > 1})
    if duplicates:
        errors.append(
            _error(
                "archive_corrupt",
                path="archive.records",
                message="案情档案存在重复记录标识",
                expected="unique record_id",
                received=duplicates,
                recoverable=True,
            )
        )
    dangling_references = sorted(
        {
            reference
            for block in _record_content_blocks(markdown)
            for reference in RECORD_REFERENCE_PATTERN.findall(block)
            if reference not in set(record_ids)
        }
    )
    if dangling_references:
        errors.append(
            _error(
                "dangling_reference",
                path="archive.records",
                message="案情档案包含不存在的记录引用",
                expected="reference to an existing record_id",
                received=dangling_references,
                recoverable=True,
            )
        )
    if errors:
        return errors
    return markdown, int(revision_match.group("revision")), updated_match.group("updated_at")


class _ArchiveLockTimeout(RuntimeError):
    pass


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # POSIX kill(pid, 0) is destructive on Windows; query a waitable handle.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # Only an invalid PID proves absence; access denial must not steal a lock.
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _case_lock(archive_path: Path):
    key = str(public_path(archive_path.resolve()))
    with _LOCKS_GUARD:
        local_lock = _LOCKS.setdefault(key, RLock())
    lock_path = archive_path.parent / ".archive.lock"
    local_lock.acquire()
    acquired_file_lock = False
    try:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                lock_path.mkdir()
                acquired_file_lock = True
                owner = lock_path / "owner"
                owner.write_text(str(os.getpid()), encoding="ascii")
                break
            except FileExistsError:
                owner = lock_path / "owner"
                try:
                    owner_pid = int(owner.read_text(encoding="ascii").strip())
                except (FileNotFoundError, ValueError, OSError):
                    owner_pid = 0
                if owner_pid == os.getpid() or (owner_pid and not _process_is_alive(owner_pid)):
                    try:
                        shutil.rmtree(lock_path)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise _ArchiveLockTimeout("case archive lock timeout")
                time.sleep(0.01)
            except PermissionError as error:
                raise _ArchiveLockTimeout("case archive lock permission denied") from error
        yield
    finally:
        if acquired_file_lock:
            try:
                shutil.rmtree(lock_path)
            except FileNotFoundError:
                pass
            except OSError:
                # The next writer can recover a lock owned by this dead process
                # only if it can inspect the owner file; leave it in place.
                pass
        local_lock.release()


def _validate_commit_request(request: Any) -> list[dict[str, Any]]:
    if not isinstance(request, Mapping):
        return [
            _error(
                "invalid_change",
                path="commit",
                message="提交输入必须是对象",
                expected="object",
                received=type(request).__name__,
            )
        ]

    errors: list[dict[str, Any]] = []
    required = {"case_id", "expected_revision", "change_summary", "changes"}
    unknown = sorted(set(request) - required)
    for key in unknown:
        errors.append(
            _error(
                "invalid_change",
                path=f"commit.{key}",
                message="提交请求包含未公开字段",
                expected=sorted(required),
                received=request[key],
            )
        )
    for key in sorted(required - set(request)):
        errors.append(
            _error(
                "invalid_change",
                path=f"commit.{key}",
                message="提交请求缺少必填字段",
                expected="required",
                received=None,
            )
        )

    case_id = request.get("case_id")
    if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
        errors.append(
            _error(
                "case_not_found",
                path="commit.case_id",
                message="案件标识无效或不存在",
                expected="case- followed by 24 lowercase hexadecimal characters",
                received=case_id,
            )
        )

    expected_revision = request.get("expected_revision")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        errors.append(
            _error(
                "invalid_change",
                path="commit.expected_revision",
                message="expected_revision 必须是非负整数",
                expected="non-negative integer",
                received=expected_revision,
            )
        )

    summary = request.get("change_summary")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or "\n" in summary
        or "\r" in summary
        or "<!--" in summary
        or "-->" in summary
    ):
        errors.append(
            _error(
                "invalid_change",
                path="commit.change_summary",
                message="change_summary 必须是非空单行文本",
                expected="non-empty single-line string",
                received=summary,
            )
        )

    changes = request.get("changes")
    if not isinstance(changes, list) or not changes:
        errors.append(
            _error(
                "invalid_change",
                path="commit.changes",
                message="changes 必须是非空数组",
                expected="non-empty array",
                received=changes,
            )
        )
        return errors

    allowed_operations = {"append", "replace", "remove"}
    allowed_record_types = set(RECORD_TYPE_PREFIXES)
    for index, change in enumerate(changes):
        path = f"commit.changes[{index}]"
        if not isinstance(change, Mapping):
            errors.append(
                _error(
                    "invalid_change",
                    path=path,
                    message="change 必须是对象",
                    expected="object",
                    received=type(change).__name__,
                )
            )
            continue
        allowed_keys = {"operation", "record_type", "record_id", "content_markdown", "reason"}
        for key in sorted(set(change) - allowed_keys):
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.{key}",
                    message="change 包含未公开字段",
                    expected=sorted(allowed_keys),
                    received=change[key],
                )
            )
        operation = change.get("operation")
        if operation not in allowed_operations:
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.operation",
                    message="operation 不在公开枚举内",
                    expected=sorted(allowed_operations),
                    received=operation,
                )
            )
        record_type = change.get("record_type")
        if record_type not in allowed_record_types:
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.record_type",
                    message="record_type 不在公开枚举内",
                    expected=sorted(allowed_record_types),
                    received=record_type,
                )
            )
        record_id = change.get("record_id")
        if operation == "append" and "record_id" in change:
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.record_id",
                    message="append 不得指定 record_id，由工具生成",
                    expected="omitted",
                    received=record_id,
                )
            )
        if operation in {"replace", "remove"}:
            if not isinstance(record_id, str) or RECORD_ID_PATTERN.fullmatch(record_id) is None:
                errors.append(
                    _error(
                        "invalid_change",
                        path=f"{path}.record_id",
                        message="replace/remove 必须引用有效 record_id",
                        expected="stable record_id",
                        received=record_id,
                    )
                )
        content = change.get("content_markdown")
        if operation in {"append", "replace"}:
            if not isinstance(content, str) or not content.strip():
                errors.append(
                    _error(
                        "invalid_change",
                        path=f"{path}.content_markdown",
                        message="append/replace 必须提供非空 Markdown",
                        expected="non-empty Markdown string",
                        received=content,
                    )
                )
            elif (
                "\x00" in content
                or re.search(r"(?m)^##\s", content)
                or re.search(r"(?m)^### \[[A-Z][A-Z0-9_]*-\d{3,}\] ", content)
                or "<!-- ARBIBUDDY:COMMIT" in content
            ):
                errors.append(
                    _error(
                        "invalid_change",
                        path=f"{path}.content_markdown",
                        message="content_markdown 不能破坏档案章节锚点",
                        expected="Markdown without top-level section headings",
                        received=content,
                    )
                )
        if operation == "remove" and "content_markdown" in change:
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.content_markdown",
                    message="remove 不得提供 content_markdown",
                    expected="omitted",
                    received=content,
                )
            )
        reason = change.get("reason")
        if operation in {"replace", "remove"} and (
            not isinstance(reason, str) or not reason.strip() or "\n" in reason or "\r" in reason
        ):
            errors.append(
                _error(
                    "invalid_change",
                    path=f"{path}.reason",
                    message="replace/remove 必须提供非空单行 reason",
                    expected="non-empty single-line string",
                    received=reason,
                )
            )
    return errors


def _commit_key(request: Mapping[str, Any]) -> str:
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _record_blocks(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    headings = list(RECORD_HEADING_PATTERN.finditer(markdown))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        next_section = re.search(r"^## [^\r\n]+$", markdown[heading.end():end], re.MULTILINE)
        if next_section is not None:
            end = heading.end() + next_section.start()
        blocks.append((heading.group("record_id"), markdown[heading.end():end]))
    return blocks


def _record_ids(markdown: str) -> list[str]:
    return [record_id for record_id, _ in _record_blocks(markdown)]


def _record_content_blocks(markdown: str) -> list[str]:
    return [content for _, content in _record_blocks(markdown)]


def _historical_record_ids(markdown: str) -> set[str]:
    historical = set(_record_ids(markdown))
    historical.update(RECORD_REFERENCE_PATTERN.findall(markdown))
    for marker in COMMIT_MARKER_PATTERN.finditer(markdown):
        for item in marker.group("applied").split(","):
            historical.add(item.split("|", 2)[2])
    return historical


def _next_record_id(markdown: str, record_type: str) -> str:
    return _next_record_id_from_reserved(
        _historical_record_ids(markdown),
        record_type,
    )


def _next_record_id_from_reserved(
    reserved_ids: set[str],
    record_type: str,
) -> str:
    prefix = RECORD_TYPE_PREFIXES[record_type]
    numbers = [
        int(record_id.rsplit("-", 1)[1])
        for record_id in reserved_ids
        if record_id.startswith(f"{prefix}-")
    ]
    return f"{prefix}-{(max(numbers, default=0) + 1):03d}"


def _record_type_matches(record_id: str, record_type: str) -> bool:
    return record_id.startswith(f"{RECORD_TYPE_PREFIXES[record_type]}-")


def _section_span(markdown: str, heading: str) -> tuple[int, int]:
    start_match = re.search(rf"^{re.escape(heading)}$", markdown, re.MULTILINE)
    if start_match is None:
        raise ValueError(f"missing archive section: {heading}")
    next_match = re.search(r"^## [^\r\n]+$", markdown[start_match.end():], re.MULTILINE)
    end = start_match.end() + (next_match.start() if next_match else len(markdown[start_match.end():]))
    return start_match.end(), end


def _render_record(record_id: str, record_type: str, content: str) -> str:
    body = content.strip()
    return (
        f"### [{record_id}] {RECORD_TYPE_LABELS[record_type]}\n\n"
        f"{body}\n\n"
    )


def _append_record(markdown: str, record_id: str, record_type: str, content: str) -> str:
    start, end = _section_span(markdown, RECORD_TYPE_SECTIONS[record_type])
    section_body = markdown[start:end]
    section_body = re.sub(r"(?m)^暂无记录。\s*\n?", "", section_body)
    record = _render_record(record_id, record_type, content)
    if section_body and not section_body.endswith("\n"):
        section_body += "\n"
    section_body += record
    return markdown[:start] + section_body + markdown[end:]


def _has_duplicate_record_content(
    markdown: str,
    *,
    record_type: str,
    content: str,
) -> bool:
    start, end = _section_span(markdown, RECORD_TYPE_SECTIONS[record_type])
    section_body = markdown[start:end]
    headings = list(RECORD_HEADING_PATTERN.finditer(section_body))
    normalized = content.strip()
    for index, heading in enumerate(headings):
        record_id = heading.group("record_id")
        if not _record_type_matches(record_id, record_type):
            continue
        record_end = headings[index + 1].start() if index + 1 < len(headings) else len(section_body)
        body = section_body[heading.end():record_end].strip()
        if body == normalized:
            return True
    return False


def _missing_references(content: str, known_ids: set[str]) -> list[str]:
    return sorted(
        {reference for reference in RECORD_REFERENCE_PATTERN.findall(content) if reference not in known_ids}
    )


def _referencing_record_ids(markdown: str, target_id: str) -> set[str]:
    return {
        record_id
        for record_id, content in _record_blocks(markdown)
        if record_id != target_id and target_id in RECORD_REFERENCE_PATTERN.findall(content)
    }


def _replace_or_remove_record(
    markdown: str,
    *,
    record_id: str,
    record_type: str,
    operation: str,
    content: str | None,
) -> str:
    heading = re.compile(
        rf"^### \[{re.escape(record_id)}\] [^\r\n]+$", re.MULTILINE
    )
    matches = list(heading.finditer(markdown))
    if len(matches) != 1:
        raise KeyError(record_id)
    match = matches[0]
    expected_section = RECORD_TYPE_SECTIONS[record_type]
    section_start, section_end = _section_span(markdown, expected_section)
    if not section_start <= match.start() < section_end:
        raise KeyError(record_id)
    if not _record_type_matches(record_id, record_type):
        raise KeyError(record_id)
    section_tail = markdown[match.end():section_end]
    next_record = re.search(r"^### \[[^\r\n]+\] [^\r\n]+$", section_tail, re.MULTILINE)
    end = match.end() + (next_record.start() if next_record else len(section_tail))
    if operation == "remove":
        return markdown[:match.start()] + markdown[end:]
    return markdown[:match.start()] + _render_record(record_id, record_type, content or "") + markdown[end:]


def _find_replay(markdown: str, key: str) -> dict[str, Any] | None:
    for marker in COMMIT_MARKER_PATTERN.finditer(markdown):
        if marker.group("key") != key:
            continue
        applied: list[dict[str, str]] = []
        generated: list[str] = []
        for item in marker.group("applied").split(","):
            operation, record_type, record_id = item.split("|", 2)
            applied.append(
                {
                    "operation": operation,
                    "record_type": record_type,
                    "record_id": record_id,
                }
            )
            if operation == "append":
                generated.append(record_id)
        return {
            "previous_revision": int(marker.group("previous")),
            "revision": int(marker.group("revision")),
            "applied_changes": applied,
            "generated_record_ids": generated,
        }
    return None


def _atomic_write(path: Path, content: str) -> list[dict[str, Any]]:
    temporary_path: Path | None = None
    warnings: list[dict[str, Any]] = []
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        replaced = True
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                if replaced:
                    warnings.append(
                        {
                            "code": "temporary_cleanup_failed",
                            "message": "提交临时文件清理失败，可安全重试回收",
                            "recoverable": True,
                        }
                    )
    return warnings


class CaseArchive:
    """Expose only create/read/commit operations for the Markdown case archive."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = absolute_path_for_io(workspace_root)
        self._cases_root = self._workspace_root / ".arbibuddy" / "cases"
        self._runtime_input_root = self._workspace_root / ".arbibuddy" / "runtime-input"

    @contextmanager
    def _read_transaction(self, case_id: str):
        """Internal file transaction shared by commit and document publication."""
        if CASE_ID_PATTERN.fullmatch(case_id) is None:
            yield self.read(case_id)
            return
        with _case_lock(self._cases_root / case_id / ARCHIVE_FILENAME):
            yield self.read(case_id)

    def create(self, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        integrity_failure = _runtime_integrity_failure("create")
        if integrity_failure is not None:
            return integrity_failure
        request = {} if request is None else request
        if not isinstance(request, Mapping):
            return _failure("create", _validate_create_request(request))
        errors = _validate_create_request(request)
        if errors:
            return _failure("create", errors)
        try:
            self._runtime_input_root.mkdir(parents=True, exist_ok=True)
            self._cases_root.mkdir(parents=True, exist_ok=True)
            for _ in range(16):
                case_id = f"case-{secrets.token_hex(12)}"
                case_dir = self._cases_root / case_id
                try:
                    case_dir.mkdir()
                except FileExistsError:
                    continue
                created_at = _now()
                markdown = _render_initial_markdown(
                    case_id, request, created_at=created_at
                )
                archive_path = case_dir / ARCHIVE_FILENAME
                try:
                    warnings = _atomic_write(archive_path, markdown)
                except PermissionError:
                    shutil.rmtree(case_dir, ignore_errors=True)
                    return _failure(
                        "create",
                        [
                            _error(
                                "permission_denied",
                                path="workspace",
                                message="没有创建案情档案的权限",
                                recoverable=True,
                            )
                        ],
                    )
                except OSError as error:
                    shutil.rmtree(case_dir, ignore_errors=True)
                    return _failure(
                        "create",
                        [
                            _error(
                                "io_failure",
                                path="workspace",
                                message="创建案情档案失败",
                                expected="atomic writable archive",
                                received=error.__class__.__name__,
                            )
                        ],
                    )
                return _success(
                    "create",
                    {
                        "case_id": case_id,
                        "canonical_location": str(public_path(archive_path.resolve())),
                        "revision": 0,
                        "markdown": markdown,
                    },
                    warnings,
                )
            return _failure(
                "create",
                [
                    _error(
                        "io_failure",
                        path="case_id",
                        message="无法生成唯一案件标识",
                        expected="unique unpredictable case_id",
                        recoverable=True,
                    )
                ],
            )
        except PermissionError:
            return _failure(
                "create",
                [
                    _error(
                        "permission_denied",
                        path="workspace",
                        message="没有创建案情档案的权限",
                        recoverable=True,
                    )
                ],
            )
        except OSError as error:
            return _failure(
                "create",
                [
                    _error(
                        "io_failure",
                        path="workspace",
                        message="创建案情档案失败",
                        expected="writable workspace",
                        received=error.__class__.__name__,
                    )
                ],
            )

    def read(self, case_id: str) -> dict[str, Any]:
        operation = "read"
        integrity_failure = _runtime_integrity_failure(operation)
        if integrity_failure is not None:
            return integrity_failure
        if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
            return _failure(
                operation,
                [
                    _error(
                        "case_not_found",
                        path="case_id",
                        message="案件标识无效或不存在",
                        expected="case- followed by 24 lowercase hexadecimal characters",
                        received=case_id,
                    )
                ],
            )
        archive_path = self._cases_root / case_id / ARCHIVE_FILENAME
        parsed = _parse_archive(case_id, archive_path)
        if isinstance(parsed, list):
            return _failure(operation, parsed)
        markdown, revision, updated_at = parsed
        return _success(
            operation,
            {
                "case_id": case_id,
                "canonical_location": str(public_path(archive_path.resolve())),
                "revision": revision,
                "updated_at": updated_at,
                "markdown": markdown,
            },
        )

    def commit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        integrity_failure = _runtime_integrity_failure("commit")
        if integrity_failure is not None:
            return integrity_failure
        try:
            return self._commit(request)
        except FileNotFoundError:
            return _failure(
                "commit",
                [
                    _error(
                        "case_not_found",
                        path="case_id",
                        message="案情档案不存在",
                        expected="existing case archive",
                        recoverable=True,
                    )
                ],
            )
        except _ArchiveLockTimeout:
            return _failure(
                "commit",
                [
                    _error(
                        "io_failure",
                        path="case_id",
                        message="案情档案暂时被占用，请稍后重新读取并提交",
                        expected="available archive lock",
                        recoverable=True,
                    )
                ],
            )

    def _commit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        operation = "commit"
        errors = _validate_commit_request(request)
        if errors:
            return _failure(operation, errors)

        case_id = request["case_id"]
        archive_path = self._cases_root / case_id / ARCHIVE_FILENAME
        with _case_lock(archive_path):
            parsed = _parse_archive(case_id, archive_path)
            if isinstance(parsed, list):
                return _failure(operation, parsed)
            markdown, current_revision, _ = parsed
            key = _commit_key(request)
            replay = _find_replay(markdown, key)
            if replay is not None:
                replay_result = {
                    "case_id": case_id,
                    "canonical_location": str(public_path(archive_path.resolve())),
                    **replay,
                }
                return _success(operation, replay_result)

            expected_revision = request["expected_revision"]
            if expected_revision != current_revision:
                conflict = _error(
                    "revision_conflict",
                    path="commit.expected_revision",
                    message="案情档案已被其他更新修改，请重新读取后重建提交",
                    expected=current_revision,
                    received=expected_revision,
                    recoverable=True,
                )
                conflict["current_revision"] = current_revision
                return _failure(operation, [conflict])

            changes = request["changes"]
            base_ids = set(_record_ids(markdown))
            reserved_ids = _historical_record_ids(markdown)
            planned_append_ids: dict[int, str] = {}
            for index, change in enumerate(changes):
                if change["operation"] != "append":
                    continue
                record_id = _next_record_id_from_reserved(
                    reserved_ids,
                    change["record_type"],
                )
                planned_append_ids[index] = record_id
                reserved_ids.add(record_id)
            remove_targets = {
                change["record_id"]
                for change in changes
                if change["operation"] == "remove"
            }
            referenceable_ids = (
                (base_ids - remove_targets) | set(planned_append_ids.values())
            )
            seen_targets: set[str] = set()
            working = markdown
            applied_changes: list[dict[str, str]] = []
            generated_record_ids: list[str] = []
            change_reasons: list[str] = []
            apply_errors: list[dict[str, Any]] = []
            for index, change in enumerate(changes):
                change_path = f"commit.changes[{index}]"
                change_operation = change["operation"]
                record_type = change["record_type"]
                if change_operation in {"append", "replace"}:
                    missing_references = _missing_references(
                        change["content_markdown"],
                        referenceable_ids,
                    )
                    if missing_references:
                        apply_errors.append(
                            _error(
                                "dangling_reference",
                                path=f"{change_path}.content_markdown",
                                message="变更引用了当前档案中不存在的记录",
                                expected="existing record_id",
                                received=missing_references,
                            )
                        )
                        continue
                if change_operation == "append":
                    if _has_duplicate_record_content(
                        working,
                        record_type=record_type,
                        content=change["content_markdown"],
                    ):
                        apply_errors.append(
                            _error(
                                "duplicate_record",
                                path=f"{change_path}.content_markdown",
                                message="相同记录已经存在；如需重放原提交，请重放完全相同的请求",
                                expected="new record content",
                                received=change["content_markdown"],
                            )
                        )
                        continue
                    record_id = planned_append_ids[index]
                    working = _append_record(
                        working,
                        record_id,
                        record_type,
                        change["content_markdown"],
                    )
                    generated_record_ids.append(record_id)
                    applied_changes.append(
                        {
                            "operation": change_operation,
                            "record_type": record_type,
                            "record_id": record_id,
                        }
                    )
                    continue

                record_id = change["record_id"]
                if record_id in seen_targets:
                    apply_errors.append(
                        _error(
                            "invalid_change",
                            path=f"{change_path}.record_id",
                            message="同一批次不得重复修改同一记录",
                            expected="unique record_id per batch",
                            received=record_id,
                        )
                    )
                    continue
                seen_targets.add(record_id)
                if record_id not in base_ids:
                    apply_errors.append(
                        _error(
                            "record_not_found",
                            path=f"{change_path}.record_id",
                            message="指定记录不存在",
                            expected="record_id from current archive",
                            received=record_id,
                        )
                    )
                    continue
                if change_operation == "remove":
                    referencing_ids = _referencing_record_ids(working, record_id)
                    referencing_ids.difference_update(remove_targets)
                    if referencing_ids:
                        apply_errors.append(
                            _error(
                                "dangling_reference",
                                path=f"{change_path}.record_id",
                                message="不能删除仍被其他记录引用的记录",
                                expected="record with no remaining references",
                                received=sorted(referencing_ids),
                            )
                        )
                        continue
                try:
                    working = _replace_or_remove_record(
                        working,
                        record_id=record_id,
                        record_type=record_type,
                        operation=change_operation,
                        content=change.get("content_markdown"),
                    )
                except KeyError:
                    apply_errors.append(
                        _error(
                            "invalid_change",
                            path=f"{change_path}.record_type",
                            message="record_type 与现有记录章节不一致",
                            expected="record type owning record_id",
                            received=record_type,
                        )
                    )
                    continue
                applied_changes.append(
                    {
                        "operation": change_operation,
                        "record_type": record_type,
                        "record_id": record_id,
                    }
                )
                if change_operation in {"replace", "remove"}:
                    change_reasons.append(change["reason"])
            if apply_errors:
                return _failure(operation, apply_errors)

            new_revision = current_revision + 1
            updated_at = _now()
            working = re.sub(
                REVISION_PATTERN,
                f"- 档案修订版本：{new_revision}",
                working,
                count=1,
            )
            working = re.sub(
                UPDATED_AT_PATTERN,
                f"- 最后更新：{updated_at}",
                working,
                count=1,
            )
            marker_items = ",".join(
                f"{item['operation']}|{item['record_type']}|{item['record_id']}"
                for item in applied_changes
            )
            marker = (
                f"<!-- ARBIBUDDY:COMMIT key={key} previous={current_revision} "
                f"revision={new_revision} applied={marker_items} -->\n"
                f"- {updated_at}：{request['change_summary']}（档案修订版本 "
                f"{current_revision}→{new_revision}；已应用 "
                f"{', '.join(item['record_id'] for item in applied_changes)}。）\n"
            )
            if change_reasons:
                marker = marker.rstrip("\n") + f"（变更原因：{'；'.join(change_reasons)}）\n"
            update_heading = "## 更新记录"
            update_start, update_end = _section_span(working, update_heading)
            working = working[:update_start] + "\n" + marker + working[update_start:update_end] + working[update_end:]
            try:
                warnings = _atomic_write(archive_path, working)
            except PermissionError:
                return _failure(
                    operation,
                    [
                        _error(
                            "permission_denied",
                            path="archive",
                            message="没有写入案情档案的权限",
                            recoverable=True,
                        )
                    ],
                )
            except OSError as error:
                return _failure(
                    operation,
                    [
                        _error(
                            "io_failure",
                            path="archive",
                            message="提交案情档案失败，未应用本批变更",
                            expected="atomic writable archive",
                            received=error.__class__.__name__,
                        )
                    ],
                )
            return _success(
                operation,
                {
                    "case_id": case_id,
                    "canonical_location": str(public_path(archive_path.resolve())),
                    "previous_revision": current_revision,
                    "revision": new_revision,
                    "applied_changes": applied_changes,
                    "generated_record_ids": generated_record_ids,
                },
                warnings,
            )
