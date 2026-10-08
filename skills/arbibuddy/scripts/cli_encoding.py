"""公共 CLI 的 UTF-8 标准流初始化。

入口在解析参数和输出任何业务结果前调用本模块，使成功与失败信封共享
同一个编码契约。测试中的 StringIO、管道捕获流和不可重配置流均保持可用。
"""

from __future__ import annotations

import io
import sys
from typing import TextIO


class _Utf8BufferWriter:
    """Minimal text writer used only when a host stream cannot reconfigure."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self.buffer = getattr(stream, "buffer", None)

    def write(self, value: str) -> int:
        if self.buffer is None:
            return self._stream.write(value)
        data = value.encode("utf-8")
        self.buffer.write(data)
        return len(value)

    def flush(self) -> None:
        if self.buffer is not None:
            self.buffer.flush()
        else:
            self._stream.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _configure(name: str) -> None:
    stream = getattr(sys, name, None)
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="strict")
            return
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            pass
    # Redirected StringIO and test doubles deliberately remain untouched.
    if getattr(stream, "buffer", None) is not None:
        setattr(sys, name, _Utf8BufferWriter(stream))


def configure_utf8_stdio() -> None:
    """Configure both stdout and stderr before a public CLI emits output."""

    _configure("stdout")
    _configure("stderr")
