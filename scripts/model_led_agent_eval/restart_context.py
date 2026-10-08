"""从公开 CaseArchive 观察结果构造重启恢复上下文。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


class RestartContextBuilder:
    """不读取 Harness 隐藏状态，只读取公开档案并返回可序列化上下文。"""

    def build(
        self,
        *,
        workspace: str | Path,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        archives = observation.get("case_archives", [])
        if not isinstance(archives, list) or len(archives) != 1:
            return {"status": "missing", "reason": "current_case_archive_not uniquely observable"}
        archive = archives[0]
        if not isinstance(archive, Mapping):
            return {"status": "missing", "reason": "archive_observation_invalid"}
        relative = archive.get("archive_path")
        case_id = archive.get("case_id")
        revision = archive.get("archive_revision")
        if not isinstance(relative, str) or not isinstance(case_id, str) or not isinstance(revision, int):
            return {"status": "missing", "reason": "archive_identity_invalid"}
        workspace_root = Path(workspace).resolve()
        normalized_relative = relative.replace("\\", "/")
        raw_path = workspace_root.joinpath(*normalized_relative.split("/"))
        path = raw_path.resolve()
        if (
            Path(normalized_relative).is_absolute()
            or ".." in Path(normalized_relative).parts
            or raw_path.is_symlink()
            or not path.is_relative_to(workspace_root)
            or not path.is_file()
        ):
            return {"status": "missing", "reason": "archive_path_invalid"}
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return {"status": "missing", "reason": "archive_read_failed"}
        facts_section = text
        marker = "\n## 事实\n"
        if marker in text:
            facts_section = text.split(marker, 1)[1]
            if "\n## " in facts_section:
                facts_section = facts_section.split("\n## ", 1)[0]
        facts_digest = sha256(
            facts_section.strip().encode("utf-8")
        ).hexdigest()
        return {
            "status": "available",
            "case_id": case_id,
            "archive_revision": revision,
            "archive_path": relative,
            "confirmed_fact_summary_sha256": facts_digest,
            "archive_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "archive_excerpt": text[:12000],
        }


__all__ = ["RestartContextBuilder"]
