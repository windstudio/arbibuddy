from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.request import Request, urlopen
import uuid
import zipfile

from scripts.runtime_resources import (
    CONTRACT_VERSION as CORE_CONTRACT_VERSION,
    RUNTIME_FILES as COMMON_REQUIRED_RESOURCES,
    RuntimeResourceError, runtime_paths, inspect_runtime_resources, installed_resource_failures,
)
from scripts.network_diagnostics import classify_network_error
from scripts.runtime_temp import (
    _remove_tree_without_recycle_bin,
    runtime_temporary_directory,
)
from scripts.runtime_identity import (
    RUNTIME_IDENTITY_SCHEMA_VERSION,
    WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME,
    RuntimeIdentityError,
    RuntimeIdentityModule,
    is_source_link,
    build_workbuddy_portable_runtime_marker,
    build_runtime_identity,
    compare_runtime_identity,
    measure_runtime_identity,
    runtime_identity_digest,
    write_workbuddy_trust_anchor,
    _workbuddy_external_anchor_failures,
)
from scripts.platform_paths import path_for_io, public_path
from scripts.version import display_version
from scripts.runtime_manifest import (
    RUNTIME_MANIFEST_NAME,
    RUNTIME_ROOT_NAME,
    RuntimeManifestError,
    read_and_validate_runtime_manifest,
)


DOCUMENT_DEPENDENCY_FILE = "requirements-documents.txt"
DOCUMENT_DEPENDENCY_PATTERN = re.compile(
    r"(?m)^\s*python-docx\s*(?:[<>=!~].*)?$", re.IGNORECASE
)
ADAPTERS_ROOT = Path(__file__).resolve().parents[2] / "adapters"
SUPPORTED_PLATFORMS = ("codex", "claude-code", "workbuddy")
ARTIFACT_MANIFEST_VERSION = 1
INSTALL_RECEIPT_SCHEMA_VERSION = 2
MAX_ARTIFACT_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
WORKBUDDY_TRANSACTION_PREFIX = ".arbibuddy-workbuddy-"
WORKBUDDY_RECEIPT_NAME = "arbibuddy.install.json"
WORKBUDDY_DISCOVERY_RELATIVE = Path("arbibuddy")
WORKBUDDY_WHEELHOUSE_ENV = "ARBIBUDDY_WORKBUDDY_WHEELHOUSE"
WORKBUDDY_RUNTIME_LOCK_RELATIVE = Path(
    "references/workbuddy-document-runtime.lock.json"
)
_HOME_FALLBACK_PLATFORMS = frozenset(("codex", "claude-code"))


class PlatformAdapterError(ValueError):
    """可预期的平台适配错误。"""


def platform_process_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """为平台 CLI 子进程准备环境，并保留用户显式的 HOME。"""

    environment = {**os.environ, **(overrides or {}), "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt" and not environment.get("HOME", "").strip():
        user_profile = environment.get("USERPROFILE", "").strip()
        if user_profile:
            environment["HOME"] = user_profile
    return environment


def _platform_environment_for(platform: str) -> dict[str, str] | None:
    if platform in _HOME_FALLBACK_PLATFORMS:
        return platform_process_environment()
    return None


def evaluate_capabilities(platform: str, capabilities: dict[str, Any]) -> dict[str, Any]:
    """校验能力观察形状；不根据结果选择或编排业务流程。"""
    _platform_config(platform)
    if not isinstance(capabilities, dict):
        raise PlatformAdapterError("能力观察必须是对象")
    for name in ("filesystem", "network", "scripts", "word", "structure_inspection"):
        value = capabilities.get(name)
        if not isinstance(value, bool) and not (isinstance(value, dict) and isinstance(value.get("available"), bool)):
            raise PlatformAdapterError(f"能力字段 {name} 必须是布尔值或含 available 布尔值的对象")
    return {"platform": platform, "capabilities": capabilities}

def build_artifact(
    *,
    platform: str,
    source: Path,
    output: Path,
    workbuddy_runtime_python: Path | None = None,
    workbuddy_wheelhouse: Path | None = None,
) -> dict[str, Any]:
    config = _platform_config(platform)
    installation = config.get("installation", {})
    if not installation.get("artifact_build_supported"):
        raise PlatformAdapterError(f"{platform} 不支持生成导入产物")
    source = public_path(path_for_io(source).resolve())
    _validate_skill_source(source)
    if platform == "workbuddy":
        _read_runtime_lock(source)
    skill_version = _skill_version(source)
    runtime_identity = build_runtime_identity(source)
    output = public_path(Path(os.path.abspath(output)))
    if path_for_io(output).exists():
        raise PlatformAdapterError("产物目标已存在，未覆盖")

    payload = _package_payload(source)
    if platform == "workbuddy":
        runtime_payload, renderer_dependencies = _build_workbuddy_runtime_layer(
            source,
            runtime_python=workbuddy_runtime_python,
            wheelhouse=workbuddy_wheelhouse,
        )
        payload.update(runtime_payload)
        try:
            payload[WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME] = (
                build_workbuddy_portable_runtime_marker(
                    runtime_identity,
                    runtime_payload["workbuddy-runtime/runtime-manifest.json"],
                )
            )
        except (KeyError, TypeError, RuntimeIdentityError) as error:
            raise PlatformAdapterError(
                "WorkBuddy 构建缺少可验证的原生上传便携身份标记"
            ) from error
    else:
        renderer_dependencies = {
            "verified": True,
            "required_files": [],
            "missing_files": [],
            "missing_packages": [],
        }
    files = {
        relative: hashlib.sha256(data).hexdigest()
        for relative, data in payload.items()
    }
    manifest = {
        "schema_version": ARTIFACT_MANIFEST_VERSION,
        "platform": platform,
        "skill": "arbibuddy",
        "skill_version": skill_version,
        "core_contract_version": CORE_CONTRACT_VERSION,
        "files": files,
        "runtime_identity": runtime_identity,
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
    }
    if platform == "workbuddy":
        if output.suffix.casefold() != ".zip":
            raise PlatformAdapterError("WorkBuddy 本地 Skill 包必须输出为 .zip 审计包")
        _enforce_build_payload_gate(payload, manifest)
        path_for_io(output.parent).mkdir(parents=True, exist_ok=True)
        _write_deterministic_zip(output, payload, manifest)
        format_name = "deterministic-zip"
        skill_root = "arbibuddy/"
    else:
        raise PlatformAdapterError(f"{platform} 不使用可生成的导入产物")

    return {
        "platform": platform,
        "artifact": str(output),
        "format": format_name,
        "skill_root": skill_root,
        "skill_version": skill_version,
        "runtime_identity": runtime_identity,
        "file_count": len(files),
        "artifact_contract_valid": True,
        "renderer_dependencies": renderer_dependencies,
        "renderer_dependencies_verified": renderer_dependencies["verified"],
        "client_import_observed": False,
        "runtime_discovered": False,
        "boundary": installation["verification_boundary"],
    }


def verify_artifact(
    *,
    platform: str,
    artifact: Path,
    workbuddy_runtime_python: Path | None = None,
) -> dict[str, Any]:
    _platform_config(platform)
    artifact = public_path(Path(os.path.abspath(artifact)))
    if platform == "workbuddy":
        return _verify_workbuddy_artifact(
            artifact,
            workbuddy_runtime_python=workbuddy_runtime_python,
        )
    raise PlatformAdapterError(f"{platform} 不使用导入产物验签")


def _enforce_build_payload_gate(
    payload: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> None:
    for relative, data in payload.items():
        path = Path(relative.replace("\\", "/"))
        if (
            path.is_absolute()
            or ".." in path.parts
            or not isinstance(data, bytes)
        ):
            raise PlatformAdapterError(
                f"WorkBuddy 构建输入包含不安全或无效文件：{relative}"
            )
    declared_files = manifest.get("files")
    if not isinstance(declared_files, Mapping):
        raise PlatformAdapterError("WorkBuddy 构建 manifest 缺少完整文件集")
    observed_files = {
        relative: hashlib.sha256(data).hexdigest()
        for relative, data in payload.items()
    }
    if dict(declared_files) != observed_files:
        raise PlatformAdapterError(
            "WorkBuddy 构建 manifest 与最终 payload 文件集或摘要不一致"
        )
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    uncompressed_size = len(manifest_bytes) + sum(
        len(data) for data in payload.values()
    )
    if uncompressed_size > MAX_ARTIFACT_UNCOMPRESSED_BYTES:
        raise PlatformAdapterError(
            "WorkBuddy 构建载荷解压后超过 50 MiB 上限；未创建 ZIP"
        )








def verify_core_contract(*, platform: str, skill_root: Path) -> dict[str, Any]:
    _platform_config(platform)
    return {"platform": platform, **inspect_runtime_resources(skill_root)}


def install_skill(
    *,
    platform: str,
    scope: str,
    target_root: Path,
    source: Path | None,
    mode: str,
    artifact: Path | None = None,
    workbuddy_runtime_python: Path | None = None,
    workbuddy_wheelhouse: Path | None = None,
) -> dict[str, Any]:
    if platform == "workbuddy":
        return _install_workbuddy(
            scope=scope,
            target_root=target_root,
            source=source,
            mode=mode,
            artifact=artifact,
            workbuddy_runtime_python=workbuddy_runtime_python,
            workbuddy_wheelhouse=workbuddy_wheelhouse,
        )
    if artifact is not None:
        raise PlatformAdapterError(f"{platform} 不支持从 WorkBuddy 审计包安装")
    if source is None:
        raise PlatformAdapterError(f"{platform}_source_missing:必须提供 source")
    config = _platform_config(platform)
    installation = config.get("installation", {})
    if installation.get("automated_local_install") is False:
        raise PlatformAdapterError(
            f"{platform} 官方资料未支持本地目录自动安装；本适配层不提供自动本地安装"
        )
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")
    if mode not in {"copy", "link"}:
        raise PlatformAdapterError("安装方式必须是 copy 或 link")

    source = public_path(path_for_io(source).resolve())
    _validate_skill_source(source)
    relative = Path(config[f"{scope}_discovery_path"])
    target_root = public_path(path_for_io(target_root).resolve())
    destination = target_root / relative
    if path_for_io(destination).exists() or path_for_io(destination).is_symlink():
        raise PlatformAdapterError("目标 Skill 路径已存在，未覆盖")
    path_for_io(destination.parent).mkdir(parents=True, exist_ok=True)

    if mode == "link":
        try:
            path_for_io(destination).symlink_to(path_for_io(source), target_is_directory=True)
        except OSError as error:
            raise PlatformAdapterError(f"无法创建目录链接：{error}") from error
    else:
        path_for_io(destination).mkdir()
        _copy_package_files(source, destination)

    runtime_identity = build_runtime_identity(source)
    receipt = {
        "schema_version": INSTALL_RECEIPT_SCHEMA_VERSION,
        "platform": platform,
        "scope": scope,
        "mode": mode,
        "target_root": str(target_root),
        "skill_root": str(destination),
        "discovery_path": relative.as_posix(),
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime_identity": runtime_identity,
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
    }
    receipt_path = _receipt_path(destination, platform=platform)
    path_for_io(receipt_path).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    verification = verify_install(platform=platform, skill_root=destination)
    if not (
        verification["discovery_contract_valid"]
        and verification["resources_complete"]
        and verification["renderer_dependencies_verified"]
        and verification["runtime_identity_verified"]
    ):
        raise PlatformAdapterError("安装后资源发现验证失败")
    return {
        "platform": platform,
        "scope": scope,
        "mode": mode,
        "skill_root": str(destination),
        "discovery_path": relative.as_posix(),
        "receipt": str(receipt_path),
        "skill_version": verification["skill_version"],
        "verified": True,
    }


def uninstall_skill(
    *,
    platform: str,
    scope: str,
    target_root: Path,
    skill_root: Path | None = None,
) -> dict[str, Any]:
    """Remove one owned Codex or Claude Code installation safely and idempotently."""

    if platform == "workbuddy":
        return _uninstall_workbuddy(
            platform=platform,
            scope=scope,
            target_root=target_root,
            skill_root=skill_root,
        )
    if platform not in {"codex", "claude-code"}:
        raise PlatformAdapterError(
            "当前完整生命周期只支持 Codex 和 Claude Code；其他平台由后续版本处理"
        )
    config = _platform_config(platform)
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")

    target_root = public_path(path_for_io(target_root).resolve())
    relative = Path(config[f"{scope}_discovery_path"])
    destination = public_path(target_root / relative)
    receipt_path = _receipt_path(destination)
    destination_exists = path_for_io(destination).exists() or path_for_io(
        destination
    ).is_symlink()
    receipt_exists = path_for_io(receipt_path).is_file()

    if not destination_exists and not receipt_exists:
        return {
            "platform": platform,
            "scope": scope,
            "skill_root": str(destination),
            "receipt": str(receipt_path),
            "skill_version": None,
            "uninstalled": False,
            "already_absent": True,
        }

    receipt = _read_receipt(destination)
    if not _receipt_binds_install(
        receipt,
        platform=platform,
        scope=scope,
        target_root=target_root,
        skill_root=destination,
        discovery_path=relative.as_posix(),
    ):
        raise PlatformAdapterError(
            "安装回执未绑定当前平台、作用域和发现路径；为避免删除非本 Skill 文件，已停止卸载"
        )
    if destination_exists and not (
        path_for_io(destination).is_dir() or path_for_io(destination).is_symlink()
    ):
        raise PlatformAdapterError("卸载目标不是目录或目录链接；已停止卸载")

    if destination_exists:
        _remove_update_path(destination)
    cleanup_error: OSError | None = None
    if path_for_io(receipt_path).exists():
        try:
            path_for_io(receipt_path).unlink()
        except OSError as error:
            cleanup_error = error
    report = {
        "platform": platform,
        "scope": scope,
        "skill_root": str(destination),
        "receipt": str(receipt_path),
        "skill_version": receipt.get("runtime_identity", {}).get("skill_version"),
        "uninstalled": True,
        "already_absent": False,
    }
    if cleanup_error is not None:
        report.update(
            {
                "cleanup_pending": True,
                "warnings": [
                    f"Skill 已卸载，但安装回执清理失败：{cleanup_error.__class__.__name__}"
                ],
            }
        )
    return report


def update_skill(
    *,
    platform: str,
    scope: str,
    target_root: Path,
    source: Path | None,
    mode: str,
    artifact: Path | None = None,
    workbuddy_runtime_python: Path | None = None,
    workbuddy_wheelhouse: Path | None = None,
) -> dict[str, Any]:
    """Transactionally update an existing directory installation.

    The staging tree and the rollback copy live beside the destination so all
    directory replacements stay on one filesystem, including Windows.  The
    live tree and receipt are untouched until the staged tree has passed the
    same verification used by install.
    """

    if platform == "workbuddy":
        return _update_workbuddy(
            scope=scope,
            target_root=target_root,
            source=source,
            mode=mode,
            artifact=artifact,
            workbuddy_runtime_python=workbuddy_runtime_python,
            workbuddy_wheelhouse=workbuddy_wheelhouse,
        )
    if artifact is not None:
        raise PlatformAdapterError(f"{platform} 不支持从 WorkBuddy 审计包更新")
    if source is None:
        raise PlatformAdapterError(f"{platform}_source_missing:必须提供 source")
    config = _platform_config(platform)
    installation = config.get("installation", {})
    if installation.get("automated_local_install") is False:
        raise PlatformAdapterError(
            f"{platform} 官方资料未支持本地目录自动更新；本适配层不提供自动本地更新"
        )
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")
    if mode != "copy":
        raise PlatformAdapterError(
            "update 只支持 mode=copy；如需链接安装，请重新执行 install --mode link"
        )

    source = public_path(path_for_io(source).resolve())
    _validate_skill_source(source)
    target_root = public_path(path_for_io(target_root).resolve())
    destination = target_root / Path(config[f"{scope}_discovery_path"])
    if not path_for_io(destination).is_dir() or path_for_io(destination).is_symlink():
        raise PlatformAdapterError("更新目标不是现有的目录安装；请先执行 install")

    receipt_path = _receipt_path(destination, platform=platform)
    try:
        old_receipt = path_for_io(receipt_path).read_bytes()
    except OSError as error:
        raise PlatformAdapterError("现有安装缺少可恢复的安装回执") from error

    transaction_root = destination.parent / (
        f".{destination.name}.update-{os.getpid()}-{uuid.uuid4().hex}"
    )
    staged_target_root = transaction_root / "target-root"
    staged_destination = staged_target_root / Path(config[f"{scope}_discovery_path"])
    staged_receipt = _receipt_path(staged_destination, platform=platform)
    final_receipt = transaction_root / "final-receipt.json"
    rollback_destination = transaction_root / "previous-skill"
    rollback_receipt = transaction_root / "previous-receipt.json"
    committed = False
    cleanup_error: OSError | None = None
    try:
        path_for_io(staged_destination.parent).mkdir(parents=True, exist_ok=False)
        _copy_package_files(source, staged_destination)

        runtime_identity = build_runtime_identity(source)
        receipt = _install_receipt(
            platform=platform,
            scope=scope,
            mode="copy",
            target_root=staged_target_root,
            skill_root=staged_destination,
            discovery_path=Path(config[f"{scope}_discovery_path"]).as_posix(),
            runtime_identity=runtime_identity,
        )
        path_for_io(staged_receipt).write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        final_receipt_value = _install_receipt(
            platform=platform,
            scope=scope,
            mode="copy",
            target_root=target_root,
            skill_root=destination,
            discovery_path=Path(config[f"{scope}_discovery_path"]).as_posix(),
            runtime_identity=runtime_identity,
        )
        path_for_io(final_receipt).write_text(
            json.dumps(final_receipt_value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        verification = verify_install(platform=platform, skill_root=staged_destination)
        if not (
            verification["discovery_contract_valid"]
            and verification["resources_complete"]
            and verification["runtime_identity_verified"]
        ):
            raise PlatformAdapterError("更新后资源发现验证失败")

        path_for_io(rollback_receipt).write_bytes(old_receipt)
        _replace_update_path(destination, rollback_destination)
        try:
            _replace_update_path(staged_destination, destination)
            _replace_update_path(final_receipt, receipt_path)
            final_verification = verify_install(
                platform=platform, skill_root=destination
            )
            if not (
                final_verification["discovery_contract_valid"]
                and final_verification["resources_complete"]
                and final_verification["runtime_identity_verified"]
            ):
                raise PlatformAdapterError("换入后的安装验证失败")
            committed = True
        except BaseException:
            _rollback_update(
                destination=destination,
                rollback_destination=rollback_destination,
                receipt_path=receipt_path,
                rollback_receipt=rollback_receipt,
            )
            raise
    except PlatformAdapterError:
        raise
    except OSError as error:
        raise PlatformAdapterError(
            f"更新事务失败：{error.__class__.__name__}"
        ) from error
    finally:
        try:
            if transaction_root.exists():
                shutil.rmtree(transaction_root)
        except OSError as error:
            cleanup_error = error

    if not committed:
        if cleanup_error is not None:
            raise PlatformAdapterError(
                f"更新事务未完成且暂存清理失败：{cleanup_error.__class__.__name__}"
            ) from cleanup_error
        raise PlatformAdapterError("更新事务未完成")

    report = {
        "platform": platform,
        "scope": scope,
        "mode": "copy",
        "skill_root": str(destination),
        "receipt": str(receipt_path),
        "skill_version": final_verification["skill_version"],
        "updated": True,
        "verified": True,
    }
    if cleanup_error is not None:
        report.update(
            {
                "cleanup_pending": True,
                "warnings": [
                    f"更新已完成，但事务暂存清理失败：{cleanup_error.__class__.__name__}"
                ],
            }
        )
    return report


def _install_workbuddy(
    *,
    scope: str,
    target_root: Path,
    source: Path | None,
    mode: str,
    artifact: Path | None,
    workbuddy_runtime_python: Path | None,
    workbuddy_wheelhouse: Path | None,
) -> dict[str, Any]:
    _validate_workbuddy_arguments(scope=scope, mode=mode)
    payload, runtime_identity, origin = _workbuddy_payload(
        source=source,
        artifact=artifact,
        runtime_python=workbuddy_runtime_python,
        wheelhouse=workbuddy_wheelhouse,
    )
    target_root, destination = _workbuddy_destination(scope, target_root)
    pending_cleanup = reap_workbuddy_cleanup(
        platform="workbuddy", target_root=target_root, scope=scope
    )
    if pending_cleanup["pending"]:
        raise PlatformAdapterError(
            "workbuddy_cleanup_pending:存在未恢复的 WorkBuddy 事务；请先重试 cleanup"
        )
    receipt_path = _receipt_path(destination, platform="workbuddy")
    if (
        path_for_io(destination).exists()
        or path_for_io(destination).is_symlink()
        or path_for_io(receipt_path).exists()
    ):
        if not path_for_io(destination).exists() and path_for_io(receipt_path).exists():
            raise PlatformAdapterError(
                "workbuddy_install_receipt_exists:安装目标缺失但遗留回执存在；请先清理后重试"
            )
        existing = verify_install(platform="workbuddy", skill_root=destination)
        if (
            existing.get("installation_mode") == "managed"
            and existing["discovery_contract_valid"]
            and existing["resources_complete"]
            and existing["renderer_dependencies_verified"]
            and existing["runtime_identity_verified"]
            and existing["runtime_identity"] == runtime_identity
        ):
            return {
                "platform": "workbuddy",
                "scope": scope,
                "mode": "copy",
                "skill_root": str(destination),
                "receipt": str(_receipt_path(destination, platform="workbuddy")),
                "installed": False,
                "already_installed": True,
                "verified": True,
                "cleanup": {"state": "complete", "warnings": []},
            }
        raise PlatformAdapterError(
            "workbuddy_install_target_exists:安装目标已存在且身份不一致；请执行 update"
        )

    transaction_root = _workbuddy_transaction_root(target_root, "install")
    staged_destination = transaction_root / WORKBUDDY_DISCOVERY_RELATIVE
    tree_committed = False
    committed = False
    try:
        path_for_io(target_root).mkdir(parents=True, exist_ok=True)
        path_for_io(staged_destination).mkdir(parents=True, exist_ok=False)
        _write_package_payload(staged_destination, payload)
        _verify_staged_workbuddy_install(
            staged_destination, expected_identity=runtime_identity
        )
        _write_workbuddy_receipt(
            staged_destination,
            _workbuddy_receipt(
                scope=scope,
                mode="copy",
                target_root=target_root,
                skill_root=destination,
                runtime_identity=runtime_identity,
                origin=origin,
                runtime_manifest_sha256=_runtime_manifest_digest(payload),
            ),
        )
        _replace_workbuddy_path(staged_destination, destination)
        tree_committed = True
        write_workbuddy_trust_anchor(
            target_root=target_root,
            skill_root=destination,
            runtime_identity=runtime_identity,
        )
        verification = verify_install(
            platform="workbuddy", skill_root=destination
        )
        if not (
            verification["discovery_contract_valid"]
            and verification["resources_complete"]
            and verification["renderer_dependencies_verified"]
            and verification["runtime_identity_verified"]
        ):
            raise PlatformAdapterError("workbuddy_install_verification_failed:安装后身份验证失败")
        readonly_hardening = _set_workbuddy_tree_readonly(destination)
        committed = True
    except BaseException as error:
        if not committed:
            try:
                _rollback_workbuddy_install(
                    destination=destination,
                    tree_committed=tree_committed,
                )
            except PlatformAdapterError as rollback_error:
                public_error = _workbuddy_exception("install", error)
                raise PlatformAdapterError(
                    f"{public_error};rollback_failed"
                ) from rollback_error
            _cleanup_workbuddy_transaction_best_effort(transaction_root)
        if isinstance(error, PlatformAdapterError):
            raise
        if isinstance(error, OSError):
            raise _workbuddy_io_error("install", error) from error
        raise

    cleanup = _finish_workbuddy_transaction(transaction_root)
    return {
        "platform": "workbuddy",
        "scope": scope,
        "mode": "copy",
        "skill_root": str(destination),
        "receipt": str(_receipt_path(destination, platform="workbuddy")),
        "installed": True,
        "already_installed": False,
        "verified": True,
        "readonly_hardening": readonly_hardening,
        "cleanup": cleanup,
    }


def _update_workbuddy(
    *,
    scope: str,
    target_root: Path,
    source: Path | None,
    mode: str,
    artifact: Path | None,
    workbuddy_runtime_python: Path | None,
    workbuddy_wheelhouse: Path | None,
) -> dict[str, Any]:
    _validate_workbuddy_arguments(scope=scope, mode=mode)
    payload, runtime_identity, origin = _workbuddy_payload(
        source=source,
        artifact=artifact,
        runtime_python=workbuddy_runtime_python,
        wheelhouse=workbuddy_wheelhouse,
    )
    target_root, destination = _workbuddy_destination(scope, target_root)
    pending_cleanup = reap_workbuddy_cleanup(
        platform="workbuddy", target_root=target_root, scope=scope
    )
    if pending_cleanup["pending"]:
        raise PlatformAdapterError(
            "workbuddy_cleanup_pending:存在未恢复的 WorkBuddy 事务；请先重试 cleanup"
        )
    if not path_for_io(destination).is_dir() or path_for_io(destination).is_symlink():
        raise PlatformAdapterError(
            "workbuddy_update_target_missing:更新目标不是现有的目录安装；请先执行 install"
        )
    existing = verify_install(platform="workbuddy", skill_root=destination)
    # 更新会原子换走旧树。仅附加普通文件可作为维护输入；运行身份仍拒绝它们。
    # 摘要、回执、外部锚点或 vendor 的任何其他失败均不得借此绕过。
    existing_identity_valid = existing["runtime_identity_verified"] or (
        set(existing["runtime_identity_verification"].get("failures", []))
        == {"installed_resource_set_mismatch"}
        and not installed_resource_failures(
            destination, allow_vendor=True, allow_extra_files=True
        )
    )
    if not (
        existing["discovery_contract_valid"]
        and existing["resources_complete"]
        and existing["renderer_dependencies_verified"]
        and existing_identity_valid
    ):
        raise PlatformAdapterError(
            "workbuddy_update_receipt_invalid:现有安装缺少可恢复的有效安装回执"
        )
    if not _make_workbuddy_tree_writable(destination)["verified"]:
        raise PlatformAdapterError(
            "workbuddy_update_readonly_clear_failed:无法解除现有安装树只读属性"
        )

    transaction_root = _workbuddy_transaction_root(target_root, "update")
    staged_destination = transaction_root / WORKBUDDY_DISCOVERY_RELATIVE
    rollback_destination = transaction_root / "previous"
    old_tree_moved = False
    new_tree_committed = False
    committed = False
    try:
        path_for_io(staged_destination).mkdir(parents=True, exist_ok=False)
        _write_package_payload(staged_destination, payload)
        _verify_staged_workbuddy_install(
            staged_destination, expected_identity=runtime_identity
        )
        _write_workbuddy_receipt(
            staged_destination,
            _workbuddy_receipt(
                scope=scope,
                mode="copy",
                target_root=target_root,
                skill_root=destination,
                runtime_identity=runtime_identity,
                origin=origin,
                runtime_manifest_sha256=_runtime_manifest_digest(payload),
            ),
        )

        _replace_workbuddy_path(destination, rollback_destination)
        old_tree_moved = True
        _replace_workbuddy_path(staged_destination, destination)
        new_tree_committed = True
        write_workbuddy_trust_anchor(
            target_root=target_root,
            skill_root=destination,
            runtime_identity=runtime_identity,
        )
        verification = verify_install(
            platform="workbuddy", skill_root=destination
        )
        if not (
            verification["discovery_contract_valid"]
            and verification["resources_complete"]
            and verification["renderer_dependencies_verified"]
            and verification["runtime_identity_verified"]
        ):
            raise PlatformAdapterError(
                "workbuddy_update_verification_failed:换入后的安装身份验证失败"
            )
        readonly_hardening = _set_workbuddy_tree_readonly(destination)
        committed = True
    except BaseException as error:
        if not committed:
            try:
                _rollback_workbuddy_update(
                    destination=destination,
                    rollback_destination=rollback_destination,
                    old_tree_moved=old_tree_moved,
                    new_tree_committed=new_tree_committed,
                )
            except PlatformAdapterError as rollback_error:
                public_error = _workbuddy_exception("update", error)
                raise PlatformAdapterError(
                    f"{public_error};rollback_failed"
                ) from rollback_error
            _cleanup_workbuddy_transaction_best_effort(transaction_root)
        if isinstance(error, PlatformAdapterError):
            raise
        if isinstance(error, OSError):
            raise _workbuddy_io_error("update", error) from error
        raise

    cleanup = _finish_workbuddy_transaction(transaction_root)
    return {
        "platform": "workbuddy",
        "scope": scope,
        "mode": "copy",
        "skill_root": str(destination),
        "receipt": str(_receipt_path(destination, platform="workbuddy")),
        "updated": True,
        "verified": True,
        "readonly_hardening": readonly_hardening,
        "cleanup": cleanup,
    }


def _uninstall_workbuddy(
    *,
    platform: str,
    scope: str,
    target_root: Path,
    skill_root: Path | None = None,
) -> dict[str, Any]:
    if platform != "workbuddy":
        raise PlatformAdapterError(
            f"{platform}_uninstall_unsupported:当前工单只提供 WorkBuddy 可恢复卸载"
        )
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")
    if skill_root is None:
        target_root, destination = _workbuddy_destination(scope, target_root)
    else:
        target_root, expected_destination = _workbuddy_destination(scope, target_root)
        destination = public_path(path_for_io(skill_root).resolve())
        if destination != expected_destination:
            raise PlatformAdapterError(
                "workbuddy_uninstall_scope_violation:卸载路径必须位于指定 target_root 的发现路径"
            )
    pending_cleanup = reap_workbuddy_cleanup(
        platform="workbuddy", target_root=target_root, scope=scope
    )
    if pending_cleanup["pending"]:
        raise PlatformAdapterError(
            "workbuddy_cleanup_pending:存在未恢复的 WorkBuddy 事务；请先重试 cleanup"
        )
    if not path_for_io(destination).exists():
        cleanup = reap_workbuddy_cleanup(
            platform="workbuddy", target_root=target_root, scope=scope
        )
        return {
            "platform": "workbuddy",
            "scope": scope,
            "skill_root": str(destination),
            "uninstalled": False,
            "already_absent": True,
            "cleanup": cleanup,
        }

    verification = verify_install(platform="workbuddy", skill_root=destination)
    if not (
        verification["discovery_contract_valid"]
        and verification["runtime_identity_verified"]
    ):
        raise PlatformAdapterError(
            "workbuddy_uninstall_receipt_invalid:拒绝删除身份未验证的安装目录"
        )
    if not _make_workbuddy_tree_writable(destination)["verified"]:
        raise PlatformAdapterError(
            "workbuddy_uninstall_readonly_clear_failed:无法解除安装树只读属性"
        )

    tombstone = _workbuddy_transaction_root(target_root, "uninstall")
    tree_moved = False
    try:
        _replace_workbuddy_path(destination, tombstone)
        tree_moved = True
    except BaseException as error:
        try:
            if tree_moved and path_for_io(tombstone).exists():
                _replace_workbuddy_path(tombstone, destination)
        except (OSError, PlatformAdapterError) as rollback_error:
            public_error = _workbuddy_exception("uninstall", error)
            raise PlatformAdapterError(
                f"{public_error};rollback_failed"
            ) from rollback_error
        _cleanup_workbuddy_transaction_best_effort(tombstone)
        if isinstance(error, PlatformAdapterError):
            raise
        if isinstance(error, OSError):
            raise _workbuddy_io_error("uninstall", error) from error
        raise

    cleanup = _finish_workbuddy_transaction(tombstone)
    return {
        "platform": "workbuddy",
        "scope": scope,
        "skill_root": str(destination),
        "uninstalled": True,
        "already_absent": False,
        "cleanup": cleanup,
    }


def identify_installation(
    *, platform: str, skill_root: Path
) -> dict[str, Any]:
    verification = verify_install(platform=platform, skill_root=skill_root)
    receipt = verification.get("installation")
    return {
        "platform": platform,
        "skill_root": verification["skill_root"],
        "skill_name": verification["skill_name"],
        "skill_version": verification["skill_version"],
        "runtime_identity_sha256": (
            receipt.get("runtime_identity_sha256")
            if isinstance(receipt, dict)
            else None
        ),
        "runtime_identity_verified": verification["runtime_identity_verified"],
        "receipt": str(_receipt_path(Path(verification["skill_root"]), platform=platform)),
        "verified": bool(
            verification["discovery_contract_valid"]
            and verification["resources_complete"]
            and verification["renderer_dependencies_verified"]
            and verification["runtime_identity_verified"]
        ),
    }


def reap_workbuddy_cleanup(
    *, platform: str, target_root: Path, scope: str
) -> dict[str, Any]:
    if platform != "workbuddy":
        raise PlatformAdapterError("cleanup_unsupported:当前工单只提供 WorkBuddy 清理回收")
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")
    target_root = public_path(path_for_io(target_root).resolve())
    if not path_for_io(target_root).is_dir():
        return {
            "platform": platform,
            "target_root": str(target_root),
            "cleaned": True,
            "already_clean": True,
            "pending": [],
            "warnings": [],
        }
    candidates = sorted(
        path
        for path in path_for_io(target_root).iterdir()
        if path.name.startswith(WORKBUDDY_TRANSACTION_PREFIX)
    )
    warnings: list[dict[str, str]] = []
    pending: list[str] = []
    for candidate in candidates:
        public_candidate = public_path(candidate)
        try:
            _recover_workbuddy_transaction(
                public_candidate, target_root=target_root, scope=scope
            )
            _cleanup_workbuddy_transaction(public_candidate)
        except (OSError, PlatformAdapterError) as error:
            pending.append(str(public_candidate))
            warnings.append(
                {
                    "code": "post_commit_cleanup_warning",
                    "path": str(public_candidate),
                    "detail": str(error),
                }
            )
    return {
        "platform": platform,
        "target_root": str(target_root),
        "cleaned": not pending,
        "already_clean": not candidates,
        "pending": pending,
        "warnings": warnings,
    }


def _workbuddy_install_is_verified(skill_root: Path) -> bool:
    if not path_for_io(skill_root).is_dir() or path_for_io(skill_root).is_symlink():
        return False
    verification = verify_install(platform="workbuddy", skill_root=skill_root)
    return bool(
        verification.get("installation_mode") == "managed"
        and verification["discovery_contract_valid"]
        and verification["resources_complete"]
        and verification["renderer_dependencies_verified"]
        and verification["runtime_identity_verified"]
    )


def _recover_workbuddy_transaction(
    transaction_root: Path, *, target_root: Path, scope: str
) -> None:
    """Recover an interrupted transaction before deleting its temporary tree."""

    target_root, destination = _workbuddy_destination(scope, target_root)
    name = transaction_root.name

    if "-update-" in name:
        rollback_destination = transaction_root / "previous"
        if _workbuddy_install_is_verified(destination):
            return
        if not path_for_io(rollback_destination).is_dir():
            raise PlatformAdapterError(
                "workbuddy_recovery_pending:更新事务缺少完整的旧安装回滚材料"
            )
        if path_for_io(destination).exists() or path_for_io(destination).is_symlink():
            _remove_update_path(destination)
        _replace_workbuddy_path(rollback_destination, destination)
        if not _workbuddy_install_is_verified(destination):
            raise PlatformAdapterError(
                "workbuddy_recovery_failed:旧安装恢复后身份验证失败"
            )
        return

    if "-install-" in name:
        if _workbuddy_install_is_verified(destination):
            return
        if path_for_io(destination).exists() or path_for_io(destination).is_symlink():
            _remove_update_path(destination)
        return

    if "-uninstall-" in name:
        if path_for_io(destination).exists():
            if _workbuddy_install_is_verified(destination):
                return
            raise PlatformAdapterError(
                "workbuddy_recovery_pending:卸载事务发现未验证的目标目录"
            )
        # The tombstone itself is the complete installation tree, including its
        # receipt.  If the destination is already absent, the uninstall commit
        # completed and only the tombstone cleanup remains.
        if path_for_io(transaction_root).is_dir():
            return
        raise PlatformAdapterError(
            "workbuddy_recovery_pending:卸载事务状态无法安全判定"
        )

    raise PlatformAdapterError("workbuddy_recovery_unknown:未知 WorkBuddy 事务")


def _validate_workbuddy_arguments(*, scope: str, mode: str) -> None:
    if scope not in {"project", "user"}:
        raise PlatformAdapterError("安装作用域必须是 project 或 user")
    if mode != "copy":
        raise PlatformAdapterError(
            "workbuddy_install_mode_unsupported:WorkBuddy 安装和更新只支持 copy"
        )


def _workbuddy_destination(scope: str, target_root: Path) -> tuple[Path, Path]:
    _validate_workbuddy_arguments(scope=scope, mode="copy")
    config = _platform_config("workbuddy")
    relative_value = config.get(f"{scope}_discovery_path")
    if not isinstance(relative_value, str) or not relative_value:
        raise PlatformAdapterError("workbuddy_discovery_path_missing:缺少本地发现路径")
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise PlatformAdapterError("workbuddy_discovery_path_invalid:发现路径越界")
    target = public_path(path_for_io(target_root).resolve())
    return target, target / relative


def _workbuddy_transaction_root(target_root: Path, operation: str) -> Path:
    return target_root / (
        f"{WORKBUDDY_TRANSACTION_PREFIX}{operation}-{os.getpid()}-{uuid.uuid4().hex}"
    )


def _workbuddy_payload(
    *,
    source: Path | None,
    artifact: Path | None,
    runtime_python: Path | None = None,
    wheelhouse: Path | None = None,
) -> tuple[dict[str, bytes], dict[str, Any], str]:
    if source is not None and artifact is not None:
        raise PlatformAdapterError("workbuddy_source_ambiguous:source 与 artifact 只能二选一")
    if source is None and artifact is None:
        raise PlatformAdapterError("workbuddy_source_missing:必须提供 source 或 artifact")
    if artifact is not None:
        verification = verify_artifact(platform="workbuddy", artifact=artifact)
        if not (
            verification["artifact_contract_valid"]
            and verification["integrity_verified"]
            and verification["resources_complete"]
            and verification["renderer_dependencies_verified"]
            and verification.get("native_upload_runtime_identity_verified", False)
            and verification["runtime_identity_verified"]
        ):
            raise PlatformAdapterError("workbuddy_artifact_invalid:审计包未通过身份与资源验证")
        payload, manifest = _read_workbuddy_artifact_payload(artifact)
        identity = manifest.get("runtime_identity")
        if not isinstance(identity, dict):
            raise PlatformAdapterError("workbuddy_artifact_identity_missing:审计包缺少运行时身份")
        return payload, identity, "artifact"

    source_root = public_path(path_for_io(source).resolve())
    _validate_skill_source(source_root)
    payload = _package_payload(source_root)
    runtime_payload, _runtime_report = _build_workbuddy_runtime_layer(
        source_root,
        runtime_python=runtime_python,
        wheelhouse=wheelhouse,
    )
    payload.update(runtime_payload)
    runtime_identity = build_runtime_identity(source_root)
    try:
        payload[WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME] = (
            build_workbuddy_portable_runtime_marker(
                runtime_identity,
                runtime_payload["workbuddy-runtime/runtime-manifest.json"],
            )
        )
    except (KeyError, TypeError, RuntimeIdentityError) as error:
        raise PlatformAdapterError(
            "WorkBuddy 构建缺少可验证的原生上传便携身份标记"
        ) from error
    return payload, runtime_identity, "source"


def _read_workbuddy_artifact_payload(
    artifact: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    artifact = public_path(path_for_io(artifact).resolve())
    try:
        with zipfile.ZipFile(path_for_io(artifact)) as archive:
            _validate_workbuddy_zip_member_names(archive.namelist())
            manifest = _load_artifact_manifest_from_bytes(
                archive.read("arbibuddy.package.json")
            )
            payload: dict[str, bytes] = {}
            expected_names = {
                "arbibuddy.package.json",
                *(f"arbibuddy/{relative}" for relative in manifest["files"]),
            }
            if set(archive.namelist()) != expected_names:
                raise PlatformAdapterError("WorkBuddy 审计包包含未登记或缺失文件")
            for relative, expected_digest in manifest["files"].items():
                data = archive.read(f"arbibuddy/{relative}")
                if hashlib.sha256(data).hexdigest() != expected_digest:
                    raise PlatformAdapterError(
                        f"WorkBuddy 审计包文件摘要不一致：{relative}"
                    )
                payload[relative] = data
            return payload, manifest
    except PlatformAdapterError:
        raise
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        raise PlatformAdapterError(
            f"workbuddy_artifact_read_failed:{error.__class__.__name__}"
        ) from error


def _load_artifact_manifest_from_bytes(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise PlatformAdapterError("WorkBuddy 审计包清单不是 UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != ARTIFACT_MANIFEST_VERSION
        or value.get("platform") != "workbuddy"
        or not isinstance(value.get("files"), dict)
    ):
        raise PlatformAdapterError("WorkBuddy 审计包清单不符合契约")
    _validate_artifact_file_map(value["files"])
    return value


def _validate_workbuddy_zip_member_names(names: list[str]) -> None:
    normalized = [name.replace("\\", "/") for name in names]
    if len(names) != len(set(normalized)):
        raise PlatformAdapterError("WorkBuddy 审计包包含重复的规范化文件名")
    for raw, name in zip(names, normalized):
        path = Path(name)
        if (
            raw != name
            or not name
            or name.endswith("/")
            or name.startswith("/")
            or (len(name) >= 2 and name[1] == ":")
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
        ):
            raise PlatformAdapterError("WorkBuddy 审计包包含不安全路径")


def _validate_artifact_file_map(files: Mapping[object, object]) -> None:
    normalized: set[str] = set()
    for relative, digest in files.items():
        if (
            not isinstance(relative, str)
            or not relative
            or relative != relative.replace("\\", "/")
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise PlatformAdapterError("WorkBuddy 审计包清单文件摘要格式无效")
        parts = relative.split("/")
        path = Path(relative)
        if (
            any(not part or part == "." for part in parts)
            or relative.startswith("/")
            or (len(relative) >= 2 and relative[1] == ":")
            or path.is_absolute()
            or ".." in path.parts
            or relative in normalized
        ):
            raise PlatformAdapterError("WorkBuddy 审计包清单包含不安全路径")
        normalized.add(relative)


def _workbuddy_receipt(
    *,
    scope: str,
    mode: str,
    target_root: Path,
    skill_root: Path,
    runtime_identity: dict[str, Any],
    origin: str,
    runtime_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        **_install_receipt(
            platform="workbuddy",
            scope=scope,
            mode=mode,
            target_root=target_root,
            skill_root=skill_root,
            discovery_path=WORKBUDDY_DISCOVERY_RELATIVE.as_posix(),
            runtime_identity=runtime_identity,
        ),
        "receipt_location": "skill_root",
        "origin": origin,
        "commit_boundary": "installation_tree_and_receipt",
        "cleanup_policy": "post_commit_warning_with_idempotent_reap",
        "runtime_manifest_sha256": runtime_manifest_sha256,
    }


def _write_workbuddy_receipt(skill_root: Path, receipt: dict[str, Any]) -> None:
    receipt_path = _receipt_path(skill_root, platform="workbuddy")
    path_for_io(receipt_path).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _verify_staged_workbuddy_install(
    skill_root: Path, *, expected_identity: dict[str, Any]
) -> None:
    missing, skill_name = _inspect_skill_source(skill_root)
    if missing or skill_name != "arbibuddy":
        raise PlatformAdapterError(
            "workbuddy_staged_resources_invalid:暂存安装树资源不完整"
        )
    _require_document_dependency_contract(skill_root)
    identity = compare_runtime_identity(expected_identity, skill_root)
    if not identity["verified"]:
        raise PlatformAdapterError(
            "workbuddy_staged_identity_mismatch:暂存安装树身份不一致"
        )
    core = verify_core_contract(platform="workbuddy", skill_root=skill_root)
    if not core["verified"]:
        raise PlatformAdapterError(
            "workbuddy_staged_core_invalid:暂存核心契约未通过;"
            f"missing={core.get('missing_resources', [])};"
            f"checks={core.get('checks', {})}"
        )


def _replace_workbuddy_path(source: Path, destination: Path) -> None:
    try:
        path_for_io(source).replace(path_for_io(destination))
    except OSError as error:
        raise _workbuddy_io_error("commit", error) from error


def _rollback_workbuddy_install(
    *,
    destination: Path,
    tree_committed: bool,
) -> None:
    try:
        if tree_committed and (
            path_for_io(destination).exists() or path_for_io(destination).is_symlink()
        ):
            _remove_update_path(destination)
    except OSError as error:
        raise PlatformAdapterError(
            "workbuddy_install_rollback_failed:安装失败且新安装清理失败"
        ) from error


def _rollback_workbuddy_update(
    *,
    destination: Path,
    rollback_destination: Path,
    old_tree_moved: bool,
    new_tree_committed: bool,
) -> None:
    try:
        if new_tree_committed and (
            path_for_io(destination).exists() or path_for_io(destination).is_symlink()
        ):
            _remove_update_path(destination)
        if old_tree_moved and path_for_io(rollback_destination).exists():
            _replace_workbuddy_path(rollback_destination, destination)
    except (OSError, PlatformAdapterError) as error:
        raise PlatformAdapterError(
            "workbuddy_rollback_failed:更新失败且旧安装回滚失败"
        ) from error


def _workbuddy_exception(operation: str, error: BaseException) -> PlatformAdapterError:
    if isinstance(error, PlatformAdapterError):
        return error
    if isinstance(error, OSError):
        return _workbuddy_io_error(operation, error)
    if isinstance(error, Exception):
        return PlatformAdapterError(
            f"filesystem_error:WorkBuddy {operation} 失败（{error.__class__.__name__}）"
        )
    raise error


def _cleanup_workbuddy_transaction(path: Path) -> None:
    _make_workbuddy_tree_writable(path)
    _remove_tree_without_recycle_bin(public_path(path))


def _cleanup_workbuddy_transaction_best_effort(path: Path) -> None:
    try:
        _cleanup_workbuddy_transaction(path)
    except OSError:
        pass


def _finish_workbuddy_transaction(path: Path) -> dict[str, Any]:
    try:
        _cleanup_workbuddy_transaction(path)
    except OSError as error:
        public_path_value = str(public_path(path))
        return {
            "state": "warning",
            "code": "post_commit_cleanup_warning",
            "pending_paths": [public_path_value],
            "warnings": [
                {
                    "code": "post_commit_cleanup_warning",
                    "path": public_path_value,
                    "detail": str(error),
                }
            ],
        }
    return {"state": "complete", "pending_paths": [], "warnings": []}


def _workbuddy_io_error(operation: str, error: OSError) -> PlatformAdapterError:
    text = str(error).casefold()
    winerror = getattr(error, "winerror", None)
    if winerror in {32, 33} or any(token in text for token in ("占用", "locked", "in use")):
        code = "file_in_use"
    elif isinstance(error, PermissionError) or winerror in {5, 1314}:
        code = "permission_denied"
    elif isinstance(error, FileNotFoundError) or winerror in {2, 3}:
        code = "path_not_found"
    else:
        code = "filesystem_error"
    return PlatformAdapterError(
        f"{code}:WorkBuddy {operation} 失败（{error.__class__.__name__}）"
    )


def _install_receipt(
    *,
    platform: str,
    scope: str,
    mode: str,
    target_root: Path,
    skill_root: Path,
    discovery_path: str,
    runtime_identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": INSTALL_RECEIPT_SCHEMA_VERSION,
        "platform": platform,
        "scope": scope,
        "mode": mode,
        "target_root": str(target_root),
        "skill_root": str(skill_root),
        "discovery_path": discovery_path,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime_identity": runtime_identity,
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
    }


def _replace_update_path(source: Path, destination: Path) -> None:
    path_for_io(source).replace(path_for_io(destination))


def _remove_update_path(path: Path) -> None:
    io_path = path_for_io(path)
    _make_workbuddy_tree_writable(path)
    if io_path.is_symlink() or io_path.is_file():
        io_path.unlink()
    elif io_path.is_dir():
        shutil.rmtree(io_path)


def _workbuddy_tree_files(root: Path) -> tuple[Path, ...]:
    io_root = path_for_io(root)
    if not io_root.is_dir() or io_root.is_symlink():
        return ()
    return tuple(
        candidate
        for candidate in io_root.rglob("*")
        if candidate.is_file() and not candidate.is_symlink()
    )


def _make_workbuddy_tree_writable(root: Path) -> dict[str, Any]:
    """Clear best-effort Windows read-only bits before lifecycle mutation."""

    if os.name != "nt":
        return {"attempted": False, "verified": True, "failures": []}
    failures: list[str] = []
    for candidate in _workbuddy_tree_files(root):
        try:
            os.chmod(candidate, stat.S_IREAD | stat.S_IWRITE)
        except OSError as error:
            failures.append(type(error).__name__)
    return {"attempted": True, "verified": not failures, "failures": failures}


def _set_workbuddy_tree_readonly(root: Path) -> dict[str, Any]:
    """Apply a best-effort Windows read-only bit; hashes remain the security boundary."""

    if os.name != "nt":
        return {"attempted": False, "verified": True, "failures": []}
    failures: list[str] = []
    for candidate in _workbuddy_tree_files(root):
        try:
            os.chmod(candidate, stat.S_IREAD)
        except OSError as error:
            failures.append(type(error).__name__)
    return {"attempted": True, "verified": not failures, "failures": failures}


def _rollback_update(
    *,
    destination: Path,
    rollback_destination: Path,
    receipt_path: Path,
    rollback_receipt: Path,
) -> None:
    try:
        if path_for_io(destination).exists() or path_for_io(destination).is_symlink():
            _remove_update_path(destination)
        if path_for_io(rollback_destination).exists():
            _replace_update_path(rollback_destination, destination)
        if path_for_io(rollback_receipt).is_file():
            _replace_update_path(rollback_receipt, receipt_path)
    except OSError as error:
        raise PlatformAdapterError(
            f"更新失败且旧安装回滚失败：{error.__class__.__name__}"
        ) from error


def inspect_skill_source(*, platform: str, source: Path) -> dict[str, Any]:
    config = _platform_config(platform)
    source = public_path(path_for_io(source).resolve())
    missing, skill_name = _inspect_skill_source(source)
    installation = config.get(
        "installation",
        {"mechanism": "local-discovery-path", "automated_local_install": True},
    )
    return {
        "platform": platform,
        "source": str(source),
        "skill_name": skill_name,
        "source_valid": not missing and skill_name == "arbibuddy",
        "resources_complete": not missing,
        "missing_resources": missing,
        "installation": installation,
        "runtime_discovered": False,
        "limitation": (
            "仅验证共享核心 Skill 源；须生成并验签平台产物，客户端导入或运行时发现需另行观察"
        ),
    }


def verify_install(*, platform: str, skill_root: Path) -> dict[str, Any]:
    config = _platform_config(platform)
    skill_root = public_path(Path(os.path.abspath(skill_root)))
    required = COMMON_REQUIRED_RESOURCES + tuple(config["required_metadata"])
    missing = [
        relative
        for relative in required
        if not path_for_io(skill_root / relative).is_file()
    ]
    renderer_dependencies = (
        _document_dependency_contract(skill_root)
        if platform == "workbuddy"
        else {"verified": True, "required_files": [], "missing_files": [], "missing_packages": []}
    )

    skill_file = skill_root / "SKILL.md"
    skill_name = _skill_name(skill_file) if path_for_io(skill_file).is_file() else None
    try:
        skill_version = _skill_version(skill_root)
    except PlatformAdapterError:
        skill_version = None
    receipt_path = _receipt_path(skill_root, platform=platform)
    managed_receipt_present = path_for_io(receipt_path).is_file()
    try:
        receipt = _read_receipt(skill_root, platform=platform)
    except PlatformAdapterError as error:
        receipt = {}
        receipt_error = type(error).__name__
    else:
        receipt_error = None
    receipt_scope = receipt.get("scope")
    expected_discovery_path = config.get(f"{receipt_scope}_discovery_path")
    receipt_target_root = public_path(
        Path(os.path.abspath(receipt.get("target_root", "")))
    )
    expected_skill_root = (
        public_path(Path(os.path.abspath(receipt_target_root / expected_discovery_path)))
        if expected_discovery_path is not None
        else None
    )
    receipt_matches = (
        receipt.get("schema_version") == INSTALL_RECEIPT_SCHEMA_VERSION
        and receipt.get("platform") == platform
        and receipt.get("mode") in {"copy", "link"}
        and public_path(Path(os.path.abspath(receipt.get("skill_root", "")))) == skill_root
        and expected_discovery_path is not None
        and receipt.get("discovery_path") == expected_discovery_path
        and expected_skill_root == skill_root
    )
    runtime_identity = None
    installation_mode = "managed"
    runtime_identity_verification: dict[str, Any] = {
        "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
        "verified": False,
        "failures": ["install_receipt_identity_missing"],
    }
    runtime_manifest_verification: dict[str, Any] = {
        "verified": False,
        "failures": ["runtime_manifest_missing"],
    }
    if platform == "workbuddy" and not managed_receipt_present and (
        path_for_io(skill_root / WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME).exists()
        or path_for_io(skill_root / RUNTIME_ROOT_NAME).exists()
    ):
        installation_mode = "native-upload"
        native_verification = RuntimeIdentityModule().preflight(skill_root)
        native_identity = native_verification.get("identity")
        runtime_identity = native_identity if isinstance(native_identity, dict) else None
        runtime_identity_verification = native_verification
        vendor_runtime = native_verification.get("vendor_runtime")
        runtime_manifest_digest_value = (
            vendor_runtime.get("runtime_manifest_sha256")
            if isinstance(vendor_runtime, dict)
            else None
        )
        receipt = {
            "schema_version": INSTALL_RECEIPT_SCHEMA_VERSION,
            "platform": "workbuddy",
            "scope": "native-upload",
            "mode": "portable",
            "target_root": str(skill_root.parent),
            "skill_root": str(skill_root),
            "discovery_path": skill_root.name,
            "runtime_identity": runtime_identity,
            "runtime_identity_sha256": (
                runtime_identity_digest(runtime_identity)
                if isinstance(runtime_identity, dict)
                else None
            ),
            "runtime_manifest_sha256": runtime_manifest_digest_value,
            "source": "portable-runtime-marker",
            "managed_receipt_required": False,
        }
        receipt_matches = native_verification.get("mode") == "portable"
        receipt_error = None
    if platform == "workbuddy":
        runtime_manifest_path = skill_root / RUNTIME_ROOT_NAME / RUNTIME_MANIFEST_NAME
        receipt_manifest_digest = receipt.get("runtime_manifest_sha256")
        if path_for_io(runtime_manifest_path).is_file():
            actual_manifest_digest = hashlib.sha256(
                path_for_io(runtime_manifest_path).read_bytes()
            ).hexdigest()
            manifest_failures: list[str] = []
            try:
                read_and_validate_runtime_manifest(
                    skill_root,
                    expected_abi="cp313",
                    expected_platform="win_amd64",
                )
            except (OSError, RuntimeManifestError):
                manifest_failures.append("runtime_manifest_contract_invalid")
            runtime_manifest_verification = {
                "verified": (
                    isinstance(receipt_manifest_digest, str)
                    and receipt_manifest_digest == actual_manifest_digest
                    and not manifest_failures
                ),
                "expected": receipt_manifest_digest,
                "observed": actual_manifest_digest,
                "failures": [
                    *(
                        []
                        if receipt_manifest_digest == actual_manifest_digest
                        else ["runtime_manifest_digest_mismatch"]
                    ),
                    *manifest_failures,
                ],
            }
    expected_identity = receipt.get("runtime_identity")
    if installation_mode == "managed" and isinstance(expected_identity, dict):
        try:
            runtime_identity = measure_runtime_identity(
                skill_root,
                source_commit=expected_identity.get("source_commit"),
            )
            runtime_identity_verification = compare_runtime_identity(
                expected_identity,
                skill_root,
            )
            source_link = receipt.get("mode") == "link" and is_source_link(skill_root)
            if not source_link:
                resource_failures = installed_resource_failures(skill_root, allow_vendor=platform == "workbuddy")
                if resource_failures:
                    runtime_identity_verification = {**runtime_identity_verification, "verified": False, "failures": [*runtime_identity_verification.get("failures", []), *resource_failures]}
            if receipt.get("runtime_identity_sha256") != runtime_identity_digest(
                expected_identity
            ):
                runtime_identity_verification = {
                    **runtime_identity_verification,
                    "verified": False,
                    "failures": [
                        *runtime_identity_verification.get("failures", []),
                        "install_receipt_identity_digest_mismatch",
                    ],
                }
            if receipt.get("schema_version") != INSTALL_RECEIPT_SCHEMA_VERSION:
                runtime_identity_verification = {
                    **runtime_identity_verification,
                    "verified": False,
                    "failures": [
                        *runtime_identity_verification.get("failures", []),
                        "install_receipt_schema_mismatch",
                    ],
                }
        except (OSError, ValueError, KeyError) as error:
            runtime_identity_verification = {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False,
                "failures": [f"runtime_identity_unreadable:{type(error).__name__}"],
            }
    if installation_mode == "managed" and receipt_error is not None:
        runtime_identity_verification = {
            **runtime_identity_verification,
            "verified": False,
            "failures": [receipt_error, *runtime_identity_verification.get("failures", [])],
        }
    if (
        platform == "workbuddy"
        and installation_mode == "managed"
        and isinstance(runtime_identity, dict)
    ):
        try:
            external_anchor_failures = _workbuddy_external_anchor_failures(
                skill_root,
                receipt,
                runtime_identity,
            )
        except (OSError, ValueError, TypeError):
            external_anchor_failures = ["external_trust_anchor_invalid"]
        if external_anchor_failures:
            runtime_identity_verification = {
                **runtime_identity_verification,
                "verified": False,
                "failures": [
                    *runtime_identity_verification.get("failures", []),
                    *external_anchor_failures,
                ],
            }
    if platform == "workbuddy" and not runtime_manifest_verification["verified"]:
        runtime_identity_verification = {
            **runtime_identity_verification,
            "verified": False,
            "failures": [
                *runtime_identity_verification.get("failures", []),
                *runtime_manifest_verification.get("failures", []),
            ],
        }
    core_identity = _core_identity(runtime_identity)
    return {
        "platform": platform,
        "skill_root": str(skill_root),
        "skill_name": skill_name,
        "skill_version": skill_version,
        "installation_mode": installation_mode,
        "discovery_contract_valid": skill_name == "arbibuddy" and receipt_matches,
        "resources_complete": not missing,
        "missing_resources": missing,
        "renderer_dependencies": renderer_dependencies,
        "renderer_dependencies_verified": renderer_dependencies["verified"],
        "runtime_manifest_verification": runtime_manifest_verification,
        "installation": receipt,
        "runtime_identity": runtime_identity,
        "core_identity": core_identity,
        "runtime_identity_verified": runtime_identity_verification["verified"],
        "runtime_identity_verification": runtime_identity_verification,
    }


def present_files(*, files: tuple[Path, ...]) -> dict[str, Any]:
    """Return a local file manifest; this does not display or attach files in a host UI."""

    presented: list[dict[str, Any]] = []
    for value in files:
        path = public_path(Path(os.path.abspath(value)))
        io_path = path_for_io(path)
        exists = io_path.is_file()
        presented.append(
            {
                "name": path.name,
                "path": str(path),
                "exists": exists,
                "size": io_path.stat().st_size if exists else None,
            }
        )
    return {
        "displayable": all(item["exists"] for item in presented),
        "files": presented,
    }


def verify_platform_discovery(
    *,
    platform: str,
    skill_root: Path,
    platform_command: list[str],
    lifecycle_evidence: Path | None = None,
) -> dict[str, Any]:
    verification = verify_install(platform=platform, skill_root=skill_root)
    if not (
        verification["discovery_contract_valid"]
        and verification["resources_complete"]
        and verification.get("renderer_dependencies_verified", True)
        and verification["runtime_identity_verified"]
    ):
        raise PlatformAdapterError("平台安装回执或资源发现契约验证失败")
    config = _platform_config(platform)
    probe = config["discovery_probe"]
    if platform == "workbuddy":
        return _verify_workbuddy_lifecycle_evidence(
            verification=verification,
            probe=probe,
            evidence_path=lifecycle_evidence,
            platform_command=platform_command,
        )
    process_environment = _platform_environment_for(platform)
    version = _probe_platform_version(platform_command, env=process_environment)
    target_root = verification["installation"]["target_root"]

    if probe["kind"] == "codex-prompt-input":
        result = _run_platform_command(
            [
                *platform_command,
                "-C",
                target_root,
                "debug",
                "prompt-input",
                probe["trigger"],
            ],
            timeout=60,
            env=process_environment,
        )
        output = f"{result.stdout}\n{result.stderr}".replace("\\", "/")
        expected_path = skill_root.as_posix()
        verified = (
            result.returncode == 0
            and "arbibuddy" in output
            and expected_path.casefold() in output.casefold()
        )
        evidence = {
            "level": "runtime-discovery",
            "runtime_discovered": verified,
            "method": "Codex debug prompt-input 解析后的技能上下文",
        }
    elif probe["kind"] == "claude-help-contract":
        result = _run_platform_command(
            [*platform_command, "--bare", "--help"],
            timeout=15,
            env=process_environment,
        )
        output = f"{result.stdout}\n{result.stderr}"
        verified = result.returncode == 0 and probe["marker"] in output
        evidence = {
            "level": "local-platform-contract",
            "runtime_discovered": False,
            "method": "Claude Code 本地 --bare --help 技能解析契约与官方发现路径",
        }
    else:
        raise PlatformAdapterError("平台发现探测类型不受支持")

    return {
        "platform": platform,
        "platform_version": version,
        "verified": verified,
        **evidence,
        "installation": verification,
        "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _verify_workbuddy_lifecycle_evidence(
    *,
    verification: dict[str, Any],
    probe: dict[str, Any],
    evidence_path: Path | None,
    platform_command: list[str],
) -> dict[str, Any]:
    required = tuple(probe.get("required_observations", ()))
    base = {
        "platform": "workbuddy",
        "platform_version": "unavailable",
        "verified": False,
        "level": "runtime-lifecycle-evidence",
        "runtime_discovered": False,
        "client_import_observed": False,
        "installation": verification,
        "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "missing_observations": list(required),
        "next_action": probe.get(
            "next_action", "重载、重启或重新导入 WorkBuddy Skill 后重新验证"
        ),
    }
    if evidence_path is None:
        return {
            **base,
            "reason": "lifecycle_evidence_missing:缺少 WorkBuddy 原生生命周期证据",
        }
    try:
        evidence = json.loads(
            path_for_io(public_path(path_for_io(evidence_path).resolve())).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {
            **base,
            "reason": f"lifecycle_evidence_unreadable:{error.__class__.__name__}",
        }
    if not isinstance(evidence, dict):
        return {**base, "reason": "lifecycle_evidence_invalid:证据顶层必须是对象"}
    if (
        evidence.get("schema_version") != 1
        or evidence.get("platform") != "workbuddy"
        or evidence.get("skill") != "arbibuddy"
    ):
        return {
            **base,
            "reason": "lifecycle_evidence_invalid:身份字段不匹配",
        }
    observations = evidence.get("observations")
    if not isinstance(observations, dict):
        return {**base, "reason": "lifecycle_evidence_invalid:缺少 observations"}
    missing = [
        name for name in required if observations.get(name) is not True
    ]
    expected_identity = verification["installation"].get("runtime_identity_sha256")
    identity_matches = (
        isinstance(expected_identity, str)
        and evidence.get("runtime_identity_sha256") == expected_identity
    )
    if not identity_matches:
        missing.append("runtime_identity_sha256")
    expected_root = public_path(path_for_io(verification["skill_root"]).resolve())
    observed_root = evidence.get("skill_root")
    if not isinstance(observed_root, str) or public_path(
        path_for_io(observed_root).resolve()
    ) != expected_root:
        missing.append("skill_root")
    version = evidence.get("platform_version")
    if isinstance(platform_command, list) and platform_command:
        try:
            version = _probe_platform_version(platform_command)
        except PlatformAdapterError as error:
            base["platform_version_probe"] = {
                "available": False,
                "state": "不可用（非阻断）",
                "detail": str(error),
            }
    if not isinstance(version, str) or not version.strip():
        version = "unavailable"
    deduplicated_missing = list(dict.fromkeys(missing))
    return {
        **base,
        "platform_version": version,
        "verified": not deduplicated_missing,
        "runtime_discovered": not deduplicated_missing,
        "client_import_observed": observations.get("registered") is True,
        "missing_observations": deduplicated_missing,
        "evidence": {
            "schema_version": evidence.get("schema_version"),
            "observations": {
                name: observations.get(name) for name in required
            },
            "runtime_identity_sha256": evidence.get("runtime_identity_sha256"),
        },
    }


def probe_capabilities(
    *,
    platform: str,
    skill_root: Path,
    platform_command: list[str],
    workspace: Path,
    network_check: str,
    network_url: str,
) -> dict[str, Any]:
    verification = verify_install(platform=platform, skill_root=skill_root)
    if (
        not verification["discovery_contract_valid"]
        or not verification["resources_complete"]
        or not verification["renderer_dependencies_verified"]
    ):
        raise PlatformAdapterError("平台安装回执或资源发现契约验证失败")
    process_environment = _platform_environment_for(platform)
    try:
        platform_version = _probe_platform_version(
            platform_command,
            env=process_environment,
        )
        platform_version_probe = {
            "available": True,
            "state": "已验证",
            "detail": platform_version,
        }
    except PlatformAdapterError as error:
        # Desktop-packaged clients may intentionally deny launching themselves
        # from their own sandbox. This probe still reports local workspace
        # capabilities without turning that boundary into a false failure.
        platform_version = "unavailable"
        platform_version_probe = {
            "available": False,
            "state": "不可用（非阻断）",
            "detail": str(error),
        }
    installation = verification["installation"]
    workspace = workspace.resolve()
    filesystem = _probe_filesystem(workspace)
    scripts = _probe_scripts()
    network = _probe_network(network_check, network_url)
    word = _probe_word(skill_root)
    structure = _probe_structure_inspection(skill_root)
    capabilities = {
        "filesystem": filesystem,
        "network": network,
        "scripts": scripts,
        "word": word,
        "structure_inspection": structure,
    }
    report = evaluate_capabilities(platform, capabilities)
    report["document_capabilities"] = {
        "docx_generation": word,
        "structure_inspection": structure,
    }
    report["attachment_presentation"] = _probe_attachment_presentation(workspace)
    report["complete_document_pipeline_available"] = bool(
        word["available"] and structure["available"]
    )
    report.update(
        {
            "platform_version": platform_version,
            "platform_version_probe": platform_version_probe,
            "install_method": f"{installation['scope']}-{installation['mode']}",
            "installation": verification,
            "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return report


_PROBE_DIAGNOSTIC_LIMIT = 1024
_PROBE_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\b\s*[:=]\s*[^\s,;]+"
)
_PROBE_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:[\\/]+|\\\\+|/(?!/))[^\r\n\s\"'<>]+"
)


def _sanitize_probe_output(value: object) -> str:
    text = value if isinstance(value, str) else str(value or "")
    text = _PROBE_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}=<REDACTED>",
        text,
    )
    text = _PROBE_ABSOLUTE_PATH_PATTERN.sub("<ABSOLUTE_PATH>", text)
    encoded = text.encode("utf-8", errors="replace")
    return encoded[:_PROBE_DIAGNOSTIC_LIMIT].decode("utf-8", errors="ignore")


def _probe_diagnostic(result: Any) -> dict[str, Any]:
    raw_stdout = getattr(result, "stdout", "")
    raw_stderr = getattr(result, "stderr", "")
    stdout = raw_stdout if isinstance(raw_stdout, str) else ""
    stderr = raw_stderr if isinstance(raw_stderr, str) else ""
    return {
        "stdout": _sanitize_probe_output(stdout),
        "stderr": _sanitize_probe_output(stderr),
        "stdout_truncated": len(stdout.encode("utf-8")) > _PROBE_DIAGNOSTIC_LIMIT,
        "stderr_truncated": len(stderr.encode("utf-8")) > _PROBE_DIAGNOSTIC_LIMIT,
    }


def _probe_exception_kind(error: BaseException) -> str:
    if isinstance(error, FileNotFoundError):
        return "tool_missing"
    if isinstance(error, subprocess.TimeoutExpired):
        return "execution_failed"
    return "execution_failed"


def _probe_structure_inspection(skill_root: Path) -> dict[str, Any]:
    return _probe_python_module(
        skill_root,
        module_name="scripts.documents.ooxml_check",
        available_method="实际导入 OOXML 结构检查入口",
        unavailable_method="结构检查入口加载失败",
    )


def _probe_python_module(
    skill_root: Path,
    *,
    module_name: str,
    available_method: str,
    unavailable_method: str,
) -> dict[str, Any]:
    """统一 Python 模块能力探针，避免 Word/OOXML 两套异常拼装漂移。"""

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import {module_name}",
            ],
            cwd=skill_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "available": False,
            "state": "不可用",
            "method": f"{unavailable_method}探测失败：{error.__class__.__name__}",
            "failure_kind": _probe_exception_kind(error),
            "returncode": None,
            "diagnostic": {
                "stdout": "",
                "stderr": error.__class__.__name__,
                "stdout_truncated": False,
                "stderr_truncated": False,
            },
        }
    return {
        "available": result.returncode == 0,
        "state": "可用" if result.returncode == 0 else "不可用",
        "method": (
            available_method
            if result.returncode == 0
            else f"{unavailable_method}（返回码 {result.returncode}）"
        ),
        "failure_kind": None if result.returncode == 0 else "module_load_failed",
        "returncode": result.returncode,
        "diagnostic": _probe_diagnostic(result),
    }


def _probe_filesystem(workspace: Path) -> dict[str, Any]:
    try:
        path_for_io(workspace).mkdir(parents=True, exist_ok=True)
        with runtime_temporary_directory(
            workspace, prefix="arbibuddy-platform-probe-"
        ) as temp:
            root = Path(temp)
            original = root / "能力探测-中文.tmp"
            final = root / "能力探测-中文.txt"
            path_for_io(original).write_text(
                "arbibuddy-platform-probe：中文路径与编码",
                encoding="utf-8",
            )
            os.replace(path_for_io(original), path_for_io(final))
            available = (
                path_for_io(final).read_text(encoding="utf-8")
                == "arbibuddy-platform-probe：中文路径与编码"
            )
            atomic_replace_verified = not path_for_io(original).exists()
            unicode_path_verified = final.name == "能力探测-中文.txt"
            permissions = {"write": True, "read": True}
        cleanup_verified = not path_for_io(Path(temp)).exists()
        return {
            "available": available,
            "state": "可用" if available else "不可用",
            "method": "中文路径下 UTF-8 文件写入、原子替换、读取与清理",
            "encoding": "utf-8",
            "unicode_path_verified": unicode_path_verified,
            "atomic_replace_verified": atomic_replace_verified,
            "cleanup_verified": cleanup_verified,
            "permissions": permissions,
        }
    except OSError as error:
        return {
            "available": False,
            "state": "不可用",
            "method": f"文件探测失败：{error}",
            "encoding": "utf-8",
            "unicode_path_verified": False,
            "atomic_replace_verified": False,
            "cleanup_verified": False,
            "permissions": {"write": False, "read": False},
        }


def _probe_attachment_presentation(workspace: Path) -> dict[str, Any]:
    probe = workspace / "附件展示探测-中文.txt"
    try:
        path_for_io(probe).write_text(
            "attachment-presentation-probe", encoding="utf-8"
        )
        manifest = present_files(files=(probe,))
        report = {
            "available": False,
            "state": "宿主展示未验证",
            "method": "本地文件清单可读不代表宿主已展示附件；当前 CLI 未连接宿主原生展示通道",
            "file_manifest_readable": manifest["displayable"],
            "host_presentation_verified": False,
        }
        try:
            path_for_io(probe).unlink()
        except OSError as error:
            report["cleanup_warning"] = error.__class__.__name__
        return report
    except OSError as error:
        return {
            "available": False,
            "state": "宿主展示未验证",
            "method": f"本地文件清单探测失败：{error.__class__.__name__}；当前 CLI 未连接宿主原生展示通道",
            "file_manifest_readable": False,
            "host_presentation_verified": False,
        }


def _probe_scripts() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        version = (result.stdout or result.stderr).strip()
        return {
            "available": result.returncode == 0,
            "state": "可用" if result.returncode == 0 else "不可用",
            "method": f"执行 {version or 'Python 版本探测失败'}",
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "state": "不可用", "method": f"脚本探测失败：{error}"}


def _probe_word(skill_root: Path) -> dict[str, Any]:
    return _probe_python_module(
        skill_root,
        module_name="scripts.documents.docx_builder",
        available_method="实际导入统一Word生成器及python-docx依赖",
        unavailable_method="统一Word生成器CLI加载失败",
    )


def _probe_network(mode: str, url: str) -> dict[str, Any]:
    if mode == "skip":
        return {"available": False, "state": "未测试", "method": "本次未执行联网探测"}
    if mode != "official-source":
        raise PlatformAdapterError("network-check 必须是 skip 或 official-source")
    try:
        request = Request(url, headers={"User-Agent": "ArbiBuddy-Capability-Probe/1"})
        with urlopen(request, timeout=10) as response:
            response.read(1)
            status = getattr(response, "status", 200)
        available = 200 <= status < 400
        return {
            "available": available,
            "state": "可用" if available else "不可用",
            "method": f"读取官方文档来源（HTTP {status}）",
        }
    except (OSError, ValueError) as error:
        diagnostic = classify_network_error(error)
        return {
            "available": False,
            "state": (
                "需要宿主联网权限"
                if diagnostic["kind"] == "network_permission"
                else "不可用"
            ),
            "method": f"联网探测失败：{diagnostic['error_code']}",
            "error_code": diagnostic["error_code"],
            "next_action": diagnostic["next_action"],
            "details": diagnostic["details"],
        }


def _package_paths(source: Path) -> tuple[Path, ...]:
    try:
        return runtime_paths(source)
    except (OSError, RuntimeResourceError) as error:
        raise PlatformAdapterError(str(error)) from error



def _package_payload(source: Path) -> dict[str, bytes]:
    source = public_path(path_for_io(source).resolve())
    return {
        path.relative_to(source).as_posix(): _published_file_bytes(source, path)
        for path in _package_paths(source)
    }


def _published_file_bytes(source: Path, path: Path) -> bytes:
    return path_for_io(path).read_bytes()

def _resolve_workbuddy_runtime_python(value: Path | None) -> Path:
    candidates: list[Path] = []
    if value is not None:
        candidates.append(Path(value))
    configured = os.environ.get("ARBIBUDDY_WORKBUDDY_RUNTIME_PYTHON", "").strip()
    if configured:
        candidates.append(Path(configured))
    home = Path.home()
    candidates.extend(
        [
            home
            / ".workbuddy"
            / "binaries"
            / "python"
            / "envs"
            / "arbibuddy"
            / "Scripts"
            / "python.exe",
            home
            / ".workbuddy"
            / "binaries"
            / "python"
            / "envs"
            / "default"
            / "Scripts"
            / "python.exe",
        ]
    )
    versions_root = home / ".workbuddy" / "binaries" / "python" / "versions"
    if versions_root.is_dir():
        candidates.extend(sorted(versions_root.glob("*/python.exe"), reverse=True))
    for candidate in candidates:
        resolved = path_for_io(candidate).resolve()
        if resolved.is_file():
            return public_path(resolved)
    raise PlatformAdapterError(
        "WorkBuddy 文书发布必须提供可执行的目标 Python（--runtime-python）；"
        "不得使用构建解释器 site-packages 代替目标解释器验证"
    )


def _runtime_manifest_digest(payload: Mapping[str, bytes]) -> str:
    relative = f"{RUNTIME_ROOT_NAME}/{RUNTIME_MANIFEST_NAME}"
    data = payload.get(relative)
    if not isinstance(data, bytes):
        raise PlatformAdapterError("WorkBuddy 文书运行时清单缺失")
    return hashlib.sha256(data).hexdigest()


def _probe_workbuddy_runtime_python(runtime_python: Path) -> dict[str, Any]:
    script = (
        "import json, platform, sys\n"
        "machine = platform.machine().casefold()\n"
        "platform_tag = 'win_amd64' if sys.platform == 'win32' and machine in {'amd64', 'x86_64'} else f'{sys.platform}_{machine}'\n"
        "print(json.dumps({'python_abi': f'cp{sys.version_info.major}{sys.version_info.minor}', 'platform_tag': platform_tag}, separators=(',', ':')))\n"
    )
    try:
        result = subprocess.run(
            [str(runtime_python), "-s", "-B", "-c", script],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlatformAdapterError(
            f"WorkBuddy 目标 Python 探测失败：{error.__class__.__name__}"
        ) from error
    if result.returncode != 0:
        raise PlatformAdapterError(
            "WorkBuddy 目标 Python 探测退出失败；禁止使用构建解释器冒充"
        )
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise PlatformAdapterError("WorkBuddy 目标 Python 探测未返回 JSON") from error
    if not isinstance(value, dict):
        raise PlatformAdapterError("WorkBuddy 目标 Python 探测结果无效")
    return value


def _reject_duplicate_json_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"运行时锁文件存在重复字段：{key}")
        result[key] = value
    return result


def _read_runtime_lock(source: Path) -> dict[str, Any]:
    path = source / WORKBUDDY_RUNTIME_LOCK_RELATIVE
    try:
        value = json.loads(
            path_for_io(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise PlatformAdapterError(
            f"WorkBuddy 文书运行时锁文件不可读取：{error.__class__.__name__}"
        ) from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise PlatformAdapterError("WorkBuddy 文书运行时锁文件契约无效")
    if value.get("python_abi") != "cp313" or value.get("platform_tag") != "win_amd64":
        raise PlatformAdapterError("WorkBuddy 文书运行时锁文件目标 ABI/平台无效")
    if not isinstance(value.get("wheelhouse"), str) or not value["wheelhouse"].strip():
        raise PlatformAdapterError("WorkBuddy 文书运行时锁文件缺少 wheelhouse")
    packages = value.get("packages")
    if not isinstance(packages, list) or not packages:
        raise PlatformAdapterError("WorkBuddy 文书运行时锁文件缺少 packages")
    for package in packages:
        if not isinstance(package, dict):
            raise PlatformAdapterError("WorkBuddy 文书运行时锁定包条目无效")
        if not all(
            isinstance(package.get(key), str) and package[key].strip()
            for key in ("distribution", "version", "wheel_filename", "wheel_path", "expand_path")
        ) or not re.fullmatch(r"[0-9a-f]{64}", str(package.get("wheel_sha256", ""))):
            raise PlatformAdapterError(
                f"WorkBuddy 文书运行时锁定包缺少 wheel SHA-256：{package.get('distribution')}"
            )
        wheel_filename = str(package["wheel_filename"])
        wheel_path = str(package["wheel_path"])
        expand_path = str(package["expand_path"])
        for label, path_value in (
            ("wheel_filename", wheel_filename),
            ("wheel_path", wheel_path),
            ("expand_path", expand_path),
        ):
            normalized = path_value.replace("\\", "/")
            candidate = Path(normalized)
            if (
                not normalized
                or candidate.is_absolute()
                or ".." in candidate.parts
            ):
                raise PlatformAdapterError(
                    f"WorkBuddy 文书运行时锁定包路径不安全：{label}"
                )
        if Path(wheel_filename.replace("\\", "/")).name != wheel_filename:
            raise PlatformAdapterError("WorkBuddy wheel_filename 必须是文件名")
        if Path(wheel_path.replace("\\", "/")).name != wheel_filename:
            raise PlatformAdapterError("WorkBuddy wheel_path 必须指向 wheel_filename")
        paths = package.get("paths")
        licenses = package.get("licenses")
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(item, str) or not item.strip() for item in paths)
            or not isinstance(licenses, list)
            or not licenses
            or any(not isinstance(item, str) or not item.strip() for item in licenses)
        ):
            raise PlatformAdapterError(
                f"WorkBuddy 文书运行时锁定包缺少展开路径或许可证：{package.get('distribution')}"
            )
    return value


def resolve_workbuddy_wheelhouse(
    source: Path,
    *,
    wheelhouse: Path | None = None,
    environment: Mapping[str, str] | None = None,
    lock: Mapping[str, Any] | None = None,
) -> Path:
    """Resolve the WorkBuddy wheelhouse using one caller-visible contract.

    The precedence is intentionally fixed: an explicit argument wins over the
    environment, and the environment wins over the wheelhouse declared by the
    repository runtime lock.  This function only selects and normalizes the
    directory; callers retain the existing existence, symlink, hash, ABI,
    platform, license, and archive-path gates.
    """

    selected: Path | None = Path(wheelhouse) if wheelhouse is not None else None
    if selected is None:
        environment = os.environ if environment is None else environment
        configured = environment.get(WORKBUDDY_WHEELHOUSE_ENV, "")
        if not isinstance(configured, str):
            raise PlatformAdapterError(
                f"{WORKBUDDY_WHEELHOUSE_ENV} 必须是路径字符串"
            )
        configured = configured.strip()
        if configured:
            selected = Path(configured)

    if selected is None:
        lock_value = lock if lock is not None else _read_runtime_lock(source)
        declared = lock_value.get("wheelhouse")
        if not isinstance(declared, str) or not declared.strip():
            raise PlatformAdapterError("WorkBuddy 文书运行时锁文件缺少 wheelhouse")
        normalized_declared = declared.replace("\\", "/")
        declared_path = Path(normalized_declared)
        if (
            declared_path.is_absolute()
            or declared_path.anchor
            or ".." in declared_path.parts
            or not normalized_declared.strip()
        ):
            raise PlatformAdapterError(
                "WorkBuddy 文书运行时锁定 wheelhouse 路径不安全"
            )
        selected = public_path(path_for_io(source).resolve()) / declared_path

    try:
        return public_path(path_for_io(selected).resolve())
    except (OSError, RuntimeError, ValueError) as error:
        raise PlatformAdapterError(
            "WorkBuddy 文书运行时 wheelhouse 路径不可解析"
        ) from error


def _runtime_payload_file(payload: dict[str, bytes], relative: str, data: bytes) -> None:
    if relative in payload:
        raise PlatformAdapterError(f"WorkBuddy 文书运行时文件名重复：{relative}")
    payload[relative] = data


def _build_workbuddy_runtime_layer(
    source: Path,
    *,
    runtime_python: Path | None,
    wheelhouse: Path | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    target_python = _resolve_workbuddy_runtime_python(runtime_python)
    lock = _read_runtime_lock(source)
    probe = _probe_workbuddy_runtime_python(target_python)
    if probe != {
        "python_abi": lock["python_abi"],
        "platform_tag": lock["platform_tag"],
    }:
        raise PlatformAdapterError(
            "WorkBuddy 目标 Python ABI/平台与锁文件不一致；禁止构建不可用文书包"
        )
    return _build_workbuddy_runtime_layer_from_wheels(
        source=source,
        lock=lock,
        target_python=target_python,
        probe=probe,
        wheelhouse=wheelhouse,
    )


def _build_workbuddy_runtime_layer_from_wheels(
    *,
    source: Path,
    lock: Mapping[str, Any],
    target_python: Path,
    probe: Mapping[str, Any],
    wheelhouse: Path | None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    configured = resolve_workbuddy_wheelhouse(
        source,
        wheelhouse=wheelhouse,
        lock=lock,
    )
    if not configured.is_dir() or configured.is_symlink():
        raise PlatformAdapterError(
            "WorkBuddy 文书运行时锁定 wheelhouse 不存在；"
            "请提供包含全部锁定 wheel 的受管目录，未联网下载或回退宿主 site-packages"
        )
    configured_for_io = path_for_io(configured).resolve()
    payload: dict[str, bytes] = {}
    runtime_files: dict[str, str] = {}
    package_reports: list[dict[str, Any]] = []
    licenses: list[str] = []
    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        raise PlatformAdapterError("WorkBuddy 文书运行时锁文件缺少 packages")
    for index, package in enumerate(packages, start=1):
        if not isinstance(package, Mapping):
            raise PlatformAdapterError(f"WorkBuddy wheel 锁条目 {index} 不是对象")
        distribution = package.get("distribution")
        version = package.get("version")
        wheel_filename = package.get("wheel_filename")
        wheel_sha256 = package.get("wheel_sha256")
        wheel_path_value = package.get("wheel_path")
        expand_path = package.get("expand_path")
        roots = package.get("paths")
        package_licenses = package.get("licenses")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                distribution,
                version,
                wheel_filename,
                wheel_path_value,
                expand_path,
            )
        ) or (
            not isinstance(roots, list)
            or not roots
            or any(not isinstance(value, str) or not value.strip() for value in roots)
            or not isinstance(package_licenses, list)
            or not package_licenses
            or any(
                not isinstance(value, str) or not value.strip()
                for value in package_licenses
            )
        ):
            raise PlatformAdapterError(f"WorkBuddy wheel 锁条目 {index} 字段不完整")
        normalized_expand = expand_path.replace("\\", "/")
        if (
            Path(normalized_expand).is_absolute()
            or ".." in Path(normalized_expand).parts
            or not normalized_expand.strip()
        ):
            raise PlatformAdapterError(
                f"WorkBuddy wheel 锁条目 {distribution} 展开路径不安全"
            )
        if Path(str(wheel_path_value).replace("\\", "/")).name != wheel_filename:
            raise PlatformAdapterError(
                f"WorkBuddy wheel 锁条目 {distribution} wheel_path 与文件名不一致"
            )
        for value in (*roots, *package_licenses):
            if not isinstance(value, str):
                raise PlatformAdapterError(
                    f"WorkBuddy wheel 锁条目 {distribution} 路径字段无效"
                )
            normalized = value.replace("\\", "/")
            if (
                not normalized.strip()
                or Path(normalized).is_absolute()
                or ".." in Path(normalized).parts
            ):
                raise PlatformAdapterError(
                    f"WorkBuddy wheel 锁条目 {distribution} 路径穿越"
                )
        if not isinstance(wheel_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", wheel_sha256
        ):
            raise PlatformAdapterError(
                f"WorkBuddy wheel 锁条目 {distribution} 缺少有效 SHA-256"
            )
        normalized_filename = str(wheel_filename).replace("\\", "/")
        normalized_wheel_path = str(wheel_path_value).replace("\\", "/")
        if (
            Path(normalized_filename).is_absolute()
            or ".." in Path(normalized_filename).parts
            or Path(normalized_filename).name != wheel_filename
            or Path(normalized_wheel_path).is_absolute()
            or ".." in Path(normalized_wheel_path).parts
            or Path(normalized_wheel_path).name != wheel_filename
        ):
            raise PlatformAdapterError(
                f"WorkBuddy wheel 锁条目 {distribution} wheel 路径不安全"
            )
        raw_wheel = configured / wheel_filename
        wheel = path_for_io(raw_wheel).resolve()
        if (
            raw_wheel.is_symlink()
            or not wheel.is_file()
            or not wheel.is_relative_to(configured_for_io)
        ):
            raise PlatformAdapterError(
                f"WorkBuddy 锁定 wheel 缺失：{wheel_filename}；"
                "请提供可复验的受管 wheelhouse"
            )
        observed_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if observed_sha256 != wheel_sha256:
            raise PlatformAdapterError(
                f"WorkBuddy wheel SHA-256 不匹配：{wheel_filename}"
            )
        try:
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                normalized_archive_names = [name.replace("\\", "/") for name in names]
                if len(names) != len(set(normalized_archive_names)):
                    raise PlatformAdapterError(
                        f"WorkBuddy wheel 包含重复文件：{wheel_filename}"
                    )
                safe_names = []
                for name, normalized in zip(names, normalized_archive_names):
                    name_path = Path(normalized)
                    if (
                        not normalized
                        or name_path.is_absolute()
                        or ".." in name_path.parts
                        or normalized.endswith("/")
                    ):
                        continue
                    safe_names.append(normalized)
                if len(safe_names) != len(names):
                    raise PlatformAdapterError(
                        f"WorkBuddy wheel 包含路径穿越或目录条目：{wheel_filename}"
                    )
                copied: list[str] = []
                expected_roots = tuple(
                    root.rstrip("/").replace("\\", "/") for root in roots
                )
                # This is an extracted runtime subset, not a republished wheel.
                # The unused ISO Schematron module loads unlicensed XSL resources
                # at import time, so omit the whole module rather than two files.
                excluded = []
                for name in safe_names:
                    if not any(
                        name == root or name.startswith(root + "/")
                        for root in expected_roots
                    ):
                        continue
                    if distribution == "lxml" and name.startswith("lxml/isoschematron/"):
                        excluded.append(name)
                        continue
                    data = archive.read(name)
                    relative = f"{expand_path.rstrip('/')}/{name}"
                    target_relative = (
                        Path(RUNTIME_ROOT_NAME) / relative
                    ).as_posix()
                    _runtime_payload_file(payload, target_relative, data)
                    runtime_files[relative] = hashlib.sha256(data).hexdigest()
                    copied.append(relative)
                for root in expected_roots:
                    target_root = f"{expand_path.rstrip('/')}/{root}"
                    if not any(
                        item == target_root or item.startswith(target_root + "/")
                        for item in copied
                    ):
                        raise PlatformAdapterError(
                            f"WorkBuddy wheel 展开缺少锁定路径：{root}"
                        )
                for license_relative in package_licenses:
                    if not isinstance(license_relative, str):
                        raise PlatformAdapterError(
                            f"WorkBuddy wheel 许可证路径无效：{distribution}"
                        )
                    normalized_license = (
                        f"{expand_path.rstrip('/')}/"
                        f"{license_relative.replace('\\', '/')}"
                    )
                    if normalized_license not in runtime_files:
                        raise PlatformAdapterError(
                            f"WorkBuddy wheel 缺少许可证：{license_relative}"
                        )
                    licenses.append(normalized_license)
        except zipfile.BadZipFile as error:
            raise PlatformAdapterError(
                f"WorkBuddy 锁定 wheel 不是有效 ZIP：{wheel_filename}"
            ) from error
        package_reports.append(
            {
                "distribution": distribution,
                "version": version,
                "wheel_filename": wheel_filename,
                "wheel_sha256": wheel_sha256,
                "expand_path": expand_path,
                "files": copied,
                "excluded_files": excluded,
            }
        )
    manifest = {
        "schema_version": 1,
        "python_abi": lock["python_abi"],
        "platform_tag": lock["platform_tag"],
        "packages": package_reports,
        "licenses": sorted(set(licenses)),
        "files": dict(sorted(runtime_files.items())),
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    manifest_relative = (Path(RUNTIME_ROOT_NAME) / RUNTIME_MANIFEST_NAME).as_posix()
    _runtime_payload_file(payload, manifest_relative, manifest_bytes)
    return payload, {
        "verified": True,
        "runtime_available": True,
        "packaged_runtime": True,
        "python_abi": lock["python_abi"],
        "platform_tag": lock["platform_tag"],
        "required_files": [WORKBUDDY_RUNTIME_LOCK_RELATIVE.as_posix()],
        "missing_files": [],
        "missing_packages": [],
        "vendor_files": sorted(runtime_files),
        "packages": package_reports,
        "licenses": sorted(set(licenses)),
        "target_python": str(target_python),
        "target_probe": dict(probe),
        "wheelhouse": str(configured),
    }


def _document_dependency_contract(skill_root: Path) -> dict[str, Any]:
    packaged_runtime_root = skill_root / RUNTIME_ROOT_NAME
    packaged_manifest = skill_root / RUNTIME_ROOT_NAME / RUNTIME_MANIFEST_NAME
    if (
        (packaged_runtime_root.exists() or packaged_runtime_root.is_symlink())
        and not (
            path_for_io(packaged_manifest).exists()
            or path_for_io(packaged_manifest).is_symlink()
        )
    ):
        return {
            "verified": False,
            "runtime_available": False,
            "packaged_runtime": True,
            "required_files": [RUNTIME_MANIFEST_NAME],
            "missing_files": [RUNTIME_MANIFEST_NAME],
            "missing_packages": [],
        }
    if path_for_io(packaged_manifest).exists() or path_for_io(packaged_manifest).is_symlink():
        try:
            value = read_and_validate_runtime_manifest(
                skill_root,
                expected_abi="cp313",
                expected_platform="win_amd64",
            )
            return {
                "verified": True,
                "runtime_available": True,
                "packaged_runtime": True,
                "python_abi": value["python_abi"],
                "platform_tag": value["platform_tag"],
                "required_files": [RUNTIME_MANIFEST_NAME],
                "missing_files": [],
                "missing_packages": [],
                "vendor_files": sorted(value["files"]),
                "packages": value["packages"],
                "licenses": value["licenses"],
            }
        except (OSError, UnicodeError, RuntimeManifestError) as error:
            return {
                "verified": False,
                "runtime_available": False,
                "packaged_runtime": True,
                "required_files": [RUNTIME_MANIFEST_NAME],
                "missing_files": [f"runtime_manifest:{type(error).__name__}"],
                "missing_packages": [],
            }
    path = skill_root / DOCUMENT_DEPENDENCY_FILE
    if not path_for_io(path).is_file():
        return {
            "verified": False,
            "runtime_available": False,
            "required_files": [DOCUMENT_DEPENDENCY_FILE],
            "missing_files": [DOCUMENT_DEPENDENCY_FILE],
            "missing_packages": ["python-docx"],
        }
    try:
        text = path_for_io(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {
            "verified": False,
            "runtime_available": False,
            "required_files": [DOCUMENT_DEPENDENCY_FILE],
            "missing_files": [],
            "missing_packages": ["python-docx"],
        }
    declared = bool(DOCUMENT_DEPENDENCY_PATTERN.search(text))
    runtime_available = importlib.util.find_spec("docx") is not None
    return {
        "verified": declared and runtime_available,
        "runtime_available": runtime_available,
        "required_files": [DOCUMENT_DEPENDENCY_FILE],
        "missing_files": [],
        "missing_packages": []
        if declared and runtime_available
        else ["python-docx"],
    }


def _require_document_dependency_contract(skill_root: Path) -> None:
    contract = _document_dependency_contract(skill_root)
    if not contract["verified"]:
        missing = ", ".join(
            [*contract["missing_files"], *contract["missing_packages"]]
        )
        raise PlatformAdapterError(
            f"document_renderer_dependency_contract_missing:{missing}"
        )


def _write_package_payload(destination: Path, payload: dict[str, bytes]) -> None:
    for relative, data in payload.items():
        target = destination / Path(relative)
        path_for_io(target.parent).mkdir(parents=True, exist_ok=True)
        path_for_io(target).write_bytes(data)


def _copy_package_files(source: Path, destination: Path) -> None:
    for path in _package_paths(source):
        relative = path.relative_to(source)
        target = destination / relative
        path_for_io(target.parent).mkdir(parents=True, exist_ok=True)
        path_for_io(target).write_bytes(_published_file_bytes(source, path))


def _write_deterministic_zip(
    output: Path,
    payload: dict[str, bytes],
    manifest: dict[str, Any],
) -> None:
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    with zipfile.ZipFile(path_for_io(output), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_zip_entry(archive, "arbibuddy.package.json", manifest_bytes)
        for relative, data in payload.items():
            _write_zip_entry(archive, f"arbibuddy/{relative}", data)


def _write_zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def _load_artifact_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path_for_io(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, ValueError) as error:
        raise PlatformAdapterError(f"无法读取产物清单：{error.__class__.__name__}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != ARTIFACT_MANIFEST_VERSION
        or value.get("skill") != "arbibuddy"
        or not isinstance(value.get("files"), dict)
    ):
        raise PlatformAdapterError("产物清单不符合契约")
    skill_version = value.get("skill_version")
    if skill_version is not None and (
        not isinstance(skill_version, str)
        or re.fullmatch(r"v\d+\.\d+\.\d+(?:-rc\d+)?", skill_version) is None
    ):
        raise PlatformAdapterError("产物清单的 skill_version 不符合契约")
    _validate_artifact_file_map(value["files"])
    return value


def _manifest_integrity(skill_root: Path, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    expected = manifest["files"]
    io_skill_root = path_for_io(skill_root)
    actual_paths = tuple(
        sorted(
            (
                public_path(path)
                for path in io_skill_root.rglob("*")
                if path.is_file()
            ),
            key=lambda path: path.relative_to(skill_root).as_posix(),
        )
    )
    actual = {
        path.relative_to(skill_root).as_posix(): hashlib.sha256(
            path_for_io(path).read_bytes()
        ).hexdigest()
        for path in actual_paths
    }
    differences = sorted(
        relative
        for relative in set(expected) | set(actual)
        if expected.get(relative) != actual.get(relative)
    )
    return not differences, differences


def _workbuddy_runtime_smoke(skill_root: Path, runtime_python: Path) -> dict[str, Any]:
    script = (
        "import json, sys, tempfile, zipfile\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1]).resolve()\n"
        "sys.path.insert(0, str(root))\n"
        "from scripts.documents.runtime_dependencies import ensure_document_runtime\n"
        "ensure_document_runtime(root)\n"
        "from scripts.documents.docx_builder import _new_legal_document\n"
        "with tempfile.TemporaryDirectory() as temp:\n"
        "    output = Path(temp) / 'smoke.docx'\n"
        "    document = _new_legal_document()\n"
        "    document.add_paragraph('ArbiBuddy WorkBuddy runtime smoke')\n"
        "    document.save(output)\n"
        "    with zipfile.ZipFile(output) as package:\n"
        "        parts = set(package.namelist())\n"
        "    if not {'[Content_Types].xml', 'word/document.xml', 'word/footer1.xml'} <= parts:\n"
        "        raise RuntimeError('OOXML parts missing')\n"
        "print(json.dumps({'verified': True, 'ooxml_parts': ['[Content_Types].xml', 'word/document.xml', 'word/footer1.xml']}))\n"
    )
    try:
        process = subprocess.run(
            [str(runtime_python), "-s", "-B", "-c", script, str(skill_root)],
            cwd=path_for_io(skill_root),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"verified": False, "error": type(error).__name__}
    try:
        value = json.loads(process.stdout.strip())
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    return {
        **value,
        "verified": process.returncode == 0 and value.get("verified") is True,
        "returncode": process.returncode,
        "stderr": process.stderr[-500:] if process.stderr else "",
    }


def _verify_workbuddy_artifact(
    artifact: Path,
    *,
    workbuddy_runtime_python: Path | None = None,
) -> dict[str, Any]:
    if not path_for_io(artifact).is_file() or artifact.suffix.casefold() != ".zip":
        raise PlatformAdapterError("WorkBuddy 审计包不存在或不是 .zip")
    try:
        with zipfile.ZipFile(path_for_io(artifact)) as archive:
            names = archive.namelist()
            infos = archive.infolist()
            _validate_workbuddy_zip_member_names(names)
            if names.count("arbibuddy.package.json") != 1:
                raise PlatformAdapterError("WorkBuddy 审计包必须且只能包含一份清单")
            if sum(info.file_size for info in infos) > MAX_ARTIFACT_UNCOMPRESSED_BYTES:
                raise PlatformAdapterError("WorkBuddy 审计包解压后超过 50 MiB 上限")
            manifest_info = next(
                info for info in infos if info.filename == "arbibuddy.package.json"
            )
            if manifest_info.file_size > 2 * 1024 * 1024:
                raise PlatformAdapterError("WorkBuddy 审计包清单超过 2 MiB 上限")
            manifest_value = json.loads(
                archive.read("arbibuddy.package.json").decode("utf-8")
            )
            manifest_files = manifest_value.get("files")
            if not isinstance(manifest_files, dict):
                raise PlatformAdapterError("WorkBuddy 审计包清单缺少 files")
            expected_names = {
                "arbibuddy.package.json",
                *(f"arbibuddy/{relative}" for relative in manifest_files),
            }
            if set(names) != expected_names or len(names) != len(expected_names):
                raise PlatformAdapterError("WorkBuddy 审计包包含未登记或重复的文件")
            verification_anchor = artifact.parent
            use_configured_temp_root = True
            configured_temp_root = os.environ.get("ARBIBUDDY_TEST_TEMP_ROOT")
            projected_root = (
                Path(configured_temp_root).expanduser().resolve()
                if configured_temp_root
                else verification_anchor.expanduser().resolve() / ".tmp"
            )
            longest_member = max((len(name) for name in names), default=0)
            projected_runtime_path_length = (
                len(str(projected_root))
                + len("arbibuddy-artifact-")
                + 12
                + 1
                + longest_member
            )
            if projected_runtime_path_length >= 240:
                # Keep the extracted runtime smoke tree short enough for the
                # target Windows interpreter; the artifact itself is still
                # read from and verified at its original path.
                local_appdata = os.environ.get("LOCALAPPDATA")
                verification_anchor = (
                    Path(local_appdata) / "Temp"
                    if local_appdata
                    else Path(tempfile.gettempdir())
                )
                use_configured_temp_root = False
            with runtime_temporary_directory(
                verification_anchor,
                prefix="arbibuddy-artifact-",
                use_configured_root=use_configured_temp_root,
            ) as temp:
                root = Path(temp)
                archive.extractall(path_for_io(root))
                skill_root = root / "arbibuddy"
                manifest_path = root / "arbibuddy.package.json"
                path_for_io(manifest_path).write_text(
                    json.dumps(manifest_value, ensure_ascii=False),
                    encoding="utf-8",
                )
                manifest = _load_artifact_manifest(manifest_path)
                if manifest.get("platform") != "workbuddy":
                    raise PlatformAdapterError("产物清单平台不匹配")
                integrity, differences = _manifest_integrity(skill_root, manifest)
                missing, skill_name = _inspect_skill_source(skill_root)
                document_dependencies = _document_dependency_contract(skill_root)
                try:
                    target_python = _resolve_workbuddy_runtime_python(
                        workbuddy_runtime_python
                    )
                    isolated_smoke = _workbuddy_runtime_smoke(
                        skill_root,
                        target_python,
                    )
                except PlatformAdapterError as error:
                    isolated_smoke = {
                        "verified": False,
                        "error": str(error),
                    }
                document_dependencies = {
                    **document_dependencies,
                    "isolated_smoke": isolated_smoke,
                    "verified": bool(
                        document_dependencies.get("verified")
                        and isolated_smoke.get("verified")
                    ),
                }
                core = verify_core_contract(platform="workbuddy", skill_root=skill_root)
                # Verify the exact subtree a native WorkBuddy upload retains.
                # The ZIP-root manifest is deliberately removed from this
                # temporary tree so it cannot satisfy the fallback marker path.
                path_for_io(manifest_path).unlink()
                native_upload_root = root / "native-upload"
                native_upload_skill_root = native_upload_root / "arbibuddy"
                shutil.copytree(skill_root, native_upload_skill_root)
                native_upload_runtime_verification = RuntimeIdentityModule().preflight(
                    native_upload_skill_root
                )
                expected_identity = manifest.get("runtime_identity")
                if not isinstance(expected_identity, dict):
                    runtime_identity = None
                    runtime_verification = {
                        "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                        "verified": False,
                        "failures": ["artifact_runtime_identity_missing"],
                    }
                else:
                    runtime_identity = measure_runtime_identity(
                        skill_root,
                        source_commit=expected_identity.get("source_commit"),
                    )
                    runtime_verification = compare_runtime_identity(
                        expected_identity,
                        skill_root,
                    )
                    if manifest.get("runtime_identity_sha256") != runtime_identity_digest(
                        expected_identity
                    ):
                        runtime_verification = {
                            **runtime_verification,
                            "verified": False,
                            "failures": [
                                *runtime_verification.get("failures", []),
                                "artifact_runtime_identity_digest_mismatch",
                            ],
                        }
                combined_runtime_failures = list(
                    dict.fromkeys(
                        [
                            *runtime_verification.get("failures", []),
                            *native_upload_runtime_verification.get("failures", []),
                        ]
                    )
                )
                runtime_verification = {
                    **runtime_verification,
                    "verified": bool(
                        runtime_verification.get("verified")
                        and native_upload_runtime_verification.get("verified")
                    ),
                    "failures": combined_runtime_failures,
                    "native_upload": native_upload_runtime_verification,
                }
    except PlatformAdapterError:
        # 保留清单、路径遍历和身份契约的精确诊断；否则由于
        # PlatformAdapterError 继承 ValueError，会被下面的兜底包装成
        # “WorkBuddy 审计包无效：PlatformAdapterError”，丢失可修复定位。
        raise
    except (
        KeyError,
        OSError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise PlatformAdapterError(f"WorkBuddy 审计包无效：{error.__class__.__name__}") from error
    return {
        "platform": "workbuddy",
        "artifact": str(artifact),
        "skill_root": "zip://arbibuddy/",
        "skill_version": manifest.get("skill_version"),
        "skill_name": skill_name,
        "artifact_contract_valid": bool(
            integrity
            and runtime_verification.get("verified") is True
            and document_dependencies["verified"]
        ),
        "integrity_verified": integrity,
        "integrity_differences": differences,
        "resources_complete": not missing,
        "missing_resources": missing,
        "renderer_dependencies": document_dependencies,
        "renderer_dependencies_verified": document_dependencies["verified"],
        "core_contract": core,
        "runtime_discovered": False,
        "client_import_observed": False,
        "runtime_identity": runtime_identity,
        "native_upload_runtime_identity_verified": native_upload_runtime_verification[
            "verified"
        ],
        "native_upload_runtime_identity_verification": native_upload_runtime_verification,
        "runtime_identity_verified": runtime_verification["verified"],
        "runtime_identity_verification": runtime_verification,
    }




def _validate_skill_source(source: Path) -> None:
    missing, skill_name = _inspect_skill_source(source)
    if missing:
        raise PlatformAdapterError(f"Skill 源目录资源不完整：{', '.join(missing)}")
    if skill_name != "arbibuddy":
        raise PlatformAdapterError("Skill 源目录的 name 不是 arbibuddy")


def _skill_version(source: Path) -> str:
    try:
        return display_version(source)
    except ValueError as error:
        raise PlatformAdapterError(str(error)) from error


def _inspect_skill_source(source: Path) -> tuple[list[str], str | None]:
    missing = [
        relative
        for relative in COMMON_REQUIRED_RESOURCES
        if not path_for_io(source / relative).is_file()
    ]
    skill_file = source / "SKILL.md"
    skill_name = _skill_name(skill_file) if path_for_io(skill_file).is_file() else None
    return missing, skill_name


def _skill_name(skill_file: Path) -> str | None:
    text = path_for_io(skill_file).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    for line in text.split("---", 2)[1].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def _platform_config(platform: str) -> dict[str, Any]:
    if platform not in SUPPORTED_PLATFORMS:
        raise PlatformAdapterError(f"不支持的平台：{platform}")
    path = ADAPTERS_ROOT / platform / "adapter.json"
    try:
        value = json.loads(path_for_io(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlatformAdapterError(f"无法读取平台元数据：{error.__class__.__name__}") from error
    required = {
        "platform",
        "project_discovery_path",
        "user_discovery_path",
        "required_metadata",
        "discovery_probe",
    }
    if not isinstance(value, dict) or required - value.keys() or value.get("platform") != platform:
        raise PlatformAdapterError("平台元数据不符合契约")
    discovery_probe = value["discovery_probe"]
    if not isinstance(discovery_probe, dict) or not isinstance(
        discovery_probe.get("kind"), str
    ):
        raise PlatformAdapterError("平台发现探测元数据不符合契约")
    if discovery_probe["kind"] == "codex-prompt-input" and not isinstance(
        discovery_probe.get("trigger"), str
    ):
        raise PlatformAdapterError("Codex 发现探测缺少 trigger")
    if discovery_probe["kind"] == "claude-help-contract" and not isinstance(
        discovery_probe.get("marker"), str
    ):
        raise PlatformAdapterError("Claude Code 发现探测缺少 marker")
    return value


def _receipt_path(skill_root: Path, *, platform: str | None = None) -> Path:
    if platform == "workbuddy":
        return skill_root / WORKBUDDY_RECEIPT_NAME
    return skill_root.parent / "arbibuddy.install.json"


def _receipt_binds_install(
    receipt: dict[str, Any],
    *,
    platform: str,
    scope: str,
    target_root: Path,
    skill_root: Path,
    discovery_path: str,
) -> bool:
    if not isinstance(receipt, dict):
        return False
    try:
        receipt_target_root = public_path(
            Path(os.path.abspath(str(receipt["target_root"])))
        )
        receipt_skill_root = public_path(
            Path(os.path.abspath(str(receipt["skill_root"])))
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        receipt.get("schema_version") == INSTALL_RECEIPT_SCHEMA_VERSION
        and receipt.get("platform") == platform
        and receipt.get("scope") == scope
        and receipt.get("mode") in {"copy", "link"}
        and receipt_target_root == target_root
        and receipt_skill_root == skill_root
        and receipt.get("discovery_path") == discovery_path
    )


def _core_identity(identity: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(identity, dict):
        return None
    keys = (
        "schema_version",
        "skill_version",
        "core_contract_version",
        "core_manifest_sha256",
        "critical_resource_sha256",
    )
    return {key: identity[key] for key in keys if key in identity}


def _read_receipt(skill_root: Path, *, platform: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(
            path_for_io(_receipt_path(skill_root, platform=platform)).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        raise PlatformAdapterError(f"无法读取安装回执：{error.__class__.__name__}") from error
    if not isinstance(value, dict):
        raise PlatformAdapterError("安装回执顶层必须是对象")
    return value


def _probe_platform_version(
    command: list[str], *, env: dict[str, str] | None = None
) -> str:
    if not command:
        raise PlatformAdapterError("平台命令不能为空")
    try:
        result = _run_platform_command(
            [*command, "--version"], timeout=15, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlatformAdapterError(f"平台版本探测失败：{error.__class__.__name__}") from error
    version = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not version:
        raise PlatformAdapterError("平台版本探测未返回有效版本")
    return version.splitlines()[0]


def _run_platform_command(
    command: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not command:
        raise PlatformAdapterError("平台命令不能为空")
    resolved_command = list(command)
    executable = resolved_command[0]
    if not Path(executable).is_absolute() and not any(
        separator in executable for separator in ("/", "\\")
    ):
        resolved_command[0] = shutil.which(executable) or executable
    try:
        return subprocess.run(
            resolved_command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlatformAdapterError(f"平台命令执行失败：{error.__class__.__name__}") from error




def load_capabilities(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path_for_io(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlatformAdapterError(f"无法读取能力文件：{error}") from error
    if not isinstance(value, dict):
        raise PlatformAdapterError("能力文件顶层必须是对象")
    return value
