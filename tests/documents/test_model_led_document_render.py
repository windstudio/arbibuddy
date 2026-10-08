from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree

from scripts.case_archive import CaseArchive
from scripts.documents.public import _DocumentRenderer as DocumentRenderer
from scripts.platform_paths import path_for_io
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


def _create_archive(workspace: Path) -> tuple[CaseArchive, str, int]:
    archive = CaseArchive(workspace)
    created = archive.create({"initial_goal": "准备核心仲裁文书"})
    assert created["ok"], created
    case_id = created["result"]["case_id"]
    committed = archive.commit(
        {
            "case_id": case_id,
            "expected_revision": 0,
            "change_summary": "记录文书渲染测试所需的脱敏档案事实",
            "changes": [
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "申请人：张三；身份证号：310000199001010000；"
                        "联系地址：上海市示例路1号；联系电话：13800000000。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": (
                        "被申请人：示例公司；住所：上海市示例路2号；"
                        "法定代表人：李四。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "申请日期：2026年9月7日；签名：张三。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "文书标题：劳动人事争议仲裁申请书。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "文书标题：劳动人事争议仲裁答辩书。",
                },
                {
                    "operation": "append",
                    "record_type": "fact",
                    "content_markdown": "文书标题：证据目录。",
                },
                {
                    "operation": "append",
                    "record_type": "claim",
                    "content_markdown": "仲裁请求：支付工资差额。",
                },
                {
                    "operation": "append",
                    "record_type": "calculation",
                    "content_markdown": "确定性计算结果：6000.00元。",
                },
                {
                    "operation": "append",
                    "record_type": "evidence",
                    "content_markdown": (
                        "证据名称：工资流水；证据内容：银行流水；"
                        "证明目的：证明工资差额。"
                    ),
                },
                {
                    "operation": "append",
                    "record_type": "confirmation",
                    "content_markdown": (
                        "用户自然语言确认摘要：确认按当前工资差额范围准备仲裁材料。\n"
                        "绑定档案修订版本：1\n"
                        "关键事实：劳动关系和工资差额已陈述。\n"
                        "权益范围：本次处理工资差额。\n"
                        "主要口径：以档案中的工资差额计算为准。\n"
                        "重要假设：未核实事项留待核对。\n"
                        "拟交付内容：仲裁申请书和证据目录。"
                    ),
                },
            ],
        }
    )
    assert committed["ok"], committed
    return archive, case_id, committed["result"]["revision"]


def _application_request(case_id: str, revision: int, *, mode: str = "external_final") -> dict:
    request = {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "arbitration_application",
        "template_version": "1.0.0",
        "mode": mode,
        "title": "劳动人事争议仲裁申请书",
        "sections": [
            {
                "section_id": "applicant",
                "heading": "申请人",
                "full_text": "申请人：张三，身份证号：310000199001010000。",
            },
            {
                "section_id": "respondent",
                "heading": "被申请人",
                "full_text": "被申请人：示例公司，法定代表人：李四。",
            },
            {
                "section_id": "requests",
                "heading": "仲裁请求",
                "full_text": "请求支付工资差额6000.00元。",
            },
            {
                "section_id": "facts_and_reasons",
                "heading": "事实与理由",
                "full_text": "双方存在劳动关系，被申请人未支付已经发生的工资差额。",
            },
            {
                "section_id": "closing",
                "heading": "落款",
                "full_text": "此致\n上海市示例劳动人事争议仲裁委员会\n申请人：张三\n2026年9月7日",
            },
        ],
        "locked_bindings": [
            {
                "kind": "document_heading",
                "archive_record_ref": "F-004",
                "rendered_value": "劳动人事争议仲裁申请书",
            },
            {
                "kind": "party",
                "archive_record_ref": "F-001",
                "rendered_value": "申请人：张三",
            },
            {
                "kind": "party",
                "archive_record_ref": "F-002",
                "rendered_value": "被申请人：示例公司",
            },
            {
                "kind": "claim",
                "archive_record_ref": "CL-001",
                "rendered_value": "支付工资差额",
            },
            {
                "kind": "amount",
                "archive_record_ref": "CAL-001",
                "rendered_value": "6000.00元",
            },
            {
                "kind": "date",
                "archive_record_ref": "F-003",
                "rendered_value": "2026年9月7日",
            },
            {
                "kind": "signature",
                "archive_record_ref": "F-001",
                "rendered_value": "申请人：张三",
            },
        ],
    }
    return with_occurrence_bindings(request)


def _defense_request(case_id: str, revision: int, *, mode: str = "external_final") -> dict:
    request = _application_request(case_id, revision, mode=mode)
    request.update(
        {
            "document_type": "arbitration_defense",
            "title": "劳动人事争议仲裁答辩书",
            "sections": [
                {
                    "section_id": "defender",
                    "heading": "答辩人",
                    "full_text": "答辩人：张三，身份证号：310000199001010000。",
                },
                {
                    "section_id": "opposing_party",
                    "heading": "被答辩人",
                    "full_text": "被答辩人：示例公司，法定代表人：李四。",
                },
                {
                    "section_id": "opinions",
                    "heading": "答辩意见",
                    "full_text": "请求驳回对方关于工资差额的请求。",
                },
                {
                    "section_id": "closing",
                    "heading": "落款",
                    "full_text": "此致\n上海市示例劳动人事争议仲裁委员会\n答辩人：张三\n2026年9月7日",
                },
            ],
            "locked_bindings": [
                {
                    "kind": "document_heading",
                    "archive_record_ref": "F-005",
                    "rendered_value": "劳动人事争议仲裁答辩书",
                },
                {
                    "kind": "party",
                    "archive_record_ref": "F-001",
                    "rendered_value": "答辩人：张三",
                },
                {
                    "kind": "party",
                    "archive_record_ref": "F-002",
                    "rendered_value": "被答辩人：示例公司",
                },
                {
                    "kind": "claim",
                    "archive_record_ref": "CL-001",
                    "rendered_value": "支付工资差额",
                },
                {
                    "kind": "date",
                    "archive_record_ref": "F-003",
                    "rendered_value": "2026年9月7日",
                },
                {
                    "kind": "signature",
                    "archive_record_ref": "F-001",
                    "rendered_value": "答辩人：张三",
                },
            ],
        }
    )
    return with_occurrence_bindings(request)


def _evidence_request(case_id: str, revision: int, *, mode: str = "external_final") -> dict:
    return {
        "case_id": case_id,
        "archive_revision": revision,
        "document_type": "evidence_catalog",
        "template_version": "1.0.0",
        "mode": mode,
        "title": "证据目录",
        "sections": [
            {"section_id": "title", "heading": "标题", "full_text": "证据目录"},
            {
                "section_id": "table",
                "heading": "五列表格",
                "full_text": (
                    "| 编号 | 证据名称 | 证据内容 | 证明目的 | 页码 |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| 1 | 工资流水 | 银行流水 | 证明工资差额 |  |"
                ),
            },
        ],
        "locked_bindings": [
            {
                "kind": "document_heading",
                "archive_record_ref": "F-006",
                "rendered_value": "证据目录",
            },
            {
                "kind": "evidence",
                "archive_record_ref": "E-001",
                "rendered_value": "工资流水｜银行流水｜证明工资差额",
            },
        ],
    }


def _rewrite_checklist(checklist_path: Path, manifest_path: Path, text: str) -> None:
    checklist_bytes = text.encode("utf-8")
    checklist_path.write_bytes(checklist_bytes)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["verification_checklist"]["sha256"] = hashlib.sha256(
        checklist_bytes
    ).hexdigest()
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        .encode("utf-8")
    )


class ModelLedDocumentRenderTests(unittest.TestCase):
    def test_target_without_docx_returns_runtime_unavailable_before_staging(self):
        """The WorkBuddy-like interpreter must fail closed before DOCX staging."""

        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            script = (
                "import json, sys\n"
                "sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')\n"
                "from scripts.documents.public import render\n"
                "request = json.loads(sys.stdin.read())\n"
                "print(json.dumps(render(request, sys.argv[1]), ensure_ascii=False))\n"
            )
            process = subprocess.run(
                [sys.executable, "-S", "-B", "-c", script, str(workspace)],
                cwd=Path(__file__).resolve().parents[2],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ok"], result)
            self.assertIn(
                "document_runtime_unavailable",
                {error["code"] for error in result["errors"]},
            )
            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
            self.assertFalse(output_root.exists())

    def test_document_render_contract_is_self_describing(self):
        with TemporaryDirectory() as temporary_root:
            contract = DocumentRenderer(Path(temporary_root)).describe()

        self.assertEqual(contract["contract_version"], "document.render-v1")
        self.assertEqual(contract["operation"], "document.render")
        fields = {field["name"] for field in contract["input"]["fields"]}
        self.assertTrue(
            {
                "case_id",
                "archive_revision",
                "document_type",
                "template_version",
                "mode",
                "title",
                "sections",
                "locked_bindings",
            } <= fields
        )
        self.assertEqual(
            set(contract["input"]["document_types"]),
            {
                "employment_obligation_demand_letter",
                "forced_termination_notice",
                "arbitration_application",
                "arbitration_defense",
                "evidence_catalog",
                "company_deregistration_restriction_request_shanghai",
                "property_preservation_application",
                "enforcement_application",
            },
        )
        self.assertEqual(set(contract["input"]["modes"]), {"candidate", "external_final"})
        self.assertIn("success", contract["examples"])
        self.assertIn("aggregate_failure", contract["examples"])
        self.assertIn("recovery", contract["examples"])
        self.assertIsInstance(contract["examples"]["success"], dict)
        self.assertIsInstance(contract["examples"]["aggregate_failure"], dict)
        self.assertIsInstance(contract["examples"]["recovery"], dict)
        self.assertTrue(contract["examples"]["success"]["ok"])
        self.assertTrue(
            {
                "contract_version",
                "operation",
                "ok",
                "result",
                "errors",
                "warnings",
            }
            <= set(contract["examples"]["success"])
        )
        self.assertTrue(
            {
                "delivery_state",
                "document_type",
                "mode",
                "archive_revision",
                "canonical_docx",
                "verification_checklist",
                "machine_manifest",
                "content_digest",
                "validation_summary",
            }
            <= set(contract["examples"]["success"]["result"])
        )
        self.assertTrue(
            {
                "case_id",
                "archive_revision",
                "document_type",
                "template_version",
                "mode",
                "title",
                "sections",
                "locked_bindings",
            }
            <= set(contract["examples"]["request"])
        )
        self.assertTrue(
            {
                "unsupported_document_type",
                "template_version_mismatch",
                "archive_revision_conflict",
                "missing_section",
                "binding_not_found",
                "binding_mismatch",
                "undeclared_placeholder",
                "placeholder_in_final",
                "internal_content_leak",
                "ooxml_invalid",
                "publication_conflict",
                "permission_denied",
                "io_failure",
            }
            <= set(contract["errors"]["codes"])
        )

    def test_external_final_renders_real_docx_and_separate_checklist(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            result = DocumentRenderer(workspace).render(
                _application_request(case_id, revision)
            )

            self.assertTrue(result["ok"], result)
            payload = result["result"]
            self.assertEqual(payload["delivery_state"], "final_ready")
            docx = Path(payload["canonical_docx"])
            checklist = Path(payload["verification_checklist"])
            manifest = Path(payload["machine_manifest"])
            self.assertTrue(docx.is_file())
            self.assertTrue(checklist.is_file())
            self.assertTrue(manifest.is_file())
            self.assertNotEqual(docx, checklist)
            checklist_text = checklist.read_text(encoding="utf-8")
            self.assertIn("仅供核对，请勿外发", checklist_text)
            for internal in ("case_id", "F-001", "CL-001", "CAL-001", "sha256"):
                self.assertNotIn(internal, checklist_text)
            self.assertNotIn("双方存在劳动关系", manifest.read_text(encoding="utf-8"))

            with ZipFile(docx) as package:
                self.assertIn("word/document.xml", package.namelist())
                xml = package.read("word/document.xml").decode("utf-8")
            visible_text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml))
            for expected in (
                "劳动人事争议仲裁申请书",
                "申请人：张三",
                "请求支付工资差额6000.00元。",
                "双方存在劳动关系，被申请人未支付已经发生的工资差额。",
            ):
                self.assertIn(expected, visible_text)
            for internal in ("F-001", "CL-001", "CAL-001", "case_id", "sha256"):
                self.assertNotIn(internal, visible_text)

    def test_candidate_with_declared_privacy_placeholder_is_ready_but_not_promoted(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision, mode="candidate")
            request["sections"][0]["full_text"] = "申请人：张三，联系电话：【待补：联系电话】。"
            request["placeholders"] = [
                {
                    "placeholder_id": "applicant_phone",
                    "text": "【待补：联系电话】",
                }
            ]
            request["locked_bindings"] = [
                item
                for item in request["locked_bindings"]
                if item["kind"] != "signature"
            ]
            renderer = DocumentRenderer(workspace)

            candidate = renderer.render(request)

            self.assertTrue(candidate["ok"], candidate)
            self.assertEqual(candidate["result"]["delivery_state"], "candidate_ready")
            request["mode"] = "external_final"
            promoted = renderer.render(request)
            self.assertFalse(promoted["ok"], promoted)
            self.assertIn(
                "placeholder_in_final",
                {error["code"] for error in promoted["errors"]},
            )

    def test_defense_and_evidence_catalog_external_final_are_real_docx_results(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            renderer = DocumentRenderer(workspace)

            defense = renderer.render(_defense_request(case_id, revision))
            evidence = renderer.render(_evidence_request(case_id, revision))

            self.assertTrue(defense["ok"], defense)
            self.assertTrue(evidence["ok"], evidence)
            self.assertEqual(defense["result"]["delivery_state"], "final_ready")
            self.assertEqual(evidence["result"]["delivery_state"], "final_ready")
            with ZipFile(Path(defense["result"]["canonical_docx"])) as package:
                defense_xml = package.read("word/document.xml").decode("utf-8")
            self.assertIn("劳动人事争议仲裁答辩书", defense_xml)
            with ZipFile(Path(evidence["result"]["canonical_docx"])) as package:
                xml = package.read("word/document.xml").decode("utf-8")
            self.assertIn('w:orient="landscape"', xml)
            self.assertIn("<w:tblHeader", xml)
            self.assertIn('w:type="fixed"', xml)
            for header in ("编号", "证据名称", "证据内容", "证明目的", "页码"):
                self.assertIn(header, xml)

    def test_each_supported_document_type_can_produce_a_candidate_with_a_declared_gap(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            renderer = DocumentRenderer(workspace)
            requests = (
                _application_request(case_id, revision, mode="candidate"),
                _defense_request(case_id, revision, mode="candidate"),
                _evidence_request(case_id, revision, mode="candidate"),
            )
            for index, request in enumerate(requests):
                with self.subTest(document_type=request["document_type"]):
                    if request["document_type"] == "evidence_catalog":
                        request["sections"][1]["full_text"] = (
                            "| 编号 | 证据名称 | 证据内容 | 证明目的 | 页码 |\n"
                            "| 1 | 【待补：证据名称】 | 原始材料 | 证明待核事项 |  |"
                        )
                        request["locked_bindings"] = request["locked_bindings"][:1]
                    else:
                        request["sections"][0]["full_text"] += " 联系方式：【待补：联系电话】。"
                        request["locked_bindings"] = [
                            item
                            for item in request["locked_bindings"]
                            if item["kind"] != "signature"
                        ]
                    request["placeholders"] = [
                        {"placeholder_id": f"gap_{index}", "text": "【待补：联系电话】" if request["document_type"] != "evidence_catalog" else "【待补：证据名称】"}
                    ]
                    result = renderer.render(request)
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(result["result"]["delivery_state"], "candidate_ready")

    def test_each_document_type_covers_candidate_promotion_and_fact_conflict(self):
        factories = (
            ("arbitration_application", _application_request, 0, "F-001", "fact"),
            ("arbitration_defense", _defense_request, 0, "F-001", "fact"),
            ("evidence_catalog", _evidence_request, 1, "E-001", "evidence"),
        )
        for document_type, factory, gap_index, conflict_ref, record_type in factories:
            with self.subTest(document_type=document_type), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision = _create_archive(workspace)
                renderer = DocumentRenderer(workspace)
                candidate = factory(case_id, revision, mode="candidate")
                if document_type == "evidence_catalog":
                    candidate["sections"][1]["full_text"] = (
                        "| 编号 | 证据名称 | 证据内容 | 证明目的 | 页码 |\n"
                        "| 1 | 【待补：证据材料名称】 | 原始材料 | 证明待核事项 |  |"
                    )
                    candidate["locked_bindings"] = candidate["locked_bindings"][:1]
                    placeholder_text = "【待补：证据材料名称】"
                else:
                    candidate["sections"][gap_index]["full_text"] += " 联系方式：【待补：联系电话】。"
                    candidate["locked_bindings"] = [
                        item
                        for item in candidate["locked_bindings"]
                        if item["kind"] != "signature"
                    ]
                    placeholder_text = "【待补：联系电话】"
                candidate["placeholders"] = [
                    {"placeholder_id": "document_gap", "text": placeholder_text}
                ]

                candidate_result = renderer.render(candidate)

                self.assertTrue(candidate_result["ok"], candidate_result)
                self.assertEqual(candidate_result["result"]["delivery_state"], "candidate_ready")
                candidate["mode"] = "external_final"
                promoted = renderer.render(candidate)
                self.assertFalse(promoted["ok"], promoted)
                self.assertIn(
                    "placeholder_in_final",
                    {error["code"] for error in promoted["errors"]},
                )

                changed = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "记录事实冲突以验证三类文书共同阻断",
                        "changes": [
                            {
                                "operation": "replace",
                                "record_type": record_type,
                                "record_id": conflict_ref,
                                "content_markdown": "事实冲突：该锁定要素存在两种说法。",
                                "reason": "测试事实冲突门禁",
                            }
                        ],
                    }
                )
                self.assertTrue(changed["ok"], changed)
                conflict_request = factory(
                    case_id,
                    changed["result"]["revision"],
                    mode="external_final",
                )
                conflict_result = renderer.render(conflict_request)
                self.assertFalse(conflict_result["ok"], conflict_result)
                self.assertIn(
                    "fact_conflict",
                    {error["code"] for error in conflict_result["errors"]},
                )

    def test_validation_aggregates_unsupported_type_version_missing_section_and_internal_leak(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["document_type"] = "unsupported_document_type"
            request["template_version"] = "0.0.0"
            request["sections"] = request["sections"][:-1]
            request["sections"][2]["full_text"] = "请执行 python -m scripts.documents.cli --root D:\\private。"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            codes = {error["code"] for error in result["errors"]}
            self.assertTrue(
                {"unsupported_document_type", "template_version_mismatch", "internal_content_leak"}
                <= codes
            )
            request["document_type"] = "arbitration_application"
            request["template_version"] = "1.0.0"
            missing = DocumentRenderer(workspace).render(request)
            self.assertIn("missing_section", {error["code"] for error in missing["errors"]})
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_evidence_catalog_with_missing_table_section_returns_validation_error(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _evidence_request(case_id, revision)
            request["sections"] = request["sections"][:1]

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("missing_section", {error["code"] for error in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_evidence_catalog_checklist_uses_its_typed_policy(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)

            result = DocumentRenderer(workspace).render(
                _evidence_request(case_id, revision)
            )

            self.assertTrue(result["ok"], result)
            checklist_path = Path(result["result"]["verification_checklist"])
            checklist_text = checklist_path.read_text(encoding="utf-8")
            self.assertIn("证据引用或附件完整性：", checklist_text)
            self.assertNotIn("金额及计算依据：", checklist_text)
            self.assertNotIn("落款角色与日期：", checklist_text)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)

    def test_amount_document_receipt_requires_a_distinct_amount_checklist_item(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = DocumentRenderer(workspace).render(
                _application_request(case_id, revision)
            )
            self.assertTrue(rendered["ok"], rendered)
            checklist_path = Path(rendered["result"]["verification_checklist"])
            manifest_path = Path(rendered["result"]["machine_manifest"])
            checklist = checklist_path.read_text(encoding="utf-8")
            checklist = "\n".join(
                line
                for line in checklist.splitlines()
                if not line.startswith("- 金额及计算依据：")
            ) + "\n"
            _rewrite_checklist(checklist_path, manifest_path, checklist)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"], view)
            self.assertIn("核验清单", view.get("reason", ""), view)

    def test_external_final_receipt_requires_distinct_closing_role_and_date_item(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = DocumentRenderer(workspace).render(
                _application_request(case_id, revision)
            )
            self.assertTrue(rendered["ok"], rendered)
            checklist_path = Path(rendered["result"]["verification_checklist"])
            manifest_path = Path(rendered["result"]["machine_manifest"])
            checklist = checklist_path.read_text(encoding="utf-8")
            checklist = "\n".join(
                line
                for line in checklist.splitlines()
                if not line.startswith("- 落款角色与日期：")
            ) + "\n"
            _rewrite_checklist(checklist_path, manifest_path, checklist)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"], view)
            self.assertIn("核验清单", view.get("reason", ""), view)

    def test_candidate_checklist_does_not_require_a_final_closing_item(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = DocumentRenderer(workspace).render(
                _application_request(case_id, revision, mode="candidate")
            )

            self.assertTrue(rendered["ok"], rendered)
            checklist = Path(
                rendered["result"]["verification_checklist"]
            ).read_text(encoding="utf-8")
            self.assertIn("- 金额及计算依据：", checklist)
            self.assertNotIn("- 落款角色与日期：", checklist)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)

    def test_receipt_rejects_keyword_pile_without_typed_checklist_items(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = DocumentRenderer(workspace).render(
                _application_request(case_id, revision)
            )
            self.assertTrue(rendered["ok"], rendered)
            checklist_path = Path(rendered["result"]["verification_checklist"])
            manifest_path = Path(rendered["result"]["machine_manifest"])
            checklist_text = (
                "《劳动人事争议仲裁申请书》提交前核验清单\n\n"
                "本清单仅供核对，请勿外发。\n\n"
                "## 一、文书状态\n"
                "- 外发定稿，仍请在提交前完成人工核对。\n\n"
                "## 二、请核对的内容\n"
                "- 核对主体、身份、请求、事实、金额、日期、证据、落款和材料。\n"
                "- 核对所有内容后再提交。\n"
                "- 核对文件是否齐全。\n"
                "- 提交前请在 Word/WPS 中预览。\n"
            )
            _rewrite_checklist(checklist_path, manifest_path, checklist_text)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)

            self.assertTrue(view["stopped"], view)
            self.assertEqual(view["presentation_files"], [])
            self.assertIn("核验清单", view.get("reason", ""), view)

    def test_archive_revision_and_binding_errors_do_not_create_output(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision - 1)
            stale = DocumentRenderer(workspace).render(request)
            self.assertFalse(stale["ok"], stale)
            self.assertIn("archive_revision_conflict", {error["code"] for error in stale["errors"]})

            request["archive_revision"] = revision
            request["locked_bindings"][4]["rendered_value"] = "7000.00元"
            request["locked_bindings"].append(
                {
                    "kind": "claim",
                    "archive_record_ref": "CL-999",
                    "rendered_value": "另一项请求",
                }
            )
            failed = DocumentRenderer(workspace).render(request)
            self.assertFalse(failed["ok"], failed)
            codes = {error["code"] for error in failed["errors"]}
            self.assertIn("binding_mismatch", codes)
            self.assertIn("binding_not_found", codes)
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

            archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录冲突以验证文书阻断",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "content_markdown": "事实冲突：申请人身份信息存在两种说法。",
                            "reason": "测试事实冲突门禁",
                        }
                    ],
                }
            )
            request = _application_request(case_id, revision + 1)
            conflict = DocumentRenderer(workspace).render(request)
            self.assertFalse(conflict["ok"], conflict)
            self.assertIn("fact_conflict", {error["code"] for error in conflict["errors"]})

    def test_distinct_dates_from_one_archive_fact_can_be_bound_separately(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录同一就业事实中的两个独立日期",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户于2024年3月1日入职，"
                                "并于2025年8月31日离职。"
                            ),
                        }
                    ],
                }
            )
            date_ref = committed["result"]["generated_record_ids"][0]
            request = _application_request(
                case_id, committed["result"]["revision"], mode="candidate"
            )
            request["sections"][3]["full_text"] += (
                "用户于2024年3月1日入职，并于2025年8月31日离职。"
            )
            request["locked_bindings"].extend(
                [
                    {
                        "kind": "date",
                        "archive_record_ref": date_ref,
                        "rendered_value": "2024年3月1日",
                        "occurrences": [
                            {"section_id": "facts_and_reasons", "text": "2024年3月1日"}
                        ],
                    },
                    {
                        "kind": "date",
                        "archive_record_ref": date_ref,
                        "rendered_value": "2025年8月31日",
                        "occurrences": [
                            {"section_id": "facts_and_reasons", "text": "2025年8月31日"}
                        ],
                    },
                ]
            )

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "candidate_ready")

            duplicate = json.loads(json.dumps(request, ensure_ascii=False))
            duplicate["locked_bindings"].append(
                json.loads(json.dumps(duplicate["locked_bindings"][-1]))
            )
            rejected = DocumentRenderer(workspace).render(duplicate)

            self.assertFalse(rejected["ok"], rejected)
            self.assertIn("binding_mismatch", {error["code"] for error in rejected["errors"]})
            self.assertEqual(rejected["errors"][0]["path"], "locked_bindings[9]")

    def test_render_does_not_publish_a_revision_changed_during_docx_build(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            from scripts.documents import public as public_module

            original_builder = public_module._build_docx

            def concurrent_update(path: Path, payload: dict[str, object]) -> None:
                changed = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "模拟文书生成期间的并发更正",
                        "changes": [
                            {
                                "operation": "replace",
                                "record_type": "calculation",
                                "record_id": "CAL-001",
                                "reason": "模拟另一调用更新档案",
                                "content_markdown": "确定性计算结果：9000.00元。",
                            }
                        ],
                    }
                )
                self.assertTrue(changed["ok"], changed)
                original_builder(path, payload)

            with patch.object(
                public_module, "_build_docx", side_effect=concurrent_update
            ):
                result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "archive_revision_conflict",
                {error["code"] for error in result["errors"]},
            )
            self.assertEqual(archive.read(case_id)["result"]["revision"], revision + 1)
            self.assertFalse(
                (
                    workspace
                    / ".arbibuddy"
                    / "cases"
                    / case_id
                    / "output"
                    / "arbitration_application"
                ).exists()
            )

    def test_external_final_rejects_heading_and_body_amount_drift(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            renderer = DocumentRenderer(workspace)

            heading_request = _application_request(case_id, revision)
            heading_request["locked_bindings"][0]["rendered_value"] = "另一份仲裁申请书"
            heading_result = renderer.render(heading_request)
            self.assertFalse(heading_result["ok"], heading_result)
            self.assertIn(
                "binding_mismatch",
                {error["code"] for error in heading_result["errors"]},
            )

            amount_request = _application_request(case_id, revision)
            amount_request["sections"][2]["full_text"] = "请求支付工资差额7000.00元。"
            amount_result = renderer.render(amount_request)
            self.assertFalse(amount_result["ok"], amount_result)
            self.assertIn(
                "binding_mismatch",
                {error["code"] for error in amount_result["errors"]},
            )
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_amount_binding_does_not_match_digits_inside_a_larger_calculation_result(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "把虚构计算结果更正为更大金额",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "calculation",
                            "record_id": "CAL-001",
                            "reason": "校验完整金额绑定",
                            "content_markdown": "确定性计算结果：16000.00元。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)

            request = _application_request(
                case_id, changed["result"]["revision"]
            )
            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertTrue(
                any(
                    error["code"] == "binding_mismatch"
                    and error["path"] == "locked_bindings[4].rendered_value"
                    for error in result["errors"]
                ),
                result["errors"],
            )
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_distinct_amount_bindings_can_coexist_in_one_document_section(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "增加独立的虚构加班计算结果",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "calculation",
                            "content_markdown": "确定性计算结果：1000.00元。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            request = _application_request(
                case_id, changed["result"]["revision"]
            )
            request["sections"][2]["full_text"] = (
                "请求支付工资差额6000.00元；另请求加班工资1000.00元。"
            )
            request["locked_bindings"].append(
                {
                    "kind": "amount",
                    "archive_record_ref": "CAL-002",
                    "rendered_value": "1000.00元",
                }
            )
            with_occurrence_bindings(request)

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "final_ready")

    def test_external_final_rejects_unbound_closing_date_and_missing_signature(self):
        for scenario in (
            "missing_date",
            "wrong_date",
            "missing_signature_text",
            "missing_signature_binding",
        ):
            with self.subTest(scenario=scenario), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision)
                closing = request["sections"][-1]
                if scenario == "missing_date":
                    closing["full_text"] = closing["full_text"].replace(
                        "\n2026年9月7日", ""
                    )
                elif scenario == "wrong_date":
                    closing["full_text"] = closing["full_text"].replace(
                        "2026年9月7日", "2026年9月8日"
                    )
                elif scenario == "missing_signature_text":
                    closing["full_text"] = closing["full_text"].replace(
                        "\n申请人：张三", ""
                    )
                else:
                    request["locked_bindings"] = [
                        binding
                        for binding in request["locked_bindings"]
                        if binding["kind"] != "signature"
                    ]

                result = DocumentRenderer(workspace).render(request)

                self.assertFalse(result["ok"], result)
                codes = {error["code"] for error in result["errors"]}
                if scenario == "missing_signature_binding":
                    self.assertIn("missing_input", codes)
                else:
                    self.assertIn("binding_mismatch", codes)
                self.assertFalse(
                    (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
                )

    def test_external_final_rejects_an_impossible_calendar_date_even_if_archive_matches(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "为日期校验写入虚构的无效日期",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-003",
                            "reason": "验证日历日期门禁",
                            "content_markdown": "申请日期：2026年2月31日；签名：张三。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            request = _application_request(
                case_id, changed["result"]["revision"]
            )
            date_binding = next(
                binding
                for binding in request["locked_bindings"]
                if binding["kind"] == "date"
            )
            date_binding["rendered_value"] = "2026年2月31日"
            request["sections"][-1]["full_text"] = request["sections"][-1][
                "full_text"
            ].replace("2026年9月7日", "2026年2月31日")
            with_occurrence_bindings(request)

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {error["code"] for error in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_external_final_rejects_signature_name_under_a_different_closing_role(self):
        for wrong_role in ("联系人",):
            with self.subTest(wrong_role=wrong_role), TemporaryDirectory() as temp:
                workspace = Path(temp)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision)
                closing = request["sections"][-1]
                closing["full_text"] = closing["full_text"].replace(
                    "申请人：张三", f"{wrong_role}：张三"
                )

                result = DocumentRenderer(workspace).render(request)

                self.assertFalse(result["ok"], result)
                self.assertIn(
                    "binding_mismatch",
                    {error["code"] for error in result["errors"]},
                )
                self.assertFalse(
                    (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
                )

    def test_external_final_rejects_an_impossible_date_outside_the_closing_section(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += "解除事实发生于2026年2月31日。"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {error["code"] for error in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_external_final_rejects_an_unbound_complete_date_in_body(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += "劳动关系自2026年8月1日起持续。"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {error["code"] for error in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_each_repeated_full_date_occurrence_requires_its_own_location(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += "申请日期仍为2026年9月7日。"
            date_binding = next(
                binding
                for binding in request["locked_bindings"]
                if binding["kind"] == "date"
            )
            date_binding["occurrences"] = [
                occurrence
                for occurrence in date_binding["occurrences"]
                if occurrence["section_id"] == "closing"
            ]

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {error["code"] for error in result["errors"]})
            self.assertFalse(
                (workspace / ".arbibuddy" / "cases" / case_id / "output").exists()
            )

    def test_external_final_accepts_the_same_date_when_each_body_occurrence_is_bound(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += "申请日期采用 ISO 形式：2026-09-07。"
            with_occurrence_bindings(request)

            result = DocumentRenderer(workspace).render(request)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "final_ready")

    def test_undeclared_placeholder_and_stale_confirmation_or_authority_are_recoverable(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision, mode="candidate")
            request["sections"][3]["full_text"] += " 待补：未声明项目【待补：仲裁案号】。"
            request["confirmation_refs"] = ["CONF-999"]
            request["authority_refs"] = ["AUTH-999"]

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            codes = {error["code"] for error in result["errors"]}
            self.assertIn("undeclared_placeholder", codes)
            self.assertIn("confirmation_stale", codes)
            self.assertIn("authority_stale", codes)

    def test_existing_confirmation_and_authority_records_must_match_current_revision_and_status(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录过期确认和未核验依据",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": "自然语言确认记录：用户确认。\n绑定档案修订版本：1",
                        },
                        {
                            "operation": "append",
                            "record_type": "authority",
                            "content_markdown": "核验事项：当前口径。\n核验结论：基线过期。",
                        },
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            current_revision = committed["result"]["revision"]
            request = _application_request(case_id, current_revision)
            request["confirmation_refs"] = ["CONF-001"]
            request["authority_refs"] = ["AUTH-001"]

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            codes = {error["code"] for error in result["errors"]}
            self.assertIn("confirmation_stale", codes)
            self.assertIn("authority_stale", codes)

    def test_record_ids_in_model_text_are_internal_leaks(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += " 内部引用F-001。"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("internal_content_leak", {error["code"] for error in result["errors"]})

    def test_xml_incompatible_text_is_a_stable_ooxml_error_without_output(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][3]["full_text"] += "\x01"

            result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("ooxml_invalid", {error["code"] for error in result["errors"]})
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_relationship_target_corruption_is_a_stable_ooxml_error_without_output(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            from scripts.documents import public as public_module
            from unittest.mock import patch

            original_builder = public_module._build_docx

            def corrupting_builder(path: Path, payload: dict[str, object]) -> None:
                original_builder(path, payload)
                with ZipFile(path) as package:
                    parts = {name: package.read(name) for name in package.namelist()}
                relationship_part = parts["word/_rels/document.xml.rels"]
                self.assertIn(b'Target="footer1.xml"', relationship_part)
                parts["word/_rels/document.xml.rels"] = relationship_part.replace(
                    b'Target="footer1.xml"', b'Target="missing-footer.xml"'
                )
                with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
                    for name, data in parts.items():
                        package.writestr(name, data)

            with patch.object(public_module, "_build_docx", side_effect=corrupting_builder):
                result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("ooxml_invalid", {error["code"] for error in result["errors"]})
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_non_a4_page_dimensions_are_rejected_as_ooxml_error(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            from scripts.documents import public as public_module
            from unittest.mock import patch

            original_builder = public_module._build_docx

            def letter_builder(path: Path, payload: dict[str, object]) -> None:
                original_builder(path, payload)
                with ZipFile(path) as package:
                    parts = {name: package.read(name) for name in package.namelist()}
                document = ElementTree.fromstring(parts["word/document.xml"])
                page = document.find(
                    ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgSz"
                )
                assert page is not None
                page.set(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}w",
                    "12240",
                )
                page.set(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}h",
                    "15840",
                )
                parts["word/document.xml"] = ElementTree.tostring(document, encoding="utf-8")
                with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
                    for name, data in parts.items():
                        package.writestr(name, data)

            with patch.object(public_module, "_build_docx", side_effect=letter_builder):
                result = DocumentRenderer(workspace).render(request)

            self.assertFalse(result["ok"], result)
            self.assertIn("ooxml_invalid", {error["code"] for error in result["errors"]})
            self.assertFalse((workspace / ".arbibuddy" / "cases" / case_id / "output").exists())

    def test_ooxml_io_and_publication_failures_leave_no_half_finished_document(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            renderer = DocumentRenderer(workspace)

            from unittest.mock import patch

            with patch("scripts.documents.public.audit_docx", side_effect=ValueError("bad XML")):
                ooxml = renderer.render(request)
            self.assertFalse(ooxml["ok"], ooxml)
            self.assertIn("ooxml_invalid", {error["code"] for error in ooxml["errors"]})
            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"
            self.assertFalse(output_root.exists())

            with patch("scripts.documents.public.Path.mkdir", side_effect=PermissionError("denied")):
                denied = renderer.render(request)
            self.assertFalse(denied["ok"], denied)
            self.assertIn("permission_denied", {error["code"] for error in denied["errors"]})

    def test_replay_is_idempotent_and_failed_replacement_rolls_back_to_old_canonical(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            renderer = DocumentRenderer(workspace)
            request = _application_request(case_id, revision)
            first = renderer.render(request)
            replay = renderer.render(request)
            self.assertTrue(first["ok"], first)
            self.assertEqual(replay, first)
            docx = Path(first["result"]["canonical_docx"])
            before = docx.read_bytes()

            changed = _application_request(case_id, revision)
            changed["sections"][3]["full_text"] = "双方存在劳动关系，工资差额事实已经由材料支持。"
            from unittest.mock import patch

            with patch(
                "scripts.documents.public.commit_generation_staging",
                side_effect=OSError("simulated publication interruption"),
            ):
                failed = renderer.render(changed)
            self.assertFalse(failed["ok"], failed)
            self.assertIn("io_failure", {error["code"] for error in failed["errors"]})
            self.assertEqual(docx.read_bytes(), before)
            self.assertEqual(list(docx.parent.glob("*.docx")), [docx])
            self.assertEqual(list(docx.parent.glob("*.staging-*")), [])

    def test_existing_unmanaged_output_is_a_publication_conflict(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            output = workspace / ".arbibuddy" / "cases" / case_id / "output" / "arbitration_application"
            output.mkdir(parents=True)
            foreign = output / "foreign.txt"
            foreign.write_text("not a managed render", encoding="utf-8")

            result = DocumentRenderer(workspace).render(_application_request(case_id, revision))

            self.assertFalse(result["ok"], result)
            self.assertIn("publication_conflict", {error["code"] for error in result["errors"]})
            self.assertEqual(foreign.read_text(encoding="utf-8"), "not a managed render")

    @unittest.skipUnless(os.name == "nt", "Windows extended-length path regression")
    def test_windows_long_workspace_path_renders_and_workbuddy_reads_the_receipt(self):
        temporary_root = Path(mkdtemp(prefix="arbibuddy-long-path-"))
        workspace = (
            temporary_root
            / ("workspace-" + "a" * 80)
            / ("cases-" + "b" * 80)
            / ("deliveries-" + "c" * 80)
        )
        try:
            path_for_io(workspace).mkdir(parents=True)
            self.assertGreater(len(str(workspace)), 260)
            _archive, case_id, revision = _create_archive(workspace)
            rendered = DocumentRenderer(workspace).render(
                _application_request(case_id, revision)
            )
            self.assertTrue(rendered["ok"], rendered)

            from scripts.documents.view import managed_delivery_view

            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            self.assertEqual(
                {item["kind"] for item in view["presentation_files"]},
                {"document", "checklist"},
            )
            for item in view["presentation_files"]:
                self.assertFalse(item["path"].startswith("\\\\?\\"))
                self.assertTrue(path_for_io(item["path"]).is_file(), item["path"])
        finally:
            # Clean the complete test-owned root with the long-path adapter.
            shutil.rmtree(path_for_io(temporary_root))


if __name__ == "__main__":
    unittest.main()
