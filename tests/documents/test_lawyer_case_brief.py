"""Brief delivery boundary: no finalization dialogue or second user file."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from scripts.case_archive import CaseArchive
from scripts.documents.lawyer_case_brief import scaled_source_amount_matches
from scripts.documents.public import _binding_value_matches, _DOCUMENT_TYPE_TITLES
from scripts.documents.public import create_delivery_set, describe_contract, render
from scripts.documents.view import managed_delivery_view, managed_edit_context

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


class BriefSourceAmountTests(unittest.TestCase):
    def test_scaled_sources_preserve_sign_and_unit(self):
        for unit, amount in (("万", "10000"), ("亿", "100000000")):
            for sign in ("-", "−", "﹣", "－", "负", "負"):
                for spacing in ("", " "):
                    source = f"材料记载：{sign}{spacing}1{unit}元"
                    with self.subTest(source=source):
                        self.assertFalse(_binding_value_matches(source,
                            {"kind": "fact_amount", "rendered_value": f"{amount}元"}, brief_amount=True))
                        self.assertTrue(scaled_source_amount_matches(source, -Decimal(amount)))
            for sign in ("", "+", "＋"):
                with self.subTest(unit=unit, sign=sign):
                    self.assertTrue(_binding_value_matches(f"材料记载：{sign}1{unit}元",
                        {"kind": "fact_amount", "rendered_value": f"{amount}元"}, brief_amount=True))
        self.assertTrue(scaled_source_amount_matches("材料记载：-1,234.5万元", Decimal("-12345000")))


class LawyerCaseBriefTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.example = deepcopy(describe_contract("render", document_type="lawyer_case_brief")["examples"]["worked_example"])
        self.archive = CaseArchive(self.workspace)
        created = self.archive.create(self.example["create"])
        self.assertTrue(created["ok"], created)
        self.case_id = created["result"]["case_id"]
        commit = self.example["commit"]
        commit["case_id"] = self.case_id
        committed = self.archive.commit(commit)
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request = self.example["render_requests"][0]
        self.request.update(case_id=self.case_id, archive_revision=self.revision)
        self.select()

    def select(self):
        selected = create_delivery_set({
            "case_id": self.case_id, "archive_revision": self.revision,
            "mode": "external_final", "requested_templates": ["lawyer-case-brief"], "confirmation_refs": [],
        }, self.workspace)
        self.assertTrue(selected["ok"], selected)
        self.request["delivery_set_id"] = selected["result"]["delivery_set_id"]

    def deliver(self):
        result = render(self.request, self.workspace)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["result"]["presentation_authorized"])
        view = managed_delivery_view(workspace=self.workspace, case_id=self.case_id)
        self.assertFalse(view["stopped"], view)
        self.assertEqual([item["kind"] for item in view["presentation_files"]], ["document"])
        path = Path(view["presentation_files"][0]["path"])
        self.assertEqual(path.name, "《案情简报》.docx")
        with ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        paragraphs = ["".join(paragraph.itertext()) for paragraph in root.findall("./w:body/w:p", NS)]
        self.assertEqual(paragraphs[:2], ["案情简报", "供律师咨询参考"])
        text = "".join(root.itertext())
        return result, root, text

    def test_first_delivery_without_confirmation_calculation_or_provided_screenshot(self):
        _, root, text = self.deliver()
        archive = self.archive.read(self.case_id)["result"]["markdown"]
        self.assertNotIn("[CONF-", archive)
        self.assertNotIn("[CAL-", archive)
        for phrase in ("当事人：张三", "一、案件概要", "五、证据及待明确事项", "（二）尚待明确的事项",
                       "六、当事人诉求及咨询问题", "（二）拟与律师讨论的问题", "截图尚未提供",
                       "8000元", "10000元", "差异原因尚不明确", "对方意见尚不清楚", "编制说明："):
            self.assertIn(phrase, text)
        self.assertNotIn("面谈优先核对", text)
        self.assertEqual(len(root.findall(".//w:tbl", NS)), 1)
        self.assertIsNotNone(root.find(".//w:trPr/w:tblHeader", NS))
        for private in ("310000199001010000", "13800000000", "F-00", "E-00", ".arbibuddy", "CL-00"):
            self.assertNotIn(private, text)
        context = managed_edit_context(workspace=self.workspace, case_id=self.case_id)
        self.assertTrue(context["ok"], context)
        self.assertEqual(context["result"]["documents"][0]["document_type"], "lawyer_case_brief")

    def test_existing_delivery_with_the_previous_title_still_opens(self):
        old_title = "律师阅案简报"
        committed = self.archive.commit({
            "case_id": self.case_id, "expected_revision": self.revision, "change_summary": "准备旧版标题来源",
            "changes": [{"operation": "replace", "record_type": "fact", "record_id": "F-003",
                         "reason": "模拟升级前记录", "content_markdown": f"文书准备信息：{old_title}。"}],
        })
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.request["locked_bindings"][0]["rendered_value"] = old_title
        with (
            patch.dict(_DOCUMENT_TYPE_TITLES, {"lawyer_case_brief": old_title}),
            patch("scripts.documents.lawyer_case_brief.add_subtitle"),
        ):
            legacy = render(self.request, self.workspace)
        self.assertTrue(legacy["ok"], legacy)
        view = managed_delivery_view(workspace=self.workspace, case_id=self.case_id)
        self.assertFalse(view["stopped"], view)
        self.assertEqual(Path(view["presentation_files"][0]["path"]).name, f"《{old_title}》.docx")
        context = managed_edit_context(workspace=self.workspace, case_id=self.case_id)
        self.assertTrue(context["ok"], context)
        self.assertEqual(context["result"]["documents"][0]["title"], old_title)

    def test_complex_brief_uses_word_heading_levels_and_natural_pagination(self):
        committed = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "补充虚构开庭通知", "changes": [{"operation": "append", "record_type": "evidence",
                "content_markdown": "已阅仲裁委开庭通知：开庭日期为2026年5月8日。"}]})
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        overview = self.request["sections"][1]
        overview["full_text"] = (
            overview["full_text"].replace("，尚未收到开庭通知", "")
            + "\n临近安排：仲裁委开庭通知载明2026年5月8日开庭。"
        )
        self.request["locked_bindings"].append({"kind": "date", "archive_record_ref": "E-002", "rendered_value": "2026年5月8日",
            "occurrences": [{"section_id": "overview", "text": "仲裁委开庭通知载明2026年5月8日开庭"}]})
        self.request["sections"][2]["full_text"] += "\n### 关键事件时间线\n据当事人陈述，先有口头通知，随后收到返岗微信。"
        combined = self.request["sections"][-3:-1]
        materials, gaps = combined[0]["full_text"].split("\n### 尚待明确的事项\n")
        objectives, questions = combined[1]["full_text"].split("\n### 拟与律师讨论的问题\n")
        self.request["sections"][-3:-1] = [
            {"section_id": "objectives", "heading": "当事人诉求", "full_text": objectives.split("\n", 1)[1]},
            {"section_id": "materials", "heading": "证据及材料情况", "full_text": materials.split("\n", 1)[1]},
            {"section_id": "gaps", "heading": "尚待明确的事项", "full_text": gaps},
            {"section_id": "questions", "heading": "拟与律师讨论的问题", "full_text": questions},
        ]
        _, root, text = self.deliver()
        self.assertFalse(root.findall('.//w:br[@w:type="page"]', NS))
        self.assertLess(text.index("2026年5月8日"), text.index("关键事件时间线"))
        headings = {
            level: ["".join(p.itertext()) for p in root.findall(".//w:p", NS)
                    if p.find(f'w:pPr/w:pStyle[@w:val="Heading{level}"]', NS) is not None]
            for level in (1, 2)
        }
        self.assertEqual(headings[1][0], "一、案件概要")
        self.assertEqual(headings[1][-1], "八、拟与律师讨论的问题")
        self.assertEqual(headings[2], ["（一）关键事件时间线", "（一）工资差额", "（二）离岗及返岗"])

    def test_structure_allows_pruning_and_rejects_empty_or_misplaced_sections(self):
        base = deepcopy(self.request["sections"])
        for name, mutate in (
            ("missing_note", lambda sections: sections.pop()),
            ("empty_subheading", lambda sections: sections[3].update(full_text="### 工资差额")),
            ("wrong_order", lambda sections: sections.insert(1, sections.pop(3))),
            ("old_structure", lambda sections: sections[1].update(heading="案情概要")),
            ("mixed_combined", lambda sections: sections.insert(-1,
                {"section_id": "duplicate", "heading": "拟与律师讨论的问题", "full_text": "测试"})),
        ):
            with self.subTest(name=name):
                self.request["sections"] = deepcopy(base)
                mutate(self.request["sections"])
                failed = render(self.request, self.workspace)
                self.assertFalse(failed["ok"])
                self.assertIn("missing_section", {error["code"] for error in failed["errors"]})
        self.request["sections"] = base[:4] + base[5:]
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"]
                                           if item["archive_record_ref"] != "CL-001"]
        _, _, text = self.deliver()
        self.assertIn("四、证据及待明确事项", text)
        self.assertIn("五、当事人诉求及咨询问题", text)

    def test_provided_materials_retain_reading_status_in_the_same_table(self):
        sources = [
            ("工资流水", "已提供，未阅"),
            ("排班记录", "已提供，部分已阅"),
        ]
        committed = self.archive.commit({
            "case_id": self.case_id, "expected_revision": self.revision, "change_summary": "记录虚构材料状态",
            "changes": [{"operation": "append", "record_type": "evidence",
                         "content_markdown": f"材料名称：{name}\n材料状态：{state}。"}
                        for name, state in sources],
        })
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        materials = self.request["sections"][5]
        rows = "".join(f"| {name} | 工资差额 | {state} |\n" for name, state in sources)
        materials["full_text"] = materials["full_text"].replace("### 尚待明确的事项", rows + "### 尚待明确的事项")
        self.request["sections"][-1]["full_text"] += "工资流水未阅，排班记录部分已阅。"
        self.request["locked_bindings"].extend(
            {"kind": "evidence", "archive_record_ref": f"E-{index:03}", "rendered_value": name}
            for index, (name, _) in enumerate(sources, start=2)
        )
        _, root, _ = self.deliver()
        rows = ["".join(row.itertext()) for row in root.findall(".//w:tbl/w:tr", NS)]
        for name, state in sources:
            self.assertIn(f"{name}工资差额{state}", rows)

    def test_estimate_and_material_amount_can_use_conflicting_source_without_changing_other_types(self):
        committed = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "记录虚构材料相反口径", "changes": [{"operation": "append", "record_type": "evidence",
                "content_markdown": "材料状态：已阅\n申请书载明3月工资请求1万元；与当事人现估存在冲突，差异待核。"}]})
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        amount = next(item for item in self.request["locked_bindings"] if item["archive_record_ref"] == "CL-001")
        amount["archive_record_ref"] = "E-002"
        self.deliver()
        amount["rendered_value"] = "11000元"
        rejected = render(self.request, self.workspace)
        self.assertFalse(rejected["ok"])
        self.assertIn("binding_mismatch", {error["code"] for error in rejected["errors"]})

    def test_render_rejects_positive_amount_bound_to_negative_source(self):
        committed = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "记录虚构负差额材料", "changes": [{"operation": "replace", "record_type": "claim",
                "record_id": "CL-001", "reason": "按材料核实负差额",
                "content_markdown": "材料状态：已阅\n申请书记载3月工资差额为-1万元。"}]})
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        rejected = render(self.request, self.workspace)
        self.assertFalse(rejected["ok"], rejected)
        self.assertTrue(any(error["code"] == "binding_mismatch"
                            and error["path"].endswith("rendered_value")
                            for error in rejected["errors"]), rejected)

    def test_anonymization_changes_only_delivery_then_verified_fact_correction_invalidates_old_brief(self):
        added = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "记录虚构入职日期", "changes": [{"operation": "append", "record_type": "fact",
                "content_markdown": "事实状态：用户陈述\n入职日期：2024年3月1日。"}]})
        self.assertTrue(added["ok"], added)
        self.revision = added["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.request["sections"][2]["full_text"] += "\n入职经过：当事人称2024年3月1日入职。"
        self.request["locked_bindings"].append({"kind": "date", "archive_record_ref": "F-006", "rendered_value": "2024年3月1日",
            "occurrences": [{"section_id": "background", "text": "当事人称2024年3月1日入职"}]})
        self.deliver()
        before = self.archive.read(self.case_id)["result"]
        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace("当事人：张三", "当事人：当事人")
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"] if item["archive_record_ref"] != "F-001"]
        _, _, text = self.deliver()
        self.assertNotIn("张三", text)
        self.assertIn("当事人：当事人", text)
        self.assertEqual(self.archive.read(self.case_id)["result"], before)
        corrected = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "用户核实后纠正入职日期", "changes": [{"operation": "replace", "record_type": "fact", "record_id": "F-006",
                "reason": "用户核实后明确更正入职日期", "content_markdown": "事实状态：用户陈述\n入职日期：2024年2月29日。用户核实后更正。"}]})
        self.assertTrue(corrected["ok"], corrected)
        self.assertTrue(managed_delivery_view(workspace=self.workspace, case_id=self.case_id)["stopped"])
        self.revision = corrected["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.request["sections"][2]["full_text"] = self.request["sections"][2]["full_text"].replace("2024年3月1日", "2024年2月29日")
        for binding in self.request["locked_bindings"]:
            if binding["archive_record_ref"] == "F-006":
                binding["rendered_value"] = "2024年2月29日"
                binding["occurrences"][0]["text"] = "当事人称2024年2月29日入职"
        _, _, text = self.deliver()
        self.assertIn("2024年2月29日", text)
        self.assertNotIn("2024年3月1日", text)

    def test_conversation_date_cannot_be_bound_as_the_brief_cutoff(self):
        updated = self.archive.commit({
            "case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "记录陈述日期", "changes": [{
                "operation": "replace", "record_type": "fact", "record_id": "F-004",
                "reason": "当事人说明信息截止日不详",
                "content_markdown": "事实状态：用户陈述\n当事人于2026年4月15日向本工具陈述案情。已申请仲裁，尚未收到开庭通知。",
            }],
        })
        self.assertTrue(updated["ok"], updated)
        self.revision = updated["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        rejected = render(self.request, self.workspace)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("brief_cutoff_source_missing", {error.get("reason") for error in rejected["errors"]})

        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace(
            "案情信息截至：2026年4月15日（此后进展尚未核实）", "案情信息截至：不详")
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"] if item["kind"] != "date"]
        self.assertIn("案情信息截至：不详", self.deliver()[2])

    def test_legacy_archive_with_explicit_information_scope_can_still_render(self):
        updated = self.archive.commit({
            "case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "记录原始案情范围", "changes": [{
                "operation": "replace", "record_type": "fact", "record_id": "F-004",
                "reason": "保留当事人说明信息范围的原有表述",
                "content_markdown": "事实状态：用户陈述\n当事人于2026年4月15日整理上述情况，此后进展未核实。已申请仲裁，尚未收到开庭通知。",
            }],
        })
        self.assertTrue(updated["ok"], updated)
        self.revision = updated["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.assertIn("案情信息截至：2026年4月15日", self.deliver()[2])

    def test_unknown_dates_are_not_invented_but_internal_ids_and_unbound_dates_still_fail(self):
        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace(
            "案情信息截至：2026年4月15日（此后进展尚未核实）", "案情信息截至：不详")
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"] if item["kind"] != "date"]
        self.deliver()
        self.request["sections"][0]["full_text"] += "\n开庭安排：2026年5月8日，F-001。"
        rejected = render(self.request, self.workspace)
        self.assertFalse(rejected["ok"])
        self.assertIn("internal_content_leak", {error["code"] for error in rejected["errors"]})
        self.assertIn("binding_mismatch", {error["code"] for error in rejected["errors"]})


if __name__ == "__main__":
    unittest.main()
