from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
from uuid import uuid4

from scripts.platform_paths import path_for_io, public_path


DELIVERY_COMMIT_LOCK = ".delivery-commit.lock"
GENERATION_BACKUP_MARKER = ".regeneration-"
GENERATION_COMMITTED_BACKUP_MARKER = ".committed-backup-"


def _public_resolved(value: Path) -> Path:
    """Resolve a path for policy decisions without leaking ``\\\\?\\``."""
    return public_path(path_for_io(value).resolve())


def _public_absolute(value: Path) -> Path:
    """Make a lexical absolute path without resolving a link target."""
    return public_path(path_for_io(value).absolute())


def _fsync_parent_directory(path: Path) -> None:
    """Flush a directory entry when the current platform exposes that seam."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path_for_io(path).parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_with_retry(source: Path, target: Path) -> None:
    source = path_for_io(source)
    target = path_for_io(target)
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            # Windows scanners may briefly retain a handle after DOCX checks.
            import time

            time.sleep(0.02 * (attempt + 1))


def _is_reparse_point(path: Path) -> bool:
    path = path_for_io(path)
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(
        callable(is_junction) and is_junction()
    )


def _assert_safe_visible_path(workspace: Path, path: Path) -> None:
    workspace = _public_resolved(workspace)
    # Keep the link itself in the lexical path so the link/reparse-point
    # diagnostic wins; each existing component is resolved and checked below.
    absolute = _public_absolute(path)
    try:
        relative = absolute.relative_to(workspace)
    except ValueError as error:
        raise ValueError("用户可见交付路径必须位于工作区内") from error
    current = workspace
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            raise ValueError(
                f"用户可见交付路径不得包含链接或重解析点：{current}"
            )
        current_io = path_for_io(current)
        if current_io.exists():
            try:
                _public_resolved(current_io).relative_to(workspace)
            except (FileNotFoundError, ValueError) as error:
                raise ValueError("用户可见交付路径逃逸工作区") from error


def _lock_metadata(
    *,
    case_id: str | None,
    transaction_id: str | None,
    stage: str,
    recovery_action: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "transaction_id": transaction_id,
        "stage": stage,
        "recovery_action": recovery_action,
        "recovery_basis": "same_public_request_transaction_id",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _read_lock_metadata(stream) -> dict[str, object] | None:
    stream.seek(0)
    raw = stream.read().strip()
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("交付事务锁元数据无效，必须使用同一请求恢复") from error
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != 1
        or not isinstance(metadata.get("stage"), str)
    ):
        raise ValueError("交付事务锁元数据无效，必须使用同一请求恢复")
    return metadata


def _write_lock_metadata(stream, metadata: dict[str, object]) -> None:
    stream.seek(0)
    stream.truncate()
    stream.write(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


@contextmanager
def _workspace_commit_guard(
    workspace: Path,
    *,
    transaction_id: str | None = None,
    case_id: str | None = None,
    recovery_action: str = "document.render",
):
    workspace = path_for_io(_public_resolved(workspace))
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        # Windows 会把工作区根目录的非 FILE_SHARE_DELETE 句柄同时视为
        # 对其直接子目录 rename 的共享冲突。锁定工作区内稳定存在的
        # `.arbibuddy` 后代目录，既阻止工作区本身被并发交换，又不阻塞
        # 根目录下随机 staging 到受管可见目录的原子 rename。
        identity_guard = workspace / ".arbibuddy"
        if not identity_guard.is_dir() or _is_reparse_point(identity_guard):
            raise OSError("无法锁定工作区交付目录身份")
        identity_handle = create_file(
            str(identity_guard),
            0x00010000 | 0x0080,
            0x00000001 | 0x00000002,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if identity_handle == wintypes.HANDLE(-1).value:
            raise OSError(
                ctypes.get_last_error(),
                "无法锁定工作区交付目录身份",
            )
        lock_path = identity_guard / DELIVERY_COMMIT_LOCK
        lock_handle = create_file(
            str(lock_path),
            0x80000000 | 0x40000000,
            0,
            None,
            4,
            0x00000002,
            None,
        )
        if lock_handle == wintypes.HANDLE(-1).value:
            close_handle(identity_handle)
            raise OSError(
                ctypes.get_last_error(),
                "另一交付事务正在提交，请稍后使用同一请求恢复",
            )
        import msvcrt
        lock_fd = msvcrt.open_osfhandle(
            int(lock_handle), os.O_RDWR | getattr(os, "O_BINARY", 0)
        )
        lock_stream = os.fdopen(lock_fd, "r+", encoding="utf-8")
        try:
            previous = _read_lock_metadata(lock_stream)
            if (
                previous is not None
                and previous.get("stage") != "completed"
                and previous.get("transaction_id") != transaction_id
            ):
                raise ValueError("交付事务身份不一致，必须使用同一请求恢复")
            _write_lock_metadata(
                lock_stream,
                _lock_metadata(
                    case_id=case_id,
                    transaction_id=transaction_id,
                    stage="publishing",
                    recovery_action=recovery_action,
                ),
            )
            try:
                yield None
            except BaseException:
                _write_lock_metadata(
                    lock_stream,
                    _lock_metadata(
                        case_id=case_id,
                        transaction_id=transaction_id,
                        stage="failed_recoverable",
                        recovery_action=recovery_action,
                    ),
                )
                raise
            else:
                _write_lock_metadata(
                    lock_stream,
                    _lock_metadata(
                        case_id=case_id,
                        transaction_id=transaction_id,
                        stage="completed",
                        recovery_action=recovery_action,
                    ),
                )
        finally:
            lock_stream.close()
            close_handle(identity_handle)
    else:
        import fcntl

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(workspace, flags)
        lock_path = workspace / ".arbibuddy" / DELIVERY_COMMIT_LOCK
        lock_stream = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            previous = _read_lock_metadata(lock_stream)
            if (
                previous is not None
                and previous.get("stage") != "completed"
                and previous.get("transaction_id") != transaction_id
            ):
                raise ValueError("交付事务身份不一致，必须使用同一请求恢复")
            _write_lock_metadata(
                lock_stream,
                _lock_metadata(
                    case_id=case_id,
                    transaction_id=transaction_id,
                    stage="publishing",
                    recovery_action=recovery_action,
                ),
            )
            try:
                yield directory_fd
            except BaseException:
                _write_lock_metadata(
                    lock_stream,
                    _lock_metadata(
                        case_id=case_id,
                        transaction_id=transaction_id,
                        stage="failed_recoverable",
                        recovery_action=recovery_action,
                    ),
                )
                raise
            else:
                _write_lock_metadata(
                    lock_stream,
                    _lock_metadata(
                        case_id=case_id,
                        transaction_id=transaction_id,
                        stage="completed",
                        recovery_action=recovery_action,
                    ),
                )
        finally:
            lock_stream.close()
            fcntl.flock(directory_fd, fcntl.LOCK_UN)
            os.close(directory_fd)


def _generation_backup_path(output_dir: Path, transaction_id: str) -> Path:
    return output_dir.parent / (
        f".{output_dir.name}{GENERATION_BACKUP_MARKER}{transaction_id}"
    )


def _generation_archive_path(
    output_dir: Path, transaction_id: str
) -> Path:
    return output_dir.parent / (
        f".{output_dir.name}{GENERATION_COMMITTED_BACKUP_MARKER}"
        f"{transaction_id}-{uuid4().hex}"
    )


def _safe_generation_tree(path: Path) -> None:
    path = path_for_io(path)
    if not path.is_dir() or _is_reparse_point(path):
        raise ValueError("文书生成事务目录不是安全的受管目录")
    pending = [path]
    while pending:
        current = pending.pop()
        for child in current.iterdir():
            if _is_reparse_point(child):
                raise ValueError("文书生成事务目录不得包含链接或重解析点")
            if child.is_dir():
                pending.append(child)
            elif not child.is_file():
                raise ValueError("文书生成事务目录只能包含普通文件或目录")


def _replace_generation_child(source: Path, target: Path) -> None:
    source = path_for_io(source)
    target = path_for_io(target)
    if source.parent != target.parent:
        raise ValueError("文书生成事务只能交换同一父目录下的子目录")
    _replace_with_retry(source, target)
    _fsync_parent_directory(target)


def commit_generation_staging(
    *,
    case_root: Path,
    case_id: str,
    output_dir: Path,
    staging_dir: Path,
    transaction_id: str,
    replace_existing: bool,
) -> None:
    """Promote one validated generation staging directory durably.

    The service owns document validation; this module owns the only directory
    promotion seam, backup naming, rollback and recovery-safe retention.
    """

    if (
        not isinstance(transaction_id, str)
        or len(transaction_id) != 64
        or any(char not in "0123456789abcdef" for char in transaction_id)
    ):
        raise ValueError("文书生成事务编号无效")
    root = _public_resolved(case_root)
    output_dir = _public_resolved(output_dir)
    staging_dir = _public_absolute(staging_dir)
    if root.name != "cases" or root.parent.name != ".arbibuddy":
        raise ValueError("文书事务必须使用当前案件档案根目录")
    output_root = _public_resolved(root / case_id / "output")
    try:
        relative_output = output_dir.relative_to(output_root)
    except ValueError as error:
        raise ValueError("文书生成事务输出目录必须位于当前案件 output 目录") from error
    if len(relative_output.parts) > 1:
        raise ValueError("文书生成事务只接受 output 或其直接子目录")
    if staging_dir.parent != output_dir.parent:
        raise ValueError("文书生成 staging 必须与目标目录同父目录")
    if not staging_dir.name.startswith(f".{output_dir.name}.staging-"):
        raise ValueError("文书生成 staging 名称不受管")
    workspace = root.parent.parent
    _assert_safe_visible_path(workspace, output_dir.parent)
    _assert_safe_visible_path(workspace, staging_dir)
    _safe_generation_tree(staging_dir)
    if path_for_io(output_dir).exists() and _is_reparse_point(output_dir):
        raise ValueError("既有文书输出目录不得是链接或重解析点")
    if path_for_io(output_dir).exists() and not replace_existing:
        raise FileExistsError(f"输出目录已存在：{output_dir}")

    backup = _generation_backup_path(output_dir, transaction_id)

    def commit() -> None:
        if path_for_io(backup).exists() or _is_reparse_point(backup):
            _safe_generation_tree(backup)
            if path_for_io(output_dir).exists():
                # A complete new output is already visible.  Preserve the old
                # directory by archiving it; never delete an ambiguous backup.
                archive = _generation_archive_path(output_dir, transaction_id)
                _replace_generation_child(backup, archive)
            else:
                # The prior process stopped after moving the old output.  The
                # fixed transaction suffix is the only proof needed here.
                _replace_generation_child(backup, output_dir)
        if path_for_io(output_dir).exists() and not replace_existing:
            raise FileExistsError(f"输出目录已存在：{output_dir}")
        moved_old = False
        if path_for_io(output_dir).exists():
            _replace_generation_child(output_dir, backup)
            moved_old = True
        try:
            _replace_generation_child(staging_dir, output_dir)
            _safe_generation_tree(output_dir)
        except BaseException:
            if (
                not path_for_io(output_dir).exists()
                and moved_old
                and path_for_io(backup).exists()
            ):
                _replace_generation_child(backup, output_dir)
            raise
        if path_for_io(backup).exists():
            # The new output is already complete.  A failed archival rename is
            # recoverable and intentionally leaves the backup for admin-only
            # reconcile instead of risking a recursive delete.
            try:
                _replace_generation_child(
                    backup,
                    _generation_archive_path(output_dir, transaction_id),
                )
            except OSError:
                pass

    path_for_io(workspace).mkdir(parents=True, exist_ok=True)
    with _workspace_commit_guard(
        workspace, transaction_id=transaction_id, case_id=case_id,
        recovery_action="document.render",
    ):
        commit()
