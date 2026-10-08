"""文书交付只读视图：校验档案与生成回执，不运行案情或对话编排。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from scripts.case_archive import CaseArchive
from scripts.case_archive.service import CONTRACT_VERSION as CASE_ARCHIVE_CONTRACT
from scripts.platform_paths import absolute_path_for_io, public_path
from .receipts import validate_archive_read as _validate_model_archive_read
from .receipts import read_delivery_entries as _model_public_delivery_entries
from .public import CONTRACT_VERSION as DOCUMENT_RENDER_CONTRACT, DELIVERY_SET_CONTRACT_VERSION, load_delivery_set

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
