"""平台运行时共享的、非业务工具调用描述。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


_OPERATIONS = frozenset({"create", "read", "read-current", "commit"})


@dataclass(frozen=True)
class CaseArchiveInvocation:
    """绑定执行目录与显式案件工作区的公共命令描述。"""

    skill_root: Path
    workspace_root: Path
    python_executable: str

    @property
    def cwd(self) -> Path:
        return self.skill_root

    @property
    def command_prefix(self) -> tuple[str, ...]:
        return (
            self.python_executable,
            "-B",
            "-X",
            "utf8",
            "-m",
            "scripts.case_archive.cli",
            "--root",
            str(self.workspace_root),
        )

    def command(self, operation: str, *arguments: str) -> tuple[str, ...]:
        if operation not in _OPERATIONS:
            raise ValueError(
                "CaseArchive operation must be create, read, read-current or commit"
            )
        return (*self.command_prefix, operation, *arguments)

    def as_dict(self) -> dict[str, object]:
        return {
            "cwd": str(self.cwd),
            "workspace_root": str(self.workspace_root),
            "command_prefix": list(self.command_prefix),
            "operations": sorted(_OPERATIONS),
        }


def build_case_archive_invocation(
    *,
    skill_root: str | Path,
    workspace_root: str | Path,
    python_executable: str | None = None,
) -> CaseArchiveInvocation:
    """构建并验证安装副本的 CaseArchive 公共调用描述。

    ``skill_root`` 只决定 Python 模块的执行目录；所有案情文件都由显式
    ``--root`` 指向 ``workspace_root``。该函数不修改任一目录。
    """

    resolved_skill_root = Path(skill_root).expanduser().resolve()
    resolved_workspace_root = Path(workspace_root).expanduser().resolve()
    if not resolved_skill_root.is_dir():
        raise ValueError("skill_root 必须是已安装 Skill 根目录")
    if not (resolved_skill_root / "scripts" / "case_archive" / "cli.py").is_file():
        raise ValueError("skill_root 缺少 CaseArchive 公共 CLI")
    if not resolved_workspace_root.is_dir():
        raise ValueError("workspace_root 必须是已存在的案件工作区目录")
    if resolved_workspace_root == resolved_skill_root or resolved_workspace_root.is_relative_to(
        resolved_skill_root
    ):
        raise ValueError("workspace_root 不得位于 Skill 安装目录内")
    executable = python_executable or sys.executable
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("python_executable 必须是非空路径")
    return CaseArchiveInvocation(
        skill_root=resolved_skill_root,
        workspace_root=resolved_workspace_root,
        python_executable=executable,
    )


__all__ = ["CaseArchiveInvocation", "build_case_archive_invocation"]
