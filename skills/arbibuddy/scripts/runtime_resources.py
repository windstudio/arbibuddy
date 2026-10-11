"""运行期资源白名单：身份、安装与打包共用，不能按目录通配夹带开发资源。"""
from __future__ import annotations

from pathlib import Path
import json
import os
import re

from scripts.platform_paths import path_for_io, public_path
from scripts.version import read_version, version_path


DISTRIBUTION_RESOURCE_RENAMES = {"VERSION": "VERSION.txt", "LICENSE": "LICENSE.txt"}

# 身份契约修订；Skill VERSION 不随本次架构收缩变更。
CONTRACT_VERSION = 7
RUNTIME_FILES = (
    'LICENSE',
    'README.md',
    'SKILL.md',
    'VERSION',
    'adapters/capability-contract.json',
    'adapters/claude-code/adapter.json',
    'adapters/codex/adapter.json',
    'adapters/workbuddy/adapter.json',
    'agents/openai.yaml',
    'references/amount-calculate.md',
    'references/amount-formulas.v1.json',
    'references/document-render.md',
    'references/legal-verification.md',
    'references/legal/annual-leave-pay-rules.v1.json',
    'references/legal/dispute-routing-rules.v1.json',
    'references/legal/employment-relationship-rules.v1.json',
    'references/legal/enforcement-rules.v1.json',
    'references/legal/forced-termination-rules.v1.json',
    'references/legal/holiday-schedules.v1.json',
    'references/legal/legal-baseline.v1.json',
    'references/legal/overtime-rules.v1.json',
    'references/legal/paid-leave-routing-rules.v1.json',
    'references/legal/property-preservation-rules.v1.json',
    'references/legal/shanghai-cancellation-restriction-rules.v1.json',
    'references/legal/source-cleanup.v1.json',
    'references/legal/unlawful-termination-rules.v1.json',
    'references/legal/unsigned-contract-double-wage-rules.v1.json',
    'references/legal/wage-floor-rules.v1.json',
    'references/legal/wage-rules.v1.json',
    'references/model-led-case-archive.md',
    'references/model-led/annual-leave-pay.md',
    'references/model-led/demand-and-forced-termination-documents.md',
    'references/model-led/deregistration-preservation-enforcement-documents.md',
    'references/model-led/dispute-routing.md',
    'references/model-led/employment-relationship.md',
    'references/model-led/forced-termination.md',
    'references/model-led/overtime-pay.md',
    'references/model-led/paid-leave-routing.md',
    'references/model-led/termination-analysis-and-confirmation.md',
    'references/model-led/unlawful-termination.md',
    'references/model-led/unsigned-contract-double-wage.md',
    'references/model-led/wage-analysis.md',
    'references/platform-capabilities.md',
    'references/public-interface.md',
    'references/third-party-notices.txt',
    'references/rights-scan-and-unified-confirmation.md',
    'references/workbuddy-document-runtime.lock.json',
    'requirements-documents.txt',
    'scripts/__init__.py',
    'scripts/amount_calculator/__init__.py',
    'scripts/amount_calculator/public.py',
    'scripts/amount_calculator/runtime_cli.py',
    'scripts/case_archive/__init__.py',
    'scripts/case_archive/cli.py',
    'scripts/case_archive/runtime.py',
    'scripts/case_archive/service.py',
    'scripts/cli_encoding.py',
    'scripts/documents/__init__.py',
    'scripts/documents/archive_dependencies.py',
    'scripts/documents/checklist_policy.py',
    'scripts/documents/delivery.py',
    'scripts/documents/docx_builder.py',
    'scripts/documents/ooxml_check.py',
    'scripts/documents/public.py',
    'scripts/documents/receipts.py',
    'scripts/documents/registry.py',
    'scripts/documents/runtime_cli.py',
    'scripts/documents/runtime_dependencies.py',
    'scripts/documents/view.py',
    'scripts/legal_verification/__init__.py',
    'scripts/legal_verification/runtime_cli.py',
    'scripts/legal_verification/service.py',
    'scripts/network_diagnostics.py',
    'scripts/platform_adapters/__init__.py',
    'scripts/platform_adapters/cli.py',
    'scripts/platform_adapters/service.py',
    'scripts/platform_paths.py',
    'scripts/runtime_context.py',
    'scripts/runtime_identity.py',
    'scripts/runtime_manifest.py',
    'scripts/runtime_resources.py',
    'scripts/runtime_temp.py',
    'scripts/version.py',
 )


class RuntimeResourceError(ValueError):
    """白名单资源缺失、不安全或不属于当前运行期。"""


def runtime_resource_names(root: Path) -> tuple[str, ...]:
    portable = version_path(root).name == "VERSION.txt"
    return tuple(DISTRIBUTION_RESOURCE_RENAMES.get(name, name) if portable else name
                 for name in RUNTIME_FILES)


def runtime_paths(root: Path) -> tuple[Path, ...]:
    source = public_path(path_for_io(root).resolve())
    paths = []
    for relative in runtime_resource_names(source):
        candidate = source / relative
        io_path = path_for_io(candidate)
        # 祖先符号链接同样拒绝，不能将外部文件带入安装包。
        ancestors = [candidate, *candidate.parents]
        if any(path_for_io(p).is_symlink() for p in ancestors if p != source and p.is_relative_to(source)):
            raise RuntimeResourceError(f"运行期资源不得通过符号链接读取：{relative}")
        if not io_path.is_file() or not public_path(io_path.resolve()).is_relative_to(source):
            raise RuntimeResourceError(f"运行期资源缺失或越出源码根：{relative}")
        paths.append(candidate)
    return tuple(paths)


def inspect_runtime_resources(root: Path) -> dict[str, object]:
    try:
        runtime_paths(root)
    except (OSError, RuntimeResourceError) as error:
        return {"contract_version": CONTRACT_VERSION, "resources_complete": False,
                "verified": False, "missing_resources": [str(error)]}
    return {"contract_version": CONTRACT_VERSION, "resources_complete": True,
            "verified": True, "missing_resources": []}


def _valid_workbuddy_import_metadata(path: Path) -> bool:
    """宿主上传器的非执行元数据，不属于 Skill 运行资源或身份来源。"""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate host metadata key")
            result[key] = value
        return result

    try:
        if path.stat().st_size > 16 * 1024:
            return False
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        return (
            isinstance(value, dict)
            and set(value) == {"name", "installedAt", "source"}
            and value["name"] == "arbibuddy"
            and value["source"] == "userImport"
            and type(value["installedAt"]) is int
            and value["installedAt"] >= 0
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False


def _skillhub_metadata_files(root: Path) -> set[str]:
    """只允许已知宿主非执行文件；内容不参与身份来源或资源摘要。"""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate host metadata key")
            result[key] = value
        return result

    def load(name: str) -> dict:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
            raise ValueError("invalid host metadata file")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(value, dict):
            raise ValueError("invalid host metadata object")
        return value

    def timestamp(value: object) -> bool:
        return type(value) is int and value >= 0

    allowed: set[str] = set()
    try:
        published = load("_meta.json")
        if not (
            set(published) == {"ownerId", "publishedAt", "slug", "version"}
            and isinstance(published["ownerId"], str)
            and re.fullmatch(r"\d{1,32}", published["ownerId"])
            and timestamp(published["publishedAt"])
            and published["slug"] == "arbibuddy"
            and published["version"] == read_version(root)
        ):
            return allowed
        allowed.add("_meta.json")
        if not (root / "_skillhub_meta.json").exists():
            return allowed
        installed = load("_skillhub_meta.json")
        required = {"slug", "name", "version", "installedAt", "source", "namespace", "canonicalName"}
        optional = {"iconSource", "labels", "iconLocalPath", "iconDownloadedFrom"}
        namespace = installed.get("namespace")
        if not (
            required <= set(installed) <= required | optional
            and installed["slug"] == "arbibuddy"
            and isinstance(installed["name"], str) and 0 < len(installed["name"]) <= 256
            and installed["version"] == read_version(root)
            and timestamp(installed["installedAt"])
            and installed["source"] == "skillhub"
            and isinstance(namespace, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", namespace)
            and installed["canonicalName"] == f"@{namespace}/arbibuddy"
            and all(isinstance(installed[key], str) and len(installed[key]) <= 4096
                    for key in optional - {"labels"} if key in installed)
            and ("labels" not in installed or (
                isinstance(installed["labels"], dict) and len(installed["labels"]) <= 64
                and all(isinstance(k, str) and isinstance(v, str) and len(k) <= 128 and len(v) <= 512
                        for k, v in installed["labels"].items())
            ))
        ):
            return allowed
        allowed.add("_skillhub_meta.json")
        icon = root / "_icon.png"
        if icon.is_file() and not icon.is_symlink() and icon.stat().st_size <= 1024 * 1024:
            payload = icon.read_bytes()
            if payload.startswith(b"\x89PNG\r\n\x1a\n") and payload.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
                allowed.add("_icon.png")
    except (OSError, UnicodeError, ValueError, RecursionError):
        pass
    return allowed


def installed_resource_failures(
    root: Path, *, allow_vendor: bool = False, allow_extra_files: bool = False,
    allow_skillhub_metadata: bool = False,
) -> list[str]:
    """检查安装集合；仅维护换树时可允许额外普通文件，链接仍拒绝。

    allow_extra_files 不能用于运行身份验证；更新另须验证全部登记摘要、
    回执和 vendor，额外文件只随旧树移走，不会导入或执行。
    """
    source = path_for_io(root).resolve()
    allowed = set(runtime_resource_names(source)) | {
        "arbibuddy.install.json", "arbibuddy.runtime.json", "arbibuddy.distribution.json",
    }
    cached_modules = {
        (str(Path(p).parent / "__pycache__").replace("\\", "/"), Path(p).stem)
        for p in RUNTIME_FILES if p.endswith(".py")
    }
    host_metadata = _skillhub_metadata_files(source) if allow_skillhub_metadata else set()
    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for directory, names, files in os.walk(source, followlinks=False, onerror=raise_walk_error):
            current = Path(directory)
            for name in names[:]:
                child = current / name
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                    return ["installed_resource_set_mismatch"]
                if child.parent == source and child.name in {
                    "_user_meta.json", "_meta.json", "_skillhub_meta.json", "_icon.png",
                }:
                    return ["installed_resource_set_mismatch"]
                if allow_vendor and child == source / "workbuddy-runtime":
                    # vendor 的完整集合、摘要及链接另由 runtime_manifest 验证。
                    names.remove(name)
            for name in files:
                child = current / name
                relative = child.relative_to(source).as_posix()
                if child.is_symlink() or not child.is_file():
                    return ["installed_resource_set_mismatch"]
                if relative in allowed:
                    continue
                if relative in host_metadata:
                    continue
                if (
                    allow_vendor
                    and relative == "_user_meta.json"
                    and (source / "workbuddy-runtime").is_dir()
                    and _valid_workbuddy_import_metadata(child)
                ):
                    continue
                match = re.fullmatch(r"(.+)\.cpython-\d+(?:\.opt-[12])?\.pyc", name)
                cache_parent = child.parent.relative_to(source).as_posix()
                if match and (cache_parent, match.group(1)) in cached_modules:
                    continue
                if allow_extra_files:
                    continue
                return ["installed_resource_set_mismatch"]
    except OSError:
        return ["installed_resource_set_unreadable"]
    return []
