from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "references" / "rights-scan-and-unified-confirmation.md"


RIGHTS_CATALOG = (
    ("wage_arrears", "拖欠工资"),
    ("unilateral_pay_cut", "单方降薪工资差额"),
    ("sales_commission", "销售提成"),
    ("project_bonus", "项目奖金"),
    ("performance_bonus", "绩效奖金"),
    ("annual_bonus_or_thirteenth_salary", "年终奖金或十三薪"),
    ("workday_overtime", "工作日延时加班工资"),
    ("rest_day_overtime", "休息日加班工资"),
    ("statutory_holiday_overtime", "法定休假日加班工资"),
    ("statutory_allowance", "法定津贴补贴差额"),
    ("unused_annual_leave", "未休年休假工资"),
    ("other_paid_leave", "其他法定带薪假权利或工资待遇"),
    ("social_insurance", "社会保险"),
    ("housing_fund", "住房公积金"),
    ("unsigned_contract_double_wage", "未签书面劳动合同二倍工资"),
    ("termination_remedies", "离职、辞退相关补偿"),
)


class ModelLedRightsConfirmationContractTests(unittest.TestCase):
    def test_skill_routes_model_to_the_rights_and_confirmation_reference(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("references/rights-scan-and-unified-confirmation.md", skill)
        self.assertIn("一次自然语言确认", skill)
        self.assertNotIn("权益确认工具", skill)
        self.assertNotIn("权益扫描器", skill)

    def test_reference_has_one_complete_catalog_and_model_owned_boundaries(self):
        text = REFERENCE.read_text(encoding="utf-8")

        self.assertEqual(text.count("| 权益类别 |"), 1)
        for category_id, label in RIGHTS_CATALOG:
            self.assertIn(f"`{category_id}`", text)
            self.assertIn(label, text)
        self.assertIn("16 类", text)
        self.assertIn("排除已经提出", text)
        self.assertIn("模型决定何时停止追问、何时扫描", text)
        self.assertIn("不把扫描或确认实现为固定问卷、状态机", text)
        self.assertIn("selected", text)
        self.assertIn("pending", text)
        self.assertIn("excluded", text)
        self.assertIn("不处理不等于永久放弃实体权利", text)
        self.assertIn("无新事实不得循环", text)
        self.assertIn("事实或范围实质变化", text)

    def test_reference_defines_a_single_natural_language_confirmation(self):
        text = REFERENCE.read_text(encoding="utf-8")

        for required in (
            "关键事实",
            "权益范围",
            "主要口径",
            "重要假设",
            "拟交付内容",
            "当前档案 revision",
            "拒绝",
            "歧义",
            "重新确认",
            "只重新确认受影响范围",
            "工具调用",
            "不得新增重复确认",
        ):
            self.assertIn(required, text)
        self.assertRegex(text, re.compile(r"自然语言确认记录.*revision", re.DOTALL))

    def test_reference_keeps_user_visible_language_separate_from_archive_control_data(self):
        text = REFERENCE.read_text(encoding="utf-8")

        self.assertIn("用户只看到自然语言", text)
        for hidden in ("JSON", "record_id", "CONF-", "selected/pending/excluded"):
            self.assertIn(hidden, text)
        self.assertIn("不得向用户展示", text)
        self.assertIn("候选不等于违法或成立", text)


if __name__ == "__main__":
    unittest.main()
