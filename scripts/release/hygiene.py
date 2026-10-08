"""Git 仓库卫生校验，与运行时和测试执行分离。"""
from pathlib import Path
import re
import subprocess
from typing import Any

def validate_repository_hygiene(source: Path) -> dict[str, Any]:
    """Report tracked generated or sensitive material before release writes."""
    source = source.resolve()
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=source,
            capture_output=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法读取 Git 跟踪清单，拒绝发布") from error
    paths = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    violations: list[dict[str, str]] = []
    try:
        worktree = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=source,
            capture_output=True,
            check=True,
            timeout=15,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法读取 Git 工作树状态，拒绝发布") from error
    if worktree.stdout.strip():
        violations.append({
            "path": "git-worktree",
            "reason": "worktree_not_clean",
            "details": worktree.stdout.strip(),
        })
    sensitive = re.compile(
        r"(?:private|secret|user[-_]operation|trust|\.pem$|\.key$|\.p12$)",
        re.IGNORECASE,
    )
    generated_scratch_roots = {
        "installed-debug-rc23",
        "rc22-final-release",
        "rc22-gate-failure",
        "rc22-gate-prep",
        "rc22-workbuddy-package",
        "rc23-current-final",
        "rc23-release",
    }
    for relative in paths:
        normalized = relative.replace("\\", "/")
        reason = None
        if normalized == "dist" or normalized.startswith("dist/"):
            reason = "tracked_release_artifact"
        elif normalized.startswith("tests/runs/") and normalized != "tests/runs/.gitkeep":
            reason = "tracked_test_run_output"
        elif sensitive.search(normalized):
            reason = "tracked_sensitive_material"
        elif normalized.startswith(".scratch/") and (
            (
                len(normalized.split("/", 2)) > 1
                and normalized.split("/", 2)[1] in generated_scratch_roots
            )
            or any(
                marker in normalized.split("/")
                for marker in ("artifacts", "cases", "workspace", "render")
            )
            or ".claude/skills/" in normalized
            or normalized.casefold().endswith((".zip", ".tap"))
        ):
            reason = "tracked_generated_scratch_output"
        if reason is not None:
            violations.append({"path": normalized, "reason": reason})
    return {
        "source": str(source),
        "tracked_file_count": len(paths),
        "violations": violations,
        "verified": not violations,
    }
