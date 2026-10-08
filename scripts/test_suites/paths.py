"""测试制品路径边界；不依赖 Eval 或业务执行器。"""
from pathlib import Path


def assert_external_root(source_root: str | Path, root: str | Path, *, label: str) -> Path:
    source = Path(source_root).resolve()
    destination = Path(root).resolve()
    if destination == source or destination.is_relative_to(source):
        raise ValueError(f"{label} 不得位于源码工作树内")
    return destination
