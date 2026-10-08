"""Independent, read-only oracle for the public archive and document files.

This module intentionally uses only the Python standard library. It duplicates
small parts of the public file contract so Agent Eval does not share the
production CaseArchive or document receipt validator as its oracle.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import posixpath
from pathlib import Path
import re
from typing import Any, Mapping
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


ARCHIVE_FILENAME = "案情档案.md"
DOCUMENT_CONTRACT = "document.render-v1"
DOCUMENT_OPERATION = "document.render"
TEMPLATE_VERSION = "1.0.0"
CASE_ID_PATTERN = re.compile(r"^case-[0-9a-f]{24}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CASE_LINE_PATTERN = re.compile(r"(?m)^- 案件编号：(case-[0-9a-f]{24})$")
REVISION_LINE_PATTERN = re.compile(r"(?m)^- 档案修订版本：([0-9]+)$")
UPDATED_LINE_PATTERN = re.compile(r"(?m)^- 最后更新：([^\r\n]+)$")
RECORD_ID_PATTERN = re.compile(
    r"(?m)^### \[(?P<record_id>[A-Z][A-Z0-9_]*-[0-9]{3,})\] [^\r\n]+$"
)
RECORD_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:F|CL|E|A|CAL|AUTH|R|CONF|N)-[0-9]{3,}(?![A-Za-z0-9_])"
)
REQUIRED_ARCHIVE_SECTIONS = (
    "## 案件元数据",
    "## 事实",
    "## 主张与权益",
    "## 证据材料",
    "## 分析与假设",
    "## 计算结果",
    "## 法律核验",
    "## 风险与确认",
    "## 下一步",
    "## 更新记录",
)
DOCUMENT_TITLES = {
    "employment_obligation_demand_letter": "劳动用工义务催告函",
    "forced_termination_notice": "被迫解除劳动合同通知书",
    "arbitration_application": "劳动人事争议仲裁申请书",
    "arbitration_defense": "劳动人事争议仲裁答辩书",
    "evidence_catalog": "证据目录",
    "company_deregistration_restriction_request_shanghai": "限制公司注销申请书",
    "property_preservation_application": "财产保全申请书",
    "enforcement_application": "强制执行申请书",
}
_CHECKLIST_OBJECTS = {
    "employment_obligation_demand_letter": (
        ("主体与身份", "收件人和劳动关系双方"),
        ("事实或请求", "具体用工义务、履行期限和要求采取的行动"),
        ("关键日期与程序条件", "劳动关系、义务履行期限和通知送达时间"),
        ("证据引用或附件完整性", "劳动关系、用工义务及通知送达的依据材料"),
    ),
    "forced_termination_notice": (
        ("主体与身份", "劳动者、用人单位和通知收件人"),
        ("事实或请求", "解除意思表示、解除理由和结算要求"),
        ("关键日期与程序条件", "解除日期、结算期限和解除前置程序"),
        ("证据引用或附件完整性", "解除理由、催告沟通和通知送达的依据材料"),
    ),
    "arbitration_application": (
        ("主体与身份", "申请人和被申请人"),
        ("事实或请求", "各项仲裁请求、事实理由及对应的主张依据"),
        ("关键日期与程序条件", "劳动关系、争议发生和仲裁申请相关日期及期限"),
        ("证据引用或附件完整性", "各项仲裁请求对应的证据名称和证明目的"),
    ),
    "arbitration_defense": (
        ("主体与身份", "答辩人和被答辩人"),
        ("事实或请求", "各项答辩意见、事实理由及回应的仲裁请求"),
        ("关键日期与程序条件", "争议事实、答辩期限和程序阶段相关日期"),
        ("证据引用或附件完整性", "各项答辩意见对应的证据名称和证明目的"),
    ),
    "evidence_catalog": (
        ("证据引用或附件完整性", "表格逐项列明的证据名称、证据内容、证明目的、编号和页码"),
    ),
    "company_deregistration_restriction_request_shanghai": (
        ("主体与身份", "申请人、被申请人和相关仲裁主体"),
        ("事实或请求", "注销限制申请事项、仲裁受理信息和事实理由"),
        ("关键日期与程序条件", "仲裁受理、公司注销进展和提出申请的程序时点"),
        ("证据引用或附件完整性", "仲裁受理、注销风险、沟通记录及申请附件"),
    ),
    "property_preservation_application": (
        ("主体与身份", "申请人、被申请人和财产相关主体"),
        ("事实或请求", "保全请求、程序阶段、财产线索和担保方案"),
        ("关键日期与程序条件", "案件程序阶段、财产变化风险和申请保全时点"),
        ("证据引用或附件完整性", "财产线索、担保材料及保全必要性的依据"),
    ),
    "enforcement_application": (
        ("主体与身份", "申请执行人和被执行人"),
        ("事实或请求", "执行请求、生效执行依据、履行情况和管辖信息"),
        ("关键日期与程序条件", "执行依据生效日、履行期限和申请执行时效"),
        ("证据引用或附件完整性", "生效执行依据、履行记录、财产线索和附件"),
    ),
}
_CHECKLIST_CLOSING_ROLES = {
    "employment_obligation_demand_letter": "劳动者或其授权经办人",
    "forced_termination_notice": "劳动者（解除通知人）",
    "arbitration_application": "申请人",
    "arbitration_defense": "答辩人",
    "company_deregistration_restriction_request_shanghai": "申请人",
    "property_preservation_application": "申请人",
    "enforcement_application": "申请执行人",
}
_CHECKLIST_MONEY_PATTERN = re.compile(
    r"(?<![0-9.])(?:(?:人民币|[￥¥])\s*)?"
    r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?\s*(?:万元|万?元|圆|人民币)"
)
CHECKLIST_FORBIDDEN_MARKERS = (
    ".arbibuddy",
    "case-archive-v1",
    "document.render-v1",
    "machine_manifest",
    "机器交付清单",
    "sha256",
    "system prompt",
    "模型推理",
)
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def inspect_public_artifacts(workspace: str | Path) -> dict[str, Any]:
    """Return only public file observations under managed case output roots."""

    root = Path(workspace).resolve()
    cases_root = root / ".arbibuddy" / "cases"
    case_archives: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    invalid_docx_paths: list[str] = []
    invalid_delivery_count = 0
    permission_denied_archive_count = 0
    permission_denied_delivery_count = 0
    permission_denied_docx_count = 0
    inspection_error_count = 0
    if cases_root.is_symlink() or not cases_root.is_dir():
        return {
            "case_archives": case_archives,
            "deliveries": deliveries,
            "invalid_docx_paths": invalid_docx_paths,
            "invalid_docx_count": 0,
            "invalid_delivery_count": 0,
            "permission_denied_archive_count": 0,
            "permission_denied_delivery_count": 0,
            "permission_denied_docx_count": 0,
            "inspection_error_count": 0,
        }

    try:
        case_roots = sorted(cases_root.iterdir(), key=lambda item: item.name)
    except PermissionError:
        permission_denied_archive_count += 1
        case_roots = []
    except OSError:
        inspection_error_count += 1
        case_roots = []
    for case_root in case_roots:
        if case_root.is_symlink() or not case_root.is_dir():
            continue
        case_id = case_root.name
        archive: dict[str, Any] | None = None
        try:
            archive = _read_archive(case_root / ARCHIVE_FILENAME, case_id, root)
        except PermissionError:
            permission_denied_archive_count += 1
        except (OSError, UnicodeError, ValueError, TypeError):
            archive = None
        if archive is not None:
            case_archives.append(archive)

        output_root = case_root / "output"
        if not output_root.exists() and not output_root.is_symlink():
            continue
        if output_root.is_symlink() or not output_root.is_dir():
            invalid_delivery_count += 1
            continue
        try:
            output_dirs = sorted(output_root.iterdir(), key=lambda item: item.name)
        except PermissionError:
            invalid_delivery_count += 1
            permission_denied_delivery_count += 1
            continue
        except OSError:
            inspection_error_count += 1
            continue
        for output_dir in output_dirs:
            if output_dir.is_symlink() or not output_dir.is_dir():
                invalid_delivery_count += 1
                continue
            try:
                children = tuple(output_dir.iterdir())
            except PermissionError:
                invalid_delivery_count += 1
                permission_denied_delivery_count += 1
                continue
            except OSError:
                inspection_error_count += 1
                continue
            if not children:
                continue
            try:
                deliveries.append(
                    _read_delivery(
                        root=root,
                        case_root=case_root,
                        output_root=output_root,
                        output_dir=output_dir,
                        archive=archive,
                    )
                )
            except PermissionError:
                invalid_delivery_count += 1
                permission_denied_delivery_count += 1
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                KeyError,
                ElementTree.ParseError,
                BadZipFile,
                json.JSONDecodeError,
            ):
                invalid_delivery_count += 1

        try:
            docx_paths = _managed_docx_paths(output_root)
        except PermissionError:
            permission_denied_docx_count += 1
            continue
        except OSError:
            inspection_error_count += 1
            continue
        for path in docx_paths:
            try:
                _read_docx(path)
            except PermissionError:
                invalid_docx_paths.append(_relative_path(path, root))
                permission_denied_docx_count += 1
            except (
                OSError,
                ValueError,
                ElementTree.ParseError,
                BadZipFile,
            ):
                invalid_docx_paths.append(_relative_path(path, root))

    return {
        "case_archives": case_archives,
        "deliveries": deliveries,
        "invalid_docx_paths": invalid_docx_paths,
        "invalid_docx_count": len(invalid_docx_paths),
        "invalid_delivery_count": invalid_delivery_count,
        "permission_denied_archive_count": permission_denied_archive_count,
        "permission_denied_delivery_count": permission_denied_delivery_count,
        "permission_denied_docx_count": permission_denied_docx_count,
        "inspection_error_count": inspection_error_count,
    }


def _read_archive(path: Path, case_id: str, workspace: Path) -> dict[str, Any]:
    if (
        CASE_ID_PATTERN.fullmatch(case_id) is None
        or path.is_symlink()
        or not path.is_file()
        or not _is_within(path, workspace)
    ):
        raise ValueError("archive is not a regular in-workspace public file")
    markdown = path.read_text(encoding="utf-8")
    if not markdown.startswith("# 案情档案\n") or not markdown.endswith("\n"):
        raise ValueError("public archive title or trailing newline is missing")
    sections = re.findall(r"(?m)^## [^\r\n]+$", markdown)
    if sections != list(REQUIRED_ARCHIVE_SECTIONS):
        raise ValueError("public archive sections are incomplete or reordered")
    case_values = CASE_LINE_PATTERN.findall(markdown)
    revision_values = REVISION_LINE_PATTERN.findall(markdown)
    updated_values = UPDATED_LINE_PATTERN.findall(markdown)
    if case_values != [case_id] or len(revision_values) != 1 or len(updated_values) != 1:
        raise ValueError("public archive identity or revision metadata is invalid")
    revision = int(revision_values[0])
    if revision < 0:
        raise ValueError("public archive revision cannot be negative")
    datetime.fromisoformat(updated_values[0])
    record_ids = [match.group("record_id") for match in RECORD_ID_PATTERN.finditer(markdown)]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("public archive contains duplicate record ids")
    known_records = set(record_ids)
    current_archive = markdown.split("\n## 更新记录\n", 1)[0]
    for reference in RECORD_REFERENCE_PATTERN.findall(current_archive):
        if reference not in known_records:
            raise ValueError("public archive contains a dangling record reference")
    facts_marker = "\n## 事实\n"
    if facts_marker not in markdown:
        raise ValueError("public archive facts section is missing")
    facts_section = markdown.split(facts_marker, 1)[1]
    if "\n## " in facts_section:
        facts_section = facts_section.split("\n## ", 1)[0]
    return {
        "case_id": case_id,
        "archive_revision": revision,
        "archive_path": _relative_path(path, workspace),
        "archive_sha256": sha256(markdown.encode("utf-8")).hexdigest(),
        "confirmed_fact_summary_sha256": sha256(
            facts_section.strip().encode("utf-8")
        ).hexdigest(),
        "record_ids": record_ids,
    }


def _read_delivery(
    *,
    root: Path,
    case_root: Path,
    output_root: Path,
    output_dir: Path,
    archive: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if archive is None:
        raise ValueError("managed delivery has no valid same-case archive")
    if not _is_within(output_dir, output_root) or output_dir.name not in DOCUMENT_TITLES:
        raise ValueError("delivery directory is outside the public document set")
    manifests = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".json"
    ]
    if len(manifests) != 1:
        raise ValueError("delivery directory must have one machine manifest")
    manifest_path = manifests[0]
    if manifest_path.is_symlink() or not _is_within(manifest_path, output_dir):
        raise ValueError("manifest must be an ordinary in-directory file")
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(manifest, Mapping):
        raise ValueError("machine manifest must be an object")
    if (
        manifest.get("schema_version") != 1
        or isinstance(manifest.get("schema_version"), bool)
        or manifest.get("contract_version") != DOCUMENT_CONTRACT
        or manifest.get("operation") != DOCUMENT_OPERATION
    ):
        raise ValueError("machine manifest contract identity is invalid")

    case_id = case_root.name
    document_type = manifest.get("document_type")
    mode = manifest.get("mode")
    title = DOCUMENT_TITLES.get(document_type) if isinstance(document_type, str) else None
    revision = manifest.get("archive_revision")
    if (
        manifest.get("case_id") != case_id
        or archive.get("case_id") != case_id
        or document_type != output_dir.name
        or mode not in {"candidate", "external_final"}
        or manifest.get("title") != title
        or manifest.get("template_version") != TEMPLATE_VERSION
        or not _is_integer(revision)
    ):
        raise ValueError("manifest case, type, mode, title or revision is inconsistent")
    dependencies_verified = _candidate_freshness(
        manifest, archive, case_root / ARCHIVE_FILENAME
    )
    expected_state = "candidate_ready" if mode == "candidate" else "final_ready"
    if manifest.get("delivery_state") != expected_state:
        raise ValueError("delivery state does not match document mode")
    if (
        not _is_sha256(manifest.get("render_identity"))
        or not _is_sha256(manifest.get("content_digest"))
        or not isinstance(manifest.get("warnings"), list)
    ):
        raise ValueError("manifest identity, digest or warnings are invalid")

    summary = manifest.get("validation_summary")
    if (
        not isinstance(summary, Mapping)
        or summary.get("status") != "passed"
        or not isinstance(summary.get("checks"), list)
        or not summary["checks"]
        or not all(isinstance(item, str) and item.strip() for item in summary["checks"])
        or summary.get("placeholder_state") not in {"none", "declared"}
        or not _is_nonnegative_integer(summary.get("locked_binding_count"))
    ):
        raise ValueError("manifest validation summary is missing or incomplete")

    document = manifest.get("document")
    checklist = manifest.get("verification_checklist")
    machine_manifest = manifest.get("machine_manifest")
    if not all(isinstance(item, Mapping) for item in (document, checklist, machine_manifest)):
        raise ValueError("manifest file indexes are incomplete")
    document_name = _direct_name(document.get("filename"))
    checklist_name = _direct_name(checklist.get("filename"))
    manifest_name = _direct_name(machine_manifest.get("filename"))
    expected_document_name = (
        f"《{title}》.docx"
        if mode == "external_final"
        else f"《{title}》（候选草稿）.docx"
    )
    expected_checklist_name = f"《{title}》内部核验清单（仅供核对，请勿外发）.txt"
    expected_manifest_name = f"《{title}》机器交付清单.json"
    if (
        document_name != expected_document_name
        or checklist_name != expected_checklist_name
        or manifest_name != expected_manifest_name
        or manifest_name != manifest_path.name
    ):
        raise ValueError("manifest filenames do not match the public naming contract")
    if {path.name for path in output_dir.iterdir()} != {
        document_name,
        checklist_name,
        manifest_name,
    }:
        raise ValueError("managed output contains missing or extra files")

    document_path = output_dir / document_name
    checklist_path = output_dir / checklist_name
    for path in (document_path, checklist_path):
        if path.is_symlink() or not path.is_file() or not _is_within(path, output_dir):
            raise ValueError("delivery artifacts must be ordinary in-directory files")
    document_digest = _sha256_file(document_path)
    checklist_digest = _sha256_file(checklist_path)
    if (
        not _is_sha256(document.get("sha256"))
        or not _is_sha256(checklist.get("sha256"))
        or document.get("sha256") != document_digest
        or checklist.get("sha256") != checklist_digest
    ):
        raise ValueError("delivery artifact digest does not match the manifest")
    document_text = _read_docx(document_path, expected_title=title)
    _validate_checklist(
        checklist_path,
        filename=checklist_name,
        title=title,
        document_type=document_type,
        mode=mode,
        document_text=document_text,
    )
    return {
        "case_id": case_id,
        "document_type": document_type,
        "mode": mode,
        "archive_revision": revision,
        "current_archive_revision": archive.get("archive_revision"),
        "archive_dependencies_verified": dependencies_verified,
        "document_path": _relative_path(document_path, root),
        "checklist_path": _relative_path(checklist_path, root),
        "manifest_path": _relative_path(manifest_path, root),
        "case_archive_path": archive.get("archive_path"),
    }


def _candidate_freshness(manifest, archive, path: Path) -> bool:
    """Independently reconstruct the hash projection, without SUT imports."""
    generated = manifest["archive_revision"]
    current = archive["archive_revision"]
    if generated < 0 or generated > current:
        raise ValueError("manifest archive revision is invalid")
    if generated == current and "archive_dependencies" not in manifest:
        return False
    proof = manifest.get("archive_dependencies")
    if manifest["mode"] != "candidate" or not isinstance(proof, Mapping) or set(proof) != {
        "schema_version", "business_sha256", "next_step_sha256"
    } or type(proof.get("schema_version")) is not int or proof["schema_version"] != 1:
        raise ValueError("stale manifest lacks a candidate dependency proof")
    if not _is_sha256(proof["business_sha256"]) or not isinstance(proof["next_step_sha256"], Mapping):
        raise ValueError("candidate dependency hashes are invalid")
    markdown = path.read_text(encoding="utf-8")
    if sha256(markdown.encode("utf-8")).hexdigest() != archive["archive_sha256"]:
        raise ValueError("archive changed during dependency observation")
    chunks = re.split(r"(?m)^(## [^\r\n]+)$", markdown)
    if chunks[1::2] != list(REQUIRED_ARCHIVE_SECTIONS):
        raise ValueError("candidate dependency archive sections are invalid")
    business = [chunks[0].strip()]
    next_steps = ""
    for header, body in zip(chunks[1::2], chunks[2::2]):
        if header == "## 更新记录":
            continue
        if header == "## 下一步":
            next_steps = body.strip()
            continue
        if header == "## 案件元数据":
            body = "\n".join(line for line in body.splitlines()
                             if not line.startswith(("- 档案修订版本：", "- 最后更新：")))
        business.append((header + body).strip())
    business_text = "\n\n".join(business)
    if sha256(business_text.encode("utf-8")).hexdigest() != proof["business_sha256"]:
        raise ValueError("candidate business dependencies changed")
    matches = list(RECORD_ID_PATTERN.finditer(next_steps))
    records = {match.group("record_id"): next_steps[match.start():
        matches[i + 1].start() if i + 1 < len(matches) else len(next_steps)].strip()
        for i, match in enumerate(matches)}
    expected = proof["next_step_sha256"]
    for record_id, digest in expected.items():
        if not isinstance(record_id, str) or not re.fullmatch(r"N-\d{3,}", record_id) or not _is_sha256(digest):
            raise ValueError("candidate next-step dependency proof is invalid")
        body = records.get(record_id)
        if body is None or sha256(body.encode("utf-8")).hexdigest() != digest:
            raise ValueError("candidate referenced next step changed")
    needed = {ref for text in [business_text, *(records[key] for key in expected)]
              for ref in RECORD_REFERENCE_PATTERN.findall(text) if ref.startswith("N-")}
    if not needed.issubset(expected):
        raise ValueError("candidate next-step dependency closure is incomplete")
    return generated != current


def _validate_checklist(
    path: Path,
    *,
    filename: str,
    title: str,
    document_type: str,
    mode: str,
    document_text: str,
) -> None:
    expected_filename = f"《{title}》内部核验清单（仅供核对，请勿外发）.txt"
    if filename != expected_filename:
        raise ValueError("checklist filename does not match the public contract")
    text = path.read_text(encoding="utf-8")
    if "仅供核对，请勿外发" not in text:
        raise ValueError("checklist external warning is missing")
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        raise ValueError("user checklist must be plain text, not JSON")
    folded = text.casefold()
    if any(marker.casefold() in folded for marker in CHECKLIST_FORBIDDEN_MARKERS):
        raise ValueError("checklist contains internal protocol details")
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|\bcase-[0-9a-f]{24}\b)", text):
        raise ValueError("checklist contains a path or case id")
    if RECORD_REFERENCE_PATTERN.search(text) or "#" in text and "sha" in folded:
        raise ValueError("checklist contains an archive record id or digest")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [line[2:].strip() for line in lines if line.startswith("- ")]
    required_bullets = _expected_checklist_bullets(
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
        raise ValueError("checklist lacks type- and mode-specific public review items")
    if not document_text.strip() or not document_type or mode not in {"candidate", "external_final"}:
        raise ValueError("checklist cannot be validated without its public delivery context")


def _expected_checklist_bullets(
    document_type: str,
    mode: str,
    document_text: str,
) -> tuple[str, ...]:
    if document_type not in _CHECKLIST_OBJECTS:
        raise ValueError("checklist document type is unsupported")
    if mode not in {"candidate", "external_final"}:
        raise ValueError("checklist mode is unsupported")

    bullets = []
    for category, target in _CHECKLIST_OBJECTS[document_type]:
        if category == "主体与身份":
            detail = f"核对{target}所列名称、身份信息及称谓是否准确一致。"
        elif category == "事实或请求":
            detail = f"核对{target}与正文、已确认事实和对应依据是否一致。"
        elif category == "关键日期与程序条件":
            detail = f"核对{target}及对应程序条件、期限是否完整且适用。"
        else:
            detail = f"核对{target}、编号、页码和实际附件是否完整对应。"
        bullets.append(f"{category}：{detail}")

    if (
        document_type != "evidence_catalog"
        and _CHECKLIST_MONEY_PATTERN.search(document_text)
    ):
        bullets.append(
            "金额及计算依据：核对正文金额、计算结果、基数、期间、公式和合计是否一致。"
        )
    if mode == "external_final" and document_type != "evidence_catalog":
        role = _CHECKLIST_CLOSING_ROLES[document_type]
        bullets.append(
            f"落款角色与日期：核对{role}的签署身份、姓名与文书角色一致，"
            "并确认完整落款日期准确。"
        )
    return tuple(bullets)


def _read_docx(path: Path, *, expected_title: str | None = None) -> str:
    with ZipFile(path) as package:
        names = set(package.namelist())
        required = {
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/_rels/document.xml.rels",
        }
        if not required <= names:
            raise ValueError("DOCX package is missing public OOXML parts")
        if package.testzip() is not None:
            raise ValueError("DOCX ZIP package contains a corrupt member")
        content_types = ElementTree.fromstring(package.read("[Content_Types].xml"))
        if content_types.tag != f"{{{CONTENT_TYPES_NS}}}Types":
            raise ValueError("DOCX content types root is invalid")
        for override in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override"):
            part_name = (override.get("PartName") or "").lstrip("/")
            if not part_name or part_name not in names:
                raise ValueError("DOCX content type points to a missing part")
        for relationship_part in (name for name in names if name.endswith(".rels")):
            relationships = ElementTree.fromstring(package.read(relationship_part))
            if relationships.tag != f"{{{PACKAGE_RELS_NS}}}Relationships":
                raise ValueError("DOCX relationships root is invalid")
            for relationship in relationships.findall(
                f"{{{PACKAGE_RELS_NS}}}Relationship"
            ):
                target = relationship.get("Target")
                if not target or not relationship.get("Id") or not relationship.get("Type"):
                    raise ValueError("DOCX relationship is incomplete")
                if relationship.get("TargetMode", "").casefold() == "external":
                    continue
                target = target.split("#", 1)[0].replace("\\", "/").lstrip("/")
                base = (
                    relationship_part.split("/_rels/", 1)[0]
                    if "/_rels/" in relationship_part
                    else ""
                )
                resolved = posixpath.normpath(posixpath.join(base, target))
                if resolved.startswith("../") or resolved not in names:
                    raise ValueError("DOCX relationship target is missing or escapes package")
        for name in names:
            if name.endswith(".xml") and name != "[Content_Types].xml":
                ElementTree.fromstring(package.read(name))
        document = ElementTree.fromstring(package.read("word/document.xml"))
    if document.tag != f"{{{WORD_NS}}}document":
        raise ValueError("DOCX main document root is invalid")
    text = "\n".join(
        "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        for paragraph in document.iter(f"{{{WORD_NS}}}p")
    )
    if not text.strip():
        raise ValueError("DOCX visible text is empty")
    if expected_title is not None and expected_title not in text:
        raise ValueError("DOCX title does not match its manifest")
    return text


def _managed_docx_paths(output_root: Path) -> list[Path]:
    """Enumerate explicitly so an unreadable subtree cannot look complete."""

    result: list[Path] = []
    pending = [output_root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_symlink():
                continue
            if path.is_dir():
                pending.append(path)
            elif (
                path.suffix.casefold() == ".docx"
                and path.is_file()
                and _is_within(path, output_root)
            ):
                result.append(path)
    return result


def _direct_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        raise ValueError("manifest filename is not a direct child name")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("machine manifest contains a duplicate JSON key")
        result[key] = value
    return result


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonnegative_integer(value: object) -> bool:
    return _is_integer(value) and value >= 0


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
