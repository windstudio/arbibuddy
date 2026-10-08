from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.documents.public import _build_docx
from scripts.documents.ooxml_check import audit_docx


def _build_document(path: Path, fields: dict[str, str]) -> None:
    _build_docx(path, {
        "document_type": "employment_obligation_demand_letter",
        "title": "劳动用工义务催告函", "locked_bindings": [],
        "sections": [{"heading": "劳动关系说明",
                      "full_text": "\n".join(f"{key}：{value}" for key, value in fields.items())}],
    })


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class OoxmlAuditTests(unittest.TestCase):
    @staticmethod
    def _fields(**overrides: str) -> dict[str, str]:
        fields = {
            "employee_name": "劳动者甲",
            "employer_name": "A&B公司",
            "employment_status": "存续",
            "demand_purpose": "工资支付",
            "effective_instrument_enforcement": "否",
            "obligation": "支付确认的工资差额。",
            "response_deadline": "2026年9月1日前",
            "letter_date": "2026年8月25日",
            "employer_address": "上海市示例路1号",
            "employee_address": "上海市示例路2号",
            "employee_phone": "13900000000",
        }
        fields.update(overrides)
        return fields

    @staticmethod
    def _build(root: Path, **overrides: str) -> Path:
        output = root / "notice.docx"
        _build_document(output, OoxmlAuditTests._fields(**overrides))
        return output

    @staticmethod
    def _rewrite_part(source: Path, target: Path, part: str, replacement: bytes) -> None:
        with ZipFile(source) as package:
            parts = {
                name: package.read(name)
                for name in package.namelist()
            }
        parts[part] = replacement
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as package:
            for name, data in parts.items():
                package.writestr(name, data)

    def test_xml10_invalid_field_is_rejected_before_output(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "invalid.docx"
            with self.assertRaises((ValueError, UnicodeEncodeError)):
                _build_document(
                    output,
                    self._fields(employer_name="坏\x0b字段"),
                )
            self.assertFalse(output.exists())

            with self.assertRaises((ValueError, UnicodeEncodeError)):
                _build_document(
                    output,
                    self._fields(employer_name="坏\ud800字段"),
                )
            self.assertFalse(output.exists())

    def test_valid_ampersand_is_decoded_and_entity_cannot_hide_leak(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            document = self._build(root)
            audit = audit_docx(document, "employment-obligation-demand-letter")
            self.assertIn("A&B公司", audit.visible_text)
            self.assertNotIn("&amp;", audit.visible_text)

            leaking = self._build(root, employer_name="内部核验")
            with self.assertRaisesRegex(ValueError, "内部核验"):
                audit_docx(leaking, "employment-obligation-demand-letter")

    def test_each_required_xml_part_is_parsed_and_named_on_failure(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._build(root)
            required = (
                "word/document.xml",
                "word/styles.xml",
                "word/numbering.xml",
                "word/footer1.xml",
            )
            for index, part in enumerate(required):
                target = root / f"malformed-{index}.docx"
                self._rewrite_part(source, target, part, b"<broken")
                with self.subTest(part=part):
                    with self.assertRaisesRegex(ValueError, part.replace(".", r"\.")):
                        audit_docx(target, "employment-obligation-demand-letter")

    def test_missing_eastasia_and_wrong_page_direction_fail_structurally(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._build(root)
            with ZipFile(source) as package:
                styles = ElementTree.fromstring(package.read("word/styles.xml"))
                for style in styles.findall(".//w:style", {"w": W_NS}):
                    name = style.find("w:name", {"w": W_NS})
                    if name is not None and name.get(f"{{{W_NS}}}val") == "ArbiBuddy Legal Title":
                        fonts = style.find("w:rPr/w:rFonts", {"w": W_NS})
                        assert fonts is not None
                        del fonts.attrib[f"{{{W_NS}}}eastAsia"]
                        break
                styles_target = root / "no-eastasia.docx"
                parts = {
                    name: package.read(name)
                    for name in package.namelist()
                }
            parts["word/styles.xml"] = ElementTree.tostring(styles, encoding="utf-8")
            with ZipFile(styles_target, "w", compression=ZIP_DEFLATED) as package:
                for name, data in parts.items():
                    package.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "eastAsia"):
                audit_docx(styles_target, "employment-obligation-demand-letter")

            with ZipFile(source) as package:
                document = ElementTree.fromstring(package.read("word/document.xml"))
                page = document.find(".//w:sectPr/w:pgSz", {"w": W_NS})
                assert page is not None
                width = page.get(f"{{{W_NS}}}w")
                height = page.get(f"{{{W_NS}}}h")
                page.set(f"{{{W_NS}}}w", height or "0")
                page.set(f"{{{W_NS}}}h", width or "0")
                page.set(f"{{{W_NS}}}orient", "landscape")
                parts = {
                    name: package.read(name)
                    for name in package.namelist()
                }
            parts["word/document.xml"] = ElementTree.tostring(document, encoding="utf-8")
            direction_target = root / "wrong-direction.docx"
            with ZipFile(direction_target, "w", compression=ZIP_DEFLATED) as package:
                for name, data in parts.items():
                    package.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "A4纵向"):
                audit_docx(direction_target, "employment-obligation-demand-letter")


if __name__ == "__main__":
    unittest.main()
