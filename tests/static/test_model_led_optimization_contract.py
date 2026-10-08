from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedOptimizationContractTests(unittest.TestCase):
    def test_skill_front_loads_concrete_case_gate_and_render_last_rule(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        document_reference = (
            ROOT / "references" / "document-render.md"
        ).read_text(encoding="utf-8")

        self.assertIn("### 具体案件首动作", skill)
        self.assertIn("CaseArchive", skill)
        self.assertIn("renderer 必须是本轮最后一个会改变交付内容的动作", skill)
        self.assertIn("历史摘要", skill)
        self.assertIn("references/document-render.md", skill)
        self.assertIn("document.delivery-set-v1", document_reference)
        self.assertIn("presentation_authorized=false", skill)
        self.assertIn("presentation_authorized=false", document_reference)
        self.assertIn("工作区同一案件的公开 `CaseArchive.read`", skill)

    def test_case_archive_reference_describes_only_public_operations(self):
        reference = (
            ROOT / "references" / "model-led-case-archive.md"
        ).read_text(encoding="utf-8")

        for operation in ("archive.create", ".read(case_id)", ".commit({"):
            with self.subTest(operation=operation):
                self.assertIn(operation, reference)
        self.assertIn("不得先列目录、把平台 memory/`USER.md` 当作案件事实来源", reference)
        self.assertIn("Runtime Adapter transport", reference)
        self.assertIn("不得读取", reference)

    def test_workbuddy_no_delivery_contract_is_harness_generated(self):
        reference = (
            ROOT / "docs" / "agents" / "workbuddy-acceptance.md"
        ).read_text(encoding="utf-8")

        self.assertIn("build-no-delivery-contract", reference)
        self.assertRegex(reference, r"contract\s*不由人工编辑")
        self.assertIn("workbuddy-trace-root", reference)
        self.assertIn("两个 session", reference)

    def test_recovered_read_only_transport_retry_is_strict_in_ml03(self):
        reference = (
            ROOT / "docs" / "agents" / "workbuddy-acceptance.md"
        ).read_text(encoding="utf-8")

        self.assertIn("recoverable_case_archive_transport_retry", reference)
        self.assertIn("ML03-v2 恢复-only", reference)
        self.assertIn("即使随后恢复，也计入恢复协议失败", reference)
        self.assertIn("宿主读写仅作运行时诊断观察", reference)
        self.assertIn(
            "recovery_reference_read_before_current_archive", reference
        )
        self.assertIn("第二 session 首个成功的实质案件动作", reference)
        self.assertIn("没有产生案件写入或其他副作用", reference)

    def test_claude_lifecycle_and_model_variants_are_explicitly_separated(self):
        guide = (
            ROOT / "docs" / "platforms" / "codex-claude-code.md"
        ).read_text(encoding="utf-8")
        compatibility = json.loads(
            (
                ROOT
                / "docs"
                / "platforms"
                / "compatibility"
                / "claude-code-deepseek-2026-09-12.json"
            ).read_text(encoding="utf-8")
        )

        for status in (
            "not_discovered",
            "not_invoked",
            "loaded_not_executed",
            "executed",
            "executed_then_bypassed",
            "loaded_then_contract_discovery_bypass",
        ):
            self.assertIn(status, guide)
        self.assertEqual(
            [item["model_id"] for item in compatibility["model_variants"]],
            [
                "native-claude",
                "DeepSeek-V4-Flash",
                "deepseek-ai/DeepSeek-V4-Flash-0731",
            ],
        )
        self.assertFalse(compatibility["skill"]["description_changed_for_this_test"])
        for item in compatibility["model_variants"]:
            self.assertIn("client", item)
            self.assertIn("gateway", item)
            self.assertIn("skill_version", item)
            self.assertEqual(
                set(item["activation_evidence"]),
                {"natural_language", "slash"},
            )


if __name__ == "__main__":
    unittest.main()
