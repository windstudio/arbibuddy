"""运行时身份的唯一实现。

该 Module 是发布门禁、安装回执和公共运行时共享的身份 seam。身份只包含
版本、契约和摘要，不包含源码路径、安装路径或案件内容；平台差异留在
platform adapter。
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping

from scripts.runtime_resources import CONTRACT_VERSION, installed_resource_failures, runtime_paths
from scripts.platform_paths import path_for_io
from scripts.runtime_manifest import (
    RuntimeManifestError,
    read_and_validate_runtime_manifest,
)
from scripts.version import display_version


RUNTIME_IDENTITY_SCHEMA_VERSION = 2
WORKBUDDY_TRUST_ANCHOR_SCHEMA_VERSION = 1
WORKBUDDY_TRUST_ANCHOR_DIR = ".arbibuddy-workbuddy-trust"
WORKBUDDY_PORTABLE_RUNTIME_MARKER_SCHEMA_VERSION = 1
WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME = "arbibuddy.runtime.json"
WORKBUDDY_PORTABLE_RUNTIME_MARKER_TYPE = "native-upload-portable"
_GIT_COMMAND = shutil.which("git") or "git"


class RuntimeIdentityError(ValueError):
    """身份生成、读取或比较失败。"""


def _reject_duplicate_json_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeIdentityError(f"运行身份标记存在重复字段：{key}")
        result[key] = value
    return result


def _digest(path: Path) -> str:
    try:
        return sha256(path_for_io(path).read_bytes()).hexdigest()
    except OSError as error:
        raise RuntimeIdentityError(f"运行身份资源缺失或不可读：{path.name}") from error


def _canonical_digest(root: Path, relative: str) -> str:
    return _digest(root / relative)

def _source_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            [
                _GIT_COMMAND,
                "-c",
                f"safe.directory={path_for_io(root)}",
                "rev-parse",
                "HEAD",
            ],
            cwd=path_for_io(root),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) == 40 else "unavailable"


def _valid_source_checkout(root: Path) -> bool:
    root = path_for_io(root).resolve()
    if not (root / ".git").exists():
        return False
    try:
        top = subprocess.run(
            [
                _GIT_COMMAND,
                "-c",
                f"safe.directory={path_for_io(root)}",
                "rev-parse",
                "--show-toplevel",
            ],
            cwd=path_for_io(root),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        head = subprocess.run(
            [
                _GIT_COMMAND,
                "-c",
                f"safe.directory={path_for_io(root)}",
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            cwd=path_for_io(root),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        git_dir = subprocess.run(
            [
                _GIT_COMMAND,
                "-c",
                f"safe.directory={path_for_io(root)}",
                "rev-parse",
                "--git-dir",
            ],
            cwd=path_for_io(root),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    resolved_top = Path(top.stdout.strip()).resolve() if top.returncode == 0 else None
    comparable_root = Path(str(root).replace("\\\\?\\", "")).resolve()
    return bool(
        resolved_top == comparable_root
        and head.returncode == 0
        and bool(re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()))
        and git_dir.returncode == 0
        and git_dir.stdout.strip()
    )


def is_source_link(root: Path) -> bool:
    """只有实际指向有效源码 checkout 的目录链接才可携带开发资源。"""
    candidate = path_for_io(root)
    linked = candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)()
    return linked and _valid_source_checkout(candidate)


def _iter_manifest_files(root: Path) -> tuple[Path, ...]:
    return tuple(path_for_io(path) for path in runtime_paths(root))

def _manifest(root: Path) -> dict[str, Any]:
    root = path_for_io(root).resolve()
    records = {
        path.relative_to(root).as_posix(): _canonical_digest(
            root, path.relative_to(root).as_posix()
        )
        for path in _iter_manifest_files(root)
    }
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "file_count": len(records),
        "files": list(records),
        "sha256": sha256(canonical).hexdigest(),
    }


def _artifact_manifest_sha256(root: Path) -> str:
    """摘要客户端实际收到的发布载荷，独立于普通安装回执路径。"""

    manifest = _manifest(root)
    payload = {
        "skill": "arbibuddy",
        "skill_version": display_version(root),
        "core_contract_version": CONTRACT_VERSION,
        "files": {
            relative: _canonical_digest(root, relative)
            for relative in manifest["files"]
        },
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity_from_root(root: Path, *, source_commit: str | None = None) -> dict[str, Any]:
    root = path_for_io(root).resolve()
    resources = {
        relative: _canonical_digest(root, relative)
        for relative in _KEY_RESOURCES
    }
    resource_bytes = json.dumps(
        resources,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    installation_manifest = _manifest(root)
    return {
        "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
        "source_commit": (
            _source_commit(root) if source_commit is None else source_commit
        ),
        "skill_version": display_version(root),
        "core_contract_version": CONTRACT_VERSION,
        "version_sha256": _digest(root / "VERSION"),
        "installation_manifest": installation_manifest,
        "installation_manifest_sha256": installation_manifest["sha256"],
        "artifact_manifest_sha256": _artifact_manifest_sha256(root),
        "core_manifest_sha256": sha256(resource_bytes).hexdigest(),
        "resource_sha256": resources,
        "critical_resource_sha256": resources,
    }


def _packaged_runtime_failures(root: Path) -> list[str]:
    """Validate the WorkBuddy-only vendor tree bound to an install marker."""

    runtime_root = path_for_io(root / "workbuddy-runtime")
    manifest_path = runtime_root / "runtime-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return ["runtime_manifest_missing"]
    try:
        read_and_validate_runtime_manifest(
            root,
            expected_abi="cp313",
            expected_platform="win_amd64",
        )
    except (OSError, RuntimeManifestError):
        return ["runtime_manifest_invalid"]
    return []


def _runtime_manifest_summary_from_bytes(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise RuntimeIdentityError("WorkBuddy vendor runtime 清单不是二进制内容")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, ValueError) as error:
        raise RuntimeIdentityError("WorkBuddy vendor runtime 清单不可读取") from error
    if not isinstance(value, Mapping):
        raise RuntimeIdentityError("WorkBuddy vendor runtime 清单顶层必须是对象")
    packages = value.get("packages")
    files = value.get("files")
    licenses = value.get("licenses")
    if (
        value.get("schema_version") != 1
        or not isinstance(value.get("python_abi"), str)
        or not isinstance(value.get("platform_tag"), str)
        or not isinstance(packages, list)
        or not isinstance(files, dict)
        or not isinstance(licenses, list)
        or not packages
        or not files
        or not licenses
    ):
        raise RuntimeIdentityError("WorkBuddy vendor runtime 清单字段不完整")
    return {
        "runtime_manifest_sha256": sha256(data).hexdigest(),
        "python_abi": value["python_abi"],
        "platform_tag": value["platform_tag"],
        "package_count": len(packages),
        "file_count": len(files),
        "license_count": len(licenses),
    }


def _portable_runtime_vendor_summary(root: Path) -> dict[str, Any]:
    path = path_for_io(root / "workbuddy-runtime" / "runtime-manifest.json")
    if path.is_symlink() or not path.is_file():
        raise RuntimeIdentityError("WorkBuddy vendor runtime 清单缺失")
    return _runtime_manifest_summary_from_bytes(path.read_bytes())


def _marker_present(path: Path) -> bool:
    io_path = path_for_io(path)
    return io_path.exists() or io_path.is_symlink()


_KEY_RESOURCES = (
    "SKILL.md", "VERSION", "scripts/runtime_resources.py",
    "scripts/case_archive/service.py", "scripts/amount_calculator/public.py",
    "scripts/documents/public.py", "scripts/documents/receipts.py",
    "scripts/documents/delivery.py", "scripts/runtime_identity.py",
    "scripts/runtime_manifest.py", "scripts/documents/runtime_cli.py",
    "scripts/documents/view.py", "scripts/runtime_context.py",
)

def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_path(key) or _contains_absolute_path(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    return "\\\\?\\" in value or value.startswith("/") or (
        len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}
    )


def runtime_identity_digest(identity: dict[str, Any]) -> str:
    """摘要身份对象本身，供回执和产物清单绑定。"""

    return sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_workbuddy_portable_runtime_marker(
    runtime_identity: dict[str, Any],
    runtime_manifest: bytes,
) -> bytes:
    """生成随 Skill 子树携带的 WorkBuddy 原生上传身份标记。

    标记故意不进入 ``RuntimeIdentity`` 的安装文件集合，避免标记中的身份对象
    与自身形成摘要循环；外层 WorkBuddy ZIP 清单仍会把它作为普通 payload 文件
    逐文件登记并验签。
    """

    if (
        not isinstance(runtime_identity, dict)
        or runtime_identity.get("schema_version") != RUNTIME_IDENTITY_SCHEMA_VERSION
        or not isinstance(runtime_identity.get("skill_version"), str)
    ):
        raise RuntimeIdentityError("无法为无效运行身份生成 WorkBuddy 便携标记")
    if _contains_absolute_path(runtime_identity):
        raise RuntimeIdentityError("WorkBuddy 便携标记不得包含绝对路径")
    vendor_runtime = _runtime_manifest_summary_from_bytes(runtime_manifest)
    marker = {
        "schema_version": WORKBUDDY_PORTABLE_RUNTIME_MARKER_SCHEMA_VERSION,
        "platform": "workbuddy",
        "marker_type": WORKBUDDY_PORTABLE_RUNTIME_MARKER_TYPE,
        "skill": "arbibuddy",
        "skill_version": runtime_identity["skill_version"],
        "runtime_identity": runtime_identity,
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
        "vendor_runtime": vendor_runtime,
    }
    if _contains_absolute_path(marker):
        raise RuntimeIdentityError("WorkBuddy 便携标记不得包含绝对路径")
    return (
        json.dumps(
            marker,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def runtime_identity_failure_message(preflight: Mapping[str, Any]) -> str:
    """把身份预检失败映射为不混淆安装模式的用户诊断。"""

    failures = preflight.get("failures", [])
    if not isinstance(failures, list):
        failures = []
    failure_set = {item for item in failures if isinstance(item, str)}
    if "external_trust_anchor_missing" in failure_set:
        return (
            "受管 WorkBuddy 安装缺少外部信任锚；请通过受管安装或更新恢复信任锚并重启客户端。"
            "原生上传不会生成受管安装回执。"
        )
    if any(
        item.startswith("external_") or item.startswith("install_receipt_")
        for item in failure_set
    ):
        return (
            "受管 WorkBuddy 安装的外部信任锚或安装回执不一致；请重新执行受管安装或更新并重启客户端。"
            "原生上传不会生成受管安装回执。"
        )
    if any(
        item.startswith("native_upload_")
        for item in failure_set
    ):
        return (
            "WorkBuddy 原生上传的便携身份标记缺失、陈旧或运行时已漂移；请重新上传已验签 ZIP 并完全重启客户端。"
            "原生上传不需要也不会生成受管安装回执。"
        )
    return "当前安装的 Skill 完整性校验失败；请重新安装已验签包并重启客户端"


def runtime_identity_failure_next_action(preflight: Mapping[str, Any]) -> str:
    """返回与身份来源匹配的恢复动作，避免把原生上传导向受管回执。"""

    failures = preflight.get("failures", [])
    if not isinstance(failures, list):
        failures = []
    failure_set = {item for item in failures if isinstance(item, str)}
    if any(item.startswith("native_upload_") for item in failure_set):
        return "reupload-workbuddy-skill-and-restart"
    if any(
        item.startswith("external_") or item.startswith("install_receipt_")
        for item in failure_set
    ):
        return "repair-managed-workbuddy-install-and-restart"
    return "reload-or-reinstall-current-candidate"


def workbuddy_trust_anchor_path(
    target_root: str | Path,
    skill_root: str | Path,
) -> Path:
    target = path_for_io(target_root).resolve()
    skill = path_for_io(skill_root).resolve()
    anchor_id = sha256(str(skill).casefold().encode("utf-8")).hexdigest()[:24]
    return target.parent / WORKBUDDY_TRUST_ANCHOR_DIR / f"{anchor_id}.json"


def _vendor_runtime_manifest_digest(skill_root: Path) -> str | None:
    path = path_for_io(skill_root / "workbuddy-runtime" / "runtime-manifest.json")
    if not path.is_file() or path.is_symlink():
        return None
    return sha256(path.read_bytes()).hexdigest()


def write_workbuddy_trust_anchor(
    *,
    target_root: str | Path,
    skill_root: str | Path,
    runtime_identity: dict[str, Any],
) -> Path:
    anchor = workbuddy_trust_anchor_path(target_root, skill_root)
    payload = {
        "schema_version": WORKBUDDY_TRUST_ANCHOR_SCHEMA_VERSION,
        "platform": "workbuddy",
        "runtime_identity_sha256": runtime_identity_digest(runtime_identity),
        "installation_manifest_sha256": runtime_identity.get(
            "installation_manifest_sha256"
        ),
        "artifact_manifest_sha256": runtime_identity.get("artifact_manifest_sha256"),
        "vendor_runtime_manifest_sha256": _vendor_runtime_manifest_digest(
            path_for_io(skill_root).resolve()
        ),
    }
    anchor.parent.mkdir(parents=True, exist_ok=True)
    temporary = anchor.with_name(anchor.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(anchor)
    return anchor


def _workbuddy_external_anchor_failures(
    skill_root: Path,
    receipt: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    target_root = receipt.get("target_root")
    if not isinstance(target_root, str) or not target_root.strip():
        return ["external_trust_anchor_target_missing"]
    anchor = workbuddy_trust_anchor_path(target_root, skill_root)
    try:
        if anchor.is_symlink() or not anchor.is_file():
            return ["external_trust_anchor_missing"]
        if anchor.resolve().is_relative_to(skill_root.resolve()):
            return ["external_trust_anchor_inside_installation_tree"]
        value = json.loads(anchor.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["external_trust_anchor_invalid"]
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != WORKBUDDY_TRUST_ANCHOR_SCHEMA_VERSION
        or value.get("platform") != "workbuddy"
    ):
        return ["external_trust_anchor_contract_invalid"]
    expected_identity = receipt.get("runtime_identity")
    failures: list[str] = []
    if not isinstance(expected_identity, dict):
        failures.append("runtime_identity_missing")
    else:
        expected_digest = runtime_identity_digest(expected_identity)
        if receipt.get("runtime_identity_sha256") != expected_digest:
            failures.append("install_receipt_identity_digest_mismatch")
        if value.get("runtime_identity_sha256") != expected_digest:
            failures.append("external_runtime_identity_sha256_mismatch")
    for key in ("installation_manifest_sha256", "artifact_manifest_sha256"):
        if value.get(key) != current.get(key):
            failures.append(f"external_{key}_mismatch")
    vendor_digest = _vendor_runtime_manifest_digest(skill_root)
    if value.get("vendor_runtime_manifest_sha256") != vendor_digest:
        failures.append("external_vendor_runtime_manifest_sha256_mismatch")
    if isinstance(expected_identity, dict) and value.get("runtime_identity_sha256") != runtime_identity_digest(current):
        failures.append("external_runtime_identity_current_mismatch")
    return list(dict.fromkeys(failures))


class RuntimeIdentityModule:
    """小接口、深实现的运行时身份 Module。"""

    def generate(self, source: str | Path) -> dict[str, Any]:
        root = path_for_io(source).resolve()
        identity = _identity_from_root(root)
        if _contains_absolute_path(identity):
            raise RuntimeIdentityError("运行身份不得包含绝对路径")
        return identity

    def compare(
        self,
        expected: dict[str, Any],
        installed: str | Path,
    ) -> dict[str, Any]:
        if not isinstance(expected, dict):
            raise RuntimeIdentityError("期望运行身份必须是对象")
        if expected.get("schema_version") != RUNTIME_IDENTITY_SCHEMA_VERSION:
            return {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False,
                "failures": ["runtime_identity_schema_mismatch"],
            }
        try:
            observed = _identity_from_root(
                path_for_io(installed),
                source_commit=expected.get("source_commit", "unavailable"),
            )
        except (OSError, RuntimeIdentityError, ValueError, KeyError) as error:
            return {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False,
                "failures": [f"runtime_identity_unreadable:{type(error).__name__}"],
            }
        failures = sorted(
            key
            for key in set(expected) | set(observed)
            if key not in {"source_commit"} and expected.get(key) != observed.get(key)
        )
        if not _valid_source_checkout(path_for_io(installed)):
            failures.extend(installed_resource_failures(Path(installed), allow_vendor=True))
        return {
            "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
            "verified": not failures,
            "failures": failures,
            "expected": expected,
            "observed": observed,
        }

    def _verify_install_marker(
        self,
        candidate: Path,
        current: dict[str, Any],
        marker_path: Path,
    ) -> dict[str, Any]:
        try:
            value = json.loads(
                path_for_io(marker_path).read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (OSError, UnicodeError, ValueError):
            return {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False,
                "failures": ["runtime_identity_marker_invalid"],
            }

        platform_value = value.get("platform") if isinstance(value, dict) else None
        if platform_value not in {"codex", "claude-code", "workbuddy"}:
            return {"schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION, "verified": False,
                    "failures": ["runtime_identity_marker_invalid"]}
        packaged_runtime_failures = (
            _packaged_runtime_failures(candidate)
            if platform_value == "workbuddy"
            else []
        )
        failures: list[str] = []
        if packaged_runtime_failures:
            failures.extend(packaged_runtime_failures)
            failures.append("runtime_manifest_mismatch")
        expected = value.get("runtime_identity") if isinstance(value, dict) else None
        if not isinstance(expected, dict):
            return {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False,
                "failures": [*failures, "runtime_identity_marker_invalid"],
            }

        comparison = self.compare(expected, candidate)
        source_link = value.get("mode") == "link" and is_source_link(candidate)
        if not source_link:
            failures.extend(installed_resource_failures(candidate, allow_vendor=platform_value == "workbuddy"))
        if (
            platform_value == "workbuddy"
            and marker_path.name == "arbibuddy.install.json"
        ):
            try:
                external_anchor_failures = _workbuddy_external_anchor_failures(
                    candidate, value, current
                )
            except (OSError, TypeError, ValueError):
                external_anchor_failures = ["external_trust_anchor_invalid"]
        else:
            # The package manifest authenticates an extracted audit artifact
            # before installation. Its external trust anchor is created only
            # when WorkBuddy commits the install tree.
            external_anchor_failures = []
        failures.extend(external_anchor_failures)
        if (
            comparison["verified"]
            and not packaged_runtime_failures
            and not external_anchor_failures
            and not failures
        ):
            return {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": True,
                "mode": (
                    "installation"
                    if marker_path.name.endswith("install.json")
                    else "artifact"
                ),
                "identity": current,
                "expected": expected,
                "failures": [],
            }
        failures.extend(comparison.get("failures", []))
        failures.append("runtime_identity_marker_mismatch")
        return {
            "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
            "verified": False,
            "identity": current,
            "expected": expected,
            "failures": list(dict.fromkeys(failures)),
        }

    def _verify_portable_marker(
        self,
        candidate: Path,
        current: dict[str, Any],
        marker_path: Path,
    ) -> dict[str, Any]:
        common = {
            "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
            "mode": "portable",
            "identity": current,
        }
        io_marker = path_for_io(marker_path)
        try:
            if io_marker.is_symlink() or not io_marker.is_file():
                raise RuntimeIdentityError("便携身份标记路径不安全")
            value = json.loads(
                io_marker.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (OSError, UnicodeError, ValueError, RuntimeIdentityError):
            return {
                **common,
                "verified": False,
                "failures": ["native_upload_marker_invalid"],
            }
        if not isinstance(value, dict):
            return {
                **common,
                "verified": False,
                "failures": ["native_upload_marker_invalid"],
            }

        expected = value.get("runtime_identity")
        vendor_runtime = value.get("vendor_runtime")
        if (
            value.get("schema_version")
            != WORKBUDDY_PORTABLE_RUNTIME_MARKER_SCHEMA_VERSION
            or value.get("platform") != "workbuddy"
            or value.get("marker_type") != WORKBUDDY_PORTABLE_RUNTIME_MARKER_TYPE
            or value.get("skill") != "arbibuddy"
            or not isinstance(value.get("skill_version"), str)
            or not isinstance(expected, dict)
            or not isinstance(value.get("runtime_identity_sha256"), str)
            or not isinstance(vendor_runtime, dict)
        ):
            return {
                **common,
                "verified": False,
                "failures": ["native_upload_marker_invalid"],
                "expected": expected,
            }

        failures: list[str] = installed_resource_failures(candidate, allow_vendor=True)
        if value["skill_version"] != current.get("skill_version"):
            failures.append("native_upload_marker_stale")
        expected_digest = runtime_identity_digest(expected)
        if value["runtime_identity_sha256"] != expected_digest:
            failures.append("native_upload_marker_identity_digest_mismatch")

        comparison = self.compare(expected, candidate)
        if not comparison["verified"]:
            failures.extend(comparison.get("failures", []))
            failures.append("native_upload_marker_stale")

        packaged_runtime_failures = _packaged_runtime_failures(candidate)
        if packaged_runtime_failures:
            failures.extend(packaged_runtime_failures)
            failures.append("runtime_manifest_mismatch")
            failures.append("native_upload_vendor_runtime_invalid")
        else:
            try:
                observed_vendor_runtime = _portable_runtime_vendor_summary(candidate)
            except (OSError, RuntimeIdentityError, ValueError):
                observed_vendor_runtime = None
                failures.append("native_upload_vendor_runtime_invalid")
            if observed_vendor_runtime != vendor_runtime:
                failures.append("native_upload_vendor_runtime_mismatch")

        return {
            **common,
            "verified": not failures,
            "expected": expected,
            "observed": comparison.get("observed", current),
            "vendor_runtime": vendor_runtime,
            "failures": list(dict.fromkeys(failures)),
        }

    def preflight(self, skill_root: str | Path | None = None) -> dict[str, Any]:
        """验证当前执行树；已发现安装身份失败时不切换标记或源码根。"""
        candidate = path_for_io(skill_root) if skill_root is not None else path_for_io(Path(__file__).absolute().parents[1])
        try:
            current = self.generate(candidate)
        except (OSError, RuntimeIdentityError, ValueError) as error:
            return {"schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                    "verified": False,
                    "failures": [f"runtime_identity_unreadable:{type(error).__name__}"]}

        # 保留发现路径的拼写，以便源码链接读取旁置回执；不回退解析后的源码根。
        for marker, verifier in (
            (candidate / "arbibuddy.install.json", self._verify_install_marker),
            (candidate / WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME, self._verify_portable_marker),
        ):
            if _marker_present(marker):
                return verifier(candidate, current, marker)
        if _marker_present(candidate / "workbuddy-runtime"):
            return {"schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                    "verified": False, "failures": ["native_upload_marker_missing"]}
        sibling_receipt = candidate.parent / "arbibuddy.install.json"
        if _marker_present(sibling_receipt):
            return self._verify_install_marker(candidate, current, sibling_receipt)
        # 只有没有安装标记的真实源码 checkout 才使用源码身份。
        if _valid_source_checkout(candidate):
            return {"schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                    "verified": True, "mode": "source", "identity": current,
                    "expected": current, "failures": []}
        return {"schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "verified": False, "failures": ["runtime_identity_marker_missing"]}

    def install_snapshot(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        mode: str = "copy",
    ) -> Path:
        source_root = path_for_io(source).resolve()
        destination_path = path_for_io(destination).resolve()
        if mode not in {"copy", "link"}:
            raise RuntimeIdentityError("安装方式必须是 copy 或 link")
        if destination_path.exists() or destination_path.is_symlink():
            raise RuntimeIdentityError("运行身份安装目标已存在")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "link":
            destination_path.symlink_to(source_root, target_is_directory=True)
            return destination_path
        destination_path.mkdir()
        for path in _iter_manifest_files(source_root):
            relative = path.relative_to(source_root)
            target = destination_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        return destination_path


_MODULE = RuntimeIdentityModule()


def build_runtime_identity(source: str | Path) -> dict[str, Any]:
    return _MODULE.generate(source)


def compare_runtime_identity(
    expected: dict[str, Any], installed: str | Path
) -> dict[str, Any]:
    return _MODULE.compare(expected, installed)


def measure_runtime_identity(
    installed: str | Path,
    *,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """测量实际安装树；仅供门禁和安装回执验证复用。"""

    return _identity_from_root(installed, source_commit=source_commit)


KEY_RESOURCES = _KEY_RESOURCES


__all__ = [
    "RUNTIME_IDENTITY_SCHEMA_VERSION",
    "WORKBUDDY_TRUST_ANCHOR_SCHEMA_VERSION",
    "WORKBUDDY_PORTABLE_RUNTIME_MARKER_SCHEMA_VERSION",
    "WORKBUDDY_PORTABLE_RUNTIME_MARKER_NAME",
    "WORKBUDDY_PORTABLE_RUNTIME_MARKER_TYPE",
    "RuntimeIdentityError",
    "RuntimeIdentityModule",
    "build_runtime_identity",
    "build_workbuddy_portable_runtime_marker",
    "compare_runtime_identity",
    "measure_runtime_identity",
    "KEY_RESOURCES",
    "runtime_identity_digest",
    "runtime_identity_failure_message",
    "runtime_identity_failure_next_action",
    "workbuddy_trust_anchor_path",
    "write_workbuddy_trust_anchor",
]
