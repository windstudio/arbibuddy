"""Public, model-facing ``document.render`` task contract.

The model owns the document narrative.  This module owns the narrow,
deterministic boundary around a real DOCX: archive revision reads, explicit
locked bindings, placeholder and internal-content checks, OOXML auditing and
atomic canonical delivery.  It deliberately does not compose legal prose.

The sole model-facing entry point is :func:`render` in this module.  The
Only model-authored full-text requests enter this contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
from importlib import import_module
import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.case_archive import CaseArchive
from scripts.case_archive.service import CONTRACT_VERSION as ARCHIVE_CONTRACT
from scripts.platform_paths import absolute_path_for_io, public_path
from scripts.case_archive.service import _ArchiveLockTimeout
from scripts.runtime_identity import (
    RuntimeIdentityModule,
    runtime_identity_failure_message,
)

from .delivery import commit_generation_staging
from .archive_dependencies import (
    candidate_dependencies, validate_archive_freshness, validate_dependency_shape,
)
from .checklist_policy import required_checklist_bullets
from .ooxml_check import INTERNAL_LEAK_TERMS, audit_docx
from .registry import get_template
from .runtime_dependencies import (
    DocumentRuntimeUnavailable,
    ensure_document_runtime,
)


CONTRACT_METADATA = {
    "contract_version": "document.render-v1",
    "operation": "document.render",
    "entrypoint": "scripts.documents.public.render",
    "archive_contract": ARCHIVE_CONTRACT,
}
CONTRACT_VERSION = CONTRACT_METADATA["contract_version"]
OPERATION = CONTRACT_METADATA["operation"]
PUBLIC_ENTRYPOINT = CONTRACT_METADATA["entrypoint"]
TEMPLATE_VERSION = "1.0.0"
DOCUMENT_TYPES = (
    "employment_obligation_demand_letter",
    "forced_termination_notice",
    "arbitration_application",
    "arbitration_defense",
    "evidence_catalog",
    "company_deregistration_restriction_request_shanghai",
    "property_preservation_application",
    "enforcement_application",
)
MODES = ("candidate", "external_final")
DELIVERY_SET_CONTRACT_VERSION = "document.delivery-set-v1"
DELIVERY_SET_OPERATION = "document.delivery-set"
DELIVERY_SET_FILENAME = "delivery-set-v1.json"

_PROGRAM_DOCUMENT_TYPES = frozenset(
    {
        "company_deregistration_restriction_request_shanghai",
        "property_preservation_application",
        "enforcement_application",
    }
)
_HIGH_RISK_DOCUMENT_TYPES = frozenset(
    {
        "forced_termination_notice",
        *_PROGRAM_DOCUMENT_TYPES,
    }
)
_CANDIDATE_BINDING_DOCUMENT_TYPES = frozenset(
    {
        "employment_obligation_demand_letter",
        *_HIGH_RISK_DOCUMENT_TYPES,
    }
)

_TEMPLATE_IDS = {
    "employment_obligation_demand_letter": "employment-obligation-demand-letter",
    "forced_termination_notice": "forced-termination-notice",
    "arbitration_application": "labor-arbitration-application",
    "arbitration_defense": "labor-arbitration-defense",
    "evidence_catalog": "evidence-catalog",
    "company_deregistration_restriction_request_shanghai": "shanghai-cancellation-restriction-application",
    "property_preservation_application": "property-preservation-application",
    "enforcement_application": "enforcement-application",
}
_TEMPLATE_TO_DOCUMENT_TYPE = {
    template_id: document_type
    for document_type, template_id in _TEMPLATE_IDS.items()
}
_DELIVERY_SET_BUNDLES = {
    "arbitration_application": ("arbitration_application", "evidence_catalog"),
}
_DOCUMENT_TYPE_TITLES = {
    key: get_template(value).title for key, value in _TEMPLATE_IDS.items()
}
_SECTION_ALIASES = {
    "employment_obligation_demand_letter": (
        ("收件人",),
        ("劳动关系说明",),
        ("具体义务",),
        ("履行期限",),
        ("沟通与保留权利",),
    ),
    "forced_termination_notice": (
        ("收件人",),
        ("解除意思表示",),
        ("解除理由",),
        ("结算与手续",),
        ("落款",),
    ),
    "arbitration_application": (
        ("申请人",),
        ("被申请人",),
        ("仲裁请求",),
        ("事实与理由",),
        ("落款",),
    ),
    "arbitration_defense": (
        ("答辩人",),
        ("被答辩人",),
        ("答辩意见",),
        ("落款",),
    ),
    "evidence_catalog": (
        ("标题", "证据目录"),
        ("五列表格", "证据列表", "证据目录表格"),
    ),
    "company_deregistration_restriction_request_shanghai": (
        ("申请人",),
        ("被申请人",),
        ("仲裁受理信息", "仲裁案件信息"),
        ("申请事项", "请求事项"),
        ("事实与理由",),
        ("附件", "附件材料"),
        ("落款",),
    ),
    "property_preservation_application": (
        ("申请人",),
        ("被申请人",),
        ("保全请求", "申请事项"),
        ("事实与理由",),
        ("财产线索", "财产线索及风险"),
        ("担保", "担保方案"),
        ("落款",),
    ),
    "enforcement_application": (
        ("申请执行人", "申请人"),
        ("被执行人",),
        ("执行请求", "申请执行请求"),
        ("事实与理由",),
        ("财产线索", "财产线索及风险"),
        ("附件", "附件材料"),
        ("落款",),
    ),
}
_FINAL_BINDING_COUNTS = {
    "employment_obligation_demand_letter": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "date": 1,
        "signature": 1,
    },
    "forced_termination_notice": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "evidence": 1,
        "date": 2,
        "signature": 1,
    },
    "arbitration_application": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "amount": 1,
        "date": 1,
        "signature": 1,
    },
    "arbitration_defense": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "date": 1,
        "signature": 1,
    },
    "evidence_catalog": {"document_heading": 1, "evidence": 1},
    "company_deregistration_restriction_request_shanghai": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "evidence": 1,
        "date": 1,
        "signature": 1,
    },
    "property_preservation_application": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "amount": 1,
        "evidence": 1,
        "date": 1,
        "signature": 1,
    },
    "enforcement_application": {
        "document_heading": 1,
        "party": 2,
        "claim": 1,
        "amount": 1,
        "evidence": 1,
        "date": 1,
        "signature": 1,
        "attachment": 1,
    },
}
_DOCUMENT_TYPE_CONTRACTS = {
    "employment_obligation_demand_letter": {
        "title": "劳动用工义务催告函",
        "sections": ["收件人", "劳动关系说明", "具体义务", "履行期限", "沟通与保留权利"],
        "signature_layout": "最后区块中沟通正文与签署人、日期按换行分开；仅对明确绑定或声明占位的签署行排版，不拆写正文。",
        "external_final": "不需要高风险动作确认；不得写成解除通知或生效文书履行催告",
    },
    "forced_termination_notice": {
        "title": "被迫解除劳动合同通知书",
        "sections": ["收件人", "解除意思表示", "解除理由", "结算与手续", "落款"],
        "external_final": "必须有当前 revision 的完整高风险确认和现行规则核验",
    },
    "arbitration_application": {
        "title": "劳动人事争议仲裁申请书",
        "sections": ["申请人", "被申请人", "仲裁请求", "事实与理由", "落款"],
        "external_final": "需要主体、主张、金额、日期和落款锁定要素",
    },
    "arbitration_defense": {
        "title": "劳动人事争议仲裁答辩书",
        "sections": ["答辩人", "被答辩人", "答辩意见", "落款"],
        "external_final": "需要主体、主张、日期和落款锁定要素",
    },
    "evidence_catalog": {
        "title": "证据目录",
        "sections": ["标题", "五列表格"],
        "external_final": "需要标题和至少一项证据锁定要素",
    },
    "company_deregistration_restriction_request_shanghai": {
        "title": "限制公司注销申请书",
        "sections": ["申请人", "被申请人", "仲裁受理信息", "申请事项", "事实与理由", "附件", "落款"],
        "external_final": "必须保持上海专项实务材料边界，并有当前适用的上海口径核验、实质确认和主体/主张/证据/日期/落款绑定",
    },
    "property_preservation_application": {
        "title": "财产保全申请书",
        "sections": ["申请人", "被申请人", "保全请求", "事实与理由", "财产线索", "担保", "落款"],
        "external_final": "必须说明程序阶段、财产线索、担保和错误保全风险，并有当前适用规则核验、实质确认和必要锁定要素",
    },
    "enforcement_application": {
        "title": "强制执行申请书",
        "sections": ["申请执行人", "被执行人", "执行请求", "事实与理由", "财产线索", "附件", "落款"],
        "external_final": "必须说明有效执行依据、履行情况、申请执行时效和管辖，并有当前适用规则核验、实质确认和必要锁定要素",
    },
}
_BINDING_KINDS = (
    "party",
    "claim",
    "amount",
    "fact_amount",
    "date",
    "document_heading",
    "signature",
    "evidence",
    "attachment",
)
_TOP_LEVEL_FIELDS = (
    "case_id",
    "archive_revision",
    "document_type",
    "template_version",
    "mode",
    "title",
    "sections",
    "locked_bindings",
    "placeholders",
    "confirmation_refs",
    "authority_refs",
    "additional_checklist_items",
    "delivery_set_id",
)
_FACT_AMOUNT_HEADINGS = {
    "arbitration_application": frozenset({"事实与理由"}),
    "employment_obligation_demand_letter": frozenset({"劳动关系说明"}),
    "forced_termination_notice": frozenset({"解除理由"}),
}
_SECTION_FIELDS = ("section_id", "heading", "full_text")
_BINDING_FIELDS = ("kind", "archive_record_ref", "rendered_value", "label", "occurrences")
_PLACEHOLDER_FIELDS = ("placeholder_id", "text", "label")
_RECORD_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-\d{3,}$")
_RECORD_HEADING_PATTERN = re.compile(
    r"^### \[(?P<record_id>[A-Z][A-Z0-9_]*-\d{3,})\] [^\r\n]+$",
    re.MULTILINE,
)
_PLACEHOLDER_PATTERNS = (
    re.compile(r"【待补[:：][^】\r\n]+】"),
    re.compile(r"\{\{[^{}\r\n]+\}\}"),
    re.compile(r"\[待补[:：][^\]\r\n]+\]"),
)
_RECORD_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:F|CL|E|A|CAL|AUTH|R|CONF|N|SC|OR|DR)-\d{3,}(?![A-Za-z0-9_])"
)
_INTERNAL_PATTERNS = (
    re.compile(r"(?:system|developer|assistant)\s*(?:prompt|message|instruction)", re.I),
    re.compile(r"(?:系统|开发者|模型|助手)\s*(?:提示|消息|指令|推理|待办|说明)"),
    re.compile(r"(?:忽略|无视|绕过|覆盖).{0,16}(?:规则|约束|指令|提示)"),
    re.compile(r"(?:\.arbibuddy|scripts[./\\]|tests[./\\]|(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])|\\\\)[^\r\n]*"),
    re.compile(r"(?:python\s+-m|python\s+-B|--(?:root|input|output)|CLI|JSON)\b", re.I),
    re.compile(r"(?:模型推理|链式思考|chain\s+of\s+thought|开发者指令)", re.I),
    re.compile(r"\b[0-9a-f]{64}\b", re.I),
    _RECORD_REFERENCE_PATTERN,
)
_CONFLICT_PATTERNS = (
    re.compile(r"事实冲突|冲突事实"),
    re.compile(r"存在冲突|冲突状态\s*[=:：]\s*冲突"),
    re.compile(r"\b(?:conflict|conflicted)\b", re.I),
)
_EVIDENCE_DELIMITER_PATTERN = re.compile(r"[|｜\t]")
_CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?<![0-9.])(?P<amount>[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:元|圆|人民币)"
)
_CONFIRMATION_FIELD_LABELS = {
    "action_type": ("action_type", "动作类别", "高风险确认标识"),
    "action_scope": ("action_scope", "动作范围"),
    "risk_summary": ("risk_summary", "风险摘要"),
    "alternatives_presented": ("alternatives_presented", "替代方案"),
    "user_choice": ("user_choice", "用户选择"),
    "confirmed_at": ("confirmed_at", "确认时间"),
    "archive_revision": ("archive_revision", "绑定档案修订版本"),
    "source_refs": ("source_refs", "来源引用"),
    "authority_conclusion": ("authority_conclusion", "核验结论"),
    "authority_topic": ("authority_topic", "核验事项"),
    "authority_scope": ("authority_scope", "适用地区", "适用范围"),
}
_POSITIVE_CONFIRMATION_TERMS = (
    "明确选择",
    "确认按",
    "我确认",
    "同意按",
    "确认继续",
    "选择继续",
)
_NEGATIVE_CONFIRMATION_TERMS = (
    "拒绝",
    "不同意",
    "暂缓",
    "先不要",
    "不确认",
    "不继续",
    "不要",
)
_FORCED_ACTION_TYPE_TERMS = (
    "planned_termination",
    "forced_termination",
    "被迫解除",
)
_DEMAND_NEGATION_PREFIXES = (
    "不构成",
    "不作为",
    "不用于",
    "不属于",
    "不应",
    "不得替代",
    "不得将",
    "不得把",
    "不是",
    "不得写成",
    "不得",
    "不能将",
    "不能把",
    "不能作为",
    "不能",
    "并非",
    "避免",
    "禁止",
    "不等于",
)
_DEMAND_PROHIBITED_PATTERNS = (
    (
        re.compile(
            r"(?:逾期|未按(?:期|时)|期限届满|履行期满|催告后|催告期满)"
            r"[^。；\r\n]{0,32}(?:即|就|则|将|可|视为|导致|造成|否则|因而)\s*"
            r"(?:被迫)?(?:解除|终止)(?:劳动(?:合同|关系))?"
        ),
        "期限届满后解除",
    ),
    (
        re.compile(
            r"(?:解除|终止)(?:劳动(?:合同|关系))?"
        ),
        "解除或终止",
    ),
    (
        re.compile(
            r"(?:被迫)?(?:解除|终止)(?:劳动(?:合同|关系))?通知(?:书)?"
        ),
        "解除通知",
    ),
    (
        re.compile(r"劳动(?:合同|关系)(?:已|已经)?\s*(?:被)?(?:解除|终止)"),
        "劳动关系或合同已解除",
    ),
    (re.compile(r"生效(?:的)?(?:裁判(?:文书)?|判决(?:书)?|裁定(?:书)?).{0,20}(?:履行|执行)催告"), "生效裁判履行催告"),
)
_CANDIDATE_PLACEHOLDER_TERMS = {
    "document_heading": ("标题", "文书"),
    "party": ("姓名", "单位", "公司", "主体", "收件人", "地址", "名称"),
    "claim": ("主张", "请求", "义务", "理由"),
    "date": ("日期", "时间", "期限", "生效"),
    "signature": ("签名", "落款"),
    "evidence": ("证据", "材料"),
}


def _error(
    code: str,
    path: str,
    message: str,
    expected: Any,
    received: Any,
    *,
    recoverable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "expected": expected,
        "received": received,
        "recoverable": recoverable,
    }


def _load_docx_builder() -> Any:
    return import_module("scripts.documents.docx_builder")


def _success(result: dict[str, Any], warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": OPERATION,
        "ok": True,
        "result": result,
        "errors": [],
        "warnings": warnings or [],
    }


def _failure(errors: list[dict[str, Any]], warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": OPERATION,
        "ok": False,
        "errors": errors,
        "warnings": warnings or [],
    }


def _single_line(value: Any, path: str, errors: list[dict[str, Any]]) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        errors.append(
            _error(
                "invalid_text",
                path,
                "字段必须是非空单行文本",
                "non-empty single-line string",
                value,
            )
        )
        return None
    return value.strip()


def _multiline_text(value: Any, path: str, errors: list[dict[str, Any]]) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        errors.append(
            _error(
                "invalid_text",
                path,
                "正文必须是非空文本且不得包含空字符",
                "non-empty text without NUL",
                value,
            )
        )
        return None
    return value


def _optional_single_line(value: Any, path: str, errors: list[dict[str, Any]]) -> str:
    if value is None or value == "":
        return ""
    return _single_line(value, path, errors) or ""


def _object(value: Any, path: str, errors: list[dict[str, Any]]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(_error("invalid_object", path, "字段必须是对象", "object", value))
        return None
    return value


def _exact_keys(
    value: Mapping[str, Any], allowed: Sequence[str], path: str, errors: list[dict[str, Any]]
) -> None:
    for key in sorted(set(value) - set(allowed)):
        errors.append(
            _error(
                "unknown_field",
                f"{path}.{key}",
                "字段不在公开 document.render 契约中",
                list(allowed),
                value[key],
            )
        )


def _declared_placeholders(value: Any, errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(_error("invalid_placeholder", "placeholders", "占位符必须是数组", "array", value))
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        path = f"placeholders[{index}]"
        obj = _object(item, path, errors)
        if obj is None:
            continue
        _exact_keys(obj, _PLACEHOLDER_FIELDS, path, errors)
        placeholder_id = _single_line(obj.get("placeholder_id"), f"{path}.placeholder_id", errors) or ""
        text = _single_line(obj.get("text"), f"{path}.text", errors) or ""
        label = _optional_single_line(obj.get("label", ""), f"{path}.label", errors)
        if not placeholder_id or not text:
            continue
        if placeholder_id in seen or any(item["text"] == text for item in normalized):
            errors.append(
                _error(
                    "invalid_placeholder",
                    path,
                    "占位符标识和文本必须唯一",
                    "unique placeholder_id and text",
                    item,
                )
            )
        if not any(pattern.fullmatch(text) for pattern in _PLACEHOLDER_PATTERNS):
            errors.append(
                _error(
                    "invalid_placeholder",
                    f"{path}.text",
                    "占位文本必须使用明确的待补标记",
                    "【待补：...】、{{...}} 或 [待补：...]",
                    text,
                )
            )
        seen.add(placeholder_id)
        normalized.append({"placeholder_id": placeholder_id, "text": text, "label": label})
    return normalized


def _placeholder_tokens(text: str) -> set[str]:
    return {
        token
        for pattern in _PLACEHOLDER_PATTERNS
        for token in pattern.findall(text)
    }


def _user_text_values(request: Mapping[str, Any]) -> list[str]:
    values = _request_content_values(request)
    for placeholder in request.get("placeholders", []) or []:
        if isinstance(placeholder, Mapping):
            values.extend((placeholder.get("text", ""), placeholder.get("label", "")))
    return [value for value in values if isinstance(value, str)]


def _request_content_values(request: Mapping[str, Any]) -> list[str]:
    values: list[str] = [request.get("title", "")]
    for section in request.get("sections", []):
        if isinstance(section, Mapping):
            values.extend((section.get("heading", ""), section.get("full_text", "")))
    for binding in request.get("locked_bindings", []):
        if isinstance(binding, Mapping):
            values.extend((binding.get("rendered_value", ""), binding.get("label", "")))
    values.extend(request.get("additional_checklist_items", []) or [])
    return [value for value in values if isinstance(value, str)]


def _document_body_values(request: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for section in request.get("sections", []):
        if isinstance(section, Mapping):
            values.extend((section.get("heading", ""), section.get("full_text", "")))
    return [value for value in values if isinstance(value, str)]


def _internal_leaks(values: Sequence[str]) -> list[str]:
    leaks: list[str] = []
    for value in values:
        folded_value = value.casefold()
        for term in INTERNAL_LEAK_TERMS:
            if term.casefold() in folded_value:
                if term not in leaks:
                    leaks.append(term)
                break
        else:
            for pattern in _INTERNAL_PATTERNS:
                match = pattern.search(value)
                if match is not None:
                    marker = match.group(0)[:160]
                    if marker not in leaks:
                        leaks.append(marker)
                    break
    return leaks


def _validate_refs(
    value: Any,
    path: str,
    errors: list[dict[str, Any]],
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(_error("invalid_object", path, "引用必须是数组", "array[string]", value))
        return []
    result: list[str] = []
    required_prefix = {
        "confirmation_refs": "CONF-",
        "authority_refs": "AUTH-",
    }.get(path)
    for index, item in enumerate(value):
        ref = _single_line(item, f"{path}[{index}]", errors)
        if ref is None:
            continue
        if _RECORD_ID_PATTERN.fullmatch(ref) is None:
            errors.append(
                _error(
                    "binding_not_found",
                    f"{path}[{index}]",
                    "引用必须是稳定档案记录标识",
                    "record id such as F-001",
                    ref,
                )
            )
        elif required_prefix is not None and not ref.startswith(required_prefix):
            errors.append(
                _error(
                    "confirmation_missing" if path == "confirmation_refs" else "authority_stale",
                    f"{path}[{index}]",
                    "引用必须指向对应类型的档案记录",
                    f"{required_prefix}###",
                    ref,
                )
            )
        if ref in result:
            errors.append(
                _error("binding_mismatch", f"{path}[{index}]", "引用不得重复", "unique record refs", ref)
            )
        result.append(ref)
    return result


def _validate_request(request: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(request, Mapping):
        return None, [_error("invalid_object", "request", "document.render 请求必须是对象", "object", request)]
    errors: list[dict[str, Any]] = []
    _exact_keys(request, _TOP_LEVEL_FIELDS, "request", errors)
    for field in _TOP_LEVEL_FIELDS[:8]:
        if field not in request:
            errors.append(_error("missing_input", field, "请求缺少必填字段", "required", None))

    case_id = _single_line(request.get("case_id"), "case_id", errors)
    revision = request.get("archive_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        errors.append(
            _error(
                "invalid_revision",
                "archive_revision",
                "档案修订版本必须是非负整数",
                "integer >= 0",
                revision,
            )
        )
        revision = 0

    document_type = _single_line(request.get("document_type"), "document_type", errors)
    if document_type is not None and document_type not in DOCUMENT_TYPES:
        errors.append(
            _error(
                "unsupported_document_type",
                "document_type",
                "本次公共工具只支持已开放的八类模型主导文书",
                list(DOCUMENT_TYPES),
                document_type,
            )
        )
    template_version = _single_line(request.get("template_version"), "template_version", errors)
    if template_version is not None and template_version != TEMPLATE_VERSION:
        errors.append(
            _error(
                "template_version_mismatch",
                "template_version",
                "版式模板版本不是当前公共版本",
                TEMPLATE_VERSION,
                template_version,
            )
        )
    mode = _single_line(request.get("mode"), "mode", errors)
    if mode is not None and mode not in MODES:
        errors.append(_error("invalid_mode", "mode", "文书成熟度不在公开枚举中", list(MODES), mode))
    title = _single_line(request.get("title"), "title", errors)
    if document_type in _DOCUMENT_TYPE_TITLES and title is not None:
        expected_title = _DOCUMENT_TYPE_TITLES[document_type]
        if title != expected_title:
            errors.append(_error("binding_mismatch", "title", "标题必须与文书类型的注册标题一致", expected_title, title))

    sections_value = request.get("sections")
    sections: list[dict[str, str]] = []
    if not isinstance(sections_value, list) or not sections_value:
        errors.append(_error("missing_section", "sections", "正文区块必须是非空数组", "non-empty array", sections_value))
    else:
        section_ids: set[str] = set()
        for index, item in enumerate(sections_value):
            path = f"sections[{index}]"
            obj = _object(item, path, errors)
            if obj is None:
                continue
            _exact_keys(obj, _SECTION_FIELDS, path, errors)
            section_id = _single_line(obj.get("section_id"), f"{path}.section_id", errors) or ""
            heading = _single_line(obj.get("heading"), f"{path}.heading", errors) or ""
            full_text = _multiline_text(obj.get("full_text"), f"{path}.full_text", errors) or ""
            if section_id in section_ids and section_id:
                errors.append(_error("binding_mismatch", f"{path}.section_id", "section_id 不得重复", "unique section_id", section_id))
            section_ids.add(section_id)
            sections.append({"section_id": section_id, "heading": heading, "full_text": full_text})
        if document_type in _SECTION_ALIASES and len(sections) != len(_SECTION_ALIASES[document_type]):
            errors.append(
                _error(
                    "missing_section",
                    "sections",
                    "文书正文区块数量与模板不一致",
                    len(_SECTION_ALIASES[document_type]),
                    len(sections),
                )
            )
        elif document_type in _SECTION_ALIASES:
            for index, (section, aliases) in enumerate(zip(sections, _SECTION_ALIASES[document_type], strict=True)):
                if section["heading"] not in aliases:
                    errors.append(
                        _error(
                            "missing_section",
                            f"sections[{index}].heading",
                            "正文区块标题或顺序与模板不一致",
                            list(aliases),
                            section["heading"],
                        )
                    )

    bindings_value = request.get("locked_bindings")
    bindings: list[dict[str, str]] = []
    if not isinstance(bindings_value, list) or not bindings_value:
        errors.append(_error("missing_input", "locked_bindings", "必须提供至少一个锁定要素", "non-empty array", bindings_value))
    else:
        seen_binding_keys: set[tuple[str, str, str]] = set()
        for index, item in enumerate(bindings_value):
            path = f"locked_bindings[{index}]"
            obj = _object(item, path, errors)
            if obj is None:
                continue
            _exact_keys(obj, _BINDING_FIELDS, path, errors)
            kind = _single_line(obj.get("kind"), f"{path}.kind", errors) or ""
            ref = _single_line(obj.get("archive_record_ref"), f"{path}.archive_record_ref", errors) or ""
            rendered_value = _single_line(obj.get("rendered_value"), f"{path}.rendered_value", errors) or ""
            label = _optional_single_line(obj.get("label", ""), f"{path}.label", errors)
            if kind and kind not in _BINDING_KINDS:
                errors.append(_error("binding_mismatch", f"{path}.kind", "锁定要素类型不在公开枚举中", list(_BINDING_KINDS), kind))
            if ref and _RECORD_ID_PATTERN.fullmatch(ref) is None:
                errors.append(_error("binding_not_found", f"{path}.archive_record_ref", "档案引用格式无效", "stable record id", ref))
            key = (kind, ref, rendered_value)
            if key in seen_binding_keys and kind and ref:
                errors.append(_error("binding_mismatch", path, "同一类型、档案引用和渲染值不得重复绑定", "unique kind + archive_record_ref + rendered_value", item))
            seen_binding_keys.add(key)
            occurrences = []
            raw_occurrences = obj.get("occurrences", [])
            if not isinstance(raw_occurrences, list):
                errors.append(_error("invalid_object", f"{path}.occurrences", "正文定位必须是数组", "array[object]", raw_occurrences))
            else:
                for occurrence_index, raw in enumerate(raw_occurrences):
                    occurrence_path = f"{path}.occurrences[{occurrence_index}]"
                    target = _object(raw, occurrence_path, errors)
                    if target is None:
                        continue
                    _exact_keys(target, ("section_id", "text"), occurrence_path, errors)
                    target_section = _single_line(target.get("section_id"), f"{occurrence_path}.section_id", errors)
                    target_text = _single_line(target.get("text"), f"{occurrence_path}.text", errors)
                    if target_section and target_text:
                        occurrences.append({"section_id": target_section, "text": target_text})
            if kind in {"amount", "fact_amount", "date"} and not occurrences:
                errors.append(_error("missing_input", f"{path}.occurrences", "金额和日期必须显式定位到正文原样片段", "non-empty array[{section_id, text}]", raw_occurrences))
            bindings.append({"kind": kind, "archive_record_ref": ref, "rendered_value": rendered_value, "label": label, "occurrences": occurrences})

    placeholders = _declared_placeholders(request.get("placeholders"), errors)
    confirmation_refs = _validate_refs(request.get("confirmation_refs"), "confirmation_refs", errors)
    authority_refs = _validate_refs(request.get("authority_refs"), "authority_refs", errors)
    checklist_items: list[str] = []
    if request.get("additional_checklist_items") is not None:
        value = request.get("additional_checklist_items")
        if not isinstance(value, list):
            errors.append(_error("invalid_text", "additional_checklist_items", "核验事项必须是文本数组", "array[string]", value))
        else:
            for index, item in enumerate(value):
                text = _multiline_text(item, f"additional_checklist_items[{index}]", errors)
                if text is not None:
                    checklist_items.append(text.strip())

    normalized = {
        "case_id": case_id or "",
        "archive_revision": revision,
        "document_type": document_type or "",
        "template_version": template_version or "",
        "mode": mode or "",
        "title": title or "",
        "sections": sections,
        "locked_bindings": bindings,
        "placeholders": placeholders,
        "confirmation_refs": confirmation_refs,
        "authority_refs": authority_refs,
        "additional_checklist_items": checklist_items,
        "delivery_set_id": (
            _single_line(request.get("delivery_set_id"), "delivery_set_id", errors)
            if request.get("delivery_set_id") is not None
            else None
        ),
    }
    declared = {item["text"] for item in placeholders}
    actual_tokens = set().union(
        *(_placeholder_tokens(value) for value in _document_body_values(normalized))
    )
    for token in sorted(actual_tokens - declared):
        errors.append(
            _error(
                "undeclared_placeholder",
                "sections",
                "正文包含未声明的占位符",
                sorted(declared) or ["declare placeholder"],
                token,
            )
        )
    for index, placeholder in enumerate(placeholders):
        if placeholder["text"] not in actual_tokens:
            errors.append(
                _error(
                    "invalid_placeholder",
                    f"placeholders[{index}].text",
                    "占位符必须实际出现在正文区块中",
                    "placeholder text used in sections",
                    placeholder["text"],
                )
            )
    if mode == "external_final":
        pending = sorted(actual_tokens | declared)
        if pending:
            errors.append(
                _error(
                    "placeholder_in_final",
                    "mode",
                    "external_final 不得包含未决占位符",
                    "no placeholders",
                    pending,
                )
            )
    scan_values = [
        value
        if not declared
        else re.sub(
            "|".join(re.escape(item) for item in sorted(declared, key=len, reverse=True)),
            "",
            value,
        )
        for value in _user_text_values(normalized)
    ]
    leaks = _internal_leaks(scan_values)
    if leaks:
        errors.append(
            _error(
                "internal_content_leak",
                "document_content",
                "正文或用户核验内容包含内部路径、协议、记录标识、推理或哈希",
                "user-facing text only",
                leaks,
            )
        )
    return normalized, errors


def _archive_records(markdown: str) -> dict[str, str]:
    matches = list(_RECORD_HEADING_PATTERN.finditer(markdown))
    records: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end]
        next_section = re.search(r"^## [^\r\n]+$", body, re.MULTILINE)
        if next_section is not None:
            body = body[:next_section.start()]
        records[match.group("record_id")] = body.strip()
    return records


def _value_candidates(value: str, label: str) -> tuple[str, ...]:
    candidates = [value.strip()]
    if label and value.startswith(label):
        candidates.append(value[len(label):].lstrip("：: "))
    for part in _EVIDENCE_DELIMITER_PATTERN.split(value):
        part = part.strip().strip("；;,，")
        if part:
            candidates.append(part)
            if "：" in part or ":" in part:
                candidates.append(re.split(r"[：:]", part, maxsplit=1)[1].strip())
    iso_date = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    chinese_date = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value.strip())
    if iso_date is not None:
        year, month, day = iso_date.groups()
        candidates.append(f"{year}年{int(month)}月{int(day)}日")
    if chinese_date is not None:
        year, month, day = chinese_date.groups()
        candidates.append(f"{year}-{int(month):02d}-{int(day):02d}")
    return tuple(dict.fromkeys(item for item in candidates if item))


def _binding_value_matches(body: str, binding: Mapping[str, Any]) -> bool:
    if binding["kind"] == "fact_amount":
        expected = list(_CURRENCY_AMOUNT_PATTERN.finditer(binding["rendered_value"]))
        if len(expected) != 1:
            return False
        return any(
            Decimal(match.group("amount").replace(",", ""))
            == Decimal(expected[0].group("amount").replace(",", ""))
            for match in _CURRENCY_AMOUNT_PATTERN.finditer(body)
        )
    if binding["kind"] == "amount":
        expected = list(_CURRENCY_AMOUNT_PATTERN.finditer(binding["rendered_value"]))
        if len(expected) != 1:
            return False
        # A CAL may also contain basis, duration and intermediate values. Only
        # the explicitly labelled final result is a document amount source.
        results = re.findall(
            r"确定性计算结果\s*[:：]\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:元|圆|人民币)", body
        )
        if len(results) != 1:
            return False
        return Decimal(results[0].replace(",", "")) == Decimal(expected[0].group("amount").replace(",", ""))
    if binding["kind"] == "date":
        expected = list(_DATE_VALUE_PATTERN.finditer(binding["rendered_value"]))
        if len(expected) != 1 or not _valid_date_key(_date_key(expected[0])):
            return False
        return any(
            _date_key(match) == _date_key(expected[0])
            for match in _DATE_VALUE_PATTERN.finditer(body)
        )
    return any(candidate in body for candidate in _value_candidates(binding["rendered_value"], binding["label"]))


def _record_field(body: str, field: str) -> str:
    labels = _CONFIRMATION_FIELD_LABELS.get(field, (field,))
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?m)(?:^|[；;])\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?(?:{label_pattern})(?:\*\*)?\s*[：:]\s*(?P<value>[^\r\n；;。]+)",
        body,
    )
    if match is None:
        return ""
    return match.group("value").strip().rstrip("。")


def _confirmation_revision(body: str) -> int | None:
    value = _record_field(body, "archive_revision")
    return int(value) if value.isdigit() else None


def _confirmation_source_refs(body: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_RECORD_REFERENCE_PATTERN.findall(_record_field(body, "source_refs"))))


def _authority_is_current(body: str) -> bool:
    conclusion = _record_field(body, "authority_conclusion")
    if not conclusion:
        conclusion_match = re.search(
            r"(?:核验结论|结论)\s*[：:]\s*([^\r\n；;。]+)", body
        )
        conclusion = conclusion_match.group(1).strip() if conclusion_match else ""
    return conclusion == "已核验" and not any(
        marker in body for marker in ("过期", "冲突", "受阻", "失败")
    )


def _authority_supports_forced_termination(body: str, *, archive_region: str) -> bool:
    topic = _record_field(body, "authority_topic")
    scope = _record_field(body, "authority_scope")
    region_matches = (
        not archive_region
        or "全国" in scope
        or archive_region in scope
    )
    return _authority_is_current(body) and "解除" in topic and bool(scope) and region_matches


def _archive_region(markdown: str) -> str:
    match = re.search(r"(?m)^- 适用地区：([^\r\n]+)$", markdown)
    return match.group(1).strip() if match is not None else ""


def _binding_scope_term(binding: Mapping[str, str]) -> str:
    value = binding["rendered_value"]
    if binding["kind"] == "evidence":
        return next(
            (
                part.strip()
                for part in _EVIDENCE_DELIMITER_PATTERN.split(value)
                if part.strip()
            ),
            value,
        )
    if "：" in value or ":" in value:
        return re.split(r"[：:]", value, maxsplit=1)[1].strip()
    return value.strip()


def _demand_semantic_errors(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    if request["document_type"] != "employment_obligation_demand_letter":
        return []
    errors: list[dict[str, Any]] = []
    for section_index, section in enumerate(request["sections"]):
        text = section["full_text"]
        for pattern, label in _DEMAND_PROHIBITED_PATTERNS:
            for match in pattern.finditer(text):
                context = text[max(0, match.start() - 16) : match.start()]
                if any(prefix in context for prefix in _DEMAND_NEGATION_PREFIXES):
                    continue
                errors.append(
                    _error(
                        "document_type_conflict",
                        f"sections[{section_index}].full_text",
                        "催告函正文包含解除通知或劳动关系终止语义，不得替代被迫解除通知书",
                        "obligation demand only; no termination-notice semantics",
                        match.group(0) or label,
                    )
                )
    return errors


def _candidate_binding_errors(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (
        request["mode"] != "candidate"
        or request["document_type"] not in _CANDIDATE_BINDING_DOCUMENT_TYPES
    ):
        return []
    counts: dict[str, int] = {}
    for binding in request["locked_bindings"]:
        counts[binding["kind"]] = counts.get(binding["kind"], 0) + 1
    missing = {
        kind: minimum
        for kind, minimum in _FINAL_BINDING_COUNTS[request["document_type"]].items()
        if counts.get(kind, 0) < minimum
    }
    if not missing:
        return []
    declared = {item["text"] for item in request["placeholders"]}
    actual_tokens = set().union(
        *(_placeholder_tokens(value) for value in _document_body_values(request))
    )
    used_placeholders = [
        item
        for item in request["placeholders"]
        if item["text"] in (actual_tokens & declared)
    ]
    if not used_placeholders:
        return [
            _error(
                "missing_input",
                "locked_bindings",
                "candidate 缺少锁定要素时必须在正文中声明并使用占位符",
                {"missing_kinds": missing, "declared_placeholder": True},
                {"missing_kinds": missing, "placeholders": sorted(declared)},
            )
        ]
    placeholder_labels = " ".join(
        f"{item['text']} {item['label']}" for item in used_placeholders
    )
    closing_text = "\n".join(
        section["full_text"]
        for index, section in enumerate(request["sections"])
        if section["heading"] == "落款"
        or (
            request["document_type"] == "employment_obligation_demand_letter"
            and index == len(request["sections"]) - 1
        )
    )
    signature_placeholder = any(
        item["text"] in line
        and re.search(
            r"(?:通知人|申请人|答辩人|签署人|签名|落款)(?:（[^）]{1,20}）)?\s*[：:]\s*$",
            line.split(item["text"], 1)[0],
        )
        for item in used_placeholders
        for line in closing_text.splitlines()
    )
    unsupported = {
        kind: minimum
        for kind, minimum in missing.items()
        if not any(term in placeholder_labels for term in _CANDIDATE_PLACEHOLDER_TERMS.get(kind, ()))
        and not (kind == "signature" and signature_placeholder)
    }
    if not unsupported:
        return []
    return [
        _error(
            "missing_input",
            "placeholders",
            "candidate 的占位符必须对应缺少的锁定要素类型",
            {"missing_kinds": missing, "matching_placeholder": True},
            {"missing_kinds": unsupported, "placeholders": sorted(declared)},
        )
    ]


def _forced_confirmation_errors(
    request: Mapping[str, Any],
    records: Mapping[str, str],
) -> list[dict[str, Any]]:
    if request["document_type"] != "forced_termination_notice" or request["mode"] != "external_final":
        return []

    refs = request["confirmation_refs"]
    if not refs:
        return [
            _error(
                "confirmation_missing",
                "confirmation_refs",
                "被迫解除通知书外发定稿必须引用当前档案中的高风险实质确认",
                "at least one current confirmation record",
                refs,
            )
        ]

    errors: list[dict[str, Any]] = []
    required_fields = (
        "action_type",
        "action_scope",
        "risk_summary",
        "alternatives_presented",
        "user_choice",
        "confirmed_at",
        "archive_revision",
        "source_refs",
    )
    for index, ref in enumerate(refs):
        if not ref.startswith("CONF-"):
            continue
        body = records.get(ref)
        if body is None:
            continue
        path = f"confirmation_refs[{index}]"
        missing = [field for field in required_fields if not _record_field(body, field)]
        if missing:
            errors.append(
                _error(
                    "confirmation_missing",
                    path,
                    "高风险确认记录缺少完成文书最终化所需的字段",
                    list(required_fields),
                    missing,
                )
            )
            continue

        action_type = _record_field(body, "action_type")
        action_scope = _record_field(body, "action_scope")
        action_type_matches = any(
            term.casefold() in action_type.casefold()
            for term in _FORCED_ACTION_TYPE_TERMS
        )
        action_scope_matches = (
            "被迫解除" in action_scope
            and re.search(r"通知|notice", action_scope, re.I) is not None
        )
        action_scope_excludes_employer_action = re.search(
            r"用人单位解除|employer[_ -]?termination", action_scope, re.I
        ) is None
        if not action_type_matches or not action_scope_matches or not action_scope_excludes_employer_action:
            errors.append(
                _error(
                    "confirmation_missing",
                    path,
                    "高风险确认的动作类别或范围与被迫解除通知书不匹配",
                    "confirmation for a forced termination action",
                    {"action_type": action_type, "action_scope": action_scope},
                )
            )

        user_choice = _record_field(body, "user_choice")
        if any(term in user_choice for term in _NEGATIVE_CONFIRMATION_TERMS) or not any(
            term in user_choice for term in _POSITIVE_CONFIRMATION_TERMS
        ):
            errors.append(
                _error(
                    "confirmation_missing",
                    path,
                    "用户选择不是针对当前解除动作的明确同意",
                    "explicit positive choice without refusal or ambiguity",
                    user_choice,
                )
            )

        source_refs = _confirmation_source_refs(body)
        if not source_refs:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.source_refs",
                    "高风险确认记录至少要引用一项当前档案来源",
                    "at least one source archive record ref",
                    _record_field(body, "source_refs"),
                )
            )
        unknown = sorted(set(source_refs) - set(records))
        if unknown:
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.source_refs",
                    "确认记录引用了当前档案中不存在的事实、证据或法源记录",
                    "existing archive record refs",
                    unknown,
                )
            )
        for source_ref in source_refs:
            source_body = records.get(source_ref, "")
            if any(pattern.search(source_body) for pattern in _CONFLICT_PATTERNS):
                errors.append(
                    _error(
                        "fact_conflict",
                        f"{path}.source_refs",
                        "高风险确认引用的档案记录存在事实冲突",
                        "non-conflicting archive records",
                        source_ref,
                    )
                )
        required_source_refs = {
            binding["archive_record_ref"]
            for binding in request["locked_bindings"]
            if binding["kind"] in {"party", "claim", "evidence", "date"}
        }
        required_source_refs.update(request["authority_refs"])
        missing_source_refs = sorted(required_source_refs - set(source_refs))
        if missing_source_refs:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.source_refs",
                    "高风险确认来源未覆盖本次通知的主体、理由、证据、日期或现行依据",
                    sorted(required_source_refs),
                    missing_source_refs,
                )
            )
        missing_scope_terms = sorted(
            {
                term
                for binding in request["locked_bindings"]
                if binding["kind"] in {"party", "claim", "evidence", "date"}
                for term in (_binding_scope_term(binding),)
                if term and term not in action_scope
            }
        )
        if missing_scope_terms:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.action_scope",
                    "高风险确认范围未覆盖本次通知的主体、理由、证据或日期",
                    "current forced-notice binding terms included in action_scope",
                    missing_scope_terms,
                )
            )
    return errors


_PROGRAM_ACTION_TYPE_TERMS = {
    "company_deregistration_restriction_request_shanghai": (
        "company_deregistration_restriction",
        "限制公司注销",
        "注销风险",
    ),
    "property_preservation_application": (
        "property_preservation",
        "财产保全",
    ),
    "enforcement_application": (
        "enforcement",
        "强制执行",
    ),
}
_PROGRAM_ACTION_SCOPE_TERMS = {
    "company_deregistration_restriction_request_shanghai": ("限制公司注销", "企业注销"),
    "property_preservation_application": ("财产保全",),
    "enforcement_application": ("强制执行",),
}
_PROGRAM_AUTHORITY_TOPIC_TERMS = {
    "company_deregistration_restriction_request_shanghai": ("注销", "企业"),
    "property_preservation_application": ("保全",),
    "enforcement_application": ("执行",),
}


def _program_confirmation_errors(
    request: Mapping[str, Any],
    records: Mapping[str, str],
) -> list[dict[str, Any]]:
    document_type = request["document_type"]
    if document_type not in _PROGRAM_DOCUMENT_TYPES or request["mode"] != "external_final":
        return []

    refs = request["confirmation_refs"]
    if not refs:
        return [
            _error(
                "confirmation_missing",
                "confirmation_refs",
                "高风险程序文书外发定稿必须引用当前档案中的实质确认",
                "at least one current confirmation record",
                refs,
            )
        ]

    errors: list[dict[str, Any]] = []
    required_fields = (
        "action_type",
        "action_scope",
        "risk_summary",
        "alternatives_presented",
        "user_choice",
        "confirmed_at",
        "archive_revision",
        "source_refs",
    )
    for index, ref in enumerate(refs):
        body = records.get(ref)
        if body is None:
            continue
        path = f"confirmation_refs[{index}]"
        missing = [field for field in required_fields if not _record_field(body, field)]
        if missing:
            errors.append(
                _error(
                    "confirmation_missing",
                    path,
                    "高风险确认记录缺少完成文书最终化所需的字段",
                    list(required_fields),
                    missing,
                )
            )
            continue

        action_type = _record_field(body, "action_type")
        action_scope = _record_field(body, "action_scope")
        if not any(term.casefold() in action_type.casefold() for term in _PROGRAM_ACTION_TYPE_TERMS[document_type]):
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.action_type",
                    "高风险确认的动作类别与当前程序文书不匹配",
                    f"confirmation for {document_type}",
                    action_type,
                )
            )
        if not any(term in action_scope for term in _PROGRAM_ACTION_SCOPE_TERMS[document_type]):
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.action_scope",
                    "高风险确认的动作范围与当前程序文书不匹配",
                    _PROGRAM_ACTION_SCOPE_TERMS[document_type],
                    action_scope,
                )
            )

        user_choice = _record_field(body, "user_choice")
        if any(term in user_choice for term in _NEGATIVE_CONFIRMATION_TERMS) or not any(
            term in user_choice for term in _POSITIVE_CONFIRMATION_TERMS
        ):
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.user_choice",
                    "用户选择不是针对当前程序动作的明确同意",
                    "explicit positive choice without refusal or ambiguity",
                    user_choice,
                )
            )

        source_refs = _confirmation_source_refs(body)
        if not source_refs:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.source_refs",
                    "高风险确认记录至少要引用一项当前档案来源",
                    "at least one source archive record ref",
                    _record_field(body, "source_refs"),
                )
            )
        unknown = sorted(set(source_refs) - set(records))
        if unknown:
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.source_refs",
                    "确认记录引用了当前档案中不存在的事实、证据或法源记录",
                    "existing archive record refs",
                    unknown,
                )
            )
        for source_ref in source_refs:
            source_body = records.get(source_ref, "")
            if any(pattern.search(source_body) for pattern in _CONFLICT_PATTERNS):
                errors.append(
                    _error(
                        "fact_conflict",
                        f"{path}.source_refs",
                        "高风险确认引用的档案记录存在事实冲突",
                        "non-conflicting archive records",
                        source_ref,
                    )
                )
        required_source_refs = {
            binding["archive_record_ref"]
            for binding in request["locked_bindings"]
            if binding["kind"] in {"party", "claim", "evidence", "amount", "date", "attachment"}
        }
        required_source_refs.update(request["authority_refs"])
        missing_source_refs = sorted(required_source_refs - set(source_refs))
        if missing_source_refs:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.source_refs",
                    "高风险确认来源未覆盖本次程序文书的主体、主张、金额、证据、日期或现行依据",
                    sorted(required_source_refs),
                    missing_source_refs,
                )
            )
        missing_scope_terms = sorted(
            {
                term
                for binding in request["locked_bindings"]
                if binding["kind"] in {"party", "claim", "evidence", "amount", "date", "attachment"}
                for term in (_binding_scope_term(binding),)
                if term and term not in action_scope
            }
        )
        if missing_scope_terms:
            errors.append(
                _error(
                    "confirmation_missing",
                    f"{path}.action_scope",
                    "高风险确认范围未覆盖当前程序文书的锁定要素",
                    "current program-document binding terms included in action_scope",
                    missing_scope_terms,
                )
            )
    return errors


def _authority_supports_document(
    body: str,
    *,
    document_type: str,
    archive_region: str,
) -> bool:
    if not _authority_is_current(body):
        return False
    topic = _record_field(body, "authority_topic")
    scope = _record_field(body, "authority_scope")
    if not topic or not scope:
        return False
    if not any(term in topic for term in _PROGRAM_AUTHORITY_TOPIC_TERMS[document_type]):
        return False
    if document_type == "company_deregistration_restriction_request_shanghai":
        return "上海" in scope and (not archive_region or "上海" in archive_region)
    return "全国" in scope or not archive_region or archive_region in scope


_DATE_VALUE_PATTERN = re.compile(r"(?<![0-9])(?:([0-9]{4})[ \t]*年[ \t]*([0-9]{1,2})[ \t]*月[ \t]*([0-9]{1,2})[ \t]*日|([0-9]{4})-([0-9]{2})-([0-9]{2}))(?![0-9])")


def _date_key(match: Any) -> tuple[int, int, int]:
    return tuple(int(value) for value in (match.groups()[:3] if match.group(1) else match.groups()[3:]))


def _valid_date_key(value: tuple[int, int, int]) -> bool:
    try:
        date(*value)
    except (TypeError, ValueError):
        return False
    return True


def _normalized_signature_text(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).replace(":", "：")
    if normalized.count("：") != 1:
        return ""
    role, name = normalized.split("：", 1)
    if not role or not name:
        return ""
    return f"{role}：{name}"


def _section_binding_errors(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Bind every amount, date and closing-signature occurrence to its source."""
    errors: list[dict[str, Any]] = []
    sections = {section["section_id"]: section for section in request["sections"]}
    covered_amounts: dict[str, set[tuple[int, int]]] = {
        key: set() for key in sections
    }
    covered_dates: set[tuple[str, int, int]] = set()
    closing_ids = {section["section_id"] for section in request["sections"] if section["heading"] == "落款"}
    if request["document_type"] == "employment_obligation_demand_letter" and request["sections"]:
        closing_ids.add(request["sections"][-1]["section_id"])
    bound_closing_dates: set[tuple[str, int, int]] = set()
    bound_signatures: set[tuple[str, str]] = set()
    for index, binding in enumerate(request["locked_bindings"]):
        kind = binding["kind"]
        if kind == "signature":
            occurrences = binding.get("occurrences", [])
            if request["mode"] == "external_final" and not occurrences:
                errors.append(
                    _error(
                        "missing_input",
                        f"locked_bindings[{index}].occurrences",
                        "外发定稿签署人必须定位到完整落款角色与姓名",
                        "non-empty closing signature occurrence list",
                        occurrences,
                    )
                )
            expected_signature = _normalized_signature_text(
                binding["rendered_value"]
            )
            if not expected_signature:
                errors.append(
                    _error(
                        "binding_mismatch",
                        f"locked_bindings[{index}].rendered_value",
                        "签署绑定必须包含完整角色和姓名",
                        "role：name",
                        binding["rendered_value"],
                    )
                )
                continue
            for target_index, target in enumerate(occurrences):
                path = f"locked_bindings[{index}].occurrences[{target_index}]"
                section = sections.get(target["section_id"])
                text = target["text"]
                normalized_text = re.sub(r"\s+", "", text).replace(":", "：")
                occurrence_key = (target["section_id"], text)
                if (
                    section is None
                    or target["section_id"] not in closing_ids
                    or section["full_text"].count(text) != 1
                    or normalized_text.count(expected_signature) != 1
                    or occurrence_key in bound_signatures
                ):
                    errors.append(
                        _error(
                            "binding_mismatch",
                            path,
                            "签署 occurrence 必须唯一定位到落款中的完整角色与姓名",
                            expected_signature,
                            target,
                        )
                    )
                    continue
                bound_signatures.add(occurrence_key)
            continue
        if kind not in {"amount", "fact_amount", "date"}:
            continue
        is_amount = kind in {"amount", "fact_amount"}
        pattern = _CURRENCY_AMOUNT_PATTERN if is_amount else _DATE_VALUE_PATTERN
        expected_matches = list(pattern.finditer(binding["rendered_value"]))
        if len(expected_matches) != 1:
            errors.append(_error("binding_mismatch", f"locked_bindings[{index}].rendered_value", "锁定值必须包含一个完整金额或日期", "one explicit numeric value", binding["rendered_value"]))
            continue
        key = (lambda match: Decimal(match.group("amount").replace(",", ""))) if is_amount else _date_key
        expected = key(expected_matches[0])
        if kind == "date" and not _valid_date_key(expected):
            errors.append(_error("binding_mismatch", f"locked_bindings[{index}].rendered_value", "锁定日期不是有效日历日期", "valid calendar date", binding["rendered_value"]))
            continue
        for target_index, target in enumerate(binding.get("occurrences", [])):
            path = f"locked_bindings[{index}].occurrences[{target_index}]"
            section = sections.get(target["section_id"])
            text = target["text"]
            if kind == "fact_amount" and (
                request["mode"] != "candidate"
                or section is None
                or section["heading"] not in _FACT_AMOUNT_HEADINGS.get(request["document_type"], frozenset())
            ):
                errors.append(_error("binding_mismatch", path,
                                     "事实金额只可用于候选文书的事实说明区块，不能替代请求或结算金额计算",
                                     "candidate fact section", target))
                continue
            matches = list(pattern.finditer(text))
            if (
                section is None
                or section["full_text"].count(text) != 1
                or len(matches) != 1
                or (kind == "date" and not _valid_date_key(key(matches[0])))
                or key(matches[0]) != expected
            ):
                errors.append(_error("binding_mismatch", path, "正文定位缺失、不唯一或与锁定值不一致", "unique exact excerpt containing the locked value", target))
                continue
            offset = section["full_text"].index(text)
            span = (offset + matches[0].start(), offset + matches[0].end())
            # Matching a substring such as 6000 inside 16000 is never a binding.
            full_matches = {match.span() for match in pattern.finditer(section["full_text"])}
            if (
                span not in full_matches
                or (
                    is_amount
                    and span in covered_amounts[target["section_id"]]
                )
                or (kind == "date" and (target["section_id"], *span) in covered_dates)
            ):
                errors.append(_error("binding_mismatch", path, "正文定位必须覆盖完整且唯一归属的数值", "complete numeric token", target))
                continue
            if is_amount:
                covered_amounts[target["section_id"]].add(span)
            else:
                qualified_span = (target["section_id"], *span)
                covered_dates.add(qualified_span)
                if target["section_id"] in closing_ids:
                    bound_closing_dates.add(qualified_span)
    for index, section in enumerate(request["sections"]):
        for match in _CURRENCY_AMOUNT_PATTERN.finditer(section["full_text"]):
            if match.span() not in covered_amounts[section["section_id"]]:
                errors.append(_error("binding_mismatch", f"sections[{index}].full_text", "正文金额缺少对应的计算或候选事实绑定", "explicit amount occurrence", match.group(0)))

    if request["mode"] == "external_final":
        for index, section in enumerate(request["sections"]):
            section_id = section["section_id"]
            for match in _DATE_VALUE_PATTERN.finditer(section["full_text"]):
                span = (section_id, *match.span())
                if not _valid_date_key(_date_key(match)):
                    errors.append(
                        _error(
                            "binding_mismatch",
                            f"sections[{index}].full_text",
                            "正文完整日期不是有效日历日期",
                            "valid bound calendar date",
                            match.group(0),
                        )
                    )
                elif span not in covered_dates:
                    errors.append(
                        _error(
                            "binding_mismatch",
                            f"sections[{index}].full_text",
                            "正文完整日期 occurrence 缺少对应的档案绑定",
                            "explicit date occurrence bound to a source record",
                            match.group(0),
                        )
                    )

    if (
        request["mode"] == "external_final"
        and request["document_type"] != "evidence_catalog"
    ):
        closing = "\n".join(sections[key]["full_text"] for key in closing_ids)
        closing_date_spans: set[tuple[str, int, int]] = set()
        invalid_or_unbound_date = False
        for section_id in closing_ids:
            section_text = sections[section_id]["full_text"]
            for match in _DATE_VALUE_PATTERN.finditer(section_text):
                span = (section_id, *match.span())
                closing_date_spans.add(span)
                if not _valid_date_key(_date_key(match)) or span not in bound_closing_dates:
                    invalid_or_unbound_date = True
        if not closing_date_spans or invalid_or_unbound_date:
            errors.append(_error("binding_mismatch", "sections", "外发定稿落款日期缺失或未与档案绑定", "bound closing date", closing))
    return errors


def _section_labelled_binding_errors(
    request: Mapping[str, Any],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for binding in request["locked_bindings"]:
        if binding["kind"] not in {"party", "signature"}:
            continue
        rendered_value = binding["rendered_value"]
        delimiter = "：" if "：" in rendered_value else ":" if ":" in rendered_value else ""
        if not delimiter:
            continue
        label, expected = rendered_value.split(delimiter, 1)
        label = label.strip()
        expected = expected.strip().strip("，,；;。！？.!?")
        if not label or not expected:
            continue
        pattern = re.compile(
            rf"(?<![\u4e00-\u9fff]){re.escape(label)}\s*[:：]\s*([^，,；;。！？.!?\r\n]+)"
        )
        for section_index, section in enumerate(request["sections"]):
            for match in pattern.finditer(section["full_text"]):
                actual = match.group(1).strip()
                if actual and not actual.startswith(expected):
                    errors.append(
                        _error(
                            "binding_mismatch",
                            f"sections[{section_index}].full_text",
                            "正文主体或落款与锁定值不一致",
                            binding["rendered_value"],
                            match.group(0),
                        )
                    )
    return errors


def _section_evidence_errors(
    request: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if request["document_type"] != "evidence_catalog":
        return []
    if len(request["sections"]) < 2:
        return []  # Structural validation already supplies missing_section.
    rows = _parse_evidence_rows(request["sections"][1]["full_text"])
    if not rows:
        return []
    errors: list[dict[str, Any]] = []
    for binding_index, binding in enumerate(request["locked_bindings"]):
        if binding["kind"] != "evidence":
            continue
        expected = tuple(
            part.strip()
            for part in _EVIDENCE_DELIMITER_PATTERN.split(binding["rendered_value"])
            if part.strip()
        )
        if len(expected) < 3:
            continue
        for row in rows:
            observed = row if len(expected) >= 4 else row[1:4]
            if len(observed) >= len(expected) and all(
                expected[index] in observed[index]
                for index in range(len(expected))
            ):
                break
        else:
            errors.append(
                _error(
                    "binding_mismatch",
                    f"locked_bindings[{binding_index}].rendered_value",
                    "证据表格与锁定证据不一致",
                    "locked evidence represented in evidence table",
                    binding["rendered_value"],
                )
            )
    return errors


def _binding_errors(
    request: Mapping[str, Any],
    records: Mapping[str, str],
    archive_revision: int,
    archive_markdown: str,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    bindings = request["locked_bindings"]
    archive_region = _archive_region(archive_markdown)
    for index, binding in enumerate(bindings):
        path = f"locked_bindings[{index}]"
        ref = binding["archive_record_ref"]
        body = records.get(ref)
        if body is None:
            errors.append(
                _error(
                    "binding_not_found",
                    f"{path}.archive_record_ref",
                    "锁定要素引用的档案记录不存在",
                    "existing archive record",
                    ref,
                )
            )
            continue
        if any(pattern.search(body) for pattern in _CONFLICT_PATTERNS):
            errors.append(
                _error(
                    "fact_conflict",
                    f"{path}.archive_record_ref",
                    "锁定要素引用的档案记录存在事实冲突",
                    "non-conflicting archive record",
                    ref,
                )
            )
        if request["mode"] == "external_final" and any(
            marker in body for marker in ("事实状态：分析假设", "未确认事实")
        ):
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.archive_record_ref",
                    "外发定稿不得把分析假设或未确认事实作为锁定要素",
                    "confirmed fact, claim, evidence, calculation or authority record",
                    ref,
                )
            )
        kind = binding["kind"]
        prefix = {"amount": "CAL", "fact_amount": "F", "claim": "CL", "evidence": "E", "attachment": "E"}.get(kind)
        if prefix is not None and not ref.startswith(f"{prefix}-"):
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.archive_record_ref",
                    f"{kind} 锁定要素必须引用对应类型的档案记录",
                    f"{prefix}- record",
                    ref,
                )
            )
        if kind == "document_heading" and binding["rendered_value"] != request["title"]:
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.rendered_value",
                    "标题锁定值必须与请求标题一致",
                    request["title"],
                    binding["rendered_value"],
                )
            )
        if kind == "amount" and not _binding_value_matches(body, binding):
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.rendered_value",
                    "金额渲染值与确定性计算记录不一致",
                    "value present in referenced CAL record",
                    binding["rendered_value"],
                )
            )
        elif kind != "document_heading" and not _binding_value_matches(body, binding):
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.rendered_value",
                    "锁定渲染值与所引用档案记录不一致",
                    "value present in referenced archive record",
                    binding["rendered_value"],
                )
            )
        elif kind == "document_heading" and request["title"] not in body:
            errors.append(
                _error(
                    "binding_mismatch",
                    f"{path}.rendered_value",
                    "文书标题绑定未在档案记录中得到支持",
                    request["title"],
                    binding["rendered_value"],
                )
            )
    errors.extend(_section_binding_errors(request))
    errors.extend(_section_labelled_binding_errors(request))
    errors.extend(_section_evidence_errors(request))
    for ref_path in ("confirmation_refs", "authority_refs"):
        for index, ref in enumerate(request[ref_path]):
            body = records.get(ref)
            if body is None:
                errors.append(
                    _error(
                        "confirmation_stale" if ref_path == "confirmation_refs" else "authority_stale",
                        f"{ref_path}[{index}]",
                        "引用的确认或现行依据记录不存在或已失效",
                        "existing archive record",
                        ref,
                    )
                )
                continue
            if ref_path == "confirmation_refs" and ref.startswith("CONF-"):
                bound_revision = _confirmation_revision(body)
                if bound_revision is None:
                    errors.append(
                        _error(
                            "confirmation_missing",
                            f"{ref_path}[{index}]",
                            "确认记录缺少绑定的档案修订版本",
                            archive_revision,
                            ref,
                        )
                    )
                elif bound_revision != archive_revision:
                    errors.append(
                        _error(
                            "confirmation_stale",
                            f"{ref_path}[{index}]",
                            "确认记录绑定的档案修订版本已过期",
                            archive_revision,
                            bound_revision,
                        )
                    )
            if ref_path == "authority_refs" and ref.startswith("AUTH-"):
                authority_current = (
                    _authority_supports_forced_termination(
                        body,
                        archive_region=archive_region,
                    )
                    if request["document_type"] == "forced_termination_notice"
                    else _authority_supports_document(
                        body,
                        document_type=request["document_type"],
                        archive_region=archive_region,
                    )
                    if request["document_type"] in _PROGRAM_DOCUMENT_TYPES
                    else _authority_is_current(body)
                )
                if not authority_current:
                    errors.append(
                        _error(
                            "authority_stale",
                            f"{ref_path}[{index}]",
                            "现行依据记录不是已核验状态，不能支撑当前引用",
                            "核验结论：已核验",
                            ref,
                        )
                    )
    errors.extend(_forced_confirmation_errors(request, records))
    errors.extend(_program_confirmation_errors(request, records))
    errors.extend(_demand_semantic_errors(request))
    errors.extend(_candidate_binding_errors(request))
    if any(
        _CURRENCY_AMOUNT_PATTERN.search(section["full_text"])
        for section in request["sections"]
    ) and not any(binding["kind"] in {"amount", "fact_amount"} for binding in bindings):
        errors.append(
            _error(
                "missing_input",
                "locked_bindings",
                "正文包含金额时必须提供计算或候选事实金额锁定要素",
                "at least one amount binding for every rendered currency amount",
                bindings,
            )
        )
    if request["mode"] == "external_final":
        counts: dict[str, int] = {}
        for binding in bindings:
            counts[binding["kind"]] = counts.get(binding["kind"], 0) + 1
        for kind, minimum in _FINAL_BINDING_COUNTS[request["document_type"]].items():
            if counts.get(kind, 0) < minimum:
                errors.append(
                    _error(
                        "missing_input",
                        "locked_bindings",
                        "external_final 缺少必要锁定要素",
                        {"kind": kind, "minimum": minimum},
                        counts.get(kind, 0),
                    )
                )
        if request["document_type"] in _HIGH_RISK_DOCUMENT_TYPES and not request["authority_refs"]:
            errors.append(
                _error(
                    "authority_required",
                    "authority_refs",
                    "高风险程序文书外发定稿必须引用当前适用的现行规则核验记录",
                    "at least one current authority record",
                    request["authority_refs"],
                )
            )
    return errors


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_digest(request: Mapping[str, Any], archive_markdown: str) -> str:
    payload = {
        "case_id": request["case_id"],
        "archive_revision": request["archive_revision"],
        "document_type": request["document_type"],
        "template_version": request["template_version"],
        "mode": request["mode"],
        "title": request["title"],
        "sections": request["sections"],
        "locked_bindings": request["locked_bindings"],
        "placeholders": request["placeholders"],
        "confirmation_refs": request["confirmation_refs"],
        "authority_refs": request["authority_refs"],
        "additional_checklist_items": request["additional_checklist_items"],
        "delivery_set_id": request.get("delivery_set_id"),
        "archive_sha256": sha256(archive_markdown.encode("utf-8")).hexdigest(),
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _output_dir(workspace_root: Path, case_id: str, document_type: str) -> Path:
    return workspace_root / ".arbibuddy" / "cases" / case_id / "output" / document_type


def _document_filename(title: str, mode: str) -> str:
    return f"《{title}》.docx" if mode == "external_final" else f"《{title}》（候选草稿）.docx"


def _checklist_filename(title: str) -> str:
    return f"《{title}》内部核验清单（仅供核对，请勿外发）.txt"


def _manifest_filename(title: str) -> str:
    return f"《{title}》机器交付清单.json"


def _delivery_set_path(workspace_root: Path, case_id: str) -> Path:
    return workspace_root / ".arbibuddy" / "cases" / case_id / DELIVERY_SET_FILENAME


def _safe_delivery_set_path(workspace_root: Path, case_id: str) -> Path:
    """返回位于 workspace 内的 delivery-set 路径，拒绝 symlink/junction 越界。"""

    root = workspace_root.resolve()
    path = _delivery_set_path(workspace_root, case_id)
    try:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_relative_to(root):
            raise ValueError("document.delivery-set-v1 控制对象路径越出 workspace")
    except (OSError, RuntimeError) as error:
        raise ValueError("document.delivery-set-v1 控制对象路径无效") from error
    return path


def _delivery_set_success(
    result: dict[str, Any],
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": DELIVERY_SET_CONTRACT_VERSION,
        "operation": DELIVERY_SET_OPERATION,
        "ok": True,
        "result": result,
        "errors": [],
        "warnings": warnings or [],
    }


def _delivery_set_failure(
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract_version": DELIVERY_SET_CONTRACT_VERSION,
        "operation": DELIVERY_SET_OPERATION,
        "ok": False,
        "errors": errors,
        "warnings": [],
    }


def _delivery_set_error(
    code: str,
    path: str,
    message: str,
    expected: Any,
    received: Any,
    *,
    recoverable: bool = True,
) -> dict[str, Any]:
    return _error(code, path, message, expected, received, recoverable=recoverable)


def _delivery_set_expected_types(
    requested: object,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(requested, list) or not requested:
        return [], [], [
            _delivery_set_error(
                "invalid_requested_templates",
                "requested_templates",
                "delivery set 必须声明至少一个公开模板或文书类型",
                "non-empty array[string]",
                requested,
            )
        ]
    normalized_requested: list[str] = []
    expected: list[str] = []
    for index, item in enumerate(requested):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                _delivery_set_error(
                    "invalid_requested_templates",
                    f"requested_templates[{index}]",
                    "公开模板标识必须是非空字符串",
                    "template id or document type",
                    item,
                )
            )
            continue
        value = item.strip()
        document_type = _TEMPLATE_TO_DOCUMENT_TYPE.get(value, value)
        if document_type not in DOCUMENT_TYPES:
            errors.append(
                _delivery_set_error(
                    "unsupported_document_type",
                    f"requested_templates[{index}]",
                    "请求的文书类型不在公开注册表中",
                    list(DOCUMENT_TYPES),
                    value,
                )
            )
            continue
        normalized_requested.append(value)
        for member in _DELIVERY_SET_BUNDLES.get(document_type, (document_type,)):
            if member not in expected:
                expected.append(member)
    return normalized_requested, expected, errors


def _validate_delivery_set_object(
    value: object,
    *,
    case_id: str | None = None,
    archive_revision: int | None = None,
    archive_markdown: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("document.delivery-set-v1 控制对象必须是对象")
    required = {
        "schema_version",
        "contract_version",
        "operation",
        "delivery_set_id",
        "case_id",
        "archive_revision",
        "mode",
        "requested_templates",
        "expected_document_types",
        "confirmation_refs",
        "delivery_set_digest",
    }
    if set(value) not in (required, required | {"archive_dependencies"}):
        raise ValueError("document.delivery-set-v1 控制对象字段集合无效")
    if value.get("schema_version") != 1 or value.get("contract_version") != DELIVERY_SET_CONTRACT_VERSION or value.get("operation") != DELIVERY_SET_OPERATION:
        raise ValueError("document.delivery-set-v1 控制对象契约无效")
    delivery_set_id = value.get("delivery_set_id")
    if not isinstance(delivery_set_id, str) or not re.fullmatch(r"[0-9a-f]{32}", delivery_set_id):
        raise ValueError("document.delivery-set-v1 delivery_set_id 无效")
    object_case_id = value.get("case_id")
    if not isinstance(object_case_id, str) or re.fullmatch(r"case-[0-9a-f]{24}", object_case_id) is None:
        raise ValueError("document.delivery-set-v1 case_id 无效")
    if case_id is not None and object_case_id != case_id:
        raise ValueError("document.delivery-set-v1 案件编号不一致")
    revision = value.get("archive_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("document.delivery-set-v1 archive_revision 无效")
    if "archive_dependencies" in value:
        validate_dependency_shape(value["archive_dependencies"])
    if archive_revision is not None:
        validate_archive_freshness(value, revision=archive_revision, markdown=archive_markdown)
    if value.get("mode") not in MODES:
        raise ValueError("document.delivery-set-v1 mode 无效")
    requested = value.get("requested_templates")
    expected = value.get("expected_document_types")
    refs = value.get("confirmation_refs")
    if not isinstance(requested, list) or not all(isinstance(item, str) and item for item in requested):
        raise ValueError("document.delivery-set-v1 requested_templates 无效")
    if not isinstance(expected, list) or not expected or not all(item in DOCUMENT_TYPES for item in expected):
        raise ValueError("document.delivery-set-v1 expected_document_types 无效")
    if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
        raise ValueError("document.delivery-set-v1 confirmation_refs 无效")
    normalized_requested, canonical_expected, request_errors = _delivery_set_expected_types(
        requested
    )
    if request_errors or normalized_requested != requested or canonical_expected != expected:
        raise ValueError("document.delivery-set-v1 文书集合未按公开模板规范化")
    digest = value.get("delivery_set_digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("document.delivery-set-v1 摘要无效")
    digest_payload = {
        key: value[key]
        for key in (
            "schema_version",
            "contract_version",
            "operation",
            "case_id",
            "archive_revision",
            "mode",
            "requested_templates",
            "expected_document_types",
            "confirmation_refs",
        )
    }
    if "archive_dependencies" in value:
        digest_payload["archive_dependencies"] = value["archive_dependencies"]
    expected_digest = sha256(_canonical_json(digest_payload).encode("utf-8")).hexdigest()
    if digest != expected_digest:
        raise ValueError("document.delivery-set-v1 摘要不一致")
    return dict(value)


def load_delivery_set(
    workspace_root: str | Path,
    case_id: str,
    *,
    delivery_set_id: str | None = None,
    archive_revision: int | None = None,
    archive_markdown: str | None = None,
) -> dict[str, Any]:
    """读取并校验当前案件的 delivery-set 控制对象。"""

    root = absolute_path_for_io(workspace_root)
    path = _safe_delivery_set_path(root, case_id)
    case_dir = path.parent
    try:
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(case_dir.resolve())
            or not path.is_file()
        ):
            raise ValueError("document.delivery-set-v1 控制对象路径无效")
    except (OSError, RuntimeError) as error:
        raise ValueError("document.delivery-set-v1 控制对象路径无效") from error
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("document.delivery-set-v1 控制对象不可读取") from error
    result = _validate_delivery_set_object(
        value,
        case_id=case_id,
        archive_revision=archive_revision,
        archive_markdown=archive_markdown,
    )
    if delivery_set_id is not None and result["delivery_set_id"] != delivery_set_id:
        raise ValueError("document.delivery-set-v1 delivery_set_id 不一致")
    return result


def create_delivery_set(
    request: Mapping[str, Any],
    workspace_root: str | Path,
) -> dict[str, Any]:
    """创建并绑定一次完整的模型主导文书集合。

    该控制对象只记录交付计划和渲染绑定，不复制案情正文，也不替模型决定
    法律主张。仲裁申请模板的证据目录配套文书由代码规范化加入集合。
    """

    if not isinstance(request, Mapping):
        return _delivery_set_failure([
            _delivery_set_error("invalid_object", "request", "delivery set 请求必须是对象", "object", request)
        ])
    allowed = {
        "case_id",
        "archive_revision",
        "mode",
        "requested_templates",
        "requested_document_types",
        "confirmation_refs",
    }
    unknown = sorted(set(request) - allowed)
    if unknown:
        return _delivery_set_failure([
            _delivery_set_error("invalid_object", "request", "delivery set 请求包含未知字段", sorted(allowed), unknown)
        ])
    case_id = request.get("case_id")
    revision = request.get("archive_revision")
    mode = request.get("mode")
    if not isinstance(case_id, str) or re.fullmatch(r"case-[0-9a-f]{24}", case_id) is None:
        return _delivery_set_failure([
            _delivery_set_error("invalid_case_id", "case_id", "案件编号必须是模型主导案件编号", "case-<24 hex>", case_id)
        ])
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return _delivery_set_failure([
            _delivery_set_error("invalid_revision", "archive_revision", "档案修订版本必须是非负整数", "integer >= 0", revision)
        ])
    if mode not in MODES:
        return _delivery_set_failure([
            _delivery_set_error("invalid_mode", "mode", "文书成熟度不在公开枚举中", list(MODES), mode)
        ])
    requested_templates = request.get("requested_templates")
    requested_document_types = request.get("requested_document_types")
    if requested_templates is not None and requested_document_types is not None:
        return _delivery_set_failure([
            _delivery_set_error(
                "invalid_requested_templates",
                "requested_templates",
                "不得同时提供 requested_templates 和 requested_document_types",
                "one request field",
                "both present",
            )
        ])
    requested = requested_templates if requested_templates is not None else requested_document_types
    normalized_requested, expected_types, errors = _delivery_set_expected_types(requested)
    refs = request.get("confirmation_refs", [])
    if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
        errors.append(
            _delivery_set_error(
                "invalid_confirmation_refs",
                "confirmation_refs",
                "确认引用必须是字符串数组",
                "array[string]",
                refs,
            )
        )
    if errors:
        return _delivery_set_failure(errors)
    root = absolute_path_for_io(workspace_root)
    archive_result = CaseArchive(root).read(case_id)
    if not archive_result.get("ok"):
        return _delivery_set_failure(
            [
                _delivery_set_error(
                    "archive_unavailable",
                    "case_id",
                    "创建 delivery set 前无法读取当前案件档案",
                    "readable case archive",
                    case_id,
                )
            ]
        )
    actual_revision = archive_result.get("result", {}).get("revision")
    if actual_revision != revision:
        return _delivery_set_failure([
            _delivery_set_error(
                "archive_revision_conflict",
                "archive_revision",
                "创建 delivery set 所依据的档案修订版本已过期",
                actual_revision,
                revision,
            )
        ])
    if "arbitration_application" in expected_types:
        archive_markdown = archive_result.get("result", {}).get("markdown", "")
        records = _archive_records(archive_markdown)
        confirmation_bodies = [
            body for record_id, body in records.items()
            if record_id.startswith("CONF-")
        ]
        confirmations = [
            body for body in confirmation_bodies
            if (bound_revision := _confirmation_revision(body)) is not None
            and bound_revision <= revision
        ]
        if not confirmations:
            if confirmation_bodies and all(
                _confirmation_revision(body) is None for body in confirmation_bodies
            ):
                return _delivery_set_failure([
                    _delivery_set_error(
                        "confirmation_revision_unreadable",
                        "archive.confirmation.archive_revision",
                        "档案已有确认记录，但绑定修订版本无法读取；确认记录须单独写入“绑定档案修订版本：<提交后的版本号>”",
                        "绑定档案修订版本：<非负整数>",
                        "unreadable",
                    )
                ])
            return _delivery_set_failure([
                _delivery_set_error(
                    "unified_confirmation_required",
                    "archive.confirmation",
                    "准备仲裁申请书前须先完成当前案情摘要的自然语言确认并存档；仅要求按已知信息继续不构成确认",
                    "archive confirmation bound to this case revision or earlier",
                    "missing",
                )
            ])
    payload = {
        "schema_version": 1,
        "contract_version": DELIVERY_SET_CONTRACT_VERSION,
        "operation": DELIVERY_SET_OPERATION,
        "case_id": case_id,
        "archive_revision": revision,
        "mode": mode,
        "requested_templates": normalized_requested,
        "expected_document_types": expected_types,
        "confirmation_refs": list(refs),
    }
    if mode == "candidate":
        payload["archive_dependencies"] = candidate_dependencies(
            archive_result["result"]["markdown"]
        )
    digest = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    control = {
        **payload,
        "delivery_set_id": secrets.token_hex(16),
        "delivery_set_digest": digest,
    }
    # delivery_set_id is deliberately not part of the stable digest.  The
    # control file is replaced only as a whole so managed view never sees a
    # partially written plan.
    try:
        path = _safe_delivery_set_path(root, case_id)
    except ValueError as error:
        return _delivery_set_failure([
            _delivery_set_error(
                "invalid_delivery_set_path",
                "delivery_set",
                "delivery set 控制对象路径必须位于当前 workspace 内且不得是链接",
                "workspace-local regular file path",
                str(error),
            )
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{control['delivery_set_id']}.tmp")
    try:
        temporary.write_text(
            json.dumps(control, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    except (OSError, UnicodeError) as error:
        try:
            temporary.unlink()
        except OSError:
            pass
        return _delivery_set_failure([
            _delivery_set_error(
                "io_failure",
                "delivery_set",
                "delivery set 控制对象写入失败",
                "atomic writable control file",
                error.__class__.__name__,
                recoverable=False,
            )
        ])
    return _delivery_set_success({
        "schema_version": 1,
        "contract_version": DELIVERY_SET_CONTRACT_VERSION,
        "operation": DELIVERY_SET_OPERATION,
        "delivery_set_id": control["delivery_set_id"],
        "case_id": case_id,
        "archive_revision": revision,
        "mode": mode,
        "requested_templates": normalized_requested,
        "expected_document_types": expected_types,
        "confirmation_refs": list(refs),
        "delivery_set_digest": digest,
    })


def _contract_examples() -> dict[str, Any]:
    return {
        "request": {
            "case_id": "case-0123456789abcdef01234567",
            "archive_revision": 3,
            "document_type": "arbitration_application",
            "template_version": TEMPLATE_VERSION,
            "mode": "external_final",
            "title": "劳动人事争议仲裁申请书",
            "sections": [
                {"section_id": "applicant", "heading": "申请人", "full_text": "申请人：张三。"},
                {"section_id": "respondent", "heading": "被申请人", "full_text": "被申请人：示例公司。"},
                {"section_id": "requests", "heading": "仲裁请求", "full_text": "请求支付工资差额6000.00元。"},
                {"section_id": "facts", "heading": "事实与理由", "full_text": "双方存在劳动关系。"},
                {"section_id": "closing", "heading": "落款", "full_text": "此致\n仲裁委员会\n申请人：张三\n2026年9月7日"},
            ],
            "locked_bindings": [
                {"kind": "document_heading", "archive_record_ref": "A-001", "rendered_value": "劳动人事争议仲裁申请书"},
                {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
                {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "被申请人：示例公司"},
                {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "支付工资差额"},
                {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "6000.00元", "occurrences": [{"section_id": "requests", "text": "请求支付工资差额6000.00元。"}]},
                {"kind": "date", "archive_record_ref": "F-003", "rendered_value": "2026年9月7日", "occurrences": [{"section_id": "closing", "text": "2026年9月7日"}]},
                {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请人：张三", "occurrences": [{"section_id": "closing", "text": "申请人：张三"}]},
            ],
            "placeholders": [],
            "confirmation_refs": [],
            "authority_refs": [],
        },
        "success": {
            "contract_version": CONTRACT_VERSION,
            "operation": OPERATION,
            "ok": True,
            "result": {
                "delivery_state": "final_ready",
                "document_type": "arbitration_application",
                "mode": "external_final",
                "archive_revision": 3,
                "delivery_set_id": "0123456789abcdef0123456789abcdef",
                "canonical_docx": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》.docx",
                "verification_checklist": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》内部核验清单（仅供核对，请勿外发）.txt",
                "machine_manifest": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》机器交付清单.json",
                "content_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "presentation_files": [
                    {
                        "delivery_label": "arbitration_application",
                        "kind": "document",
                        "order": 1,
                        "path": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》.docx",
                        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                        "state": "final_ready",
                    },
                    {
                        "delivery_label": "arbitration_application",
                        "kind": "checklist",
                        "order": 2,
                        "path": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》内部核验清单（仅供核对，请勿外发）.txt",
                        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                        "state": "final_ready",
                    },
                ],
                "artifact_files": [
                    {
                        "delivery_label": "arbitration_application",
                        "kind": "document",
                        "order": 1,
                        "path": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》.docx",
                        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                        "state": "final_ready",
                    }
                ],
                "presentation_authorized": False,
                "validation_summary": {
                    "status": "passed",
                    "checks": [
                        "ZIP包、内容类型、关系目标和XML部件完整",
                        "A4纵向页面与显式页边距",
                        "标题、正文与落款使用命名样式",
                        "独立页脚使用PAGE / NUMPAGES及Times New Roman小五",
                        "分页、孤行和孤立落款控制",
                        "具体义务使用真实Word编号",
                    ],
                    "placeholder_state": "none",
                    "locked_binding_count": 7,
                },
            },
            "errors": [],
            "warnings": [],
        },
        "aggregate_failure": {
            "contract_version": CONTRACT_VERSION,
            "operation": OPERATION,
            "ok": False,
            "errors": [
                {
                    "code": "archive_revision_conflict",
                    "path": "archive_revision",
                    "message": "请求所依据的档案修订版本已过期，请重新读取并重写文书",
                    "expected": 4,
                    "received": 3,
                    "recoverable": True,
                },
                {
                    "code": "binding_mismatch",
                    "path": "locked_bindings[4].rendered_value",
                    "message": "金额渲染值与确定性计算记录不一致",
                    "expected": "value present in referenced CAL record",
                    "received": "7000.00元",
                    "recoverable": True,
                },
                {
                    "code": "internal_content_leak",
                    "path": "sections[3].full_text",
                    "message": "正文包含内部路径、协议、记录标识、推理或哈希",
                    "expected": "user-facing text only",
                    "received": "record id or tool protocol marker",
                    "recoverable": True,
                },
            ],
            "warnings": [],
        },
        "recovery": {
            "when": "archive_revision_conflict、confirmation_stale、authority_stale 或 binding_mismatch",
            "action": "重新 read 当前案情档案，按错误 path 修正文书请求，再以新的 archive_revision 重试",
            "guarantee": "失败不会创建或替换 canonical DOCX；成功重放返回同一 canonical 结果",
        },
    }


def _artifact_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checklist_text(request: Mapping[str, Any]) -> str:
    candidate = request["mode"] == "candidate"
    lines = [
        f"《{request['title']}》提交前核验清单",
        "",
        "本清单仅供核对，请勿外发；它不属于正式文书内容。",
        "",
        "## 一、文书状态",
        f"- {'候选草稿，补全后再重新生成可外发定稿' if candidate else '外发定稿，仍请在提交前完成人工核对'}",
        "",
        "## 二、请核对的内容",
    ]
    body_text = "\n".join(section["full_text"] for section in request["sections"])
    lines.extend(
        "- " + item
        for item in required_checklist_bullets(
            request["document_type"], request["mode"], body_text
        )
    )
    if request["placeholders"]:
        lines.extend(
            "- 补全并重新确认：" + (item["label"] or item["text"])
            for item in request["placeholders"]
        )
    lines.extend("- " + item for item in request["additional_checklist_items"])
    if request["document_type"] == "evidence_catalog":
        preview = "提交或发送前请在 Word/WPS 中预览证据表格、名称、证明目的、页码和分页。"
    elif candidate:
        preview = "请在 Word/WPS 中预览候选稿字段、正文层级和分页。"
    else:
        preview = "提交或发送前请在 Word/WPS 中预览字段、金额、分页和落款。"
    lines.extend(
        (
            "- " + preview,
            "- 本清单不是正式文书正文，不得与正式文书一并提交或发送。",
            "",
        )
    )
    return "\n".join(lines)


def _set_cell_text(cell: Any, value: str, builder: Any, *, center: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    if center:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = builder.Pt(0)
    paragraph.paragraph_format.space_after = builder.Pt(0)
    run = paragraph.add_run(value)
    builder._set_run_font(run, name="仿宋", size=10.5)


def _parse_evidence_rows(text: str) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or re.fullmatch(r"[|｜\-\s]+", stripped):
            continue
        parts = [part.strip() for part in _EVIDENCE_DELIMITER_PATTERN.split(stripped.strip("|｜"))]
        if len(parts) < 4:
            continue
        if parts[0] in {"编号", "证据编号"}:
            continue
        rows.append(tuple((parts + [""] * 4)[:4]))
    return rows


def _evidence_rows(request: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    section = request["sections"][1]
    rows = _parse_evidence_rows(section["full_text"])
    if rows:
        return rows
    for binding in request["locked_bindings"]:
        if binding["kind"] != "evidence":
            continue
        parts = [part.strip() for part in _EVIDENCE_DELIMITER_PATTERN.split(binding["rendered_value"])]
        if len(parts) >= 4:
            return [tuple((parts + [""] * 4)[:4])]
        return [("", binding["rendered_value"], "", "")]
    return [("", "", section["full_text"], "")]


def _add_evidence_table(document: Any, rows: list[tuple[str, str, str, str]]) -> None:
    builder = _load_docx_builder()
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    headers = ("编号", "证据名称", "证据内容", "证明目的", "页码")
    table = document.add_table(rows=1, cols=5)
    table.autofit = False
    table.style = "Table Grid"
    header_row = table.rows[0]
    header_properties = header_row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    header_properties.append(repeat)
    for cell, label in zip(header_row.cells, headers, strict=True):
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "E8EEF5")
        cell._tc.get_or_add_tcPr().append(shading)
        _set_cell_text(cell, label, builder, center=True)
        cell.paragraphs[0].runs[0].font.bold = True
    for index, row_values in enumerate(rows, start=1):
        row = table.add_row()
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        values = (row_values[0] or str(index), *row_values[1:], "")
        for cell_index, (cell, value) in enumerate(zip(row.cells, values, strict=True)):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_text(cell, value, builder, center=cell_index in {0, 4})
    builder._set_table_geometry(table, (900, 2500, 4000, 6000, 1360))


_MANUAL_LIST_PREFIX = re.compile(
    r"^\s*(?:[一二三四五六七八九十百]+[、.．]|\d+\s*[.．、]|[（(]\d+[）)])\s*"
)
_HIDDEN_SECTION_HEADINGS = {
    "arbitration_application": frozenset({"申请人", "被申请人", "落款"}),
    "forced_termination_notice": frozenset({"收件人", "解除意思表示", "落款"}),
}
_NUMBERED_SECTION_HEADINGS = frozenset(
    {
        "仲裁请求",
        "答辩意见",
        "具体义务",
        "解除理由",
        "申请事项",
        "保全请求",
        "执行请求",
        "财产线索",
        "附件",
    }
)


def _canonical_placeholder(label: str) -> str:
    value = label.strip()
    value = re.sub(r"^待补\s*[:：]\s*", "", value)
    return f"【待补：{value}】"


def _display_text(value: str) -> str:
    normalized = re.sub(
        r"\{\{\s*([^{}\r\n]+?)\s*\}\}",
        lambda match: _canonical_placeholder(match.group(1)),
        value,
    )
    normalized = re.sub(
        r"\[\s*待补\s*[:：]\s*([^\]\r\n]+?)\s*\]",
        lambda match: _canonical_placeholder(match.group(1)),
        normalized,
    )
    return re.sub(
        r"【\s*待补\s*[:：]\s*([^】\r\n]+?)\s*】",
        lambda match: _canonical_placeholder(match.group(1)),
        normalized,
    )


def _text_paragraphs(value: str, *, title: str, heading: str) -> list[str]:
    paragraphs = [
        _display_text(item.strip())
        for item in re.split(r"(?:\r?\n)+", value)
        if item.strip()
    ]
    while paragraphs and paragraphs[0] in {title, heading}:
        paragraphs.pop(0)
    return paragraphs


def _add_plain_paragraph(document: Any, text: str) -> Any:
    paragraph = document.add_paragraph(style="ArbiBuddy Legal Body")
    paragraph.paragraph_format.keep_together = True
    paragraph.paragraph_format.widow_control = True
    paragraph.add_run(text)
    return paragraph


def _add_signature_lines(document: Any, lines: Sequence[str]) -> None:
    builder = _load_docx_builder()
    if not lines:
        return
    spacer = document.add_paragraph()
    spacer.paragraph_format.keep_with_next = True
    spacer.paragraph_format.keep_together = True
    for index, raw_line in enumerate(lines):
        line = re.sub(
            r"[，,；;]\s*并保留送达凭证(?=\s*[。.]?\s*$)",
            "",
            raw_line,
        )
        paragraph = document.add_paragraph(style="ArbiBuddy Legal Signature")
        builder._set_character_indentation(paragraph, left_chars=1600)
        paragraph.paragraph_format.line_spacing = 1.5
        paragraph.paragraph_format.keep_with_next = index < len(lines) - 1
        paragraph.add_run(line)


def _add_closing_section(
    document: Any,
    lines: Sequence[str],
) -> None:
    builder = _load_docx_builder()
    remaining = list(lines)
    if remaining and remaining[0] == "此致":
        recipient = remaining[1] if len(remaining) > 1 else ""
        builder._add_closing(document, recipient)
        remaining = remaining[2:]
    _add_signature_lines(document, remaining)


def _demand_signature_start(
    request: Mapping[str, Any],
    section: Mapping[str, str],
    lines: Sequence[str],
) -> int | None:
    """Locate an explicit, bound/declarative signature line, never split prose."""
    signature_values = [
        binding['rendered_value']
        for binding in request.get('locked_bindings', ())
        if binding['kind'] == 'signature'
        and any(item['section_id'] == section['section_id'] for item in binding.get('occurrences', ()))
    ]
    placeholders = [item['text'] for item in request.get('placeholders', ())]
    for index, line in enumerate(lines):
        if not re.match(r'^(?:催告人|通知人|签署人|申请人|签名)(?:（[^）]{1,20}）)?\s*[：:]', line):
            continue
        if any(value in line for value in signature_values + placeholders):
            return index
    return None


def _add_text_section(
    document: Any,
    request: Mapping[str, Any],
    section: Mapping[str, str],
) -> None:
    builder = _load_docx_builder()
    heading = section["heading"]
    document_type = request["document_type"]
    lines = _text_paragraphs(
        section["full_text"],
        title=request["title"],
        heading=heading,
    )
    hidden = heading in _HIDDEN_SECTION_HEADINGS.get(document_type, ())
    if heading == "落款":
        _add_closing_section(document, lines)
        return
    if not hidden:
        builder._add_heading(document, heading)

    if document_type == 'employment_obligation_demand_letter' and heading == '沟通与保留权利':
        signature_start = _demand_signature_start(request, section, lines)
        if signature_start is not None:
            for line in lines[:signature_start]:
                builder._add_body_paragraph(document, line)
            _add_signature_lines(document, lines[signature_start:])
            return

    if heading in _NUMBERED_SECTION_HEADINGS:
        parsed = [
            (_MANUAL_LIST_PREFIX.sub("", line, count=1), bool(_MANUAL_LIST_PREFIX.match(line)))
            for line in lines
        ]
        has_manual_numbering = any(manual for _line, manual in parsed)
        num_id = builder._start_numbered_list(document)
        for line, manually_numbered in parsed:
            if manually_numbered or not has_manual_numbering:
                builder._add_numbered_paragraph(document, line, num_id=num_id)
            else:
                builder._add_body_paragraph(document, line)
        return

    for index, line in enumerate(lines):
        if heading in {"申请人", "被申请人"} or (
            heading == "收件人" and index == 0
        ):
            _add_plain_paragraph(document, line)
        else:
            builder._add_body_paragraph(document, line)


def _build_docx(path: Path, request: Mapping[str, Any]) -> None:
    docx_builder = _load_docx_builder()
    landscape = request["document_type"] == "evidence_catalog"
    document = docx_builder._new_legal_document(landscape=landscape)
    docx_builder._add_title(document, request["title"])

    if landscape:
        _add_evidence_table(document, _evidence_rows(request))
        return docx_builder._save_legal_document(document, path)

    for section in request["sections"]:
        _add_text_section(document, request, section)
    return docx_builder._save_legal_document(document, path)


def _scan_docx_for_internal_content(path: Path, visible_text: str) -> list[str]:
    from zipfile import ZipFile

    values = [visible_text]
    with ZipFile(path) as package:
        for name in package.namelist():
            if name.endswith((".xml", ".rels", ".txt")):
                values.append(package.read(name).decode("utf-8", "ignore"))
    return _internal_leaks(values)


def _result_from_manifest(output_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    document_name = manifest["document"]["filename"]
    checklist_name = manifest["verification_checklist"]["filename"]
    manifest_name = manifest["machine_manifest"]["filename"]
    document_path = str(public_path((output_dir / document_name).resolve()))
    checklist_path = str(public_path((output_dir / checklist_name).resolve()))
    artifact_files = [
        {
            "delivery_label": manifest["document_type"],
            "kind": "document",
            "order": 1,
            "path": document_path,
            "sha256": manifest["document"]["sha256"],
            "state": manifest["delivery_state"],
        },
        {
            "delivery_label": manifest["document_type"],
            "kind": "checklist",
            "order": 2,
            "path": checklist_path,
            "sha256": manifest["verification_checklist"]["sha256"],
            "state": manifest["delivery_state"],
        },
    ]
    return {
        "delivery_state": manifest["delivery_state"],
        "document_type": manifest["document_type"],
        "mode": manifest["mode"],
        "archive_revision": manifest["archive_revision"],
        "delivery_set_id": manifest.get("delivery_set_id"),
        "delivery_set_digest": manifest.get("delivery_set_digest"),
        "canonical_docx": document_path,
        "verification_checklist": checklist_path,
        "machine_manifest": str(public_path((output_dir / manifest_name).resolve())),
        "machine_manifest_sha256": _artifact_sha256(output_dir / manifest_name),
        "content_digest": manifest["content_digest"],
        "validation_summary": deepcopy(manifest["validation_summary"]),
        "artifact_files": artifact_files,
        "presentation_files": artifact_files,
        "presentation_authorized": False,
    }


def _load_replay(
    output_dir: Path,
    identity: str,
    content_digest: str,
    *,
    expected: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    if not output_dir.exists():
        return None, [], []
    if not output_dir.is_dir() or output_dir.is_symlink():
        return None, [_error("publication_conflict", "output", "canonical 输出位置不是安全目录", "managed directory", str(output_dir))], []
    manifests = list(output_dir.glob("*机器交付清单.json"))
    if len(manifests) != 1:
        return None, [_error("publication_conflict", "output", "既有输出缺少唯一机器交付清单", "one managed manifest", len(manifests))], []
    try:
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [_error("publication_conflict", "output", "既有机器交付清单无法验证，拒绝覆盖", "valid managed manifest", error.__class__.__name__)], []
    if not isinstance(manifest, Mapping):
        return None, [_error("publication_conflict", "output", "既有机器交付清单不是对象，拒绝覆盖", "object manifest", manifest)], []
    if manifest.get("render_identity") == identity and manifest.get("content_digest") == content_digest:
        try:
            if manifest.get("contract_version") != CONTRACT_VERSION or manifest.get("operation") != OPERATION:
                raise ValueError("managed contract metadata mismatch")
            if expected is not None and any(
                manifest.get(key) != expected.get(key)
                for key in (
                    "case_id",
                    "archive_revision",
                    "document_type",
                    "template_version",
                    "mode",
                    "title",
                )
            ):
                raise ValueError("managed request metadata mismatch")
            filenames = {
                key: manifest[key]["filename"]
                for key in ("document", "verification_checklist", "machine_manifest")
            }
            if any(
                not isinstance(filename, str)
                or not filename
                or Path(filename).name != filename
                or Path(filename).is_absolute()
                for filename in filenames.values()
            ):
                raise ValueError("managed artifact filename is not direct")
            if expected is not None and filenames != {
                "document": _document_filename(expected["title"], expected["mode"]),
                "verification_checklist": _checklist_filename(expected["title"]),
                "machine_manifest": _manifest_filename(expected["title"]),
            }:
                raise ValueError("managed artifact filename mismatch")
            if set(item.name for item in output_dir.iterdir()) != set(filenames.values()):
                raise ValueError("managed output contains unexpected artifacts")
            if any((output_dir / filename).is_symlink() for filename in filenames.values()):
                raise ValueError("managed artifact is a link")
            document = output_dir / filenames["document"]
            checklist = output_dir / filenames["verification_checklist"]
            if (
                not document.is_file()
                or not checklist.is_file()
                or manifest["document"]["sha256"] != _artifact_sha256(document)
                or manifest["verification_checklist"]["sha256"] != _artifact_sha256(checklist)
            ):
                raise ValueError("artifact digest mismatch")
        except (KeyError, TypeError, OSError, ValueError) as error:
            return None, [_error("publication_conflict", "output", "重放结果不完整或摘要不一致，拒绝静默覆盖", "intact canonical artifacts", error.__class__.__name__)], []
        expected_dependencies = expected.get("archive_dependencies") if expected is not None else None
        if expected_dependencies is not None:
            if "archive_dependencies" not in manifest:
                # Files without dependency proof must be regenerated to acquire it.
                return None, [], []
            if manifest["archive_dependencies"] != expected_dependencies:
                return None, [_error("publication_conflict", "archive_dependencies", "既有候选依赖摘要不一致，拒绝静默重放", expected_dependencies, manifest["archive_dependencies"])], []
        replay_warnings = manifest.get("warnings", [])
        if not isinstance(replay_warnings, list) or any(
            not isinstance(item, Mapping) for item in replay_warnings
        ):
            return None, [_error("publication_conflict", "output", "重放结果中的 warning 摘要无效，拒绝静默覆盖", "warning array", replay_warnings)], []
        return _result_from_manifest(output_dir, manifest), [], deepcopy(replay_warnings)
    return None, [], []


def _cleanup_backup(output_dir: Path, transaction_id: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    prefix = f".{output_dir.name}.committed-backup-{transaction_id}-"
    try:
        candidates = tuple(output_dir.parent.iterdir())
    except OSError:
        return [
            {
                "code": "temporary_cleanup_failed",
                "message": "旧文书备份无法检查，但新的 canonical 文书已经原子提交，可安全重试回收",
                "recoverable": True,
            }
        ]
    for candidate in candidates:
        if not candidate.name.startswith(prefix):
            continue
        try:
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        except OSError:
            warnings.append(
                {
                    "code": "temporary_cleanup_failed",
                    "message": "旧文书备份清理失败，但新的 canonical 文书已经原子提交，可安全重试回收",
                    "recoverable": True,
                }
            )
    return warnings


def _revision_errors(response: Mapping[str, Any], expected_revision: int) -> list[dict[str, Any]]:
    if not response.get("ok"):
        return list(response.get("errors", [])) or [_error("io_failure", "archive", "案情档案无法读取", "readable archive", None)]
    revision = response["result"]["revision"]
    if revision == expected_revision:
        return []
    error = _error("archive_revision_conflict", "archive_revision", "渲染期间案情档案已更新，请重读后重新生成", revision, expected_revision)
    error["current_revision"] = revision
    return [error]


class _DocumentRenderer:
    """Implement the complete, narrow ``document.render`` contract internally."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = absolute_path_for_io(workspace_root)
        self._archive = CaseArchive(self._workspace_root)

    def describe(self) -> dict[str, Any]:
        return {
            **CONTRACT_METADATA,
            "input": {
                "fields": [
                    {"name": name, "required": name in _TOP_LEVEL_FIELDS[:8], "type": "array[object]" if name in {"sections", "locked_bindings"} else "array[string]" if name.endswith("refs") else "array[object]" if name == "placeholders" else "array[string]" if name == "additional_checklist_items" else "string" if name not in {"archive_revision"} else "non-negative integer"}
                    for name in _TOP_LEVEL_FIELDS
                ],
                "document_types": list(DOCUMENT_TYPES),
                "modes": list(MODES),
                "template_version": TEMPLATE_VERSION,
                "document_type_requirements": deepcopy(_DOCUMENT_TYPE_CONTRACTS),
                "section": {
                    "required_fields": list(_SECTION_FIELDS),
                    "full_text_owner": "model; complete text is rendered without legal rewriting",
                },
                "locked_binding": {
                    "required_fields": ["kind", "archive_record_ref", "rendered_value"],
                    "optional_fields": ["label", "occurrences"],
                    "uniqueness": "unique kind + archive_record_ref + rendered_value; one archive record may support multiple distinct locked values",
                    "occurrences": {"required_for": ["amount", "fact_amount", "date", "signature in external_final"], "fields": ["section_id", "text"], "text": "unique exact single-line excerpt; bind every complete amount/date occurrence and the full role:name signature inside the closing section"},
                    "external_final_date_coverage": "every complete Chinese or ISO calendar date in every body section must be valid and individually located by a date occurrence supported by its referenced archive record",
                    "amount_source": "one explicitly labelled 确定性计算结果：N元 in the referenced CAL record",
                    "fact_amount_source": {"record": "F record containing the same currency value", "mode": "candidate", "allowed_headings": {key: sorted(value) for key, value in _FACT_AMOUNT_HEADINGS.items()}, "scope": "factual narrative only; claim/settlement amounts still use CAL"},
                    "kinds": list(_BINDING_KINDS),
                },
                "placeholders": {
                    "type": "array[object]",
                    "fields": list(_PLACEHOLDER_FIELDS[:2]),
                    "candidate_only": True,
                },
                "references": {
                    "confirmation_refs": "optional array of archive record ids",
                    "authority_refs": "optional array of archive record ids",
                },
            },
            "output": {
                "fields": [
                    "delivery_state",
                    "document_type",
                    "mode",
                    "archive_revision",
                    "delivery_set_id",
                    "canonical_docx",
                    "verification_checklist",
                    "machine_manifest",
                    "content_digest",
                    "validation_summary",
                    "artifact_files",
                    "presentation_files",
                    "presentation_authorized",
                ],
                "presentation_authorized": False,
                "presentation_rule": "单份 render 只返回 artifact_files 诊断；WorkBuddy 展示必须消费完整 managed delivery view",
                "delivery_states": ["candidate_ready", "final_ready"],
                "canonical_location": ".arbibuddy/cases/<case_id>/output/<document_type>/",
                "user_preview": "交付前在 Word/WPS 中核对字体、分页、表格、金额和落款",
                "verification_checklist": {
                    "keyed_by": ["document_type", "mode"],
                    "required_categories_by_document_type": {
                        "evidence_catalog": ["证据引用或附件完整性"],
                        "other_supported_types": [
                            "主体与身份",
                            "事实或请求",
                            "关键日期与程序条件",
                            "证据引用或附件完整性",
                        ],
                    },
                    "amount_category": "仅非证据目录且正文含金额时要求，单独列项",
                    "closing_category": "仅 external_final 且非证据目录时要求，单独列项",
                    "candidate_closing_category": "不要求最终签署角色与日期核验项",
                },
            },
            "errors": {
                "codes": [
                    "unsupported_document_type",
                    "template_version_mismatch",
                    "archive_revision_conflict",
                    "missing_section",
                    "binding_not_found",
                    "binding_mismatch",
                    "document_type_conflict",
                    "undeclared_placeholder",
                    "placeholder_in_final",
                    "confirmation_missing",
                    "confirmation_stale",
                    "authority_required",
                    "authority_stale",
                    "internal_content_leak",
                    "fact_conflict",
                    "ooxml_invalid",
                    "publication_conflict",
                    "permission_denied",
                    "io_failure",
                    "document_runtime_unavailable",
                    "installed_skill_modified",
                    "missing_input",
                    "invalid_object",
                    "invalid_text",
                    "invalid_revision",
                    "invalid_mode",
                    "invalid_placeholder",
                    "delivery_set_required",
                    "delivery_set_mismatch",
                    "delivery_set_member_mismatch",
                ],
                "shape": "每项包含 code、path、message、expected、received、recoverable",
                "recovery": "按 path 一次修正请求；revision/绑定/确认/来源变化先重新读取档案；发布失败不产生 canonical 半成品",
            },
            "examples": {
                **_contract_examples(),
            },
            "scope": "本公共接缝实现八类模型主导文书；不自动提交、发送或签署",
            "delivery_set": {
                "contract_version": DELIVERY_SET_CONTRACT_VERSION,
                "operation": DELIVERY_SET_OPERATION,
                "entrypoint": "scripts.documents.public.create_delivery_set",
                "managed_view_required": True,
            },
        }

    def render(self, request: Any) -> dict[str, Any]:
        normalized, errors = _validate_request(request)
        if normalized is None or any(
            error["code"] in {
                "invalid_object",
                "missing_input",
                "invalid_text",
                "invalid_revision",
                "unsupported_document_type",
                "template_version_mismatch",
                "invalid_mode",
                "invalid_placeholder",
            }
            and error["path"] in {"request", "case_id", "archive_revision", "document_type", "template_version", "mode", "title", "sections", "locked_bindings"}
            for error in errors
        ):
            return _failure(errors)
        assert normalized is not None
        try:
            preflight = RuntimeIdentityModule().preflight()
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            preflight = {
                "verified": False,
                "failures": [f"preflight_error:{type(error).__name__}"],
            }
        if preflight.get("verified") is not True:
            return _failure(
                [
                    _error(
                        "installed_skill_modified",
                        "runtime",
                        runtime_identity_failure_message(preflight),
                        "verified installed Skill identity",
                        preflight.get(
                            "failures", ["runtime_identity_preflight_failed"]
                        ),
                        recoverable=False,
                    )
                ]
            )
        try:
            ensure_document_runtime()
        except DocumentRuntimeUnavailable as error:
            return _failure(
                [
                    _error(
                        "document_runtime_unavailable",
                        "document",
                        "当前环境未生成 DOCX；请安装兼容包或更换已支持环境后重试",
                        "self-contained WorkBuddy document runtime",
                        error.reason,
                        recoverable=False,
                    )
                ]
            )
        archive_result = self._archive.read(normalized["case_id"])
        if not archive_result.get("ok"):
            translated = []
            for error in archive_result.get("errors", []):
                translated.append(
                    {
                        **error,
                        "path": error.get("path", "archive"),
                    }
                )
            return _failure(
                [
                    *errors,
                    *(
                        translated
                        or [_error("io_failure", "archive", "案情档案读取失败", "readable archive", None)]
                    ),
                ]
            )
        archive = archive_result["result"]
        if archive["revision"] != normalized["archive_revision"]:
            conflict = _error(
                "archive_revision_conflict",
                "archive_revision",
                "请求所依据的档案修订版本已过期，请重新读取并重写文书",
                archive["revision"],
                normalized["archive_revision"],
            )
            conflict["current_revision"] = archive["revision"]
            return _failure([*errors, conflict])
        delivery_set: dict[str, Any] | None = None
        delivery_set_path = _delivery_set_path(
            self._workspace_root,
            normalized["case_id"],
        )
        if normalized.get("delivery_set_id") is not None:
            try:
                delivery_set = load_delivery_set(
                    self._workspace_root,
                    normalized["case_id"],
                    delivery_set_id=normalized["delivery_set_id"],
                    archive_revision=normalized["archive_revision"],
                )
            except (OSError, UnicodeError, ValueError) as error:
                return _failure(
                    [
                        _error(
                            "delivery_set_mismatch",
                            "delivery_set_id",
                            "document.render 请求没有绑定当前有效的 delivery set",
                            "current document.delivery-set-v1",
                            str(normalized["delivery_set_id"]),
                            recoverable=True,
                        )
                    ]
                )
            if normalized["document_type"] not in delivery_set["expected_document_types"]:
                return _failure(
                    [
                        _error(
                            "delivery_set_member_mismatch",
                            "document_type",
                            "文书类型不属于当前 delivery set 的规范化集合",
                            delivery_set["expected_document_types"],
                            normalized["document_type"],
                        )
                    ]
                )
            if normalized["mode"] != delivery_set["mode"]:
                return _failure(
                    [
                        _error(
                            "delivery_set_member_mismatch",
                            "mode",
                            "文书成熟度必须与 delivery set 一致",
                            delivery_set["mode"],
                            normalized["mode"],
                        )
                    ]
                )
            if normalized["confirmation_refs"] != delivery_set["confirmation_refs"]:
                return _failure(
                    [
                        _error(
                            "delivery_set_member_mismatch",
                            "confirmation_refs",
                            "高风险确认引用必须与 delivery set 一致",
                            delivery_set["confirmation_refs"],
                            normalized["confirmation_refs"],
                        )
                    ]
                )
        elif delivery_set_path.is_file():
            return _failure(
                [
                    _error(
                        "delivery_set_required",
                        "delivery_set_id",
                        "当前案件已有结构化 delivery set，渲染必须显式绑定它",
                        "delivery_set_id",
                        None,
                    )
                ]
            )
        records = _archive_records(archive["markdown"])
        binding_errors = _binding_errors(
            normalized,
            records,
            normalized["archive_revision"],
            archive["markdown"],
        )
        validation_warnings: list[dict[str, Any]] = []
        if (
            normalized["mode"] == "candidate"
            and normalized["document_type"] in _HIGH_RISK_DOCUMENT_TYPES
        ):
            remaining_binding_errors: list[dict[str, Any]] = []
            for error in binding_errors:
                if (
                    error["code"] == "authority_stale"
                    and error.get("expected") == "核验结论：已核验"
                ):
                    validation_warnings.append(
                        {
                            "code": "authority_stale",
                            "message": "候选文书保留，但现行规则仍需完成动态核验后才能外发",
                            "recoverable": True,
                        }
                    )
                elif (
                    error["code"] == "confirmation_stale"
                    and normalized["document_type"] in _HIGH_RISK_DOCUMENT_TYPES
                ):
                    validation_warnings.append(
                        {
                            "code": "confirmation_stale",
                            "message": "候选文书保留，但高风险实质确认已过期，补充确认后才能外发",
                            "recoverable": True,
                        }
                    )
                else:
                    remaining_binding_errors.append(error)
            binding_errors = remaining_binding_errors
        if errors or binding_errors:
            return _failure([*errors, *binding_errors])
        content_digest = _content_digest(normalized, archive["markdown"])
        dependencies = (
            candidate_dependencies(archive["markdown"], _canonical_json(normalized))
            if normalized["mode"] == "candidate" else None
        )
        replay_expected = {**normalized, "archive_dependencies": dependencies}
        identity = sha256(
            _canonical_json(
                {
                    "contract_version": CONTRACT_VERSION,
                    "content_digest": content_digest,
                    "archive_revision": normalized["archive_revision"],
                    "delivery_set_id": (
                        delivery_set["delivery_set_id"] if delivery_set is not None else None
                    ),
                }
            ).encode("utf-8")
        ).hexdigest()
        output_dir = _output_dir(self._workspace_root, normalized["case_id"], normalized["document_type"])
        parent_preexisted = output_dir.parent.exists()
        committed = False
        try:
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            with self._archive._read_transaction(normalized["case_id"]) as current:
                current_errors = _revision_errors(current, normalized["archive_revision"])
                if current_errors:
                    return _failure(current_errors)
                replay, replay_errors, replay_warnings = _load_replay(
                    output_dir,
                    identity,
                    content_digest,
                    expected=replay_expected,
                )
                if replay_errors:
                    return _failure(replay_errors)
                if replay is not None:
                    return _success(replay, replay_warnings)
            staging_dir = output_dir.parent / (
                f".{output_dir.name}.staging-{uuid4().hex}"
            )
            staging_dir.mkdir()
            document_name = _document_filename(normalized["title"], normalized["mode"])
            checklist_name = _checklist_filename(normalized["title"])
            manifest_name = _manifest_filename(normalized["title"])
            document_path = staging_dir / document_name
            checklist_path = staging_dir / checklist_name
            manifest_path = staging_dir / manifest_name
            transaction_id = identity
            warnings: list[dict[str, Any]] = list(validation_warnings)
            try:
                try:
                    _build_docx(document_path, normalized)
                except (ImportError, ModuleNotFoundError) as error:
                    return _failure([_error("io_failure", "document", "DOCX 依赖不可用，无法生成真实 Word 文书", "python-docx", error.__class__.__name__)])
                except PermissionError as error:
                    return _failure([_error("permission_denied", "document", "没有写入 DOCX 的权限", "writable staging directory", error.__class__.__name__)])
                except OSError as error:
                    return _failure([_error("io_failure", "document", "写入 DOCX 失败", "writable staging directory", error.__class__.__name__)])
                except ValueError as error:
                    return _failure([_error("ooxml_invalid", "document", "DOCX 输入包含无法写入 OOXML 的内容", "XML 1.0 compatible text", str(error))])
                try:
                    audit = audit_docx(
                        document_path,
                        _TEMPLATE_IDS[normalized["document_type"]],
                    )
                    leaks = _scan_docx_for_internal_content(document_path, audit.visible_text)
                    if leaks:
                        return _failure([_error("internal_content_leak", "document.xml", "DOCX 包含内部内容泄漏", "user-facing DOCX only", leaks)])
                except ValueError as error:
                    return _failure([_error("ooxml_invalid", "document", "DOCX/OOXML 结构检查失败", "valid audited DOCX", str(error))])
                except (OSError, ImportError, ModuleNotFoundError) as error:
                    return _failure([_error("ooxml_invalid", "document", "DOCX/OOXML 结构检查不可用", "readable OOXML package", error.__class__.__name__)])

                checklist_text = _checklist_text(normalized)
                try:
                    checklist_path.write_text(checklist_text, encoding="utf-8", newline="\n")
                except PermissionError as error:
                    return _failure([_error("permission_denied", "verification_checklist", "没有写入核验清单的权限", "writable staging directory", error.__class__.__name__)])
                except OSError as error:
                    return _failure([_error("io_failure", "verification_checklist", "写入核验清单失败", "writable staging directory", error.__class__.__name__)])
                delivery_state = "candidate_ready" if normalized["mode"] == "candidate" else "final_ready"
                validation_summary = {
                    "status": "passed",
                    "checks": list(audit.checks),
                    "placeholder_state": "declared" if normalized["placeholders"] else "none",
                    "locked_binding_count": len(normalized["locked_bindings"]),
                }
                manifest: dict[str, Any] = {
                    "schema_version": 1,
                    "contract_version": CONTRACT_VERSION,
                    "operation": OPERATION,
                    "render_identity": identity,
                    "content_digest": content_digest,
                    "delivery_state": delivery_state,
                    "case_id": normalized["case_id"],
                    "archive_revision": normalized["archive_revision"],
                    "document_type": normalized["document_type"],
                    "template_version": normalized["template_version"],
                    "mode": normalized["mode"],
                    "title": normalized["title"],
                    "delivery_set_id": (
                        delivery_set["delivery_set_id"] if delivery_set is not None else None
                    ),
                    "delivery_set_digest": (
                        delivery_set["delivery_set_digest"] if delivery_set is not None else None
                    ),
                    "warnings": warnings,
                    "document": {"filename": document_name, "sha256": _artifact_sha256(document_path)},
                    "verification_checklist": {"filename": checklist_name, "sha256": _artifact_sha256(checklist_path)},
                    "machine_manifest": {"filename": manifest_name},
                    "validation_summary": validation_summary,
                }
                if normalized["mode"] == "candidate":
                    manifest["archive_dependencies"] = dependencies
                try:
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                except PermissionError as error:
                    return _failure([_error("permission_denied", "machine_manifest", "没有写入机器交付清单的权限", "writable staging directory", error.__class__.__name__)])
                except OSError as error:
                    return _failure([_error("io_failure", "machine_manifest", "写入机器交付清单失败", "writable staging directory", error.__class__.__name__)])
                with self._archive._read_transaction(normalized["case_id"]) as current:
                    current_errors = _revision_errors(current, normalized["archive_revision"])
                    if current_errors:
                        return _failure(current_errors)
                    try:
                        commit_generation_staging(
                            case_root=self._workspace_root / ".arbibuddy" / "cases",
                            case_id=normalized["case_id"],
                            output_dir=output_dir,
                            staging_dir=staging_dir,
                            transaction_id=transaction_id,
                            replace_existing=True,
                        )
                        committed = True
                    except FileExistsError as error:
                        return _failure([_error("publication_conflict", "output", "canonical 文书输出已被其他发布占用", "available canonical output", str(error))])
                    except PermissionError as error:
                        return _failure([_error("permission_denied", "output", "没有原子发布文书的权限；本次新文书未交付。保留旧DOCX、独立核验清单和机器清单，关闭Word/WPS及预览后仅用同一受管请求重试；仍失败则停止并回报，不删除、移走旧稿或清除事务锁。", "replaceable canonical output", error.__class__.__name__)])
                    except OSError as error:
                        return _failure([_error("io_failure", "output", "原子发布文书失败，旧 canonical 文件保持不变", "atomic output replacement", error.__class__.__name__)])
                    warnings.extend(_cleanup_backup(output_dir, transaction_id))
                    published, publication_errors, _publication_warnings = _load_replay(
                        output_dir,
                        identity,
                        content_digest,
                        expected=replay_expected,
                    )
                    if publication_errors or published is None:
                        return _failure(
                            publication_errors
                            or [
                                _error(
                                    "publication_conflict",
                                    "output",
                                    "canonical 文书提交后缺少完整产物或摘要不一致",
                                    "intact canonical artifacts",
                                    str(output_dir),
                                )
                            ]
                        )
                    return _success(published, warnings)
            finally:
                if not committed and staging_dir is not None and staging_dir.exists():
                    try:
                        shutil.rmtree(staging_dir)
                    except OSError:
                        warnings.append(
                            {
                                "code": "temporary_cleanup_failed",
                                "message": "文书暂存目录清理失败，但未发布 canonical 文书，可安全重试回收",
                                "recoverable": True,
                            }
                        )
        except _ArchiveLockTimeout:
            return _failure([_error("publication_conflict", "archive", "案情档案正在提交，请稍后通过受管读取重新取得当前版本再重试；不要手工删除锁或诊断进程，持续失败时停止并回报。", "available archive transaction", None)])
        except PermissionError as error:
            return _failure([_error("permission_denied", "output", "没有创建文书输出目录的权限", "writable workspace", error.__class__.__name__)])
        except OSError as error:
            return _failure([_error("io_failure", "output", "文书输出目录 I/O 失败", "writable workspace", error.__class__.__name__)])
        finally:
            if not parent_preexisted and not committed and output_dir.parent.is_dir():
                try:
                    output_dir.parent.rmdir()
                except OSError:
                    pass


def render(request: Any, workspace_root: str | Path) -> dict[str, Any]:
    """Convenience entry point for the public document.render operation."""

    return _DocumentRenderer(workspace_root).render(request)


def describe_contract() -> dict[str, Any]:
    """Return the public document.render contract without reading a case."""

    return _DocumentRenderer(Path.cwd()).describe()


__all__ = [
    "ARCHIVE_CONTRACT",
    "CONTRACT_VERSION",
    "DELIVERY_SET_CONTRACT_VERSION",
    "DELIVERY_SET_OPERATION",
    "DOCUMENT_TYPES",
    "PUBLIC_ENTRYPOINT",
    "create_delivery_set",
    "describe_contract",
    "load_delivery_set",
    "render",
]
