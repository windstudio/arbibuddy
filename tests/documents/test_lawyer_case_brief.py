"""Brief delivery boundary: no finalization dialogue or second user file."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from scripts.case_archive import CaseArchive
from scripts.documents.lawyer_case_brief import scaled_source_amount_matches
from scripts.documents.public import _binding_value_matches
from scripts.documents.public import create_delivery_set, describe_contract, render
from scripts.documents.view import managed_delivery_view, managed_edit_context


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
        self.assertEqual(path.suffix, ".docx")
        self.assertNotIn("候选", path.name)
        with ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        text = "".join(root.itertext())
        return result, root, text

    def test_first_delivery_without_confirmation_calculation_or_provided_screenshot(self):
        _, _, text = self.deliver()
        archive = self.archive.read(self.case_id)["result"]["markdown"]
        self.assertNotIn("[CONF-", archive)
        self.assertNotIn("[CAL-", archive)
        for phrase in ("本次咨询", "主体关系", "当前阶段", "主要分歧", "面谈先核实", "截图尚未提供", "8000元", "10000元", "差异待核", "对方意见尚不清楚"):
            self.assertIn(phrase, text)
        for private in ("张三", "310000199001010000", "13800000000", "F-00", "E-00", ".arbibuddy", "CL-00"):
            self.assertNotIn(private, text)
        context = managed_edit_context(workspace=self.workspace, case_id=self.case_id)
        self.assertTrue(context["ok"], context)
        self.assertEqual(context["result"]["documents"][0]["document_type"], "lawyer_case_brief")

    def test_complex_brief_places_hearing_on_overview_and_has_one_shared_timeline(self):
        committed = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "补充虚构开庭通知", "changes": [{"operation": "append", "record_type": "evidence",
                "content_markdown": "已阅仲裁委开庭通知：开庭日期为2026年5月8日。"}]})
        self.assertTrue(committed["ok"], committed)
        self.revision = committed["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.request["sections"][0]["full_text"] += "\n临近安排：仲裁委开庭通知载明2026年5月8日开庭。"
        self.request["locked_bindings"].append({"kind": "date", "archive_record_ref": "E-002", "rendered_value": "2026年5月8日",
            "occurrences": [{"section_id": "overview", "text": "仲裁委开庭通知载明2026年5月8日开庭"}]})
        self.request["sections"].extend([
            {"section_id": "timeline", "heading": "共用时间线", "full_text": "日期不详：当事人称先被主管口头要求离开，随后又收到HR返岗微信。"},
            {"section_id": "wages", "heading": "工资争议", "full_text": "双方工资口径不同，待核对工资条和已提交申请书。"},
            {"section_id": "termination", "heading": "离职争议", "full_text": "结合前述离职及返岗经过，请律师核实是否构成解除及后续出勤情况。"},
        ])
        _, root, text = self.deliver()
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        self.assertEqual(len(root.findall('.//w:br[@w:type="page"]', ns)), 1)
        self.assertLess(text.index("2026年5月8日"), text.index("共用时间线"))
        self.assertEqual(text.count("共用时间线"), 1)
        self.request["sections"].append({"section_id": "another", "heading": "共用时间线", "full_text": "重复"})
        failed = render(self.request, self.workspace)
        self.assertFalse(failed["ok"])
        self.assertIn("missing_section", {error["code"] for error in failed["errors"]})

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
        self.request["sections"][0]["full_text"] += "\n入职经过：当事人称2024年3月1日入职。"
        self.request["locked_bindings"].append({"kind": "date", "archive_record_ref": "F-006", "rendered_value": "2024年3月1日",
            "occurrences": [{"section_id": "overview", "text": "当事人称2024年3月1日入职"}]})
        self.request["sections"][0]["full_text"] += "\n当事人姓名：张三"
        self.request["locked_bindings"].append({"kind": "party", "archive_record_ref": "F-001", "rendered_value": "当事人姓名：张三"})
        self.deliver()
        before = self.archive.read(self.case_id)["result"]
        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace("当事人姓名：张三", "当事人：隐名")
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"] if item["archive_record_ref"] != "F-001"]
        _, _, text = self.deliver()
        self.assertNotIn("张三", text)
        self.assertEqual(self.archive.read(self.case_id)["result"], before)
        corrected = self.archive.commit({"case_id": self.case_id, "expected_revision": self.revision,
            "change_summary": "用户核实后纠正入职日期", "changes": [{"operation": "replace", "record_type": "fact", "record_id": "F-006",
                "reason": "用户核实后明确更正入职日期", "content_markdown": "事实状态：用户陈述\n入职日期：2024年2月29日。用户核实后更正。"}]})
        self.assertTrue(corrected["ok"], corrected)
        self.assertTrue(managed_delivery_view(workspace=self.workspace, case_id=self.case_id)["stopped"])
        self.revision = corrected["result"]["revision"]
        self.request["archive_revision"] = self.revision
        self.select()
        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace("2024年3月1日", "2024年2月29日")
        for binding in self.request["locked_bindings"]:
            if binding["archive_record_ref"] == "F-006":
                binding["rendered_value"] = "2024年2月29日"
                binding["occurrences"][0]["text"] = "当事人称2024年2月29日入职"
        _, _, text = self.deliver()
        self.assertIn("2024年2月29日", text)
        self.assertNotIn("2024年3月1日", text)

    def test_unknown_dates_are_not_invented_but_internal_ids_and_unbound_dates_still_fail(self):
        self.request["sections"][0]["full_text"] = self.request["sections"][0]["full_text"].replace(
            "案情信息截至2026年4月15日（依据当事人当日陈述，此后进展尚未核实）。", "案情信息截至日期不详，现有材料的时间范围待核。")
        self.request["locked_bindings"] = [item for item in self.request["locked_bindings"] if item["kind"] != "date"]
        self.deliver()
        self.request["sections"][0]["full_text"] += "\n开庭安排：2026年5月8日，F-001。"
        rejected = render(self.request, self.workspace)
        self.assertFalse(rejected["ok"])
        self.assertIn("internal_content_leak", {error["code"] for error in rejected["errors"]})
        self.assertIn("binding_mismatch", {error["code"] for error in rejected["errors"]})


if __name__ == "__main__":
    unittest.main()
