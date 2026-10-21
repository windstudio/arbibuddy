"""Exercise request-only repair through the public archive/set/render/view seam."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import create_delivery_set, describe_contract, render
from scripts.documents.view import managed_delivery_view


def prepared_bundle(workspace):
    example = deepcopy(describe_contract("render", document_type="arbitration_application")["examples"]["worked_example"])
    archive = CaseArchive(workspace)
    case_id = archive.create()["result"]["case_id"]
    example["commit"]["case_id"] = case_id
    committed = archive.commit(example["commit"])
    assert committed["ok"], committed
    revision = committed["result"]["revision"]
    selection = example["delivery_set"]
    selection.update(case_id=case_id, archive_revision=revision)
    selected = create_delivery_set(selection, workspace)
    assert selected["ok"], selected
    for request in example["render_requests"]:
        request.update(case_id=case_id, archive_revision=revision,
                       delivery_set_id=selected["result"]["delivery_set_id"])
    return archive, case_id, example["render_requests"]


class RenderRequestRepairTests(unittest.TestCase):
    def test_bad_excerpts_explain_the_specific_request_fault_without_writing(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepared_bundle(workspace)
            before = archive.read(case_id)
            for fault in ("section_not_found", "excerpt_not_found", "excerpt_not_unique",
                          "excerpt_value_missing", "excerpt_value_mismatch", "incomplete_or_reused_value"):
                with self.subTest(fault=fault):
                    request = deepcopy(requests[0])
                    date = next(b for b in request["locked_bindings"] if b["kind"] == "date")
                    target = date["occurrences"][0]
                    if fault == "section_not_found":
                        target["section_id"] = "missing-section"
                    elif fault == "excerpt_not_found":
                        target["text"] = "2026年9月8日"
                    elif fault == "excerpt_not_unique":
                        request["sections"][-1]["full_text"] += "\n2026年9月7日"
                    elif fault == "excerpt_value_missing":
                        target["text"] = "申请人：张三"
                    elif fault == "excerpt_value_mismatch":
                        fact_amount = next(b for b in request["locked_bindings"] if b["kind"] == "fact_amount")
                        fact_amount["occurrences"][0]["text"] = "请求支付上述工资差额6000.00元。"
                    else:
                        request["sections"][2]["full_text"] = "请求支付工资差额16000.00元。"
                        amount = next(b for b in request["locked_bindings"] if b["kind"] == "amount")
                        amount["occurrences"][0]["text"] = "6000.00元"
                    result = render(request, workspace)
                    self.assertFalse(result["ok"], result)
                    matching = [e for e in result["errors"] if e.get("reason") == fault]
                    self.assertTrue(matching, result)
                    self.assertTrue(all(e.get("repair_scope") == "request_only" for e in matching), result)
                    self.assertEqual(archive.read(case_id), before)
                    self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_omitted_title_and_explicit_registered_title_are_the_same_delivery(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepared_bundle(workspace)
            before = archive.read(case_id)
            for request in requests:
                request.pop("title", None)
                first = render(request, workspace)
                self.assertTrue(first["ok"], first)
                explicit = deepcopy(request)
                explicit["title"] = describe_contract("render", document_type=request["document_type"])["input"]["document_type_requirements"][request["document_type"]]["title"]
                self.assertEqual(render(explicit, workspace), first)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            self.assertEqual(len(view["presentation_files"]), 4)
            self.assertEqual(archive.read(case_id), before)
            fields = describe_contract("render")["input"]["fields"]
            self.assertFalse(next(f for f in fields if f["name"] == "title")["required"])

    def test_two_dates_in_one_excerpt_are_repaired_without_archive_or_set_changes(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepared_bundle(workspace)
            request = requests[0]
            facts = request["sections"][3]
            sentence = "文书日期为2026年9月7日，履行期限为2026年9月15日。"
            facts["full_text"] += "\n" + sentence
            original_date = next(b for b in request["locked_bindings"] if b["kind"] == "date")
            original_date["occurrences"].append({"section_id": facts["section_id"], "text": sentence})
            other_date = {"kind": "date", "archive_record_ref": "F-004", "rendered_value": "2026年9月15日",
                          "occurrences": [{"section_id": facts["section_id"], "text": sentence}]}
            request["locked_bindings"].append(other_date)
            before = archive.read(case_id)
            # Capture the actual public set rather than guessing its filename.
            control_files = {p: p.read_bytes() for p in (workspace / ".arbibuddy" / "cases" / case_id).rglob("*delivery*json")}
            self.assertTrue(control_files)
            rejected = render(request, workspace)
            self.assertFalse(rejected["ok"], rejected)
            located = [e for e in rejected["errors"] if ".occurrences[" in e["path"]]
            self.assertEqual(len(located), 2, rejected)
            self.assertTrue(all(e.get("reason") == "excerpt_multiple_values" and e.get("repair_scope") == "request_only" for e in located), rejected)
            self.assertTrue(all("2" in e["message"] for e in located), rejected)
            self.assertEqual(archive.read(case_id), before)
            self.assertEqual({p: p.read_bytes() for p in control_files}, control_files)
            original_date["occurrences"][-1]["text"] = "文书日期为2026年9月7日"
            other_date["occurrences"][0]["text"] = "履行期限为2026年9月15日"
            for item in requests:
                self.assertTrue((result := render(item, workspace))["ok"], result)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            self.assertEqual(view["delivery_set"]["delivery_set_id"], request["delivery_set_id"])
            self.assertEqual(len(view["presentation_files"]), 4)
            self.assertEqual(archive.read(case_id), before)

    def test_mixed_title_and_source_errors_preserve_the_distinct_repair_scopes(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, requests = prepared_bundle(workspace)
            before = archive.read(case_id)
            request = requests[0]
            request["title"] = "劳动人事争议仲裁申请书（候选稿）"
            party = next(b for b in request["locked_bindings"] if b["kind"] == "party")
            party["rendered_value"] = "申请人：张三甲"
            result = render(request, workspace)
            self.assertFalse(result["ok"], result)
            title_error = next(e for e in result["errors"] if e["path"] == "title")
            source_error = next(e for e in result["errors"] if e.get("reason") == "source_value_mismatch")
            self.assertEqual(title_error.get("repair_scope"), "request_only")
            self.assertEqual(source_error.get("repair_scope"), "review_source")
            self.assertEqual(archive.read(case_id), before)
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())


if __name__ == "__main__":
    unittest.main()
