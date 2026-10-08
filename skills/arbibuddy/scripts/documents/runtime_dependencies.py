"""Self-contained WorkBuddy document runtime preflight.

The WorkBuddy package carries a locked, target-interpreter vendor tree.  This
module only validates that tree and adds it to ``sys.path``; it never invokes
pip, a shell, a virtual environment, or a network operation.
"""

from __future__ import annotations

import importlib
import json
import platform
from pathlib import Path
import sys
from typing import Any, Mapping

from scripts.runtime_manifest import (
    RUNTIME_MANIFEST_NAME,
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    RUNTIME_ROOT_NAME,
    RuntimeManifestError,
    read_and_validate_runtime_manifest,
)
from scripts.platform_paths import public_path


class DocumentRuntimeUnavailable(ValueError):
    """The active interpreter cannot safely load the document renderer."""

    def __init__(self, reason: str, *, details: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


def _skill_root(value: str | Path | None) -> Path:
    return (
        Path(value).resolve()
        if value is not None
        else Path(__file__).resolve().parents[2]
    )


def _runtime_tags() -> tuple[str, str]:
    python_abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    machine = platform.machine().casefold()
    platform_tag = "win_amd64" if sys.platform == "win32" and machine in {
        "amd64", "x86_64"
    } else f"{sys.platform}_{machine}"
    return python_abi, platform_tag


def _read_manifest(skill_root: Path) -> tuple[dict[str, Any] | None, Path | None]:
    runtime_root = skill_root / RUNTIME_ROOT_NAME
    path = runtime_root / RUNTIME_MANIFEST_NAME
    if not path.exists():
        if runtime_root.exists():
            raise DocumentRuntimeUnavailable("文书运行时清单缺失")
        return None, None
    if path.is_symlink() or not path.is_file():
        raise DocumentRuntimeUnavailable("文书运行时清单路径不安全")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DocumentRuntimeUnavailable(
            "文书运行时清单不可读取",
            details=type(error).__name__,
        ) from error
    if not isinstance(value, dict):
        raise DocumentRuntimeUnavailable("文书运行时清单顶层必须是对象")
    return value, path


def _validate_manifest(skill_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return read_and_validate_runtime_manifest(
            skill_root,
            expected_abi=_runtime_tags()[0],
            expected_platform=_runtime_tags()[1],
        )
    except (OSError, RuntimeManifestError) as error:
        raise DocumentRuntimeUnavailable(str(error), details=str(error)) from error

def _module_origin(name: str) -> Path | None:
    module = sys.modules.get(name)
    value = getattr(module, "__file__", None) if module is not None else None
    return public_path(Path(value).resolve()) if isinstance(value, str) else None


def _load_vendor(vendor_root: Path) -> None:
    # python-docx opens bundled templates through ``__file__/../templates``.
    # Windows rejects ``..`` inside an extended-length (\\?\) import path.
    # Keep extended paths for manifest I/O, but import from the resolved public path.
    import_root = public_path(vendor_root.resolve())
    vendor_text = str(import_root)
    if vendor_text not in sys.path:
        sys.path.insert(0, vendor_text)
    importlib.invalidate_caches()
    for prefix in ("docx", "lxml"):
        origin = _module_origin(prefix)
        if origin is not None and not origin.is_relative_to(import_root):
            for name in tuple(sys.modules):
                if name == prefix or name.startswith(prefix + "."):
                    sys.modules.pop(name, None)
    try:
        docx = importlib.import_module("docx")
        lxml = importlib.import_module("lxml")
    except (ImportError, ModuleNotFoundError) as error:
        raise DocumentRuntimeUnavailable(
            "WorkBuddy 文书运行时缺少可导入的 DOCX 依赖",
            details=type(error).__name__,
        ) from error
    for name, module in (("docx", docx), ("lxml", lxml)):
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not public_path(Path(origin).resolve()).is_relative_to(
            import_root
        ):
            raise DocumentRuntimeUnavailable(
                "文书依赖未从包内 vendor 加载",
                details=name,
            )


def ensure_document_runtime(skill_root: str | Path | None = None) -> dict[str, Any]:
    """Validate and activate the packaged runtime, or the host source runtime."""

    root = _skill_root(skill_root)
    manifest, _path = _read_manifest(root)
    if manifest is None:
        try:
            importlib.import_module("docx")
            importlib.import_module("lxml")
        except (ImportError, ModuleNotFoundError) as error:
            raise DocumentRuntimeUnavailable(
                "当前环境缺少 DOCX 运行时依赖",
                details=type(error).__name__,
            ) from error
        return {
            "verified": True,
            "mode": "host",
            "python_abi": _runtime_tags()[0],
            "platform_tag": _runtime_tags()[1],
            "vendor_root": None,
        }
    validated = _validate_manifest(root, manifest)
    _load_vendor(validated["vendor_root"])
    return {"verified": True, "mode": "packaged", **validated}


__all__ = [
    "DocumentRuntimeUnavailable",
    "RUNTIME_MANIFEST_NAME",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "RUNTIME_ROOT_NAME",
    "ensure_document_runtime",
]
