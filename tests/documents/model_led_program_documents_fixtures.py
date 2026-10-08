from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import re

from scripts.case_archive import CaseArchive
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


DOCUMENTS = {
    "company_deregistration_restriction_request_shanghai": {
        "title": "限制公司注销申请书",
        "authority": "AUTH-001",
        "confirmation_action": "company_deregistration_restriction",
    },
    "property_preservation_application": {
        "title": "财产保全申请书",
        "authority": "AUTH-002",
        "confirmation_action": "property_preservation",
    },
    "enforcement_application": {
        "title": "强制执行申请书",
        "authority": "AUTH-003",
        "confirmation_action": "enforcement",
    },
}


def create_case(
    workspace: Path,
    *,
    jurisdiction: str = "上海市",
    authority_status: str = "已核验",
    confirmation_document_type: str | None = None,
) -> tuple[CaseArchive, str, int, str | None]:
    archive = CaseArchive(workspace)
    created = archive.create(
        {"jurisdiction": jurisdiction, "initial_goal": "准备程序文书"}
    )
    assert created["ok"], created
    case_id = str(created["result"]["case_id"])
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": 0,
            "change_summary": "记录三类程序文书所需的脱敏事实、主张、证据和现行依据",
            "changes": [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "申请人：张三；申请人姓名：张三；申请人身份证号：310000199001010000；"
                        "申请人联系地址：上海市示例路1号；申请人联系电话：13800000000；签名：张三。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "被申请人：上海示例公司；被申请人名称：上海示例公司；"
                        "被申请人统一社会信用代码：91310000MA0000000X；"
                        "被申请人住所：上海市示例路2号；被申请人法定代表人：李四。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "文书标题：限制公司注销申请书；文书标题：财产保全申请书；"
                        "文书标题：强制执行申请书。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "申请日期：2026年9月8日；适用地区：上海市；签名日期：2026年9月8日。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "仲裁受理信息：上海市浦东新区劳动人事争议仲裁委员会已于2026年8月1日受理，"
                        "案号为浦劳人仲（2026）办字第1234号；当前程序阶段：劳动仲裁已受理、裁决作出前。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "财产线索：银行账户户名上海示例公司、账号622200001234、开户行虚构银行上海分行；"
                        "担保方案：拟以保险公司诉讼财产保全责任保险保函提供担保；"
                        "错误保全责任：申请错误可能承担损害赔偿责任；"
                        "管辖依据：被申请人住所地及财产所在地在上海市浦东新区。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "执行依据：上海市浦东新区劳动人事争议仲裁委员会作出的仲裁裁决书；"
                        "执行依据类型：生效仲裁裁决；执行依据案号：浦劳人仲（2026）裁字第1234号；"
                        "执行依据生效状态：已生效；执行依据生效日期：2026年8月20日；"
                        "履行期限：2026年9月1日届满；履行状态：未履行；"
                        "申请执行时效状态：有效；申请执行时效依据：自履行期限届满起计算且未超过法定期间；"
                        "执行管辖依据：作出仲裁裁决的机构所在地及被执行人住所地。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "履行期限届满日期：2026年9月1日。",
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": (
                        "限制公司注销申请：请求受理仲裁机构结合案件情况核实注销风险，"
                        "并在职责和现行工作机制范围内视情协调；上海专项实务申请材料。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": "财产保全请求：请求依法保全被申请人价值120000.00元的财产。",
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": "强制执行请求：请求执行生效仲裁裁决尚未履行金额60000.00元。",
                },
                {
                    "operation": "append",
                    "record_type": "calculation",
                    "content_markdown": "财产保全确定性计算结果：120000.00元。",
                },
                {
                    "operation": "append",
                    "record_type": "calculation",
                    "content_markdown": "申请执行确定性计算结果：60000.00元。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": "财产线索证据｜银行账户线索｜证明被申请人可供保全或执行的财产线索。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": "仲裁受理材料｜仲裁受理通知书｜证明劳动争议已经受理。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": "生效仲裁裁决书｜生效仲裁裁决及送达证明｜证明执行依据已生效、履行期限已届满。",
                },
                {
                    "operation": "append",
                    "record_type": "authority",
                    "content_markdown": (
                        "核验事项：上海企业注销协同办理现行口径；"
                        f"核验结论：{authority_status}；适用地区：上海市；访问日期：2026年9月8日。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "authority",
                    "content_markdown": (
                        "核验事项：财产保全现行规则；"
                        f"核验结论：{authority_status}；适用地区：全国；访问日期：2026年9月8日。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "authority",
                    "content_markdown": (
                        "核验事项：强制执行现行规则；"
                        f"核验结论：{authority_status}；适用地区：全国；访问日期：2026年9月8日。"
                    ),
                },
            ],
        }
    )
    assert committed["ok"], committed
    revision = int(committed["result"]["revision"])
    confirmation_id: str | None = None
    if confirmation_document_type is not None:
        meta = DOCUMENTS[confirmation_document_type]
        action_scope = {
            "company_deregistration_restriction_request_shanghai": (
                "限制公司注销申请，申请人：张三，被申请人：上海示例公司，"
                "限制公司注销申请，仲裁受理材料，仲裁于2026年8月1日受理，2026年9月8日"
            ),
            "property_preservation_application": (
                "财产保全申请，申请人：张三，被申请人：上海示例公司，"
                "财产保全请求，财产线索证据，120000.00元，2026年9月8日"
            ),
            "enforcement_application": (
                "强制执行申请，申请执行人：张三，被执行人：上海示例公司，"
                "强制执行请求，生效仲裁裁决书，生效仲裁裁决及送达证明，"
                "60000.00元，执行依据于2026年8月20日生效，履行期限于2026年9月1日届满，2026年9月8日"
            ),
        }[confirmation_document_type]
        source_refs = {
            "company_deregistration_restriction_request_shanghai": (
                "F-001、F-002、F-003、F-004、F-005、CL-001、E-002、AUTH-001"
            ),
            "property_preservation_application": (
                "F-001、F-002、F-003、F-004、CL-002、CAL-001、E-001、AUTH-002"
            ),
            "enforcement_application": (
                "F-001、F-002、F-003、F-004、F-007、F-008、CL-003、CAL-002、E-003、AUTH-003"
            ),
        }[confirmation_document_type]
        confirmation = archive.commit(
            {
                "case_id": case_id,
                "expected_revision": revision,
                "change_summary": "记录程序文书外发定稿所需的实质知情确认",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "confirmation",
                        "content_markdown": (
                            f"action_type：{meta['confirmation_action']}\n"
                            f"action_scope：{action_scope}\n"
                            "risk_summary：已说明程序阶段、现行规则、证据不足和错误申请可能产生的后果。\n"
                            "alternatives_presented：先补强证据、暂缓高风险动作并寻求专业复核。\n"
                            "user_choice：我明确选择按已说明范围继续准备本程序文书。\n"
                            "confirmed_at：2026-09-08\n"
                            f"archive_revision：{revision + 1}\n"
                            f"source_refs：{source_refs}。"
                        ),
                    }
                ],
            }
        )
        assert confirmation["ok"], confirmation
        revision = int(confirmation["result"]["revision"])
        confirmation_id = str(confirmation["result"]["generated_record_ids"][0])
    return archive, case_id, revision, confirmation_id


def _common_request(case_id: str, revision: int, document_type: str, *, mode: str) -> dict[str, object]:
    if document_type == "company_deregistration_restriction_request_shanghai":
        sections = [
            {"section_id": "applicant", "heading": "申请人", "full_text": "申请人：张三。"},
            {"section_id": "respondent", "heading": "被申请人", "full_text": "被申请人：上海示例公司。"},
            {
                "section_id": "arbitration",
                "heading": "仲裁受理信息",
                "full_text": "上海市浦东新区劳动人事争议仲裁委员会已于2026年8月1日受理，案号为浦劳人仲（2026）办字第1234号。",
            },
            {
                "section_id": "request",
                "heading": "申请事项",
                "full_text": "请求受理仲裁机构结合案件情况核实注销风险，并在职责和现行工作机制范围内视情协调。",
            },
            {
                "section_id": "facts",
                "heading": "事实与理由",
                "full_text": "本案为上海劳动争议，仲裁已经受理，现存在企业注销风险。本材料保持上海专项实务定位，不替代简易注销异议或法院财产保全。",
            },
            {"section_id": "attachments", "heading": "附件", "full_text": "仲裁受理通知书、注销风险材料。"},
            {"section_id": "closing", "heading": "落款", "full_text": "申请人：张三\n2026年9月8日"},
        ]
        bindings = [
            {"kind": "document_heading", "archive_record_ref": "F-003", "rendered_value": "限制公司注销申请书"},
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "被申请人：上海示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "限制公司注销申请"},
            {"kind": "evidence", "archive_record_ref": "E-002", "rendered_value": "仲裁受理材料｜仲裁受理通知书｜证明劳动争议已经受理"},
            {"kind": "date", "archive_record_ref": "F-004", "rendered_value": "2026年9月8日"},
            {"kind": "date", "archive_record_ref": "F-005", "rendered_value": "2026年8月1日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
        ]
        title = "限制公司注销申请书"
    elif document_type == "property_preservation_application":
        sections = [
            {"section_id": "applicant", "heading": "申请人", "full_text": "申请人：张三。"},
            {"section_id": "respondent", "heading": "被申请人", "full_text": "被申请人：上海示例公司。"},
            {"section_id": "request", "heading": "保全请求", "full_text": "请求依法保全被申请人价值120000.00元的财产。"},
            {
                "section_id": "facts",
                "heading": "事实与理由",
                "full_text": "当前处于劳动仲裁已受理、裁决作出前阶段，存在财产转移紧急风险；错误保全可能承担损害赔偿责任。",
            },
            {"section_id": "property", "heading": "财产线索", "full_text": "银行账户户名上海示例公司、账号622200001234、开户行虚构银行上海分行。"},
            {"section_id": "guarantee", "heading": "担保", "full_text": "拟以保险公司诉讼财产保全责任保险保函提供担保，具体以法院审查为准。"},
            {"section_id": "closing", "heading": "落款", "full_text": "申请人：张三\n2026年9月8日"},
        ]
        bindings = [
            {"kind": "document_heading", "archive_record_ref": "F-003", "rendered_value": "财产保全申请书"},
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "被申请人：上海示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-002", "rendered_value": "财产保全请求"},
            {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "120000.00元"},
            {"kind": "evidence", "archive_record_ref": "E-001", "rendered_value": "财产线索证据｜银行账户线索｜证明被申请人可供保全或执行的财产线索"},
            {"kind": "date", "archive_record_ref": "F-004", "rendered_value": "2026年9月8日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请人：张三"},
        ]
        title = "财产保全申请书"
    else:
        sections = [
            {"section_id": "applicant", "heading": "申请执行人", "full_text": "申请执行人：张三。"},
            {"section_id": "respondent", "heading": "被执行人", "full_text": "被执行人：上海示例公司。"},
            {"section_id": "request", "heading": "执行请求", "full_text": "请求执行生效仲裁裁决尚未履行的60000.00元。"},
            {
                "section_id": "facts",
                "heading": "事实与理由",
                "full_text": "执行依据为生效仲裁裁决，已于2026年8月20日生效，履行期限已于2026年9月1日届满，当前未履行。申请执行时效有效，执行法院具有管辖权，依据为作出仲裁裁决的机构所在地及被执行人住所地。",
            },
            {"section_id": "property", "heading": "财产线索", "full_text": "银行账户户名上海示例公司、账号622200001234、开户行虚构银行上海分行。"},
            {"section_id": "attachments", "heading": "附件", "full_text": "生效仲裁裁决及送达证明。"},
            {"section_id": "closing", "heading": "落款", "full_text": "申请执行人：张三\n2026年9月8日"},
        ]
        bindings = [
            {"kind": "document_heading", "archive_record_ref": "F-003", "rendered_value": "强制执行申请书"},
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "申请执行人：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "被执行人：上海示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-003", "rendered_value": "强制执行请求"},
            {"kind": "amount", "archive_record_ref": "CAL-002", "rendered_value": "60000.00元"},
            {"kind": "evidence", "archive_record_ref": "E-003", "rendered_value": "生效仲裁裁决书｜生效仲裁裁决及送达证明｜证明执行依据已生效、履行期限已届满"},
            {"kind": "attachment", "archive_record_ref": "E-003", "rendered_value": "生效仲裁裁决及送达证明"},
            {"kind": "date", "archive_record_ref": "F-004", "rendered_value": "2026年9月8日"},
            {"kind": "date", "archive_record_ref": "F-007", "rendered_value": "2026年8月20日"},
            {"kind": "date", "archive_record_ref": "F-008", "rendered_value": "2026年9月1日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "申请执行人：张三"},
        ]
        title = "强制执行申请书"
    request = {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": document_type,
        "template_version": "1.0.0",
        "mode": mode,
        "title": title,
        "sections": sections,
        "locked_bindings": bindings,
        "placeholders": [],
        "confirmation_refs": [],
        "authority_refs": [],
        "additional_checklist_items": [],
    }
    return with_occurrence_bindings(request)


def request_for(
    case_id: str,
    revision: int,
    document_type: str,
    *,
    mode: str = "external_final",
    confirmation_id: str | None = None,
) -> dict[str, object]:
    request = _common_request(case_id, revision, document_type, mode=mode)
    if confirmation_id:
        request["confirmation_refs"] = [confirmation_id] if confirmation_id else []
        request["authority_refs"] = (
            [DOCUMENTS[document_type]["authority"]] if confirmation_id else []
        )
    return request


def visible_text(path: Path) -> str:
    with ZipFile(path) as package:
        xml = package.read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml))


__all__ = ["DOCUMENTS", "create_case", "request_for", "visible_text"]
