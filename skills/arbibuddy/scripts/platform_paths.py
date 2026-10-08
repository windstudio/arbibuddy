from __future__ import annotations

import os
from pathlib import Path
import re


WINDOWS_EXTENDED_PREFIX = "\\\\?\\"


def normalize_platform_path(value: str | Path) -> Path:
    """Normalize equivalent Windows, slash-Windows and MSYS drive paths."""
    text = str(value).strip()
    if not text:
        raise ValueError("路径不能为空")
    if os.name == "nt":
        match = re.fullmatch(r"[/\\]([A-Za-z])(?:[/\\](.*))?", text)
        if match is not None:
            drive, tail = match.groups()
            text = f"{drive.upper()}:\\" + re.sub(r"[/\\]", r"\\", tail or "")
        elif text.startswith(("/", "\\")) and re.match(r"^[/\\][A-Za-z]", text):
            raise ValueError("MSYS 盘符路径格式有歧义")
    return Path(text)


def windows_filesystem_path(value: str | Path) -> Path:
    """Return the path form used at Windows filesystem I/O boundaries.

    Drive and UNC paths are converted to the extended-length namespace only on
    Windows. Relative paths and non-Windows paths are left untouched. Callers
    must keep the ordinary value for persisted state and user-facing output.
    """

    path = normalize_platform_path(value)
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith(WINDOWS_EXTENDED_PREFIX):
        return path
    # pathlib on Windows renders UNC paths with two leading backslashes.
    if text.startswith("\\\\"):
        return Path(WINDOWS_EXTENDED_PREFIX + "UNC\\" + text[2:])
    if re.fullmatch(r"[A-Za-z]:[\\/].*", text) or re.fullmatch(
        r"[A-Za-z]:[\\/]?", text
    ):
        return Path(WINDOWS_EXTENDED_PREFIX + text.replace("/", "\\"))
    return path


def absolute_path_for_io(value: str | Path) -> Path:
    """Resolve a path after applying Windows extended-length handling."""

    path = normalize_platform_path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return windows_filesystem_path(path).resolve()


def public_path(value: str | Path) -> Path:
    """Strip an internal Windows extended prefix for persisted/user output."""

    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[len("\\\\?\\UNC\\"):])
    if text.startswith(WINDOWS_EXTENDED_PREFIX):
        return Path(text[len(WINDOWS_EXTENDED_PREFIX):])
    return Path(text)


# Short aliases make the I/O boundary easy to apply consistently without
# leaking implementation details into business modules.
path_for_io = windows_filesystem_path
