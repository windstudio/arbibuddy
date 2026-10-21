"""Public editing seam: recover rendered text without retained input or logs."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.documents.public import create_delivery_set, describe_contract, render
from scripts.documents.runtime_cli import main
from scripts.documents.view import managed_edit_context, managed_delivery_view
from scripts.platform_paths import path_for_io
from tests.documents.test_model_led_document_render import (
    _application_request, _create_archive, _evidence_request,
)
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


def _snapshot(workspace):
    return {str(p.relative_to(workspace)): sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob("*") if p.is_file()}


def _deliver(workspace, *, complete=True, mixed_numbering=False):
    archive, case_id, revision = _create_archive(workspace)
    selection = create_delivery_set({
        "case_id": case_id, "archive_revision": revision, "mode": "external_final",
        "requested_document_types": ["arbitration_application"], "confirmation_refs": [],
    }, workspace)
    assert selection["ok"], selection
    for factory in (_application_request, _evidence_request) if complete else (_application_request,):
        request = factory(case_id, revision)
        if factory is _application_request:
            request["sections"][0]["full_text"] += "联系电话：13800000000。"
            if mixed_numbering:
                request["sections"][2]["full_text"] = describe_contract(
                    "render", document_type="arbitration_application"
                )["input"]["document_type_requirements"]["arbitration_application"]["numbered_section_input"]["example"]["full_text"]
                request = with_occurrence_bindings(request)
        request["delivery_set_id"] = selection["result"]["delivery_set_id"]
        response = render(request, workspace)
        assert response["ok"], response
    return archive, case_id, revision, selection["result"]["delivery_set_id"]


class EditContextTests(unittest.TestCase):
    def test_narrow_set_description_constructs_valid_request_and_keeps_guards(self):
        contract = describe_contract("delivery-set")
        self.assertNotIn("locked_binding", contract["input"])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = deepcopy(contract["examples"]["request"])
            request.update(case_id=case_id, archive_revision=revision)
            alias = create_delivery_set(request, workspace)
            self.assertTrue(alias["ok"], alias)
            direct_request = {k: v for k, v in request.items() if k != "requested_templates"}
            direct_request["requested_document_types"] = ["arbitration_application"]
            direct = create_delivery_set(direct_request, workspace)
            self.assertTrue(direct["ok"], direct)
            self.assertEqual(alias["result"]["expected_document_types"], direct["result"]["expected_document_types"])
            for bad in ({**request, "authority_refs": []}, {**request, "requested_document_types": ["arbitration_application"]}):
                self.assertFalse(create_delivery_set(bad, workspace)["ok"])

    def test_context_is_complete_read_only_and_optionally_filters_after_set_validation(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, set_id = _deliver(workspace)
            before = _snapshot(workspace)
            context = managed_edit_context(workspace=workspace, case_id=case_id, delivery_set_id=set_id)
            self.assertTrue(context["ok"], context)
            self.assertEqual(before, _snapshot(workspace))
            result = context["result"]
            self.assertEqual(result["archive"]["revision"], revision)
            self.assertIn("13800000000", result["archive"]["markdown"])
            self.assertEqual([d["document_type"] for d in result["documents"]], ["arbitration_application", "evidence_catalog"])
            self.assertNotIn("present_files_arguments", result)
            application, catalog = result["documents"]
            requirements = describe_contract("render")["input"]["document_type_requirements"]
            for document in result["documents"]:
                self.assertEqual(document["render_requirements"], requirements[document["document_type"]])
            self.assertEqual(catalog["render_requirements"]["sections"], ["标题", "五列表格"])
            self.assertIn("联系电话：13800000000。", "\n".join(b.get("text", "") for b in application["blocks"]))
            self.assertTrue(any(b.get("numbering") for b in application["blocks"]))
            table = next(b for b in catalog["blocks"] if b["kind"] == "table")
            self.assertEqual(table["rows"][0][1][0]["text"], "证据名称")
            self.assertEqual(table["rows"][1][1][0]["text"], "工资流水")
            self.assertTrue(all(b["source_locator"].startswith("word/document.xml/body/") for b in application["blocks"]))
            selected = managed_edit_context(workspace=workspace, case_id=case_id, document_types=["evidence_catalog"])
            self.assertTrue(selected["ok"], selected)
            self.assertEqual(len(selected["result"]["documents"]), 1)

    def test_invalid_or_changed_sources_never_return_partial_text(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, set_id = _deliver(workspace)
            for kwargs in ({"delivery_set_id": "0" * 32}, {"document_types": []},
                           {"document_types": ["evidence_catalog", "evidence_catalog"]},
                           {"document_types": ["arbitration_defense"]}):
                response = managed_edit_context(workspace=workspace, case_id=case_id, **kwargs)
                self.assertFalse(response["ok"], response)
                self.assertIsNone(response["result"])
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            docx = path_for_io(view["presentation_files"][0]["path"])
            docx.write_bytes(docx.read_bytes() + b"preview changed bytes")
            response = managed_edit_context(workspace=workspace, case_id=case_id, document_types=["evidence_catalog"])
            self.assertFalse(response["ok"], response)
            self.assertIsNone(response["result"])

    def test_incomplete_and_stale_sets_are_rejected(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _set_id = _deliver(workspace, complete=False)
            self.assertFalse(managed_edit_context(workspace=workspace, case_id=case_id)["ok"])
            changed = archive.commit({"case_id": case_id, "expected_revision": revision,
                "change_summary": "虚构事实变化", "changes": [{"operation": "append", "record_type": "fact", "content_markdown": "用户更正电话。"}]})
            self.assertTrue(changed["ok"], changed)
            self.assertFalse(managed_edit_context(workspace=workspace, case_id=case_id)["ok"])

    def test_identity_failure_is_not_bypassed(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, _revision, _set_id = _deliver(workspace)
            with patch("scripts.case_archive.service._RUNTIME_IDENTITY.preflight", return_value={"verified": False, "failures": ["changed"]}):
                result = managed_edit_context(workspace=workspace, case_id=case_id)
            self.assertFalse(result["ok"], result)
            self.assertIsNone(result["result"])

    def test_transport_consumes_input_and_describes_each_operation_without_case_io(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            for operation in ("delivery-set", "render", "view", "edit-context"):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(["--workspace", str(workspace), "describe", "--operation", operation])
                self.assertEqual(code, 0, output.getvalue())
                self.assertTrue(json.loads(output.getvalue())["ok"])
            self.assertEqual(list(workspace.iterdir()), [])
            _archive, case_id, _revision, _set_id = _deliver(workspace)
            source = workspace / ".arbibuddy/runtime-input/edit.json"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps({"case_id": case_id}), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--workspace", str(workspace), "edit-context", "--input", str(source)])
            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(source.exists())
            self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_phone_correction_rebuilds_from_rendered_blocks_without_old_request(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision, _set_id = _deliver(workspace, mixed_numbering=True)
            context = managed_edit_context(workspace=workspace, case_id=case_id)["result"]
            original_blocks = deepcopy(context["documents"])
            # A deterministic fixture stands in for model reconstruction, not a timing benchmark.
            # Model reconstruction follows the published input rule: text lacks
            # displayed Word numbers; only numbered blocks regain input markers.
            paragraphs = []
            number = 0
            for block in context["documents"][0]["blocks"]:
                if block["kind"] != "paragraph" or not block["text"]:
                    continue
                text = block["text"]
                if block["numbering"]:
                    number += 1
                    text = f"{number}. {text}"
                paragraphs.append(text)
            self.assertEqual(paragraphs.pop(0), "劳动人事争议仲裁申请书")
            section_text = {"applicant": [paragraphs.pop(0)], "respondent": [paragraphs.pop(0)]}
            active = None
            for line in paragraphs:
                if line in {"仲裁请求", "事实与理由"}:
                    active = {"仲裁请求": "requests", "事实与理由": "grounds"}[line]
                    section_text[active] = []
                elif line == "此致":
                    active = "closing"
                    section_text[active] = [line]
                else:
                    section_text[active].append(line)
            section_text["applicant"] = [s.replace("13800000000", "13900000000") for s in section_text["applicant"]]
            # Change only the fact and truthful confirmation scope within one commit.
            committed = archive.commit({"case_id": case_id, "expected_revision": revision,
                "change_summary": "按用户指示更正电话", "changes": [
                    {"operation": "replace", "record_type": "fact", "record_id": "F-001", "reason": "用户明确更正联系电话", "content_markdown": "申请人：张三；身份证号：310000199001010000；联系地址：上海市示例路1号；联系电话：13900000000。"},
                    {"operation": "replace", "record_type": "confirmation", "record_id": "CONF-001", "reason": "用户更正电话并保留原确认范围", "content_markdown": "用户自然语言确认摘要：原确认范围不变，用户仅更正电话。\n绑定档案修订版本：2\n关键事实：劳动关系和工资差额已陈述。\n权益范围：本次处理工资差额。\n主要口径：以档案中的工资差额计算为准。\n重要假设：未核实事项留待核对。\n拟交付内容：仲裁申请书和证据目录。"},
                ]})
            self.assertTrue(committed["ok"], committed)
            revision = committed["result"]["revision"]
            selection = create_delivery_set({"case_id": case_id, "archive_revision": revision, "mode": "external_final", "requested_templates": ["labor-arbitration-application"], "confirmation_refs": []}, workspace)
            self.assertTrue(selection["ok"], selection)
            common = {"case_id": case_id, "archive_revision": revision, "mode": "external_final", "template_version": "1.0.0", "delivery_set_id": selection["result"]["delivery_set_id"], "confirmation_refs": []}
            headings = {"applicant": "申请人", "respondent": "被申请人", "requests": "仲裁请求", "grounds": "事实与理由", "closing": "落款"}
            bindings = [{"kind": kind, "archive_record_ref": ref, "rendered_value": value} for kind, ref, value in (
                ("document_heading", "F-004", "劳动人事争议仲裁申请书"), ("party", "F-001", "申请人：张三"),
                ("party", "F-002", "被申请人：示例公司"), ("claim", "CL-001", "支付工资差额"),
                ("amount", "CAL-001", "6000.00元"), ("date", "F-003", "2026年9月7日"), ("signature", "F-001", "申请人：张三"),
            )]
            application = with_occurrence_bindings({**common, "document_type": "arbitration_application", "title": "劳动人事争议仲裁申请书", "sections": [
                {"section_id": key, "heading": headings[key], "full_text": "\n".join(lines)} for key, lines in section_text.items()], "locked_bindings": bindings})
            self.assertTrue((rendered := render(application, workspace))["ok"], rendered)
            table = next(b for b in context["documents"][1]["blocks"] if b["kind"] == "table")
            rows = ["|".join("\n".join(p["text"] for p in cell) for cell in row[:4]) for row in table["rows"][1:]]
            catalog = {**common, "document_type": "evidence_catalog", "title": "证据目录", "sections": [{"section_id": "title", "heading": "标题", "full_text": "证据目录"}, {"section_id": "evidence", "heading": "五列表格", "full_text": "\n".join(rows)}], "locked_bindings": [
                {"kind": "document_heading", "archive_record_ref": "F-006", "rendered_value": "证据目录"},
                {"kind": "evidence", "archive_record_ref": "E-001", "rendered_value": "工资流水｜银行流水｜证明工资差额"}]}
            self.assertTrue((rendered := render(catalog, workspace))["ok"], rendered)
            updated = managed_edit_context(workspace=workspace, case_id=case_id)
            self.assertTrue(updated["ok"], updated)
            for before, after in zip(original_blocks, updated["result"]["documents"], strict=True):
                expected = json.dumps(before["blocks"], ensure_ascii=False).replace("13800000000", "13900000000")
                self.assertEqual(json.loads(expected), after["blocks"])
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            self.assertEqual(len(view["presentation_files"]), 4)
            self.assertEqual(view["current_archive_revision"], revision)


if __name__ == "__main__":
    unittest.main()
