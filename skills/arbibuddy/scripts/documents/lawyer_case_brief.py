"""Declarative brief contract and layout; the model owns all case narrative."""

from decimal import Decimal
from itertools import groupby
import re

DOCUMENT_TYPE = "lawyer_case_brief"
TEMPLATE_ID = "lawyer-case-brief"
TITLE = "案情简报"
SUBTITLE = "供律师咨询参考"
LEGACY_TITLE = "律师阅案简报"
METADATA = "案情信息"
CUTOFF = "案情信息截至"
OVERVIEW = "案件概要"
NOTE = "编制说明"
_COMMON = (OVERVIEW, "基本案情", "主要争议事项", "争议处理进展")
_OBJECTIVES = "当事人诉求"
_MATERIALS = "证据及材料情况"
_GAPS = "尚待明确的事项"
_QUESTIONS = "拟与律师讨论的问题"
_COMBINED = {
    "证据及待明确事项": (_MATERIALS, _GAPS),
    "当事人诉求及咨询问题": (_OBJECTIVES, _QUESTIONS),
}
_FULL = (*_COMMON, _OBJECTIVES, _MATERIALS, _GAPS, _QUESTIONS)
_COMPACT = (*_COMMON, "证据及待明确事项", _MATERIALS, _GAPS,
            "当事人诉求及咨询问题", _OBJECTIVES, _QUESTIONS)
_NUMERALS = "一二三四五六七八九"
_SUBHEADING = re.compile(r"### +(.+)")
_TABLE_RULE = re.compile(r":?-{3,}:?")

CONTRACT = {
    "title": TITLE,
    "subtitle": SUBTITLE,
    "sections": [METADATA, OVERVIEW, NOTE],
    "full_chapter_order": list(_FULL),
    "compact_chapter_order": "、".join((*_COMMON, *_COMBINED)),
    "compact_combined_subheadings": _COMBINED,
    "format": "section.heading 写未编号的一级标题。案情信息首区块写当事人、案件类型、案情信息截至，编制说明末区块不编号。full_text 用独立的 ### 标记未编号二级标题，下一行写正文。可用普通段落、1. 编号分项或带表头及分隔行的 2—4 列 Markdown 表格。工具连续编号、自然分页。按案情省略无有效内容的章节，合并章节必须保留对应二级标题。",
    "drafting": "当前案件绑定且咨询目的可辨即可起草。多项独立议题伴跨期事件或已知程序安排用完整结构，列关键事件时间线及分项二级标题。单一争议或日期稀疏时用精简结构，以正文简述先后。概要照录开庭日期及来源，已过仍保留。基本案情写主体与劳动条件，估算写争议事项，正式请求与结果写程序进展，当前诉求另列。其余章节简短引用。工资只知月份时在待明确事项写明所属年份不详。法律判断写成咨询问题。见 references/model-led/lawyer-case-brief.md。",
    "sources": "只绑定有来源的信息覆盖日。新增记录用「案情信息截至：日期」并保留来源；无此来源写「案情信息截至：不详」。全文只描述当事人提及或实际收到的材料状态。未发给本次整理者写「本次未收到、未阅」。抄录的裁决或开庭安排就近标作当事人抄录，编制说明保留未独立核验状态。内部绑定留在请求。",
    "privacy": "首区块默认使用档案中的当事人姓名或已确认称谓，正文以角色指代，保留公司全称。省略证件号、住址、卡号和私人手机号。",
    "editing": "以 edit-context 返回的正文逐处精确替换用户指定的值，包括页首、正文及表格。其余文字照原稿，提交完整绑定。隐名沿用原档案 revision。事实更正的 replace 正文只写有效新值及核实来源，原值记在 reason，再按新 revision 交付。",
    "amounts": "逐笔保留事项、期间、含义及出处；当事人估计、材料记载和已提出请求用 fact_amount 绑定原 F/E/CL 记录，成功计算用 amount/CAL；不重复合计，不补猜事实。",
    "external_final": "一次交付可审阅、编辑并自行转交的 Word；不要求摘要确认、高风险动作确认、落款或另行定稿。未决问题可留正文，事实来源与完整日期/金额仍需绑定。",
    "presentation": "只展示 Word 简报，内部核验清单保留受管校验但不作为用户附件。",
}


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def structure_errors(sections):
    """Check the brief's presentation contract without interpreting case facts."""
    headings = [section["heading"] for section in sections]
    chapters = headings[1:-1]
    if (headings[:2] != [METADATA, OVERVIEW] or headings[-1:] != [NOTE]
            or not any(chapters == [heading for heading in order if heading in chapters]
                       for order in (_FULL, _COMPACT))):
        yield "sections", "按简报章节顺序提交，首两区块为案情信息、案件概要，末区块为编制说明"
        return
    for combined, children in _COMBINED.items():
        if combined in chapters and any(child in chapters for child in children):
            yield "sections", f"使用{combined}时省去已合并的独立章节"
    for index, section in enumerate(sections):
        heading = section["heading"]
        lines = _lines(section["full_text"])
        path = f"sections[{index}].full_text"
        if heading == METADATA and any(
            not any(line.startswith(label + "：") and line[len(label) + 1:].strip() for line in lines)
            for label in ("当事人", "案件类型", CUTOFF)
        ):
            yield path, "案情信息须各列一行当事人、案件类型和案情信息截至，日期未知时如实注明"
        subheadings = []
        for offset, line in enumerate(lines):
            if not line.startswith("###"):
                continue
            match = _SUBHEADING.fullmatch(line)
            if (not match or heading in (METADATA, OVERVIEW, NOTE)
                    or offset + 1 == len(lines) or lines[offset + 1].startswith("###")):
                yield path, "二级标题用 ### 加空格及未编号标题，且紧随实质正文"
                continue
            subheadings.append(match[1])
        if len(subheadings) != len(set(subheadings)):
            yield path, "同一章节的二级标题不得重复"
        if heading in _COMBINED and tuple(subheadings) != _COMBINED[heading]:
            yield path, f"合并章节须依次保留{'、'.join(_COMBINED[heading])}二级标题"


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
    """Apply brief-only heading and body styles."""
    from docx.shared import Cm, Pt, RGBColor

    for section in document.sections:
        section.top_margin = section.bottom_margin = Cm(2)
        section.left_margin = section.right_margin = Cm(2.2)
    body = document.styles["ArbiBuddy Legal Body"]
    builder._set_style_font(body, name="仿宋", size=11)
    body.paragraph_format.line_spacing = 1.15
    body.paragraph_format.space_after = Pt(4)
    builder._set_style_font(document.styles["ArbiBuddy Legal Title"], name="黑体", size=18)
    for level, size, space in ((1, 14, 9), (2, 12, 6)):
        style = document.styles[f"Heading {level}"]
        builder._set_style_font(style, name="黑体", size=size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.bold = level == 1
        style.paragraph_format.space_before = Pt(space)
        style.paragraph_format.space_after = Pt(3)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.keep_together = True


def add_subtitle(document, builder):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    paragraph = document.paragraphs[-1]
    paragraph.style = "ArbiBuddy Legal Body"
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(10)
    builder._set_run_font(paragraph.add_run(SUBTITLE), name="仿宋", size=11)


def _add_table(document, lines, builder):
    """Render a small Markdown table, falling back to text for ordinary pipe lines."""
    rows = [[cell.strip() for cell in line[1:-1].split("|")] for line in lines]
    columns = len(rows[0])
    if (len(rows) < 3 or not 2 <= columns <= 4 or any(len(row) != columns for row in rows)
            or not all(_TABLE_RULE.fullmatch(cell) for cell in rows[1])):
        return False

    from docx.oxml import OxmlElement
    from docx.shared import Pt

    table = document.add_table(rows=0, cols=columns)
    table.style = "Table Grid"
    table.autofit = False
    for index, values in enumerate([rows[0], *rows[2:]]):
        row = table.add_row()
        properties = row._tr.get_or_add_trPr()
        properties.append(OxmlElement("w:cantSplit"))
        if index == 0:
            properties.append(OxmlElement("w:tblHeader"))
        for cell, value in zip(row.cells, values, strict=True):
            paragraph = cell.paragraphs[0]
            paragraph.style = "ArbiBuddy Legal Body"
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = index == 0
            builder._set_run_font(paragraph.add_run(value), name="仿宋", size=10, bold=index == 0)
    section = document.sections[-1]
    width = (section.page_width - section.left_margin - section.right_margin) // 635
    builder._set_table_geometry(table, (width // columns,) * columns)
    return True


def add_section(document, section, index, builder):
    from docx.shared import Pt

    heading = section["heading"]
    body_lines = _lines(section["full_text"])
    if heading == NOTE:
        paragraph = document.add_paragraph(style="ArbiBuddy Legal Body")
        paragraph.paragraph_format.space_before = Pt(8)
        builder._set_run_font(paragraph.add_run(f"{NOTE}：{' '.join(body_lines)}"), name="仿宋", size=9)
        return
    if heading != METADATA:
        document.add_paragraph(f"{_NUMERALS[index - 1]}、{heading}", style="Heading 1")
    subheading_index = 0
    for is_table, group in groupby(body_lines, lambda line: line.startswith("|") and line.endswith("|")):
        lines = list(group)
        if is_table and _add_table(document, lines, builder):
            continue
        for line in lines:
            subheading = _SUBHEADING.fullmatch(line)
            if subheading:
                subheading_index += 1
                number = (_NUMERALS[subheading_index - 1]
                          if subheading_index <= len(_NUMERALS) else str(subheading_index))
                document.add_paragraph(f"（{number}）{subheading[1]}", style="Heading 2")
            else:
                paragraph = document.add_paragraph(style="ArbiBuddy Legal Body")
                paragraph.paragraph_format.keep_with_next = heading == METADATA
                paragraph.add_run(line)
