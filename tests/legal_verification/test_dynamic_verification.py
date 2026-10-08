from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.case_archive import CaseArchive
from scripts.legal_verification import LegalVerificationTracer


ROOT = Path(__file__).resolve().parents[2]


class DynamicVerificationContractTests(unittest.TestCase):
    @staticmethod
    def _observation(**overrides: object) -> dict[str, object]:
        observation: dict[str, object] = {
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
            "impact": "可用于本案涉及期间的最低工资口径；不扩展为其他地区结论。",
        }
        observation.update(overrides)
        return observation

    def test_verified_official_observation_allows_current_rule_use_and_prepares_minimal_archive_change(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.assess(self._observation())

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["status"], "verified")
        change = result["result"]["archive_change"]
        self.assertEqual(change["operation"], "append")
        self.assertEqual(change["record_type"], "authority")
        self.assertIn("核验事项：上海市2026年最低工资标准", change["content_markdown"])
        self.assertIn("访问日期：2026-09-07", change["content_markdown"])
        self.assertIn("适用地区：上海市", change["content_markdown"])
        self.assertIn("影响范围：可用于本案涉及期间的最低工资口径", change["content_markdown"])
        self.assertNotIn("响应正文", change["content_markdown"])
        self.assertNotIn("source_ids", change["content_markdown"])
        self.assertIn("最终链接：https://rsj.sh.gov.cn/example/minimum-wage", change["content_markdown"])
        self.assertIn("内容 SHA-256：" + ("a" * 64), change["content_markdown"])

    def test_failed_dynamic_observation_preserves_status_for_model_led_downgrade(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.assess(
            self._observation(outcome="unavailable", trigger="local_rule")
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["status"], "unavailable")
        self.assertNotIn("continuation", result["result"])
        self.assertNotIn("external_final_blocked", result["result"])
        self.assertIn("暂未完成动态核验", result["result"]["archive_change"]["content_markdown"])

    def test_verified_observation_requires_final_url_and_content_digest_provenance(self):
        tracer = LegalVerificationTracer(ROOT)
        observation = self._observation()
        source = dict(observation["official_sources"][0])
        source.pop("final_url", None)
        source.pop("content_sha256", None)
        observation["official_sources"] = [source]

        result = tracer.assess(observation)

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("observation.official_sources[0].final_url", paths)
        self.assertIn("observation.official_sources[0].content_sha256", paths)

    def test_verified_provenance_rejects_cross_host_final_url_and_bad_digest(self):
        tracer = LegalVerificationTracer(ROOT)
        observation = self._observation()
        observation["official_sources"] = [
            {
                **observation["official_sources"][0],
                "final_url": "https://example.com/not-an-official-page",
                "content_sha256": "not-a-sha256",
            }
        ]

        result = tracer.assess(observation)

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("observation.official_sources[0].final_url", paths)
        self.assertIn("observation.official_sources[0].content_sha256", paths)

    def test_source_conflict_is_not_collapsed_into_verified_and_still_allows_candidate_work(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.assess(
            self._observation(
                outcome="source_conflict",
                trigger="source_conflict",
                impact="两个官方口径对同一期间的数值适用范围不一致，需保留条件情景。",
            )
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["status"], "source_conflict")
        self.assertIn("冲突", result["result"]["archive_change"]["content_markdown"])

    def test_stale_baseline_can_continue_general_analysis_without_being_called_current(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.assess(
            self._observation(
                outcome="stale",
                trigger="stale_baseline",
            )
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["status"], "stale")
        self.assertIn("超过复核日期", result["result"]["archive_change"]["content_markdown"])

    def test_url_presence_without_an_explicit_verification_conclusion_is_rejected(self):
        tracer = LegalVerificationTracer(ROOT)

        result = tracer.assess(self._observation(outcome="url_reachable"))

        self.assertFalse(result["ok"], result)
        self.assertIn("observation.outcome", {error["path"] for error in result["errors"]})

    def test_internal_observation_fields_are_rejected_instead_of_becoming_archive_payload(self):
        tracer = LegalVerificationTracer(ROOT)
        observation = self._observation()
        observation["response_body"] = "网页全文不应进入工具输入"
        observation["official_sources"] = [
            {
                "title": "官方来源",
                "url": "https://rsj.sh.gov.cn/example/minimum-wage",
                "jurisdiction": "上海市",
                "response_body": "网页全文不应进入档案",
            }
        ]

        result = tracer.assess(observation)

        self.assertFalse(result["ok"], result)
        paths = {error["path"] for error in result["errors"]}
        self.assertIn("observation.response_body", paths)
        self.assertIn("observation.official_sources[0].response_body", paths)

    def test_record_uses_the_existing_archive_commit_seam_and_writes_no_second_fact_source(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create(
                {"jurisdiction": "上海市", "initial_goal": "核对上海最低工资"}
            )
            case_id = created["result"]["case_id"]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=0,
                observation=self._observation(),
            )

            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["result"]["revision"], 1)
            markdown = archive.read(case_id)["result"]["markdown"]
            self.assertIn("## 法律核验", markdown)
            self.assertIn("上海市2026年最低工资标准", markdown)
            self.assertEqual(markdown.count("上海市2026年最低工资标准"), 1)
            self.assertNotIn("authority.json", markdown)
            self.assertNotIn("响应正文", markdown)

    def test_record_rejects_local_publisher_without_case_jurisdiction_and_does_not_commit(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create({"initial_goal": "核对劳动规则"})
            case_id = created["result"]["case_id"]
            observation = self._observation()
            observation["official_sources"] = [
                {
                    **observation["official_sources"][0],
                    "publisher_jurisdiction": "上海市",
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=0,
                observation=observation,
            )

            self.assertFalse(recorded["ok"], recorded)
            self.assertEqual(recorded["errors"][0]["code"], "jurisdiction_confirmation_required")
            read = archive.read(case_id)
            self.assertEqual(read["result"]["revision"], 0)
            self.assertNotIn("AUTH-001", read["result"]["markdown"])

    def test_record_allows_local_publisher_when_case_metadata_matches(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create(
                {"jurisdiction": "上海市", "initial_goal": "核对劳动规则"}
            )
            case_id = created["result"]["case_id"]
            observation = self._observation()
            observation["official_sources"] = [
                {
                    **observation["official_sources"][0],
                    "publisher_jurisdiction": "上海市",
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=0,
                observation=observation,
            )

            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["result"]["revision"], 1)

    def test_record_rejects_local_publisher_when_case_metadata_conflicts(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create(
                {"jurisdiction": "北京市", "initial_goal": "核对劳动规则"}
            )
            case_id = created["result"]["case_id"]
            observation = self._observation()
            observation["official_sources"] = [
                {
                    **observation["official_sources"][0],
                    "publisher_jurisdiction": "上海市",
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=0,
                observation=observation,
            )

            self.assertFalse(recorded["ok"], recorded)
            self.assertEqual(recorded["errors"][0]["code"], "jurisdiction_conflict")
            self.assertEqual(archive.read(case_id)["result"]["revision"], 0)

    def test_record_allows_a_national_publisher_without_case_jurisdiction(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create({"initial_goal": "核对全国劳动规则"})
            case_id = created["result"]["case_id"]
            observation = self._observation()
            observation["scope"] = "全国劳动规则"
            observation["jurisdiction"] = "全国"
            observation["official_sources"] = [
                {
                    "title": "全国官方来源",
                    "url": "https://www.npc.gov.cn/example/law",
                    "final_url": "https://www.npc.gov.cn/example/law",
                    "jurisdiction": "全国",
                    "publisher_jurisdiction": "全国",
                    "content_sha256": "b" * 64,
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=0,
                observation=observation,
            )

            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["result"]["revision"], 1)

    def test_record_does_not_self_bind_from_analysis_or_authority_text(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create({"initial_goal": "核对劳动规则"})
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录待核实分析",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "analysis",
                            "content_markdown": "适用地区：上海市（仅为分析假设，待用户确认）。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            observation = self._observation()
            observation["official_sources"] = [
                {
                    **observation["official_sources"][0],
                    "publisher_jurisdiction": "上海市",
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=1,
                observation=observation,
            )

            self.assertFalse(recorded["ok"], recorded)
            self.assertEqual(recorded["errors"][0]["code"], "jurisdiction_confirmation_required")
            self.assertEqual(archive.read(case_id)["result"]["revision"], 1)

    def test_record_accepts_a_user_fact_binding_after_case_creation(self):
        tracer = LegalVerificationTracer(ROOT)

        with tempfile.TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create({"initial_goal": "核对劳动规则"})
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户确认的履行地",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "**事实状态：用户陈述**\n"
                                "用户明确说明：劳动合同履行地：上海市。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            observation = self._observation()
            observation["official_sources"] = [
                {
                    **observation["official_sources"][0],
                    "publisher_jurisdiction": "上海市",
                }
            ]

            recorded = tracer.record(
                archive,
                case_id=case_id,
                expected_revision=1,
                observation=observation,
            )

            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["result"]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
