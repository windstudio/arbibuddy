"""Typed public checklist requirements for document.render deliveries."""

from __future__ import annotations

import re


_DOCUMENT_OBJECTS = {
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
        (
            "证据引用或附件完整性",
            "表格逐项列明的证据名称、证据内容、证明目的、编号和页码",
        ),
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

_CLOSING_ROLES = {
    "employment_obligation_demand_letter": "劳动者或其授权经办人",
    "forced_termination_notice": "劳动者（解除通知人）",
    "arbitration_application": "申请人",
    "arbitration_defense": "答辩人",
    "company_deregistration_restriction_request_shanghai": "申请人",
    "property_preservation_application": "申请人",
    "enforcement_application": "申请执行人",
}

_MONEY_PATTERN = re.compile(
    r"(?<![0-9.])(?:(?:人民币|[￥¥])\s*)?"
    r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?\s*(?:万元|万?元|圆|人民币)"
)


def required_checklist_bullets(
    document_type: str,
    mode: str,
    document_text: str,
) -> tuple[str, ...]:
    """Return canonical, separately verifiable public checklist bullets."""

    if document_type not in _DOCUMENT_OBJECTS:
        raise ValueError("unsupported checklist document type")
    if mode not in {"candidate", "external_final"}:
        raise ValueError("unsupported checklist mode")

    bullets = []
    for category, target in _DOCUMENT_OBJECTS[document_type]:
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
        and isinstance(document_text, str)
        and _MONEY_PATTERN.search(document_text)
    ):
        bullets.append(
            "金额及计算依据：核对正文金额、计算结果、基数、期间、公式和合计是否一致。"
        )

    if mode == "external_final" and document_type != "evidence_catalog":
        role = _CLOSING_ROLES[document_type]
        bullets.append(
            f"落款角色与日期：核对{role}的签署身份、姓名与文书角色一致，"
            "并确认完整落款日期准确。"
        )

    return tuple(bullets)


__all__ = ["required_checklist_bullets"]
