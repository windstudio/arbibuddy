"""Declarative brief contract and layout; the model owns all case narrative."""

from decimal import Decimal
import re

DOCUMENT_TYPE = "lawyer_case_brief"
TEMPLATE_ID = "lawyer-case-brief"
TITLE = "律师阅案简报"
OVERVIEW = "案情概要"
TIMELINE = "共用时间线"
CONTRACT = {
    "title": TITLE,
    "sections": [OVERVIEW],
    "optional_sections": "默认一页，仅案情概要。只有去重后仍放不下必要经过、材料或分歧时才续页，从共用时间线起，再按争议事项分节。",
    "drafting": "当前案件绑定且咨询目的可辨即可起草；不要求权益扫描、最终补充或摘要确认。缺口留作面谈问题。",
    "overview": "首页按实际信息范围写案情信息截至日期（不详则如实说明）、咨询目的、主体关系、关键经过、当前阶段、主要分歧、优先面谈问题；临近安排注明日期及来源。",
    "sources": "关键陈述就近用材料名称、日期、出具人或聊天双方说明来源，区分已阅材料与当事人转述；相反材料紧跟有关陈述。内部绑定不进入正文。",
    "privacy": "保留公司全称和人员角色；默认省略证件号、住址、卡号和私人手机号；隐名只调整交付稿。",
    "amounts": "逐笔保留事项、期间、含义及出处；当事人估计、材料记载和已提出请求用 fact_amount 绑定原 F/E/CL 记录，成功计算用 amount/CAL；不重复合计，不补猜事实。",
    "external_final": "一次交付可审阅、编辑并自行转交的 Word；不要求摘要确认、高风险动作确认、落款或另行定稿。未决问题可留正文，事实来源与完整日期/金额仍需绑定。",
    "presentation": "只展示 Word 简报，内部核验清单保留受管校验但不作为用户附件。",
}


def scaled_source_amount_matches(body, expected):
    """Recognise an existing reported yuan value, without calculating a claim."""
    pattern = r"(?<![0-9.])([+\-−﹣－＋负負]?)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(万|亿)\s*元"
    return any(
        Decimal(number.replace(",", ""))
        * (-1 if sign in {"-", "−", "﹣", "－", "负", "負"} else 1)
        * (Decimal(10000) if unit == "万" else Decimal(100000000)) == expected
        for sign, number, unit in re.findall(pattern, body)
    )


def configure_layout(document, builder):
    """Use a compact, readable first page without changing legal templates."""
    from docx.shared import Cm, Pt

    for section in document.sections:
        section.top_margin = section.bottom_margin = Cm(2)
        section.left_margin = section.right_margin = Cm(2.2)
    body = document.styles["ArbiBuddy Legal Body"]
    builder._set_style_font(body, name="仿宋", size=11)
    body.paragraph_format.line_spacing = 1.15
    body.paragraph_format.space_after = Pt(4)
    builder._set_style_font(document.styles["ArbiBuddy Legal Title"], name="黑体", size=18)


def add_section(document, section, builder):
    builder._add_heading(document, section["heading"])
    for line in section["full_text"].splitlines():
        if line.strip():
            paragraph = document.add_paragraph(style="ArbiBuddy Legal Body")
            paragraph.add_run(line.strip())
