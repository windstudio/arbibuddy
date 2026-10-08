"""Read-only validation of public CaseArchive and document.render deliveries.

Shared by client presentation and Journey observation; it does not generate
files or interpret legal facts.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
from time import sleep
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

from scripts.platform_paths import absolute_path_for_io, path_for_io, public_path
from scripts.case_archive.service import CONTRACT_VERSION as CASE_ARCHIVE_CONTRACT
from .checklist_policy import required_checklist_bullets
from .archive_dependencies import validate_archive_freshness
from .ooxml_check import INTERNAL_LEAK_TERMS, audit_docx
from .public import (
    CONTRACT_VERSION as DOCUMENT_RENDER_CONTRACT, DOCUMENT_TYPES,
    OPERATION as DOCUMENT_RENDER_OPERATION, TEMPLATE_VERSION as DOCUMENT_TEMPLATE_VERSION,
    _DOCUMENT_TYPE_TITLES, _TEMPLATE_IDS,
    _document_filename, _checklist_filename, _manifest_filename,
)
MODEL_DELIVERY_PHASES = {"candidate_ready", "final_ready"}

MODEL_CHECKLIST_FORBIDDEN_MARKERS = (
    ".arbibuddy",
    "case-archive-v1",
    "document.render-v1",
    "machine_manifest",
    "机器交付清单",
    "sha256",
    "system prompt",
    "模型推理",
)


def _directory_entries(path: Path, *, description: str) -> tuple[Path, ...]:
    for attempt in range(2):
        try:
            return tuple(path.iterdir())
        except PermissionError as error:
            if attempt == 0:
                sleep(0.15)
                continue
            cause = error
        except OSError as error:
            cause = error
        code = getattr(cause, "winerror", None) or cause.errno
        raise ValueError(
            f"document.render-v1 {description}不可读取"
            f"（{type(cause).__name__}, code={code}）"
        ) from cause
    raise AssertionError("目录读取重试次数异常")


def _uncommitted_render_staging(path: Path) -> bool:
    """Exclude only renderer-owned temporary directories from canonical delivery."""

    if path.is_symlink() or not path.is_dir():
        return False
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return False
    return any(
        re.fullmatch(rf"\.{re.escape(document_type)}\.staging-[a-z0-9]{{8,64}}", path.name)
        for document_type in DOCUMENT_TYPES
    )


def validate_archive_read(
    *,
    root: Path,
    case_id: str,
    response: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        response.get("contract_version") != CASE_ARCHIVE_CONTRACT
        or response.get("operation") != "read"
        or response.get("ok") is not True
    ):
        raise ValueError("case-archive-v1 案情档案读取契约无效")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("case-archive-v1 案情档案结果缺失")
    root = absolute_path_for_io(root)
    case_dir = root / ".arbibuddy" / "cases" / case_id
    archive_path = root / ".arbibuddy" / "cases" / case_id / "案情档案.md"
    canonical_location = result.get("canonical_location")
    if (
        case_dir.is_symlink()
        or not _is_relative_to(case_dir.resolve(), root.resolve())
        or archive_path.is_symlink()
        or not archive_path.is_file()
        or not _is_relative_to(archive_path.resolve(), root.resolve())
        or result.get("case_id") != case_id
        or not isinstance(result.get("revision"), int)
        or isinstance(result.get("revision"), bool)
        or result["revision"] < 0
        or not isinstance(canonical_location, str)
        or public_path(path_for_io(canonical_location).resolve())
        != public_path(archive_path.resolve())
    ):
        raise ValueError("case-archive-v1 案情档案定位或修订版本无效")
    return result


def _sha256_value(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _audit_model_docx(path: Path, template_id: str | None) -> str:
    try:
        audit = audit_docx(path, template_id)
        with ZipFile(path) as package:
            contents = (
                package.read(name).decode("utf-8", "ignore")
                for name in package.namelist()
                if name.endswith((".xml", ".rels", ".txt"))
            )
            for content in contents:
                for term in INTERNAL_LEAK_TERMS:
                    if term.casefold() in content.casefold():
                        raise ValueError(f"正式文书检测到内部内容泄漏：{term}")
        return audit.visible_text
    except (
        BadZipFile,
        KeyError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        ImportError,
    ) as error:
        raise ValueError(
            f"document.render-v1 canonical DOCX/OOXML 校验失败：{error}"
        ) from error


def _validate_model_checklist(
    path: Path,
    filename: str,
    *,
    title: str,
    document_type: str,
    mode: str,
    document_text: str,
) -> None:
    if (
        "内部核验清单" not in filename
        or "仅供核对，请勿外发" not in filename
    ):
        raise ValueError("document.render-v1 独立核验清单文件名不符合契约")
    try:
        text = path_for_io(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError("document.render-v1 独立核验清单不可按 UTF-8 读取") from error
    if "仅供核对，请勿外发" not in text:
        raise ValueError("document.render-v1 独立核验清单缺少外发禁止标识")
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        raise ValueError("document.render-v1 独立核验清单不得是 JSON")
    folded = text.casefold()
    if any(marker.casefold() in folded for marker in MODEL_CHECKLIST_FORBIDDEN_MARKERS):
        raise ValueError("document.render-v1 独立核验清单包含内部协议或摘要")
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|\bcase-[0-9a-f]{24}\b)", text):
        raise ValueError("document.render-v1 独立核验清单不得包含路径或案件标识")
    if re.search(r"\b(?:F|CL|E|A|CAL|AUTH|R|CONF|N)-\d{3,}\b", text):
        raise ValueError("document.render-v1 独立核验清单不得包含档案记录标识")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [line[2:].strip() for line in lines if line.startswith("- ")]
    required_bullets = required_checklist_bullets(
        document_type, mode, document_text
    )
    typed_bullets_match = all(
        bullets.count(expected) == 1
        and sum(
            item.startswith(expected.split("：", 1)[0] + "：")
            for item in bullets
        )
        == 1
        for expected in required_bullets
    )
    if (
        not lines
        or lines[0] != f"《{title}》提交前核验清单"
        or "## 一、文书状态" not in lines
        or "## 二、请核对的内容" not in lines
        or not document_text.strip()
        or not typed_bullets_match
        or not any("Word/WPS" in item and "预览" in item for item in bullets)
        or not any("不得与正式文书一并提交或发送" in item for item in bullets)
    ):
        raise ValueError(
            "document.render-v1 独立核验清单缺少与文书类型和成熟度对应的核验项"
        )


def read_delivery_entries(
    *,
    root: Path,
    case_id: str,
    archive: Mapping[str, Any],
    delivery_set: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    root = absolute_path_for_io(root)
    case_dir = root / ".arbibuddy" / "cases" / case_id
    output_root = case_dir / "output"
    if output_root.is_symlink() or not output_root.is_dir():
        raise ValueError("document.render-v1 canonical 输出目录缺失")
    output_root_resolved = output_root.resolve()
    children = sorted(
        (
            child
            for child in _directory_entries(output_root, description="canonical 输出目录")
            if not _uncommitted_render_staging(child)
        ),
        key=lambda path: path.name,
    )
    if not children or any(
        child.is_symlink() or not child.is_dir() for child in children
    ):
        raise ValueError("document.render-v1 canonical 输出目录结构无效")

    entries: list[dict[str, Any]] = []
    for output_dir in children:
        manifest_files = tuple(
            path
            for path in _directory_entries(output_dir, description="机器交付清单")
            if path.is_file() and path.suffix.casefold() == ".json"
        )
        if len(manifest_files) != 1:
            raise ValueError(
                f"document.render-v1 canonical 交付 {output_dir.name} 缺少唯一机器交付清单"
            )
        manifest_path = manifest_files[0]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("document.render-v1 机器交付清单不可解析") from error
        if not isinstance(manifest, Mapping):
            raise ValueError("document.render-v1 机器交付清单必须是对象")
        if (
            manifest.get("schema_version") != 1
            or manifest.get("contract_version") != DOCUMENT_RENDER_CONTRACT
            or manifest.get("operation") != DOCUMENT_RENDER_OPERATION
        ):
            raise ValueError("document.render-v1 机器交付清单契约无效")
        if delivery_set is not None and (
            manifest.get("delivery_set_id") != delivery_set.get("delivery_set_id")
            or manifest.get("delivery_set_digest") != delivery_set.get("delivery_set_digest")
        ):
            raise ValueError("document.render-v1 机器交付清单未绑定当前 delivery set")
        if manifest.get("case_id") != case_id:
            raise ValueError("document.render-v1 机器交付清单案件编号不一致")
        reused = validate_archive_freshness(
            manifest, revision=archive["revision"], markdown=archive.get("markdown")
        )
        if (
            not isinstance(manifest.get("render_identity"), str)
            or not _sha256_value(manifest["render_identity"])
            or manifest.get("template_version") != DOCUMENT_TEMPLATE_VERSION
            or manifest.get("mode") not in {"candidate", "external_final"}
            or not isinstance(manifest.get("title"), str)
            or not manifest["title"].strip()
            or not isinstance(manifest.get("warnings"), list)
        ):
            raise ValueError("document.render-v1 机器交付清单元数据无效")
        document_type = manifest.get("document_type")
        if (
            not isinstance(document_type, str)
            or not document_type
            or Path(document_type).name != document_type
            or document_type not in DOCUMENT_TYPES
            or document_type != output_dir.name
        ):
            raise ValueError("document.render-v1 文书类型或 canonical 目录名无效")
        if manifest["title"] != _DOCUMENT_TYPE_TITLES[document_type]:
            raise ValueError("document.render-v1 文书标题与注册类型不一致")
        delivery_state = manifest.get("delivery_state")
        if delivery_state not in MODEL_DELIVERY_PHASES:
            raise ValueError("document.render-v1 交付状态无效")
        if (
            (delivery_state == "candidate_ready" and manifest["mode"] != "candidate")
            or (delivery_state == "final_ready" and manifest["mode"] != "external_final")
        ):
            raise ValueError("document.render-v1 交付状态与渲染模式不一致")
        if not _sha256_value(manifest.get("content_digest")):
            raise ValueError("document.render-v1 内容摘要无效")
        validation_summary = manifest.get("validation_summary")
        if (
            not isinstance(validation_summary, Mapping)
            or validation_summary.get("status") != "passed"
            or not isinstance(validation_summary.get("checks"), list)
        ):
            raise ValueError("document.render-v1 validation_summary 缺失或未通过")
        document = manifest.get("document")
        checklist = manifest.get("verification_checklist")
        machine_manifest = manifest.get("machine_manifest")
        if (
            not isinstance(document, Mapping)
            or not isinstance(checklist, Mapping)
            or not isinstance(machine_manifest, Mapping)
        ):
            raise ValueError("document.render-v1 canonical 产物索引缺失")
        try:
            document_name = _direct_name(document["filename"])
            checklist_name = _direct_name(checklist["filename"])
            manifest_name = _direct_name(machine_manifest["filename"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("document.render-v1 canonical 产物文件名无效") from error
        if (document_name, checklist_name, manifest_name) != (
            _document_filename(manifest["title"], manifest["mode"]),
            _checklist_filename(manifest["title"]),
            _manifest_filename(manifest["title"]),
        ):
            raise ValueError("document.render-v1 产物名称与注册标题和成熟度不一致")
        if manifest_name != manifest_path.name or Path(manifest_name).suffix.casefold() != ".json":
            raise ValueError("document.render-v1 机器交付清单文件名不一致")
        if Path(document_name).suffix.casefold() != ".docx":
            raise ValueError("document.render-v1 canonical 文书必须是 DOCX")
        if Path(checklist_name).suffix.casefold() != ".txt":
            raise ValueError("document.render-v1 独立核验清单必须是 TXT")
        document_path = output_dir / document_name
        checklist_path = output_dir / checklist_name
        expected_names = {document_name, checklist_name, manifest_name}
        if {
            path.name
            for path in _directory_entries(output_dir, description="机器交付清单")
        } != expected_names:
            raise ValueError("document.render-v1 canonical 目录包含未授权或缺失产物")
        if output_dir.is_symlink():
            raise ValueError("document.render-v1 canonical 输出目录不是普通目录")
        if not _is_relative_to(output_dir.resolve(), output_root_resolved):
            raise ValueError("document.render-v1 canonical 路径越出案件输出目录")
        for path in (document_path, checklist_path, manifest_path):
            resolved = path.resolve()
            if not _is_relative_to(resolved, output_root_resolved):
                raise ValueError("document.render-v1 canonical 路径越出案件输出目录")
            if path.is_symlink() or not path.is_file():
                raise ValueError("document.render-v1 canonical 产物不是普通文件")
        if (
            document.get("sha256") != _digest(document_path)
            or checklist.get("sha256") != _digest(checklist_path)
            or not _sha256_value(document.get("sha256"))
            or not _sha256_value(checklist.get("sha256"))
        ):
            raise ValueError("document.render-v1 canonical 产物摘要不一致")
        template_id = _TEMPLATE_IDS[document_type]
        document_text = _audit_model_docx(document_path, template_id)
        _validate_model_checklist(
            checklist_path,
            checklist_name,
            title=manifest["title"],
            document_type=document_type,
            mode=manifest["mode"],
            document_text=document_text,
        )
        entries.append(
            {
                "document_type": document_type,
                "delivery_state": delivery_state,
                "manifest": manifest,
                "archive_dependencies_verified": reused,
                "document_path": public_path(document_path.resolve()),
                "checklist_path": public_path(checklist_path.resolve()),
            }
        )

    if delivery_set is not None:
        expected_types = delivery_set.get("expected_document_types")
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise ValueError("document.delivery-set-v1 expected_document_types 无效")
        if any(
            entry["document_type"] not in expected_types for entry in entries
        ):
            raise ValueError("document.render-v1 canonical 交付包含 delivery set 之外的文书")
        entries.sort(key=lambda item: expected_types.index(item["document_type"]))
    states = {entry["delivery_state"] for entry in entries}
    if len(states) != 1:
        raise ValueError("document.render-v1 canonical 交付状态必须一致")
    return entries


def _direct_name(value: object) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError("产物文件名无效")
    return value


def _digest(path: Path) -> str:
    return sha256(path_for_io(path).read_bytes()).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)
