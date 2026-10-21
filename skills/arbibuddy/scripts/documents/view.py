"""文书交付只读视图：校验档案与生成回执，不运行案情或对话编排。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from scripts.case_archive import CaseArchive
from scripts.case_archive.service import CONTRACT_VERSION as CASE_ARCHIVE_CONTRACT
from scripts.platform_paths import absolute_path_for_io, public_path, path_for_io
from .receipts import validate_archive_read as _validate_model_archive_read
from .receipts import read_delivery_entries as _model_public_delivery_entries
from .public import CONTRACT_VERSION as DOCUMENT_RENDER_CONTRACT, DELIVERY_SET_CONTRACT_VERSION, DOCUMENT_TYPES, load_delivery_set, _DOCUMENT_TYPE_CONTRACTS

MODEL_DELIVERY_NEXT_ACTIONS = {"candidate_ready": "present_candidate_deliveries_and_end_turn", "final_ready": "status_only"}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _model_unavailable_view(
    *,
    case_id: str,
    reason: str,
    error_code: str = "invalid_model_delivery",
    next_action: str = "rerun_document_render_v1",
    delivery_set: Mapping[str, Any] | None = None,
    missing_document_types: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "delivery_state": "unavailable",
        "stopped": True,
        "error_code": error_code,
        "reason": reason,
        "next_action": next_action,
        "presentation_files": [],
        "present_files_arguments": {"cwd": "", "files": []},
        "delivery_summaries": [],
        "delivery_set": (
            {
                "contract_version": DELIVERY_SET_CONTRACT_VERSION,
                "delivery_set_id": delivery_set.get("delivery_set_id"),
                "archive_revision": delivery_set.get("archive_revision"),
                "expected_document_types": list(
                    delivery_set.get("expected_document_types", [])
                ),
                "completed_document_types": [],
                "missing_document_types": list(missing_document_types),
            }
            if delivery_set is not None
            else None
        ),
        "delivery_termination": {
            "terminal": True,
            "kind": "unavailable",
            "deliveries_source": DOCUMENT_RENDER_CONTRACT,
            "end_turn": True,
        },
    }


def _model_delivery_summaries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for entry in entries:
        manifest = entry["manifest"]
        summaries.append(
            {
                "contract_version": manifest["contract_version"],
                "operation": manifest["operation"],
                "case_id": manifest["case_id"],
                "archive_revision": manifest["archive_revision"],
                "document_type": manifest["document_type"],
                "template_version": manifest.get("template_version"),
                "mode": manifest.get("mode"),
                "delivery_state": manifest["delivery_state"],
                "content_digest": manifest["content_digest"],
                "validation_summary": deepcopy(manifest["validation_summary"]),
            }
        )
    return summaries


def _model_managed_delivery_view(
    *,
    root: Path,
    case_id: str,
    archive_response: Mapping[str, Any] | None = None,
    edit_document_types: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    delivery_set: Mapping[str, Any] | None = None
    try:
        response = (
            archive_response
            if archive_response is not None
            else CaseArchive(root).read(case_id)
        )
        archive = _validate_model_archive_read(
            root=root,
            case_id=case_id,
            response=response,
        )
        delivery_set_path = root / ".arbibuddy" / "cases" / case_id / "delivery-set-v1.json"
        if delivery_set_path.is_file():
            delivery_set = load_delivery_set(
                root,
                case_id,
                archive_revision=archive["revision"],
                archive_markdown=archive.get("markdown"),
            )
        output_root = root / ".arbibuddy" / "cases" / case_id / "output"
        if delivery_set is not None and (
            not output_root.is_dir()
            or not any(path.is_dir() for path in output_root.iterdir())
        ):
            entries = []
        else:
            entries = _model_public_delivery_entries(
                root=root,
                case_id=case_id,
                archive=archive,
                delivery_set=delivery_set,
            )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        detail = str(error).strip() or "canonical 交付校验失败"
        error_code = (
            "stale_managed_delivery"
            if "修订版本不一致" in detail
            or "archive_revision" in detail
            else "invalid_model_delivery"
        )
        return _model_unavailable_view(
            case_id=case_id,
            error_code=error_code,
            reason=f"{DOCUMENT_RENDER_CONTRACT}/{CASE_ARCHIVE_CONTRACT} canonical 交付不可用：{detail}",
            delivery_set=delivery_set,
            next_action=(
                "retry_managed_view_with_access"
                if "机器交付清单不可读取" in detail
                else "rerun_document_render_v1"
            ),
        )

    if delivery_set is not None:
        expected_types = tuple(delivery_set["expected_document_types"])
        completed_types = tuple(entry["document_type"] for entry in entries)
        missing_types = tuple(
            document_type
            for document_type in expected_types
            if document_type not in completed_types
        )
        extra_types = tuple(
            document_type
            for document_type in completed_types
            if document_type not in expected_types
        )
        if missing_types or extra_types:
            detail = (
                "delivery set 尚未完成，缺失成员："
                + "、".join(missing_types or ("无",))
            )
            if extra_types:
                detail += "；存在未声明成员"
            return _model_unavailable_view(
                case_id=case_id,
                    error_code="delivery_set_incomplete",
                reason=detail,
                delivery_set=delivery_set,
                missing_document_types=missing_types,
            )
        entries.sort(key=lambda entry: expected_types.index(entry["document_type"]))

    if not entries:
        return _model_unavailable_view(case_id=case_id, reason="尚无已校验的交付文件")
    delivery_state = entries[0]["delivery_state"]
    next_action = MODEL_DELIVERY_NEXT_ACTIONS[delivery_state]
    presentation_files: list[dict[str, Any]] = []
    for entry in entries:
        for kind, path_key in (
            ("document", "document_path"),
            ("checklist", "checklist_path"),
        ):
            presentation_files.append(
                {
                    "delivery_label": entry["document_type"],
                    "kind": kind,
                    "order": len(presentation_files) + 1,
                    "path": str(entry[path_key]),
                    "sha256": _digest(entry[path_key]),
                    "state": delivery_state,
                }
            )
    edit_context = None
    if edit_document_types is not None:
        try:
            available = {entry["document_type"] for entry in entries}
            if set(edit_document_types) - available:
                raise ValueError("请求的文书不属于当前完整交付集合")
            edit_context = {
                "archive": deepcopy(archive),
                "documents": [
                    _extract_edit_document(entry)
                    for entry in entries
                    if not edit_document_types or entry["document_type"] in edit_document_types
                ],
            }
        except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError) as error:
            return _model_unavailable_view(case_id=case_id, reason=str(error))
    return {
        "schema_version": 1,
        "case_id": case_id,
        "delivery_state": delivery_state,
        "stopped": False,
        "reason": None,
        "next_action": next_action,
        "presentation_files": presentation_files,
        "present_files_arguments": {
            "cwd": str(public_path(root.resolve())),
            "files": [item["path"] for item in presentation_files],
        },
        "delivery_summaries": _model_delivery_summaries(entries),
        "current_archive_revision": archive["revision"],
        "archive_dependencies_verified": any(
            entry["archive_dependencies_verified"] for entry in entries
        ),
        **({"edit_context": edit_context} if edit_context is not None else {}),
        "delivery_set": (
            {
                "contract_version": DELIVERY_SET_CONTRACT_VERSION,
                "delivery_set_id": delivery_set.get("delivery_set_id"),
                "archive_revision": delivery_set.get("archive_revision"),
                "expected_document_types": list(delivery_set["expected_document_types"]),
                "completed_document_types": list(
                    entry["document_type"] for entry in entries
                ),
                "missing_document_types": [],
            }
            if delivery_set is not None
            else None
        ),
        "delivery_termination": {
            "terminal": True,
            "kind": "formal" if delivery_state == "final_ready" else "candidate",
            "deliveries_source": DOCUMENT_RENDER_CONTRACT,
            "preview_reminder": "提交或发送前由用户在 Word/WPS 中预览字段、金额、分页和落款。",
            "next_action": next_action,
            "end_turn": True,
            "platform_follow_up": "status_only",
        },
    }


def managed_delivery_view(*, workspace: Path, case_id: str) -> dict[str, Any]:
    """仅展示当前档案授权且文件摘要一致的交付；校验失败不猜测其他来源。"""
    root = absolute_path_for_io(workspace)
    try:
        response = CaseArchive(root).read(case_id)
        if response.get("ok") is not True:
            errors = response.get("errors", [])
            detail = errors[0].get("message", "案情档案不可读取") if errors else "案情档案不可读取"
            raise ValueError(detail)
    except (OSError, TypeError, ValueError) as error:
        return _model_unavailable_view(case_id=case_id, reason=str(error))
    return _model_managed_delivery_view(root=root, case_id=case_id, archive_response=response)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_edit_document(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Extract rendered text, never a stored render request or inferred bindings."""
    manifest = entry["manifest"]
    data = path_for_io(entry["document_path"]).read_bytes()
    if sha256(data).hexdigest() != manifest["document"]["sha256"]:
        raise ValueError("改稿正文读取时 canonical 产物摘要不一致")
    with ZipFile(BytesIO(data)) as package:
        body = ET.fromstring(package.read("word/document.xml")).find(_W + "body")
    if body is None:
        raise ValueError("改稿正文缺少 OOXML body")

    def blocks(parent: Any, locator: str) -> list[dict[str, Any]]:
        result = []
        for index, node in enumerate(parent):
            source = f"{locator}/*[{index + 1}]"
            if node.tag == _W + "p":
                text = "".join(
                    (child.text or "") if child.tag == _W + "t" else
                    "\t" if child.tag == _W + "tab" else "\n"
                    for child in node.iter()
                    if child.tag in {_W + "t", _W + "tab", _W + "br", _W + "cr"}
                )
                style = node.find(f"{_W}pPr/{_W}pStyle")
                numbering = node.find(f"{_W}pPr/{_W}numPr")
                result.append({
                    "kind": "paragraph", "source_locator": source, "text": text,
                    "style_id": style.get(_W + "val") if style is not None else None,
                    "numbering": {
                        child.tag.rsplit("}", 1)[-1]: child.get(_W + "val")
                        for child in numbering
                    } if numbering is not None else None,
                })
            elif node.tag == _W + "tbl":
                result.append({
                    "kind": "table", "source_locator": source,
                    "rows": [
                        [blocks(cell, f"{source}/tr[{row_index + 1}]/tc[{cell_index + 1}]")
                         for cell_index, cell in enumerate(row.findall(_W + "tc"))]
                        for row_index, row in enumerate(node.findall(_W + "tr"))
                    ],
                })
        return result

    return {
        "document_type": manifest["document_type"], "title": manifest["title"],
        "mode": manifest["mode"], "template_version": manifest["template_version"],
        "archive_revision": manifest["archive_revision"],
        "render_requirements": deepcopy(_DOCUMENT_TYPE_CONTRACTS[manifest["document_type"]]),
        "source": {"canonical_docx": str(entry["document_path"]),
                   "sha256": manifest["document"]["sha256"],
                   "delivery_set_id": manifest.get("delivery_set_id"),
                   "delivery_set_digest": manifest.get("delivery_set_digest"),
                   "content_digest": manifest["content_digest"]},
        "blocks": blocks(body, "word/document.xml/body"),
    }


def describe_read_contract(operation: str) -> dict[str, Any]:
    edit = operation == "edit-context"
    return {
        **({"contract_version": "document.edit-context-v1"} if edit else {"schema_version": 1}),
        "operation": f"document.{operation}",
        "entrypoint": "scripts.documents.view.managed_edit_context" if edit else "scripts.documents.view.managed_delivery_view",
        "input": {"required": {"case_id": "case-<24 hex>"},
                  "optional": {"delivery_set_id": "expected current set id", **({"document_types": "non-empty unique array of current member types; omit for all"} if edit else {})},
                  "additional_fields": False if edit else "ignored by legacy view transport"},
        "output": ({"archive": "current authoritative CaseArchive read result",
                    "current_archive_revision": "current non-negative archive revision",
                    "archive_dependencies_verified": "candidate dependency reuse verified",
                    "documents": "ordered rendered paragraph/table blocks with text, styles, numbering and source locators; all selected members, without truncation",
                    "document_fields": ["document_type", "title", "mode", "template_version", "archive_revision", "render_requirements", "source", "blocks"],
                    "render_requirements": "same registered per-type contract as render describe; sections lists every required block, including headings not rendered separately",
                    "source_fields": ["canonical_docx", "sha256", "content_digest", "delivery_set_id", "delivery_set_digest"],
                    "paragraph": {"kind": "paragraph", "text": "exact rendered text, including empty paragraphs",
                                  "style_id": "OOXML style id or null", "numbering": "numPr child attributes or null; rendered numbering is not duplicated in text",
                                  "source_locator": "word/document.xml/body/*[n], with 1-based OOXML child positions"},
                    "table": {"kind": "table", "source_locator": "OOXML child location",
                              "rows": "array of rows; each row is array of cells; each cell is ordered paragraph/table blocks"},
                    "delivery_set": "validated complete current set identity, or null for legacy standalone deliveries"} if edit else
                   {"stopped": "false on success; true on validation failure with empty presentation lists",
                    "presentation": "consume successful present_files_arguments verbatim"}),
        "rules": "verify installation, archive freshness, complete set, manifests, file hashes and OOXML; no writes to archive or deliveries",
        "ownership": "archive is fact source; rendered text is editing material, not original sections, request or bindings; model reconstructs sections and bindings from current archive",
        "recovery": "on hash or freshness failure return no text; do not repair manifests or restore requests from private logs",
        "examples": {"request": {"case_id": "case-0123456789abcdef01234567"}},
    }


def managed_edit_context(*, workspace: Path, case_id: str,
                         delivery_set_id: str | None = None,
                         document_types: list[str] | None = None) -> dict[str, Any]:
    """Read verified editing material before committing a correction."""
    response: dict[str, Any] = {
        "contract_version": "document.edit-context-v1", "operation": "document.edit-context",
        "ok": False, "result": None, "errors": [], "warnings": [],
    }
    try:
        if delivery_set_id is not None and (not isinstance(delivery_set_id, str) or not delivery_set_id.strip()):
            raise ValueError("delivery_set_id 必须是非空字符串")
        if document_types is not None and (
            not isinstance(document_types, list) or not document_types
            or any(not isinstance(item, str) or item not in DOCUMENT_TYPES for item in document_types)
            or len(set(document_types)) != len(document_types)
        ):
            raise ValueError("document_types 必须是非空且无重复的文书类型数组")
        root = absolute_path_for_io(workspace)
        with CaseArchive(root)._read_transaction(case_id) as archive_response:
            if archive_response.get("ok") is not True:
                response["errors"] = deepcopy(archive_response.get("errors", []))
                return response
            # Check the requested identity before extracting any body text.
            if delivery_set_id is not None:
                load_delivery_set(root, case_id, delivery_set_id=delivery_set_id)
            view = _model_managed_delivery_view(
                root=root, case_id=case_id, archive_response=archive_response,
                edit_document_types=tuple(document_types or ()),
            )
            if view["stopped"]:
                response["errors"] = [{"code": view["error_code"], "path": "delivery",
                    "message": view["reason"], "expected": "complete current verified delivery",
                    "received": "unavailable", "recoverable": True}]
            else:
                if view["delivery_set"] is not None:
                    load_delivery_set(root, case_id,
                        delivery_set_id=view["delivery_set"]["delivery_set_id"],
                        archive_revision=view["current_archive_revision"],
                        archive_markdown=view["edit_context"]["archive"]["markdown"])
                response.update(ok=True, result={
                    **view["edit_context"], "delivery_set": view["delivery_set"],
                    "current_archive_revision": view["current_archive_revision"],
                    "archive_dependencies_verified": view["archive_dependencies_verified"],
                })
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        response["errors"] = [{"code": "invalid_edit_context", "path": "request",
                               "message": str(error), "expected": "valid current managed editing source",
                               "received": "unavailable", "recoverable": True}]
    return response
