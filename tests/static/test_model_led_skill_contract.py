from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedSkillContractTests(unittest.TestCase):
    def test_recovery_route_is_visible_before_skill_body_and_skips_reference_preflight(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        description = skill.split("---", 2)[1]

        self.assertIn("Skill 必须是首个工具调用", description)
        self.assertIn("不先探查工作区", description)
        self.assertIn("恢复-only 不读取参考", skill)
        self.assertIn("Skill → 受管 read-current/read → 回复", skill)
        self.assertIn("恢复-only 不读取参考", skill)
        self.assertIn("不再读该参考", skill)

    def test_root_skill_routes_to_the_archive_tracer_without_legacy_orchestration(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (
            ROOT / "references" / "model-led-case-archive.md"
        ).read_text(encoding="utf-8")

        self.assertIn("references/model-led-case-archive.md", skill)
        self.assertIn("公开操作只有 `create`、`read`、`commit`", skill)
        self.assertIn("每轮只提出一个需要用户回答的问题", skill)
        self.assertIn("用户可见回复只呈现自然语言的事实变化", skill)
        self.assertIn("所有内部恢复、测试、验证和诊断先转换成业务结果", skill)
        self.assertIn("恢复硬停", skill)
        self.assertIn("恢复-only 回合不生成恢复报告", skill)
        self.assertIn("平台 memory、全局 `USER.md`、历史摘要", skill)
        self.assertIn("不扫描父级/兄弟目录", skill)
        self.assertIn("平台 memory 的开关、宿主注入、后台自动写入和模型读写只属于客户端诊断", reference)
        self.assertIn("create`、`read`、`commit` 三个档案操作", reference)
        self.assertNotIn("PublicTurn", skill)
        self.assertNotIn("PublicRuntime", skill)
        self.assertNotIn("agent_task_ready", skill)
        self.assertNotIn("advance/status", skill)

    def test_restart_and_document_exit_are_front_loaded_and_executable(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        archive_reference = (
            ROOT / "references" / "model-led-case-archive.md"
        ).read_text(encoding="utf-8")
        document_reference = (
            ROOT / "references" / "document-render.md"
        ).read_text(encoding="utf-8")

        self.assertLess(skill.index("## 案件入口（先执行）"), skill.index("## 主路径"))
        self.assertIn("`read-current` transport", skill)
        self.assertIn("案情档案永不展示", skill)
        self.assertIn("不得用工作区根目录 Markdown", skill)
        self.assertIn("scripts.documents.runtime_cli", skill)
        self.assertIn("受管 view", skill)
        self.assertIn("read-current", archive_reference)
        self.assertIn("恢复-only 回合的硬停", archive_reference)
        self.assertIn("恢复-only 回合只允许一次承载公开", (ROOT / "references" / "public-interface.md").read_text(encoding="utf-8"))
        self.assertIn("scripts.documents.runtime_cli", document_reference)
        self.assertIn("runtime_cli --workspace \"<workspace_root>\" view", document_reference)
        self.assertIn(".arbibuddy/runtime-input/", document_reference)

    def test_skill_routes_conditional_delivery_and_verification_details_to_references(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        document_reference = (
            ROOT / "references" / "document-render.md"
        ).read_text(encoding="utf-8")
        legal_reference = (
            ROOT / "references" / "legal-verification.md"
        ).read_text(encoding="utf-8")

        self.assertIn("references/document-render.md", skill)
        self.assertIn("references/legal-verification.md", skill)
        self.assertIn("受管 view", skill)
        self.assertNotIn("retry_managed_view_with_access", skill)
        self.assertIn("retry_managed_view_with_access", document_reference)
        self.assertIn("读取仍失败时", document_reference)
        self.assertIn("### 触发动态核验", legal_reference)
        self.assertIn("确需动态核验时做一次集中官方来源检索", legal_reference)
        self.assertIn("无法取得足以核验的正文与来源信息", legal_reference)

    def test_recovery_only_rule_allows_the_managed_read_transport_and_forbids_other_probe(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        start = skill.index("恢复硬停：")
        end = skill.index("\n\n读取成功后", start)
        recovery_rule = skill[start:end]

        self.assertIn("承载公开 `read-current` 或 `read` 的单条受管 transport", recovery_rule)
        self.assertIn("固定", recovery_rule)
        self.assertIn("`cd <installed_skill_root> && python ...` 前缀", recovery_rule)
        self.assertIn(
            "python -B -X utf8 -m scripts.case_archive.cli --root \"<workspace_root>\" read-current",
            recovery_rule,
        )
        self.assertIn("属于这一次允许的档案调用", recovery_rule)
        self.assertIn("除该调用外", recovery_rule)
        self.assertTrue(
            all(marker in recovery_rule for marker in ("Bash", "PowerShell", "Read", "Glob"))
        )
        self.assertNotIn("不允许先调用 Bash、PowerShell", recovery_rule)
        self.assertIn("读取成功后立即结束工具阶段", recovery_rule)

        archive_reference = (
            ROOT / "references" / "model-led-case-archive.md"
        ).read_text(encoding="utf-8")
        reference_start = archive_reference.index("### 恢复-only 回合的硬停")
        reference_rule = archive_reference[reference_start:]
        self.assertIn("承载公开 `read-current` 或已知 `case_id` 的 `read` 的单条受管 transport", reference_rule)
        self.assertIn("除该 transport 外", reference_rule)
        self.assertIn(
            'cd "<installed_skill_root>" && python -B -X utf8 -m scripts.case_archive.cli --root "<workspace_root>" read-current',
            reference_rule,
        )
        self.assertNotIn("任何 Bash/PowerShell 探查", reference_rule)

    def test_new_case_and_archive_recovery_have_distinct_cross_client_transports(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references" / "model-led-case-archive.md").read_text(encoding="utf-8")

        self.assertIn("“继续梳理”本身不表示当前工作区已有档案", skill)
        self.assertIn("Codex、Claude Code 和 WorkBuddy 均使用以上同一档案 transport", reference)
        self.assertIn("执行目录必须是运行上下文给出的已安装 Skill 根目录", reference)
        self.assertIn("不得把命令从工作区根目录启动", reference)
        self.assertIn("不加管道、重定向、分号或内联 Python", reference)
        self.assertIn("没有对应的 JSON 信封、`ok` 为 `false` 或命令非零退出时", reference)

    def test_runtime_treats_installed_skill_tree_as_read_only(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("安装 Skill 树只读", skill)
        self.assertIn("调试结论只向用户反馈", skill)

    def test_wage_reference_separates_active_wage_limitation_from_period_proof(self):
        reference = (
            ROOT / "references" / "model-led" / "wage-analysis.md"
        ).read_text(encoding="utf-8")
        archive_reference = (
            ROOT / "references" / "model-led-case-archive.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "劳动关系存续期间因拖欠劳动报酬发生争议，不受通常一年仲裁时效期间限制",
            reference,
        )
        self.assertIn("具体欠薪月份用于确定请求期间、金额和证据对应", reference)
        self.assertIn(
            "不得仅因具体月份待核实，就把在职欠薪表述为仲裁时效起算不明",
            reference,
        )
        self.assertIn("劳动关系终止后应自终止之日起一年内提出", reference)
        self.assertIn("通用风险清单不自动进入当前案件", archive_reference)
        self.assertIn(
            "仅缺少具体欠薪月份时，把它记录为请求期间、金额和证据对应的待核实项",
            archive_reference,
        )
        self.assertIn(
            "用户没有提供企业注销、经营异常或财产转移迹象时",
            archive_reference,
        )

    def test_new_archive_package_does_not_import_the_retired_orchestration_layer(self):
        package_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "scripts" / "case_archive").glob("*.py")
        )
        self.assertNotIn("scripts.core_workflow", package_text)
        self.assertNotIn("PublicTurn", package_text)
        self.assertNotIn("next-turn", package_text)


if __name__ == "__main__":
    unittest.main()
