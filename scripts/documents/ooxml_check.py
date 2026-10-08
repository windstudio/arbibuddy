"""解析式 DOCX/OOXML 结构审计。"""

from dataclasses import dataclass
import posixpath
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
REQUIRED_XML_PARTS = (
    "word/document.xml",
    "word/styles.xml",
    "word/numbering.xml",
    "word/footer1.xml",
)
REQUIRED_PACKAGE_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/_rels/document.xml.rels",
)
A4_WIDTH_TWIPS = 11906
A4_HEIGHT_TWIPS = 16838
PACKAGE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

INTERNAL_LEAK_TERMS = (
    "请勿外发", "内部核验", "模型说明", "提示注入", "忽略以上",
    "system prompt", "风险检查", "内部待办", "模型提示", "系统指令",
    "开发者指令", "隐藏字段", "field_bindings", "confirmed_snapshot",
    "internal_todo", "待补材料", "风险说明", "未确认事实", "冲突事实", "风险分",
)


@dataclass(frozen=True)
class OoxmlAudit:
    checks: tuple[str, ...]
    visible_text: str


def _qname(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def _attribute(element: ElementTree.Element | None, local: str) -> str | None:
    return element.get(_qname(local)) if element is not None else None


def _relationship_base(relationship_part: str) -> str:
    if "/_rels/" not in relationship_part:
        return ""
    return relationship_part.split("/_rels/", 1)[0]


def _resolve_relationship_target(relationship_part: str, target: str) -> str:
    target = target.split("#", 1)[0].replace("\\", "/")
    target = target.lstrip("/")
    resolved = posixpath.normpath(
        posixpath.join(_relationship_base(relationship_part), target)
    )
    return "" if resolved == "." else resolved


def _validate_package_parts(package: ZipFile, names: set[str]) -> None:
    missing = sorted(set(REQUIRED_PACKAGE_PARTS) - names)
    if missing:
        raise ValueError(f"OOXML包结构缺失：{missing[0]}")
    try:
        corrupt = package.testzip()
    except (BadZipFile, OSError) as error:
        raise ValueError("DOCX压缩包完整性校验失败") from error
    if corrupt:
        raise ValueError(f"DOCX压缩包部件损坏：{corrupt}")

    try:
        content_types = ElementTree.fromstring(package.read("[Content_Types].xml"))
    except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("OOXML部件 [Content_Types].xml XML解析失败") from error
    if content_types.tag != f"{{{PACKAGE_NS}}}Types":
        raise ValueError("OOXML部件 [Content_Types].xml 根节点无效")
    for override in content_types.findall(f"{{{PACKAGE_NS}}}Override"):
        part_name = (override.get("PartName") or "").lstrip("/")
        if not part_name or part_name not in names:
            raise ValueError(f"OOXML内容类型引用缺失：{part_name or '<empty>'}")

    for relationship_part in sorted(name for name in names if name.endswith(".rels")):
        try:
            relationships = ElementTree.fromstring(package.read(relationship_part))
        except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"OOXML关系部件 {relationship_part} XML解析失败") from error
        if relationships.tag != f"{{{RELATIONSHIP_NS}}}Relationships":
            raise ValueError(f"OOXML关系部件 {relationship_part} 根节点无效")
        for relationship in relationships.findall(f"{{{RELATIONSHIP_NS}}}Relationship"):
            relation_id = relationship.get("Id")
            relation_type = relationship.get("Type")
            target = relationship.get("Target")
            if not relation_id or not relation_type or not target:
                raise ValueError(f"OOXML关系部件 {relationship_part} 含不完整关系")
            if relationship.get("TargetMode", "").casefold() == "external":
                continue
            target_part = _resolve_relationship_target(relationship_part, target)
            if not target_part or target_part not in names:
                raise ValueError(
                    f"OOXML关系目标缺失：{relationship_part} -> {target}"
                )

    for name in sorted(names):
        if not name.endswith(".xml") or name == "[Content_Types].xml":
            continue
        try:
            ElementTree.fromstring(package.read(name))
        except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"OOXML部件 {name} XML解析失败") from error


def _parse_required_parts(path: Path) -> dict[str, ElementTree.Element]:
    try:
        package = ZipFile(path)
    except (OSError, BadZipFile) as error:
        raise ValueError("DOCX不是有效的ZIP文档") from error
    with package:
        names = set(package.namelist())
        missing = sorted(set(REQUIRED_XML_PARTS) - names)
        if missing:
            raise ValueError(f"OOXML结构缺失：{missing[0]}")
        roots: dict[str, ElementTree.Element] = {}
        for part in REQUIRED_XML_PARTS:
            try:
                roots[part] = ElementTree.fromstring(package.read(part))
            except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
                raise ValueError(f"OOXML部件 {part} XML解析失败") from error
        _validate_package_parts(package, names)
    return roots


def _element_text(root: ElementTree.Element) -> str:
    return "".join(root.itertext())


def _visible_text(root: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in root.iter(_qname("t")))


def _require_styles(styles_root: ElementTree.Element, document_root: ElementTree.Element) -> None:
    styles: dict[str, ElementTree.Element] = {}
    for style in styles_root.findall(".//w:style", NS):
        name = style.find("w:name", NS)
        value = _attribute(name, "val")
        if value:
            styles[value] = style
    required_names = (
        "ArbiBuddy Legal Title", "ArbiBuddy Legal Body", "ArbiBuddy Legal Signature",
    )
    if any(name not in styles for name in required_names):
        raise ValueError("OOXML缺少注册标题、正文或落款样式")
    for name in required_names:
        fonts = styles[name].find("w:rPr/w:rFonts", NS)
        if fonts is None or not _attribute(fonts, "eastAsia"):
            raise ValueError(f"OOXML样式缺少eastAsia字体：{name}")

    for run in document_root.findall(".//w:r", NS):
        if not _visible_text(run).strip():
            continue
        fonts = run.find("w:rPr/w:rFonts", NS)
        if fonts is None or not _attribute(fonts, "eastAsia"):
            raise ValueError("OOXML正文run缺少eastAsia字体")


def _require_page(document_root: ElementTree.Element, *, landscape: bool) -> None:
    sections = document_root.findall(".//w:sectPr", NS)
    if not sections:
        raise ValueError("OOXML缺少节页面设置")
    page = sections[0].find("w:pgSz", NS)
    margins = sections[0].find("w:pgMar", NS)
    if page is None or margins is None:
        raise ValueError("OOXML缺少页面方向或显式页边距")
    try:
        width = int(_attribute(page, "w") or "0")
        height = int(_attribute(page, "h") or "0")
        margin_values = [
            int(_attribute(margins, side) or "0")
            for side in ("top", "right", "bottom", "left", "header", "footer")
        ]
    except ValueError as error:
        raise ValueError("OOXML页面尺寸或页边距不是整数") from error
    orientation = _attribute(page, "orient")
    expected_dimensions = (
        (A4_HEIGHT_TWIPS, A4_WIDTH_TWIPS)
        if landscape
        else (A4_WIDTH_TWIPS, A4_HEIGHT_TWIPS)
    )
    if (width, height) != expected_dimensions:
        raise ValueError("OOXML页面不是A4横向" if landscape else "OOXML页面不是A4纵向")
    orientation_ok = (
        landscape and orientation == "landscape" and width > height
    ) or (
        not landscape and orientation in {None, "portrait"} and height > width
    )
    if not orientation_ok:
        raise ValueError("OOXML页面不是A4横向" if landscape else "OOXML页面不是A4纵向")
    if any(value <= 0 for value in margin_values):
        raise ValueError("OOXML页面缺少正数显式页边距")


def _require_footer(footer_root: ElementTree.Element) -> None:
    instructions = {
        (_attribute(field, "instr") or "").strip().upper()
        for field in footer_root.findall(".//w:fldSimple", NS)
    }
    instructions.update(
        (node.text or "").strip().upper()
        for node in footer_root.findall(".//w:instrText", NS)
    )
    if not {"PAGE", "NUMPAGES"} <= instructions:
        raise ValueError("OOXML页脚缺少PAGE/NUMPAGES字段")
    if " / " not in _visible_text(footer_root):
        raise ValueError("OOXML页脚缺少页码分隔符")
    for run in footer_root.findall(".//w:r", NS):
        if not _visible_text(run).strip():
            continue
        fonts = run.find("w:rPr/w:rFonts", NS)
        size = run.find("w:rPr/w:sz", NS)
        if (
            fonts is None
            or _attribute(fonts, "ascii") != "Times New Roman"
            or size is None
            or _attribute(size, "val") != "18"
        ):
            raise ValueError("OOXML页脚字体不是Times New Roman小五")


def _require_numbering(
    document_root: ElementTree.Element,
    styles_root: ElementTree.Element,
    numbering_root: ElementTree.Element,
) -> None:
    abstract_ids = {
        _attribute(node, "abstractNumId")
        for node in numbering_root.findall(".//w:abstractNum", NS)
    }
    number_to_abstract = {
        _attribute(number, "numId"): _attribute(
            number.find("w:abstractNumId", NS), "val"
        )
        for number in numbering_root.findall(".//w:num", NS)
    }
    num_prs = [
        num_pr
        for num_pr in (
        document_root.findall(".//w:numPr", NS)
        + styles_root.findall(".//w:numPr", NS)
        )
        if num_pr.find("w:numId", NS) is not None
    ]
    for num_pr in num_prs:
        num_id = _attribute(num_pr.find("w:numId", NS), "val")
        if num_id not in number_to_abstract or number_to_abstract[num_id] not in abstract_ids:
            raise ValueError("OOXML编号引用缺少对应的num/abstractNum")
    if not num_prs:
        raise ValueError("OOXML具体义务未使用真实编号")


def _require_evidence_table(document_root: ElementTree.Element) -> None:
    tables = document_root.findall(".//w:tbl", NS)
    if len(tables) != 1:
        raise ValueError("OOXML证据目录必须且只能包含一张正式表格")
    table = tables[0]
    rows = table.findall("w:tr", NS)
    if len(rows) < 2:
        raise ValueError("OOXML证据目录表格缺少证据行")
    headers = tuple(_visible_text(cell) for cell in rows[0].findall("w:tc", NS))
    if headers != ("编号", "证据名称", "证据内容", "证明目的", "页码"):
        raise ValueError("OOXML证据目录表头不是固定五列")
    if rows[0].find("w:trPr/w:tblHeader", NS) is None:
        raise ValueError("OOXML证据目录表头未设置跨页重复")
    layout = table.find("w:tblPr/w:tblLayout", NS)
    if layout is None or _attribute(layout, "type") != "fixed":
        raise ValueError("OOXML证据目录表格未使用固定布局")
    grid_widths = tuple(
        _attribute(item, "w") for item in table.findall("w:tblGrid/w:gridCol", NS)
    )
    if len(grid_widths) != 5 or any(not value or not value.isdigit() for value in grid_widths):
        raise ValueError("OOXML证据目录未设置五列显式列宽")
    table_width = table.find("w:tblPr/w:tblW", NS)
    if (
        table_width is None
        or _attribute(table_width, "type") != "dxa"
        or _attribute(table_width, "w") != str(sum(map(int, grid_widths)))
    ):
        raise ValueError("OOXML证据目录表格总宽度不是匹配列宽的DXA值")
    table_indent = table.find("w:tblPr/w:tblInd", NS)
    if (
        table_indent is None
        or _attribute(table_indent, "type") != "dxa"
        or _attribute(table_indent, "w") != "0"
    ):
        raise ValueError("OOXML证据目录表格缩进不是显式DXA值")
    for row in rows:
        cell_width_elements = tuple(
            cell.find("w:tcPr/w:tcW", NS) for cell in row.findall("w:tc", NS)
        )
        if any(
            element is None or _attribute(element, "type") != "dxa"
            for element in cell_width_elements
        ):
            raise ValueError("OOXML证据目录单元格列宽不是显式DXA值")
        cell_widths = tuple(
            _attribute(element, "w")
            for element in cell_width_elements
            if element is not None
        )
        if cell_widths != grid_widths:
            raise ValueError("OOXML证据目录网格列宽与单元格列宽不一致")
    if any(row.find("w:trPr/w:cantSplit", NS) is None for row in rows[1:]):
        raise ValueError("OOXML证据目录证据行允许跨页拆分")


def audit_docx(path: Path, template_id: str | None = None) -> OoxmlAudit:
    roots = _parse_required_parts(path)
    document_root = roots["word/document.xml"]
    styles_root = roots["word/styles.xml"]
    numbering_root = roots["word/numbering.xml"]
    footer_root = roots["word/footer1.xml"]
    visible_text = _visible_text(document_root)
    decoded_xml_text = "\n".join(_element_text(root) for root in roots.values())
    leaked = [
        term for term in INTERNAL_LEAK_TERMS
        if term.casefold() in decoded_xml_text.casefold()
    ]
    if leaked:
        raise ValueError(f"正式文书检测到内部内容泄漏：{leaked[0]}")

    checks: list[str] = ["ZIP包、内容类型、关系目标和XML部件完整"]
    _require_page(document_root, landscape=template_id == "evidence-catalog")
    checks.append(
        "A4横向页面与显式页边距"
        if template_id == "evidence-catalog"
        else "A4纵向页面与显式页边距"
    )
    _require_styles(styles_root, document_root)
    checks.append("标题、正文与落款使用命名样式")
    _require_footer(footer_root)
    checks.append("独立页脚使用PAGE / NUMPAGES及Times New Roman小五")
    if not document_root.findall(".//w:keepNext", NS) or not styles_root.findall(
        ".//w:widowControl", NS
    ):
        raise ValueError("OOXML缺少分页或孤行控制")
    checks.append("分页、孤行和孤立落款控制")
    if template_id == "evidence-catalog":
        _require_evidence_table(document_root)
        checks.append("固定五列表头、显式列宽、重复表头与整行分页")
    else:
        _require_numbering(document_root, styles_root, numbering_root)
        checks.append("具体义务使用真实Word编号")
    return OoxmlAudit(tuple(checks), visible_text)
