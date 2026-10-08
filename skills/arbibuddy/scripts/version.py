"""仓库唯一 VERSION 来源的读取与校验。"""

from __future__ import annotations

from pathlib import Path
import re


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-rc\d+)?$")


def read_version(source: str | Path) -> str:
    root = Path(source)
    path = root if root.name == "VERSION" else root / "VERSION"
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
