"""Verify literal identity fields through public archive/set/render/view calls."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import create_delivery_set, describe_contract, render
from scripts.documents.view import managed_delivery_view


# Minimized from the fictional phone Journey's F-003 revisions 6, 7 and 8.
IDENTITY_SOURCES = (
    "申请人：林安。申请人姓名：林安。身份证号码：310000199001010000。联系电话：13800000000。\n"
    "被申请人：上海样例云科有限公司。用人单位名称：上海样例云科有限公司（用户说明为虚构单位）。单位送达地址：上海市浦东新区示例路88号。",
    "姓名：林安。申请人：林安。身份证号码：310000199001010000。联系电话：13800000000。\n"
    "单位名称：上海样例云科有限公司。被申请人：上海样例云科有限公司。单位送达地址：上海市浦东新区示例路88号。",
    "姓名：林安\n申请人：林安\n身份证号码：310000199001010000\n联系电话：13800000000\n"
    "单位名称：上海样例云科有限公司\n被申请人：上海样例云科有限公司\n单位送达地址：上海市浦东新区示例路88号",
)


def prepare(workspace, source, *, company="上海样例云科有限公司"):
    example = deepcopy(describe_contract("render", document_type="arbitration_application")["examples"]["worked_example"])
    example = json.loads(json.dumps(example, ensure_ascii=False).replace("张三", "林安").replace("示例公司", company))
    example["commit"]["changes"][0]["content_markdown"] = source
    archive = CaseArchive(workspace)
    case_id = archive.create()["result"]["case_id"]
    example["commit"]["case_id"] = case_id
    committed = archive.commit(example["commit"])
    assert committed["ok"], committed
    revision = committed["result"]["revision"]
    example["delivery_set"].update(case_id=case_id, archive_revision=revision)
    selected = create_delivery_set(example["delivery_set"], workspace)
    assert selected["ok"], selected
    for request in example["render_requests"]:
        request.update(case_id=case_id, archive_revision=revision,
                       delivery_set_id=selected["result"]["delivery_set_id"])
        for binding in request["locked_bindings"]:
            if binding["kind"] in {"party", "signature"}:
                binding["archive_record_ref"] = "F-001"
    return archive, case_id, example["render_requests"]


class IdentityFieldBoundaryTests(unittest.TestCase):
    def test_real_source_layouts_complete_one_current_set_without_archive_repair(self):
        for source in IDENTITY_SOURCES:
            with self.subTest(source=source), TemporaryDirectory() as temp:
                workspace = Path(temp)
                archive, case_id, requests = prepare(workspace, source)
                before = archive.read(case_id)
                for request in requests:
                    result = render(request, workspace)
                    self.assertTrue(result["ok"], result)
                view = managed_delivery_view(workspace=workspace, case_id=case_id)
                self.assertFalse(view["stopped"], view)
                self.assertEqual(len(view["presentation_files"]), 4)
                self.assertEqual(view["delivery_set"]["delivery_set_id"], requests[0]["delivery_set_id"])
                self.assertEqual(archive.read(case_id), before)

    def test_literal_company_punctuation_and_generic_fields_are_supported(self):
        company = "ACME & Sons (Shanghai) Co., Ltd."
        source = f"姓名：林安。联系电话：13800000000。单位名称：{company}。地址：上海市示例路1号。"
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepare(workspace, source, company=company)
            before = archive.read(case_id)
            for request in requests:
                self.assertTrue((result := render(request, workspace))["ok"], result)
            self.assertEqual(archive.read(case_id), before)
            self.assertFalse(managed_delivery_view(workspace=workspace, case_id=case_id)["stopped"])

    def test_contract_salary_after_company_completes_without_archive_repair(self):
        # Minimized from canonical F-005 revision 8: the separate generic
        # company field supplies the literal name without stripping its qualifier.
        company = "上海澄明云科信息技术有限公司"
        source = (
            "姓名：林安\n"
            f"被申请人名称：{company}（用户称用人单位；登记主体信息待核）。"
            f"单位名称：{company}。合同约定月薪：12000元（工资构成待核）。"
        )
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepare(workspace, source, company=company)
            before = archive.read(case_id)
            for request in requests:
                self.assertTrue((result := render(request, workspace))["ok"], result)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            self.assertEqual(len(view["presentation_files"]), 4)
            self.assertEqual(view["delivery_set"]["delivery_set_id"], requests[0]["delivery_set_id"])
            self.assertEqual(archive.read(case_id), before)

    def test_credit_code_after_company_completes_without_archive_repair(self):
        company = "上海样例云科有限公司"
        for label in ("被申请人", "单位名称"):
            with self.subTest(label=label), TemporaryDirectory() as temp:
                source = (
                    "申请人：林安\n"
                    f"{label}：{company}。统一社会信用代码：待核。住所：虚构地址。"
                )
                workspace = Path(temp)
                archive, case_id, requests = prepare(workspace, source, company=company)
                before = archive.read(case_id)
                contract = describe_contract("render", document_type="arbitration_application")
                fields = contract["input"]["locked_binding"]["party_source_field_boundaries"]["following_fields"]
                for request in requests:
                    result = render(request, workspace)
                    self.assertTrue(result["ok"], result)
                view = managed_delivery_view(workspace=workspace, case_id=case_id)
                self.assertFalse(view["stopped"], view)
                self.assertEqual(len(view["presentation_files"]), 4)
                self.assertEqual(view["delivery_set"]["delivery_set_id"], requests[0]["delivery_set_id"])
                self.assertEqual(archive.read(case_id), before)
                self.assertIn("统一社会信用代码", fields)

    def test_ambiguous_or_wrong_sources_remain_rejected_without_writes(self):
        good_company = "被申请人：上海样例云科有限公司。地址：上海市示例路1号。"
        sources = {
            "name_prefix": "申请人：林安宁。身份证号码：310000199001010000。\n" + good_company,
            "company_prefix": "申请人：林安。联系电话：13800000000。\n被申请人：上海样例云科有限公司浦东分公司。地址：待核。",
            "wrong_role": "被申请人：林安。身份证号码：310000199001010000。\n" + good_company,
            "prose_only": "有人提到林安，但申请人姓名没有确认。\n" + good_company,
            "qualified_identity": "申请人：林安（身份待核）。联系电话：13800000000。\n" + good_company,
            "unrecognised_following_field": "申请人：林安。可能另有其他含义：待核。\n" + good_company,
            "punctuated_company_prefix": "申请人：林安。联系电话：13800000000。\n被申请人：上海样例云科有限公司。分公司。地址：待核。",
            "company_prefix_before_salary": "申请人：林安。联系电话：13800000000。\n单位名称：上海样例云科有限公司浦东分公司。合同约定月薪：12000元。",
            "qualified_company_before_salary": "申请人：林安。联系电话：13800000000。\n单位名称：上海样例云科有限公司（主体待核）。合同约定月薪：12000元。",
            "company_prefix_before_credit_code": "申请人：林安\n被申请人：上海样例云科有限公司浦东分公司。统一社会信用代码：待核。",
            "qualified_company_before_credit_code": "申请人：林安\n被申请人：上海样例云科有限公司（主体待核）。统一社会信用代码：待核。",
            "unknown_field_after_company": "申请人：林安\n被申请人：上海样例云科有限公司。其他含义待查：待核。",
            "company_prose_before_credit_code": "申请人：林安\n有人提到上海样例云科有限公司。统一社会信用代码：待核。",
            "wrong_company_role_before_credit_code": "申请人：林安\n申请人：上海样例云科有限公司。统一社会信用代码：待核。",
            "different_company_before_credit_code": "申请人：林安\n被申请人：另一家虚构公司。统一社会信用代码：待核。",
            "unlabelled_credit_code": "申请人：林安\n被申请人：上海样例云科有限公司。统一社会信用代码待核。",
            "credit_code_field_suffix": "申请人：林安\n被申请人：上海样例云科有限公司。统一社会信用代码或其他信息：待核。",
            "wrong_reference": IDENTITY_SOURCES[0],
        }
        for name, source in sources.items():
            with self.subTest(name=name), TemporaryDirectory() as temp:
                workspace = Path(temp)
                archive, case_id, requests = prepare(workspace, source)
                request = requests[0]
                if name == "wrong_reference":
                    next(b for b in request["locked_bindings"] if b["kind"] == "party")["archive_record_ref"] = "F-002"
                before = archive.read(case_id)
                result = render(request, workspace)
                self.assertFalse(result["ok"], result)
                self.assertIn("binding_mismatch", {e["code"] for e in result["errors"]})
                self.assertEqual(archive.read(case_id), before)
                self.assertFalse(list((workspace / ".arbibuddy" / "cases" / case_id).rglob("*.docx")))


if __name__ == "__main__":
    unittest.main()
