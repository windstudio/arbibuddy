"""WorkBuddy vendor runtime manifest 的唯一确定性验证器。"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from scripts.platform_paths import path_for_io


RUNTIME_MANIFEST_SCHEMA_VERSION = 1
RUNTIME_ROOT_NAME = "workbuddy-runtime"
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"


class RuntimeManifestError(ValueError):
    """运行时清单、路径或文件摘要不满足契约。"""


def _digest(path: Path) -> str:
    return sha256(path_for_io(path).read_bytes()).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeManifestError(f"运行时清单存在重复字段：{key}")
        result[key] = value
    return result


def _safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        not normalized.startswith("/")
        and not (len(normalized) >= 2 and normalized[1] == ":")
        and ".." not in path.parts
        and normalized not in {"", "."}
    )


def validate_runtime_manifest(
    runtime_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    expected_abi: str | None = None,
    expected_platform: str | None = None,
) -> dict[str, Any]:
    raw_root = path_for_io(runtime_root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise RuntimeManifestError("运行时根目录路径不安全")
    root = raw_root.resolve()
    for candidate in raw_root.rglob("*"):
        if candidate.is_symlink():
            raise RuntimeManifestError("运行时目录不得包含符号链接")
    if not isinstance(manifest, Mapping):
        raise RuntimeManifestError("运行时清单顶层必须是对象")
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA_VERSION:
        raise RuntimeManifestError("运行时清单版本不受支持")
    if expected_abi is not None and manifest.get("python_abi") != expected_abi:
        raise RuntimeManifestError("运行时清单 ABI 不匹配")
    if expected_platform is not None and manifest.get("platform_tag") != expected_platform:
        raise RuntimeManifestError("运行时清单平台不匹配")
    files = manifest.get("files")
    licenses = manifest.get("licenses")
    packages = manifest.get("packages")
    if not isinstance(files, dict) or not files:
        raise RuntimeManifestError("运行时清单缺少文件摘要")
    if not isinstance(licenses, list) or not licenses:
        raise RuntimeManifestError("运行时清单缺少许可证")
    if not isinstance(packages, list) or not packages:
        raise RuntimeManifestError("运行时清单缺少包清单")
    for relative, expected in files.items():
        if not _safe_relative(relative) or not isinstance(expected, str):
            raise RuntimeManifestError("运行时文件摘要格式无效")
        raw = root / relative
        io_raw = path_for_io(raw)
        candidate = io_raw.resolve()
        if (
            io_raw.is_symlink()
            or not candidate.is_relative_to(root)
            or not candidate.is_file()
            or _digest(candidate) != expected
        ):
            raise RuntimeManifestError(f"运行时文件摘要不一致：{relative}")
    declared_files: set[str] = set()
    for relative in files:
        normalized = str(relative).replace("\\", "/")
        if normalized in declared_files:
            raise RuntimeManifestError("运行时文件摘要存在规范化路径重复")
        declared_files.add(normalized)
    observed_files: set[str] = set()
    for candidate in root.rglob("*"):
        if path_for_io(candidate).is_symlink():
            raise RuntimeManifestError("运行时目录不得包含符号链接")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if relative == RUNTIME_MANIFEST_NAME:
            continue
        if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        observed_files.add(relative)
    if observed_files != declared_files:
        missing = sorted(declared_files - observed_files)
        extra = sorted(observed_files - declared_files)
        raise RuntimeManifestError(
            "运行时展开文件集不一致："
            f"missing={missing[:3]!r}, extra={extra[:3]!r}"
        )
    for relative in licenses:
        license_path = path_for_io(root / relative)
        if (
            not _safe_relative(relative)
            or relative.replace("\\", "/") not in declared_files
            or license_path.is_symlink()
            or not license_path.resolve().is_relative_to(root)
            or not license_path.is_file()
        ):
            raise RuntimeManifestError(f"运行时许可证缺失：{relative}")
    vendor_root = path_for_io(root / "site-packages")
    if vendor_root.is_symlink() or not vendor_root.is_dir():
        raise RuntimeManifestError("运行时 vendor 根目录缺失")
    return {
        "python_abi": manifest.get("python_abi"),
        "platform_tag": manifest.get("platform_tag"),
        "files": files,
        "licenses": licenses,
        "packages": packages,
        "vendor_root": vendor_root,
    }


def read_and_validate_runtime_manifest(
    skill_root: str | Path,
    *,
    expected_abi: str | None = None,
    expected_platform: str | None = None,
) -> dict[str, Any]:
    skill = path_for_io(skill_root).resolve()
    raw_root = path_for_io(skill / RUNTIME_ROOT_NAME)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise RuntimeManifestError("运行时根目录路径不安全")
    root = raw_root.resolve()
    path = path_for_io(root / RUNTIME_MANIFEST_NAME)
    if path.is_symlink() or not path.is_file():
        raise RuntimeManifestError("运行时清单缺失")
    try:
        manifest = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeManifestError("运行时清单不可读取") from error
    if not isinstance(manifest, Mapping):
        raise RuntimeManifestError("运行时清单顶层必须是对象")
    return validate_runtime_manifest(
        root,
        manifest,
        expected_abi=expected_abi,
        expected_platform=expected_platform,
    )


__all__ = [
    "RUNTIME_MANIFEST_NAME",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "RUNTIME_ROOT_NAME",
    "RuntimeManifestError",
    "read_and_validate_runtime_manifest",
    "validate_runtime_manifest",
]
