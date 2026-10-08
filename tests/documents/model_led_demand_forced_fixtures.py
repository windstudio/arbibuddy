from __future__ import annotations

from pathlib import Path
import re
from zipfile import ZipFile

from scripts.case_archive import CaseArchive
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


def create_case(
    workspace: Path,
    *,
    authority_status: str = "已核验",
    with_confirmation: bool = False,
) -> tuple[CaseArchive, str, int, str | None]:
    archive = CaseArchive(workspace)
    created = archive.create(
        {"jurisdiction": "上海", "initial_goal": "准备催告或解除文书"}
    )
    assert created["ok"], created
    case_id = str(created["result"]["case_id"])
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": 0,
            "change_summary": "记录两类文书所需的脱敏事实、主张、金额、证据和法源",
            "changes": [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "劳动者姓名：张三。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "用人单位名称：示例公司；用人单位地址：上海市示例路2号。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "催告函文书日期：2026年9月8日；通知日期：2026年9月8日。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "解除生效日期：2026年9月20日。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "劳动用工义务催告函标题：劳动用工义务催告函；"
                        "被迫解除通知书标题：被迫解除劳动合同通知书。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "劳动关系状态：存续；送达方式：EMS及电子邮件。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "催告履行期限：2026年9月15日。",
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": (
                        "具体义务/解除理由：支付2026年8月工资差额；"
                        "未及时足额支付劳动报酬。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "calculation",
                    "content_markdown": "确定性计算结果：6000.00元。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": "欠薪流水｜银行流水｜证明欠付劳动报酬和解除理由。",
                },
                {
                    "operation": "append",
                    "record_type": "authority",
                    "content_markdown": (
                        "核验事项：劳动者解除相关现行规则；"
                        f"核验结论：{authority_status}；适用地区：全国；"
                        "访问日期：2026年9月8日。"
                    ),
                },
            ],
        }
    )
    assert committed["ok"], committed
    revision = int(committed["result"]["revision"])
    confirmation_id: str | None = None
    if with_confirmation:
        confirmation = archive.commit(
            {
                "case_id": case_id,
                "expected_revision": revision,
                "change_summary": "记录计划被迫解除通知的实质知情确认",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "confirmation",
                        "content_markdown": (
                            "高风险确认标识：planned_termination\n"
                            "action_type：planned_termination\n"
                            "action_scope：按当前事实准备被迫解除通知书，劳动者张三向示例公司发出，理由为未及时足额支付劳动报酬，使用欠薪流水，通知日期为2026年9月8日，解除生效日期为2026年9月20日，不包含自动发送或提交。\n"
                            "risk_summary：解除理由、日期、程序、送达、时效和证据不足可能影响后果。\n"
                            "alternatives_presented：先催告履行、补强证据、暂缓解除并保留历史索赔。\n"
                            "user_choice：我明确选择按已说明范围继续准备被迫解除通知书。\n"
                            "confirmed_at：2026-09-08\n"
                            f"archive_revision：{revision + 1}\n"
                            "source_refs：F-001、F-002、F-003、F-004、CL-001、E-001、AUTH-001。"
                        ),
                    }
                ],
            }
        )
        assert confirmation["ok"], confirmation
        revision = int(confirmation["result"]["revision"])
        confirmation_id = str(confirmation["result"]["generated_record_ids"][0])
    return archive, case_id, revision, confirmation_id


def demand_request(
    case_id: str,
    revision: int,
    *,
    mode: str = "external_final",
    authority_refs: list[str] | None = None,
) -> dict[str, object]:
    request = {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "employment_obligation_demand_letter",
        "template_version": "1.0.0",
        "mode": mode,
        "title": "劳动用工义务催告函",
        "sections": [
            {"section_id": "recipient", "heading": "收件人", "full_text": "致：示例公司"},
            {
                "section_id": "relationship",
                "heading": "劳动关系说明",
                "full_text": "本人张三与贵单位的劳动关系目前处于存续期间。",
            },
            {
                "section_id": "obligation",
                "heading": "具体义务",
                "full_text": "请支付2026年8月工资差额6000.00元。",
            },
            {
                "section_id": "deadline",
                "heading": "履行期限",
                "full_text": "请于2026年9月15日前履行上述义务。",
            },
            {
                "section_id": "reservation",
                "heading": "沟通与保留权利",
                "full_text": "本函用于履行催告和沟通，不构成解除劳动合同通知。\n通知人：张三\n2026年9月8日",
            },
        ],
        "locked_bindings": [
            {"kind": "document_heading", "archive_record_ref": "F-005", "rendered_value": "劳动用工义务催告函"},
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "劳动者：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "用人单位：示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "支付2026年8月工资差额"},
            {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "6000.00元"},
            {"kind": "date", "archive_record_ref": "F-003", "rendered_value": "2026年9月8日"},
            {"kind": "date", "archive_record_ref": "F-007", "rendered_value": "2026年9月15日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "通知人：张三"},
        ],
        "placeholders": [],
        "confirmation_refs": [],
        "authority_refs": authority_refs or [],
    }
    return with_occurrence_bindings(request)


def forced_request(
    case_id: str,
    revision: int,
    *,
    mode: str = "external_final",
    confirmation_refs: list[str] | None = None,
    authority_refs: list[str] | None = None,
) -> dict[str, object]:
    request = {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "forced_termination_notice",
        "template_version": "1.0.0",
        "mode": mode,
        "title": "被迫解除劳动合同通知书",
        "sections": [
            {"section_id": "recipient", "heading": "收件人", "full_text": "致：示例公司"},
            {
                "section_id": "declaration",
                "heading": "解除意思表示",
                "full_text": "本人现依据相关规定通知贵单位解除劳动合同，解除生效日期为2026年9月20日。",
            },
            {
                "section_id": "reasons",
                "heading": "解除理由",
                "full_text": "因贵单位未及时足额支付劳动报酬，现依据已经核对的欠薪事实和相关规则提出解除理由。",
            },
            {
                "section_id": "settlement",
                "heading": "结算与手续",
                "full_text": "请依法结算工资及经济补偿6000.00元，并办理社会保险转移和出具解除证明。",
            },
            {
                "section_id": "closing",
                "heading": "落款",
                "full_text": "通知人：张三\n2026年9月8日",
            },
        ],
        "locked_bindings": [
            {"kind": "document_heading", "archive_record_ref": "F-005", "rendered_value": "被迫解除劳动合同通知书"},
            {"kind": "party", "archive_record_ref": "F-001", "rendered_value": "劳动者：张三"},
            {"kind": "party", "archive_record_ref": "F-002", "rendered_value": "用人单位：示例公司"},
            {"kind": "claim", "archive_record_ref": "CL-001", "rendered_value": "未及时足额支付劳动报酬"},
            {"kind": "evidence", "archive_record_ref": "E-001", "rendered_value": "欠薪流水｜银行流水｜证明欠付劳动报酬和解除理由"},
            {"kind": "amount", "archive_record_ref": "CAL-001", "rendered_value": "6000.00元"},
            {"kind": "date", "archive_record_ref": "F-003", "rendered_value": "2026年9月8日"},
            {"kind": "date", "archive_record_ref": "F-004", "rendered_value": "2026年9月20日"},
            {"kind": "signature", "archive_record_ref": "F-001", "rendered_value": "通知人：张三"},
        ],
        "placeholders": [],
        "confirmation_refs": confirmation_refs or [],
        "authority_refs": authority_refs or [],
    }
    return with_occurrence_bindings(request)


def visible_text(path: Path) -> str:
    with ZipFile(path) as package:
        xml = package.read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml))


__all__ = ["create_case", "demand_request", "forced_request", "visible_text"]
