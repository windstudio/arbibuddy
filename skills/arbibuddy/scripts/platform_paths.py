from __future__ import annotations

import os
from pathlib import Path
import re
import stat


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


def lexical_absolute_path(value: str | Path) -> Path:
    path = normalize_platform_path(value).expanduser()
    if ".." in path.parts:
        raise ValueError("路径不得包含上级目录")
    return public_path(path if path.is_absolute() else Path.cwd() / path)


def is_path_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path_for_io(path).lstat()
    except FileNotFoundError:
        return False
    return bool(stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400)


def assert_no_path_links(path: Path) -> None:
    """Inspect lexical components before resolving symlinks or Windows junctions."""
    for component in reversed((path, *path.parents)):
        if is_path_link_or_reparse_point(component):
            raise ValueError("路径不得包含链接或重解析点")


def validated_workspace_root(value: str | Path) -> Path:
    raw = lexical_absolute_path(value)
    assert_no_path_links(raw)
    root = public_path(absolute_path_for_io(raw))
    if not path_for_io(root).is_dir():
        raise ValueError("工作区必须是已存在的目录")
    installed_root = public_path(Path(__file__).resolve().parents[1])
    if root == installed_root or root.is_relative_to(installed_root):
        raise ValueError("工作区不得位于 Skill 安装目录内")
    return root


def validated_workspace_path(workspace: Path, path: Path) -> Path:
    root = validated_workspace_root(workspace)
    raw = lexical_absolute_path(path)
    if not raw.is_relative_to(root):
        raise ValueError("路径必须位于当前工作区内")
    assert_no_path_links(raw)
    resolved = public_path(absolute_path_for_io(raw))
    if not resolved.is_relative_to(root):
        raise ValueError("路径逃逸当前工作区")
    return path_for_io(resolved)


class RuntimeInputCleanupError(OSError):
    """The owned transient input could not be removed; retry its managed consumption."""


def runtime_input_path(value: Path, workspace: Path) -> Path:
    try:
        root = validated_workspace_root(workspace)
        raw = value.expanduser()
        if not raw.is_absolute():
            raw = root / raw
        candidate = validated_workspace_path(root, raw)
        expected = path_for_io(root / ".arbibuddy" / "runtime-input")
        if candidate.parent != expected or candidate.suffix.casefold() != ".json" or ":" in candidate.name:
            raise ValueError("invalid_runtime_input_path")
        if not stat.S_ISREG(candidate.lstat().st_mode):
            raise ValueError("invalid_runtime_input_path")
        return candidate
    except (ValueError, FileNotFoundError) as error:
        raise ValueError("invalid_runtime_input_path") from error


def consume_runtime_input(value: Path, workspace: Path, max_bytes: int) -> str:
    candidate = runtime_input_path(value, workspace)
    identity = candidate.lstat()
    try:
        with candidate.open("rb") as stream:
            if stream.seek(0, os.SEEK_END) > max_bytes:
                raise OverflowError("runtime_input_too_large")
            stream.seek(0)
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise OverflowError("runtime_input_too_large")
        return raw.decode("utf-8-sig")
    finally:
        try:
            # Recheck ownership before deleting even after a failed read/size check.
            checked = runtime_input_path(value, workspace)
            current = checked.lstat()
            if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (
                identity.st_dev, identity.st_ino, identity.st_mtime_ns, identity.st_size
            ):
                raise OSError("单次输入在消费期间被替换")
            checked.unlink()
        except (OSError, ValueError) as error:
            raise RuntimeInputCleanupError(
                "单次输入未完成清理；请仅通过原 --input 受管重试，不创建新的事实副本"
            ) from error
