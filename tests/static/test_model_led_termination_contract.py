from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedTerminationContractTests(unittest.TestCase):
    def test_skill_routes_both_termination_references_and_shared_confirmation_boundary(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for reference in (
            "references/model-led/forced-termination.md",
            "references/model-led/unlawful-termination.md",
            "references/model-led/termination-analysis-and-confirmation.md",
        ):
            self.assertIn(reference, skill)
        self.assertIn("历史解除索赔", skill)
        self.assertIn("计划实施解除", skill)
        self.assertIn("送达事实待确认", skill)
        self.assertNotIn("调用解除分析器", skill)

    def test_each_domain_reference_contains_the_complete_model_owned_analysis_card(self):
        required = (
            "成立样本",
            "不成立样本",
            "信息不足样本",
            "事实冲突样本",
            "用户陈述",
            "证据支持事实",
            "分析假设",
            "主张分析卡",
            "证据缺口",
            "对方可能抗辩",
            "初步判断",
            "全国基线",
            "上海增强",
            "法源强度",
            "最后核验日",
            "动态核验",
            "金额口径",
            "互斥",
            "重复获偿",
            "历史回归",
            "送达",
        )
        for name in ("forced-termination", "unlawful-termination"):
            text = (ROOT / "references" / "model-led" / f"{name}.md").read_text(
                encoding="utf-8"
            )
            for phrase in required:
                self.assertIn(phrase, text, f"{name} missing {phrase}")

    def test_shared_reference_defines_three_routes_and_narrow_confirmation(self):
        text = (
            ROOT / "references" / "model-led" / "termination-analysis-and-confirmation.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "仅分析历史解除索赔",
            "计划实施解除",
            "已发送但送达事实待确认",
            "核对通知内容、发送时间和送达事实",
            "主体",
            "理由",
            "日期",
            "程序",
            "风险",
            "替代方案",
            "用户选择",
            "action_type",
            "action_scope",
            "risk_summary",
            "alternatives_presented",
            "user_choice",
            "confirmed_at",
            "archive_revision",
            "来源引用",
            "CaseArchive.commit",
            "revision 不一致",
            "来源过期",
            "重复确认",
            "普通统一确认",
            "不自动发送",
            "不自动提交",
            "不替用户作出解除决定",
            "免于新的动作确认并不免于实体和程序审查",
            "只有寄件截图或本人自述",
        ):
            self.assertIn(phrase, text, phrase)
        for forbidden in (
            "scripts.cli_orchestration",
            "python -m scripts",
            '"target": "forced-termination"',
            '"target": "unlawful-termination"',
            "PublicTurn",
            "advance/status",
        ):
            self.assertNotIn(forbidden, text)

    def test_06_migration_tracking_classifies_both_termination_domains(self):
        text = (ROOT / "docs/agents/domain-migration.md").read_text(
            encoding="utf-8"
        )
        section = text.split("## 06 号工单", 1)[-1]
        self.assertIn("forced-termination", section)
        self.assertIn("unlawful-termination", section)
        self.assertIn("tests/scenarios/test_model_led_termination_analysis_and_confirmation.py", section)
        self.assertNotIn("未分类", section)
        self.assertNotIn("TBD", section)
        for row in section.splitlines():
            if row.startswith("|") and "---" not in row:
                cells = [cell.strip() for cell in row.strip("|").split("|")]
                if len(cells) >= 3 and cells[0] != "来源/分支/场景":
                    self.assertIn(cells[2], {"preserve", "revise", "calculator", "obsolete", "unresolved"})

    def test_termination_amount_formulas_are_public_and_do_not_choose_the_legal_route(self):
        registry = (ROOT / "references" / "amount-formulas.v1.json").read_text(
            encoding="utf-8"
        )
        for formula_id in ("economic_compensation", "unlawful_termination_compensation"):
            self.assertIn(f'"{formula_id}"', registry)
        reference = (ROOT / "references" / "amount-calculate.md").read_text(encoding="utf-8")
        self.assertIn("经济补偿", reference)
        self.assertIn("违法解除或终止赔偿金", reference)
        self.assertIn("互斥情景", reference)
        self.assertIn("不判断法律适用", reference)


if __name__ == "__main__":
    unittest.main()
