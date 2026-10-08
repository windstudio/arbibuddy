"""Hand-built public-contract delivery fixtures for Agent Eval tests.

This deliberately does not import CaseArchive, document.render, DOCX builders,
or production receipt validators. The archive, manifest, checklist, hashes,
filenames and OOXML package are authored here from their public contracts.
"""

from __future__ import annotations

from hashlib import sha256
from html import escape
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


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
DOCUMENT_BODY_TEXT = {
    "employment_obligation_demand_letter": "收件人\n劳动关系说明\n具体义务\n履行期限\n沟通与保留权利",
    "forced_termination_notice": "收件人\n解除意思表示\n解除理由\n结算与手续\n落款",
    "arbitration_application": "申请人：张三\n被申请人：示例公司\n仲裁请求：支付工资差额6000.00元\n事实与理由\n申请人：张三\n2026年9月12日",
    "arbitration_defense": "答辩人：张三\n被答辩人：示例公司\n答辩意见\n落款",
    "evidence_catalog": "编号 证据名称 证据内容 证明目的 页码\n1 工资流水 银行流水 证明工资差额 1",
    "company_deregistration_restriction_request_shanghai": "申请人\n被申请人\n仲裁受理信息\n申请事项\n事实与理由\n附件\n落款",
    "property_preservation_application": "申请人\n被申请人\n保全请求：保全财产10000元\n事实与理由\n财产线索\n担保\n落款",
    "enforcement_application": "申请执行人\n被执行人\n执行请求：支付8000元\n事实与理由\n财产线索\n附件\n落款",
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
CASE_A = "case-0123456789abcdef01234567"
CASE_B = "case-1123456789abcdef01234567"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_archive(workspace: Path, case_id: str, revision: int) -> Path:
    case_root = workspace / ".arbibuddy" / "cases" / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    archive = case_root / "案情档案.md"
    timestamp = "2026-09-12T00:00:00+08:00"
    archive.write_text(
        "\n".join(
            (
                "# 案情档案",
                "",
                "## 案件元数据",
                "",
                f"- 案件编号：{case_id}",
                f"- 档案修订版本：{revision}",
                f"- 创建时间：{timestamp}",
                f"- 最后更新：{timestamp}",
                "- 案件标签：仲裁申请测试",
                "- 适用地区：上海",
                "- 初始目标：准备文书",
                "",
                "## 事实",
                "",
                "### 用户陈述",
                "",
                "### [F-001] 申请人",
                "申请人：张三。",
                "",
                "### [F-002] 被申请人",
                "被申请人：示例公司。",
                "",
                "### [F-003] 申请日期",
                "申请日期：2026年9月7日。",
                "",
                "### [F-004] 文书标题",
                "文书标题：劳动人事争议仲裁申请书。",
                "",
                "## 主张与权益",
                "",
                "### [CL-001] 工资差额",
                "仲裁请求：支付工资差额。",
                "",
                "## 证据材料",
                "",
                "### [E-001] 工资流水",
                "证据名称：工资流水；证明目的：证明工资差额。",
                "",
                "## 分析与假设",
                "",
                "暂无记录。",
                "",
                "## 计算结果",
                "",
                "### [CAL-001] 工资差额计算",
                "确定性计算结果：6000.00元。",
                "",
                "## 法律核验",
                "",
                "暂无记录。",
                "",
                "## 风险与确认",
                "",
                "暂无记录。",
                "",
                "## 下一步",
                "",
                "复核文书内容。",
                "",
                "## 更新记录",
                "",
                f"- {timestamp}：建立测试档案。",
                "",
            )
        ),
        encoding="utf-8",
    )
    return archive


def write_public_archive(workspace: Path, case_id: str, revision: int) -> Path:
    """Write the public Markdown archive shape without a production reader."""
    return _write_archive(workspace, case_id, revision)


def _manual_docx_bytes(title: str, body_text: str) -> bytes:
    w = _WORD_NS
    body_paragraphs = "".join(
        '<w:p><w:r><w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(paragraph)}</w:t></w:r></w:p>'
        for paragraph in body_text.splitlines()
    )
    document_xml = (
        f'<w:document xmlns:w="{w}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<w:body><w:p><w:pPr><w:pStyle w:val=\"title\"/><w:keepNext/>"
        "<w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr></w:pPr>"
        f'<w:r><w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr><w:t>{escape(title)}</w:t></w:r>'
        f"</w:p>{body_paragraphs}<w:sectPr><w:footerReference w:type=\"default\" r:id=\"rIdFooter\"/>"
        "<w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" "
        "w:bottom=\"1440\" w:left=\"1440\" w:header=\"720\" w:footer=\"720\"/></w:sectPr>"
        "</w:body></w:document>"
    )
    styles_xml = (
        f'<w:styles xmlns:w="{w}"><w:docDefaults><w:pPrDefault><w:pPr><w:widowControl/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:styleId="title"><w:name w:val="ArbiBuddy Legal Title"/>'
        '<w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="body"><w:name w:val="ArbiBuddy Legal Body"/>'
        '<w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="signature"><w:name w:val="ArbiBuddy Legal Signature"/>'
        '<w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr></w:style></w:styles>'
    )
    numbering_xml = (
        f'<w:numbering xmlns:w="{w}"><w:abstractNum w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl>'
        '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    footer_xml = (
        f'<w:ftr xmlns:w="{w}"><w:p><w:r><w:rPr><w:rFonts w:ascii="Times New Roman" '
        'w:eastAsia="宋体"/><w:sz w:val="18"/></w:rPr><w:t>1 / 1</w:t></w:r>'
        '<w:r><w:fldSimple w:instr="PAGE"/></w:r><w:r><w:fldSimple w:instr="NUMPAGES"/></w:r>'
        "</w:p></w:ftr>"
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        "</Types>"
    )
    package_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        "</Relationships>"
    )
    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": package_rels,
        "word/document.xml": document_xml,
        "word/styles.xml": styles_xml,
        "word/numbering.xml": numbering_xml,
        "word/footer1.xml": footer_xml,
        "word/_rels/document.xml.rels": document_rels,
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for name, content in parts.items():
            package.writestr(name, content.encode("utf-8"))
    return output.getvalue()


def _fixture_checklist_bullets(
    document_type: str, mode: str, body_text: str
) -> tuple[str, ...]:
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
    if document_type != "evidence_catalog" and "元" in body_text:
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


def write_public_contract_delivery(
    workspace: Path,
    *,
    case_id: str = CASE_A,
    document_type: str = "arbitration_application",
    mode: str = "external_final",
    manifest_case_id: str | None = None,
    archive_revision: int = 3,
    manifest_revision: int | None = None,
) -> tuple[str, dict[str, Path]]:
    _write_archive(workspace, case_id, archive_revision)
    title = DOCUMENT_TITLES[document_type]
    output_dir = (
        workspace / ".arbibuddy" / "cases" / case_id / "output" / document_type
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    document_name = (
        f"《{title}》.docx"
        if mode == "external_final"
        else f"《{title}》（候选草稿）.docx"
    )
    checklist_name = f"《{title}》内部核验清单（仅供核对，请勿外发）.txt"
    manifest_name = f"《{title}》机器交付清单.json"
    document_path = output_dir / document_name
    body_text = DOCUMENT_BODY_TEXT[document_type]
    document_path.write_bytes(_manual_docx_bytes(title, body_text))
    checklist_path = output_dir / checklist_name
    if document_type == "evidence_catalog":
        preview = "提交或发送前请在 Word/WPS 中预览证据表格、名称、证明目的、页码和分页。"
    elif mode == "candidate":
        preview = "请在 Word/WPS 中预览候选稿字段、正文层级和分页。"
    else:
        preview = "提交或发送前请在 Word/WPS 中预览字段、金额、分页和落款。"
    checklist_lines = [
        f"《{title}》提交前核验清单",
        "",
        "本清单仅供核对，请勿外发；它不属于正式文书内容。",
        "",
        "## 一、文书状态",
        "- 候选草稿，补全后再重新生成可外发定稿"
        if mode == "candidate"
        else "- 外发定稿，仍请在提交前完成人工核对",
        "",
        "## 二、请核对的内容",
        *[
            "- " + item
            for item in _fixture_checklist_bullets(document_type, mode, body_text)
        ],
        "- " + preview,
        "- 本清单不是正式文书正文，不得与正式文书一并提交或发送。",
        "",
    ]
    checklist_path.write_text("\n".join(checklist_lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "contract_version": "document.render-v1",
        "operation": "document.render",
        "render_identity": "a" * 64,
        "content_digest": "b" * 64,
        "delivery_state": "candidate_ready" if mode == "candidate" else "final_ready",
        "case_id": manifest_case_id or case_id,
        "archive_revision": (
            archive_revision if manifest_revision is None else manifest_revision
        ),
        "document_type": document_type,
        "template_version": "1.0.0",
        "mode": mode,
        "title": title,
        "warnings": [],
        "document": {
            "filename": document_name,
            "sha256": sha256(document_path.read_bytes()).hexdigest(),
        },
        "verification_checklist": {
            "filename": checklist_name,
            "sha256": sha256(checklist_path.read_bytes()).hexdigest(),
        },
        "machine_manifest": {"filename": manifest_name},
        "validation_summary": {
            "status": "passed",
            "checks": ["公开契约 DOCX/OOXML 样本有效"],
            "placeholder_state": "none",
            "locked_binding_count": 1,
        },
    }
    manifest_path = output_dir / manifest_name
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return case_id, {
        "document": document_path,
        "checklist": checklist_path,
        "manifest": manifest_path,
    }
