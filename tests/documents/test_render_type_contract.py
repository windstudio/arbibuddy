"""Execute the published examples through the actual archive/render boundary."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import DOCUMENT_TYPES, create_delivery_set, describe_contract, render
from scripts.documents.runtime_cli import main
from scripts.documents.view import managed_delivery_view, managed_edit_context


class RenderTypeContractTests(unittest.TestCase):
    def test_documented_commit_correction_and_missing_reason_are_atomic(self):
        reference = (Path(__file__).resolve().parents[2] / "references/model-led-case-archive.md").read_text(encoding="utf-8")
        documented = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", reference, re.S)]
        correction = next(item for item in documented if item.get("expected_revision") == 1 and len(item.get("changes", [])) == 2)
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create()
            case_id = created["result"]["case_id"]
            setup = deepcopy(describe_contract("render", document_type="arbitration_application")["examples"]["worked_example"]["commit"])
            setup["case_id"] = case_id
            self.assertTrue(archive.commit(setup)["ok"])
            correction["case_id"] = case_id
            before = archive.read(case_id)["result"]
            invalid = deepcopy(correction)
            for change in invalid["changes"]:
                del change["reason"]
            rejected = archive.commit(invalid)
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual({e["path"] for e in rejected["errors"]},
                             {"commit.changes[0].reason", "commit.changes[1].reason"})
            self.assertEqual(archive.read(case_id)["result"], before)
            self.assertTrue(archive.commit(correction)["ok"])
            added = archive.commit({"case_id": case_id, "expected_revision": 2, "change_summary": "虚构待办", "changes": [
                {"operation": "append", "record_type": "next_step", "content_markdown": "整理材料。"}]})
            self.assertTrue(added["ok"], added)
            removal = {"case_id": case_id, "expected_revision": 3, "change_summary": "完成待办", "changes": [
                {"operation": "remove", "record_type": "next_step", "record_id": "N-001"}]}
            before = archive.read(case_id)["result"]
            self.assertFalse(archive.commit(removal)["ok"])
            self.assertEqual(archive.read(case_id)["result"], before)
            removal["changes"][0]["reason"] = "本项安排已完成，用户要求移除"
            self.assertTrue(archive.commit(removal)["ok"])

    def test_type_filter_is_explicit_and_preserves_legacy_description(self):
        legacy = describe_contract()
        self.assertIn("delivery_set", legacy)
        self.assertEqual(legacy["input"]["document_types"], list(DOCUMENT_TYPES))
        for kind in DOCUMENT_TYPES:
            contract = describe_contract("render", document_type=kind)
            self.assertEqual(contract["input"]["document_types"], [kind])
            self.assertEqual(list(contract["input"]["document_type_requirements"]), [kind])
            self.assertEqual(contract["input"]["document_type_requirements"][kind],
                             legacy["input"]["document_type_requirements"][kind])
            self.assertEqual(contract["examples"]["request"]["document_type"], kind)
            self.assertNotIn("delivery_set", contract)
        for operation, kind in ((None, "evidence_catalog"), ("view", "evidence_catalog"),
                                ("delivery-set", "evidence_catalog"), ("render", "unknown")):
            with self.assertRaises(ValueError):
                describe_contract(operation, document_type=kind)

    def test_every_type_example_really_renders_and_authorizes_its_complete_set(self):
        for kind in DOCUMENT_TYPES:
            with self.subTest(document_type=kind), TemporaryDirectory() as temp:
                workspace = Path(temp)
                contract = describe_contract("render", document_type=kind)
                example = deepcopy(contract["examples"]["worked_example"])
                archive = CaseArchive(workspace)
                created = archive.create(example["create"])
                self.assertTrue(created["ok"], created)
                case_id = created["result"]["case_id"]
                commit = example["commit"]
                commit["case_id"] = case_id
                committed = archive.commit(commit)
                self.assertTrue(committed["ok"], committed)
                revision = committed["result"]["revision"]
                selection = example["delivery_set"]
                selection.update(case_id=case_id, archive_revision=revision)
                selected = create_delivery_set(selection, workspace)
                self.assertTrue(selected["ok"], selected)
                for request in example["render_requests"]:
                    request.update(case_id=case_id, archive_revision=revision,
                                   delivery_set_id=selected["result"]["delivery_set_id"])
                    result = render(request, workspace)
                    self.assertTrue(result["ok"], result)
                view = managed_delivery_view(workspace=workspace, case_id=case_id)
                self.assertFalse(view["stopped"], view)
                self.assertEqual(len(view["presentation_files"]), 4 if kind == "arbitration_application" else 2)
                context = managed_edit_context(workspace=workspace, case_id=case_id)
                self.assertTrue(context["ok"], context)
                for document in context["result"]["documents"]:
                    self.assertEqual(document["render_requirements"],
                                     describe_contract("render", document_type=document["document_type"])["input"]["document_type_requirements"][document["document_type"]])

    def test_transport_filters_without_case_io_and_rejects_invalid_combinations(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--workspace", str(workspace), "describe", "--operation", "render",
                             "--document-type", "evidence_catalog"])
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["result"]["input"]["document_types"], ["evidence_catalog"])
            for args in (("--document-type", "evidence_catalog"),
                         ("--operation", "view", "--document-type", "evidence_catalog"),
                         ("--operation", "render", "--document-type", "unknown")):
                with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                    self.assertEqual(main(["--workspace", str(workspace), "describe", *args]), 2)
            self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
