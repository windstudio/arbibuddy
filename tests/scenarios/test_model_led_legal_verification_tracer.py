from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.case_archive import CaseArchive
from scripts.legal_verification import LegalVerificationTracer


ROOT = Path(__file__).resolve().parents[2]


class _NaturalLanguageLegalScenarioModel:
    """Scenario adapter: the model uses existing browsing, then the archive tool."""

    def __init__(self, workspace_root: Path):
        self.archive = CaseArchive(workspace_root)
        self.verifier = LegalVerificationTracer(ROOT)
        self.last_assessment: dict[str, object] | None = None
        self.last_recorded: dict[str, object] | None = None
        self.last_policy: dict[str, bool] | None = None

    @staticmethod
    def model_policy(*, outcome: str, materially_depends_on_current_rule: bool) -> dict[str, bool]:
        """Scenario-only model decision described by legal-verification.md."""

        return {
            "analysis_allowed": True,
            "calculation_allowed": True,
            "candidate_allowed": True,
            "external_final_allowed": (
                outcome == "verified" or not materially_depends_on_current_rule
            ),
        }

    def handle_dynamic_failure(self) -> str:
        created = self.archive.create(
            {"jurisdiction": "上海市", "initial_goal": "梳理上海工资争议"}
        )
        case_id = created["result"]["case_id"]
        observation = {
            "scope": "上海市2026年最低工资标准",
            "trigger": "dynamic_value",
            "outcome": "unavailable",
            "official_sources": [
                {
                    "title": "上海市人力资源和社会保障局相关公开通知",
                    "url": "https://rsj.sh.gov.cn/example/minimum-wage",
                    "jurisdiction": "上海市",
                }
            ],
            "accessed_on": "2026-09-07",
            "jurisdiction": "上海市",
            "impact": "最低工资数值暂不能作为本案当前定稿依据。",
        }
        self.last_assessment = self.verifier.assess(observation)
        self.last_recorded = self.verifier.record(
            self.archive,
            case_id=case_id,
            expected_revision=0,
            observation=observation,
        )
        self.last_policy = self.model_policy(
            outcome="unavailable", materially_depends_on_current_rule=True
        )
        if self.last_policy["external_final_allowed"]:
            return "可以直接形成外发定稿。"
        return "官方来源暂时无法完成动态核验，但我会继续一般分析、计算和候选草稿；如果要形成实质依赖该数值的外发定稿，需要先补足现行依据。"

    def handle_dynamic_success(self) -> str:
        created = self.archive.create(
            {"jurisdiction": "上海市", "initial_goal": "核对上海最低工资"}
        )
        case_id = created["result"]["case_id"]
        recorded = self.verifier.record(
            self.archive,
            case_id=case_id,
            expected_revision=0,
            observation={
                "scope": "上海市2026年最低工资标准",
                "trigger": "dynamic_value",
                "outcome": "verified",
                "official_sources": [
                    {
                        "title": "上海市人力资源和社会保障局关于调整本市最低工资标准的通知",
                        "url": "https://rsj.sh.gov.cn/example/minimum-wage",
                        "final_url": "https://rsj.sh.gov.cn/example/minimum-wage",
                        "jurisdiction": "上海市",
                        "content_sha256": "a" * 64,
                    }
                ],
                "accessed_on": "2026-09-07",
                "jurisdiction": "上海市",
                "impact": "可用于本案涉及期间的最低工资口径。",
            },
        )
        if not recorded["ok"]:
            return "暂时无法把已核验依据写入案情档案。"
        return "我已根据官方来源《上海市人力资源和社会保障局关于调整本市最低工资标准的通知》（https://rsj.sh.gov.cn/example/minimum-wage）完成当前最低工资口径的核验（访问日期：2026-09-07；适用地区：上海市），并会说明它对本案的影响。"


class ModelLedLegalVerificationScenarioTests(unittest.TestCase):
    def test_skill_and_reference_explain_layered_triggers_without_retired_authority_protocol(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references" / "legal-verification.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("references/legal-verification.md", skill)
        for required in (
            "稳定全国基础法",
            "地方规则",
            "动态数值",
            "来源冲突",
            "高风险最终化",
            "一般分析、计算和候选草稿继续",
            "authority",
        ):
            self.assertIn(required, reference)
        self.assertNotIn("authority JSON", skill)
        self.assertIn("候选 URL 状态机", reference)
        self.assertIn("用户重试轮次", reference)

    def test_network_block_keeps_user_visible_explanation_and_minimal_archive_record(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageLegalScenarioModel(Path(temporary_root))

            response = model.handle_dynamic_failure()

            self.assertIn("一般分析、计算和候选草稿", response)
            self.assertIn("外发定稿", response)
            self.assertIn("先补足现行依据", response)
            self.assertNotIn("可以直接形成外发定稿", response)
            self.assertIsNotNone(model.last_assessment)
            self.assertTrue(model.last_assessment["ok"])
            self.assertEqual(model.last_assessment["result"]["status"], "unavailable")
            self.assertIsNotNone(model.last_recorded)
            self.assertTrue(model.last_recorded["ok"])
            self.assertEqual(
                model.last_policy,
                {
                    "analysis_allowed": True,
                    "calculation_allowed": True,
                    "candidate_allowed": True,
                    "external_final_allowed": False,
                },
            )
            for internal in ("JSON", "source_ids", "authority.json", "AUTH-001", "revision", ".arbibuddy"):
                self.assertNotIn(internal, response)
            case_dirs = list((Path(temporary_root) / ".arbibuddy" / "cases").iterdir())
            markdown = (case_dirs[0] / "案情档案.md").read_text(encoding="utf-8")
            self.assertIn("官方来源暂未完成动态核验", markdown)
            self.assertIn("访问日期：2026-09-07", markdown)
            self.assertIn("适用地区：上海市", markdown)
            self.assertNotIn("响应正文", markdown)
            self.assertNotIn("operation_digest", markdown)

    def test_verified_source_is_explained_to_the_user_and_recorded_minimally(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageLegalScenarioModel(Path(temporary_root))

            response = model.handle_dynamic_success()

            self.assertIn("官方来源", response)
            self.assertIn("访问日期", response)
            self.assertIn("适用地区", response)
            self.assertIn("2026-09-07", response)
            self.assertIn("上海市", response)
            self.assertIn("上海市人力资源和社会保障局关于调整本市最低工资标准的通知", response)
            self.assertIn("https://rsj.sh.gov.cn/example/minimum-wage", response)
            for internal in ("JSON", "source_ids", "AUTH-001", "revision", ".arbibuddy"):
                self.assertNotIn(internal, response)


if __name__ == "__main__":
    unittest.main()
