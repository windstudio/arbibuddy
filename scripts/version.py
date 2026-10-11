"""仓库唯一 VERSION 来源的读取与校验。"""

from __future__ import annotations

from pathlib import Path
import re


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-rc\d+)?$")


def version_path(source: str | Path) -> Path:
    root = Path(source)
    if root.name in {"VERSION", "VERSION.txt"}:
        return root
    # 源码/受管包保留 VERSION；通用分发视图派生为带扩展名文件。
    legacy, portable = root / "VERSION", root / "VERSION.txt"
    if (legacy.exists() or legacy.is_symlink()) and (portable.exists() or portable.is_symlink()):
        raise ValueError("存在两个版本文件，安装布局不一致")
    return portable if portable.exists() else legacy


def read_version(source: str | Path) -> str:
    path = version_path(source)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"VERSION 源缺失或不可读：{path}") from error
    if VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "VERSION 格式非法：必须是三段式数字版本，可选 -rcN 后缀"
        )
    return value


def display_version(source: str | Path) -> str:
    return f"v{read_version(source)}"
