"""受控运行期临时目录，避免 Windows 系统临时目录残留 ACL 造成假性环境故障。"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import time
from typing import Iterator
from uuid import uuid4

from scripts.platform_paths import path_for_io, public_path


class RuntimeTempError(OSError):
    """受控运行期临时目录不可用。"""

    def __init__(self, path: Path, operation: str, cause: BaseException) -> None:
        self.path = public_path(path)
        self.operation = operation
        self.cause = cause
        super().__init__(
            f"受控运行期临时目录不可用：{self.path}；操作={operation}；原因={cause}"
        )


def _remove_tree_without_recycle_bin(path: Path) -> None:
    """有限重试清理受管暂存；不调用回收站，失败仍 fail closed。"""
    delete_path = path_for_io(path)
    last_error: OSError | None = None

    def ignore_disappeared(_function, _path, error_info):
        error = error_info[1] if isinstance(error_info, tuple) else error_info
        if isinstance(error, FileNotFoundError) or getattr(error, "winerror", None) in {
            2, 3, 145
        }:
            return
        raise error

    for attempt in range(3):
        if not delete_path.exists():
            return
        try:
            # onerror keeps the Skill runnable on the declared Python 3.11+
            # baseline; this is direct filesystem removal, not a recycle-bin API.
            shutil.rmtree(delete_path, onerror=ignore_disappeared)
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            if getattr(error, "winerror", None) == 145 and attempt < 2:
                time.sleep(0.05)
                continue

        if not delete_path.exists():
            return
        try:
            for current, directories, files in os.walk(delete_path, topdown=False):
                current_path = Path(current)
                for filename in files:
                    try:
                        (current_path / filename).unlink()
                    except FileNotFoundError:
                        pass
                for directory in directories:
                    try:
                        (current_path / directory).rmdir()
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        if getattr(error, "winerror", None) not in {2, 3, 145}:
                            last_error = error
            try:
                delete_path.rmdir()
            except FileNotFoundError:
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in {2, 3, 145}:
                    last_error = error
        except OSError as error:
            last_error = error

        if not delete_path.exists():
            return
        if attempt < 2:
            time.sleep(0.05)

    raise RuntimeTempError(
        path,
        "cleanup",
        last_error or OSError("临时目录清理在限定重试后仍未完成"),
    )


def runtime_temp_root(anchor: Path, *, use_configured_root: bool = True) -> Path:
    """返回当前运行可写的临时根目录。

    测试门禁通过 ``ARBIBUDDY_TEST_TEMP_ROOT`` 注入独立目录；真实客户端
    没有该变量时，把短生命周期临时目录放在案件根目录的 ``.tmp`` 下，
    让其继承客户端已经需要的案件根目录 ACL，避免额外依赖父级目录或
    Windows 系统 Temp 的 ACL。
    """
    configured = os.environ.get("ARBIBUDDY_TEST_TEMP_ROOT") if use_configured_root else None
    root = public_path(
        (
            path_for_io(Path(configured).expanduser().resolve())
            if configured
            else path_for_io(anchor.expanduser().resolve()) / ".tmp"
        )
    )
    try:
        path_for_io(root).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeTempError(root, "create_root", error) from error
    return root


@contextmanager
def runtime_temporary_directory(
    anchor: Path, *, prefix: str, use_configured_root: bool = True
) -> Iterator[Path]:
    """创建继承受控根目录 ACL 的短生命周期临时目录。

    不使用 ``tempfile.TemporaryDirectory``：其 Windows ``mkdtemp`` 子目录
    ACL 在 Codex 受限进程令牌下可能不可访问，导致只有管理员启动才成功。
    普通 ``mkdir`` 会继承案件根目录 ACL，同时仍通过 UUID 保证目录名不碰撞。
    """
    root = runtime_temp_root(anchor, use_configured_root=use_configured_root)
    temporary_path: Path | None = None
    for _ in range(32):
        candidate = root / f"{prefix}{uuid4().hex[:12]}"
        try:
            path_for_io(candidate).mkdir()
        except FileExistsError:
            continue
        except OSError as error:
            raise RuntimeTempError(candidate, "create_child", error) from error
        temporary_path = public_path(candidate)
        break
    if temporary_path is None:
        raise RuntimeTempError(
            root,
            "create_child",
            OSError("连续 32 次目录名碰撞"),
        )

    try:
        yield temporary_path
    finally:
        try:
            _remove_tree_without_recycle_bin(temporary_path)
        except RuntimeTempError:
            raise
        except OSError as error:
            raise RuntimeTempError(temporary_path, "cleanup", error) from error


def probe_runtime_temp_capability(anchor: Path) -> None:
    """在复制案件数据前验证创建、写入和直接删除能力。"""
    with runtime_temporary_directory(anchor, prefix="arbibuddy-runtime-probe-") as path:
        probe_file = Path(path) / ".delete-capability-probe"
        probe_file.write_text("ok", encoding="ascii")
