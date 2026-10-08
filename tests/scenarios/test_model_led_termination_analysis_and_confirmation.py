from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import tempfile
import unittest

from scripts.amount_calculator import AmountCalculator
from scripts.case_archive import CaseArchive


ROOT = Path(__file__).resolve().parents[2]


class _NaturalLanguageTerminationScenarioModel:
    """A deterministic scenario adapter for the model-owned public seams."""

    def __init__(self, workspace_root: Path):
        self.archive = CaseArchive(workspace_root)
        self.calculator = AmountCalculator()
        self.case_id: str | None = None
        self.sent_actions: list[str] = []

    def start(self) -> None:
        created = self.archive.create(
            {"jurisdiction": "上海", "initial_goal": "梳理解除事实及相关救济"}
        )
        assert created["ok"], created
        self.case_id = created["result"]["case_id"]
        written = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": 0,
                "change_summary": "记录解除事实、证据和当前分析范围",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": (
                            "**事实状态：用户陈述**\n"
                            "解除事件主体、日期、理由和程序需要结合材料核对。"
                        ),
                    },
                    {
                        "operation": "append",
                        "record_type": "evidence",
                        "content_markdown": "解除通知、发送记录和完整沟通记录待核对。",
                    },
                    {
                        "operation": "append",
                        "record_type": "authority",
                        "content_markdown": (
                            "核验事项：解除相关全国基线；结论：可作当前分析基线；"
                            "来源标题：劳动合同法及相关司法解释；访问日期：2026-09-07；"
                            "适用地区：全国；复核日期：2027-01-18。"
                        ),
                    },
                ],
            }
        )
        assert written["ok"], written

    def _read(self) -> dict[str, object]:
        assert self.case_id is not None
        result = self.archive.read(self.case_id)
        assert result["ok"], result
        return result["result"]

    def record_authority_status(self, status: str) -> dict[str, object]:
        """Persist a source observation so confirmation reads the archive, not a flag."""

        return self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": self._read()["revision"],
                "change_summary": f"记录当前解除来源状态：{status}",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "authority",
                        "content_markdown": (
                            f"解除来源核验状态：{status}\n"
                            "来源主题：解除理由、程序与时效；当前影响：高风险动作确认。"
                        ),
                    }
                ],
            }
        )

    def calculate_relief(self, requested_reliefs: list[str]) -> dict[str, object]:
        amount_reliefs = [
            relief for relief in requested_reliefs if relief != "continue_performance"
        ]
        return {
            "requested_reliefs": list(requested_reliefs),
            "amount_request": None
            if not amount_reliefs
            else {"relief_types": amount_reliefs},
            "excluded_from_amount": [
                relief
                for relief in requested_reliefs
                if relief == "continue_performance"
            ],
            "reason": "继续履行不换算为赔偿金额，也不进入赔偿金合计。",
        }

    def route(self, *, actor: str, action: str, delivery: str, purpose: str) -> dict[str, object]:
        if action == "planned":
            return {
                "route": "planned_action",
                "needs_high_risk_confirmation": True,
                "needs_fact_check": False,
                "assistant": (
                    "这是计划实施的解除动作。我会先说明解除主体、理由、日期和程序，"
                    "再说明时效、送达、证据和金额风险，以及先催告、继续保全证据或暂缓的替代方案。"
                    "如果你明确选择按这个范围继续，我再把你的选择记录到案情档案；可以这样确认吗？"
                ),
            }
        if action == "sent" and delivery == "unknown":
            return {
                "route": "sent_delivery_uncertain",
                "needs_high_risk_confirmation": False,
                "needs_fact_check": True,
                "assistant": (
                    "通知已经发送但送达事实还不清楚。我会先核对通知内容、发送时间、"
                    "收件地址或账号以及签收/接收证据，再判断它是历史索赔事实还是仍有后续动作；"
                    "请先告诉我现有的送达记录，可以吗？"
                ),
            }
        if actor == "劳动者" and action in {"historical", "sent"} and purpose == "claim":
            return {
                "route": "historical_claim_only",
                "needs_high_risk_confirmation": False,
                "needs_fact_check": False,
                "assistant": (
                    "我会把已经发生的解除作为既有事实，分析相关索赔、证据风险和主位/备选方案；"
                    "这不是新的解除动作，不需要你再次确认一个不存在的发送决定。"
                ),
            }
        if actor == "用人单位":
            return {
                "route": "unlawful_termination_claim",
                "needs_high_risk_confirmation": False,
                "needs_fact_check": False,
                "assistant": (
                    "我会分别核对用人单位主体、通知日期、通知所载理由和程序，"
                    "再分析违法解除或终止的赔偿金与继续履行等互斥救济；"
                    "继续履行不换算为赔偿金额，也不进入赔偿金合计。"
                ),
            }
        return {
            "route": "insufficient_termination_route",
            "needs_high_risk_confirmation": False,
            "needs_fact_check": True,
            "assistant": "解除主体或目的仍不清楚，我会先澄清一个关键事实，可以吗？",
        }

    def analyze(self, *, domain: str, state: str) -> dict[str, object]:
        judgment = {
            "supported": "倾向支持",
            "not_supported": "倾向不支持",
            "information_insufficient": "信息不足",
            "conflict": "信息不足",
        }[state]
        if domain == "forced":
            route = "劳动者依据法定事由解除并请求经济补偿"
            reason = "未依法缴纳社会保险费等法定事由"
            primary = "经济补偿"
            fallback = "先补强通知、欠付和送达证据后再决定是否实施解除"
            observations = {
                "supported": "劳动者主体、未缴社会保险事实和相关记录相互支持。",
                "not_supported": "通知和沟通记录显示实际由用人单位解除，不能直接改写为被迫解除。",
                "information_insufficient": "缺少解除主体、具体法定理由和送达材料，暂不能形成精确路径。",
                "conflict": "用户称劳动者解除，但通知记录显示用人单位解除，主体和理由发生冲突。",
            }
        else:
            route = "用人单位解除或终止的违法性与救济"
            reason = "通知所载理由及程序是否有证据支持"
            primary = "赔偿金或继续履行（二者不合并）"
            fallback = "在事实冲突时先保留条件情景并补强通知、程序和工作年限证据"
            observations = {
                "supported": "用人单位通知的主体、日期、理由和程序材料共同显示存在违法风险。",
                "not_supported": "解除通知和程序材料支持合法路径，当前不支持违法解除主张。",
                "information_insufficient": "缺少通知所载理由、送达或程序材料，暂不能判断救济路径。",
                "conflict": "用户口述与解除通知对主体、日期或理由的记载不一致，证据相互冲突。",
            }
        sample_changes: list[dict[str, str]] = [
            {
                "operation": "append",
                "record_type": "fact",
                "content_markdown": (
                    f"**事实状态：用户陈述**\n{observations[state]}"
                ),
            },
            {
                "operation": "append",
                "record_type": "evidence",
                "content_markdown": (
                    f"证据观察：{observations[state]}；来源载体：通知、沟通或送达记录。"
                ),
            },
        ]
        if state == "conflict":
            sample_changes.extend(
                [
                    {
                        "operation": "append",
                        "record_type": "fact",
                        "content_markdown": (
                        "**事实状态：证据支持事实**\n"
                            f"相反材料显示{domain}解除主体或通知理由与用户陈述不一致。"
                        ),
                    },
                    {
                        "operation": "append",
                        "record_type": "evidence",
                        "content_markdown": (
                            f"相反证据：{domain}解除通知或完整沟通记录支持另一种主体/理由情景。"
                        ),
                    },
                ]
            )
        sample_write = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": self._read()["revision"],
                "change_summary": f"记录{domain}解除{state}样本的独立事实与证据",
                "changes": sample_changes,
            }
        )
        assert sample_write["ok"], sample_write
        sample_record_ids = sample_write["result"]["generated_record_ids"]
        sample_fact_id, sample_evidence_id = sample_record_ids[:2]
        conflict_fact_id = sample_record_ids[2] if state == "conflict" else None
        conflict_evidence_id = sample_record_ids[3] if state == "conflict" else None
        source_reference_text = f"{sample_fact_id}、{sample_evidence_id}、AUTH-001"
        if conflict_fact_id and conflict_evidence_id:
            source_reference_text += f"、{conflict_fact_id}、{conflict_evidence_id}"
        conflict_note = ""
        if state == "conflict":
            if domain == "forced":
                conflict_scenarios = (
                    "情景一：劳动者确因未依法缴纳社会保险等法定事由解除；"
                    "情景二：实际是主动辞职或用人单位解除。"
                )
            else:
                conflict_scenarios = (
                    "情景一：用人单位通知理由或程序存在证据缺陷；"
                    "情景二：通知内容和程序证据支持合法路径。"
                )
            conflict_note = (
                f"事实冲突时保留{conflict_scenarios}只澄清一个当前冲突，"
                "不把任一情景当作已确认事实。"
            )
        assistant = (
            f"{route}：主体、理由、日期和程序分别核对；理由/通知理由为{reason}；当前判断为{judgment}。"
            f"当前样本事实：{observations[state]}"
            f"证据风险包括通知与送达记录，可能抗辩包括对方否认解除或主张理由、程序已完成。"
            f"主位方案：{primary}；稳健备选：{fallback}。{conflict_note}"
            "所有结论都是当前材料下的条件化分析，不自动发送、提交或替你作解除决定。"
        )
        record = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": self._read()["revision"],
                "change_summary": f"记录{domain}解除分析的{state}样本",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "analysis",
                        "content_markdown": (
                            f"分析类型：{domain}-termination；样本：{state}\n"
                            f"主张分析卡：初步判断={judgment}；主体、理由、日期、程序均分别记录；理由/通知理由={reason}。\n"
                            f"当前样本事实：{observations[state]}\n"
                            "证据缺口：通知原件、发送与送达完整记录；对方可能抗辩：解除主体、理由或程序存在争议。\n"
                            f"事实冲突处理：{conflict_note or '当前未发现需要分情景的事实冲突。'}\n"
                            "跨主张边界：同一工资或证据可以分别说明证明目的，但不得重复获偿或重复计入。\n"
                            "历史回归边界：已经发生的解除索赔不触发新的动作确认。\n"
                            "方案比较：primary 与 fallback；赔偿金、继续履行等互斥请求不合并求和。\n"
                            f"来源引用：{source_reference_text}。"
                        ),
                    }
                ],
            }
        )
        assert record["ok"], record
        return {
            "assistant": assistant,
            "judgment": judgment,
            "state": state,
            "fact_id": sample_fact_id,
            "evidence_id": sample_evidence_id,
            "conflict_fact_id": conflict_fact_id,
            "conflict_evidence_id": conflict_evidence_id,
        }

    def confirm_high_risk(
        self,
        answer: str,
        *,
        action_type: str = "planned_termination",
        action_scope: str = "按当前事实准备解除动作，不包含自动发送或提交。",
        source_refs: tuple[str, ...] = ("F-001", "E-001", "AUTH-001"),
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        current = self._read()
        revision = int(current["revision"])
        markdown = str(current["markdown"])
        normalized = answer.strip()
        scope_text = action_scope.rstrip("。")
        source_refs_text = "、".join(source_refs)

        def has_line(block: str, label: str, value: str) -> bool:
            return re.search(
                rf"(?m)^{re.escape(label)}{re.escape(value)}。?$", block
            ) is not None

        confirmation_blocks = re.split(
            r"(?m)^### \[CONF-\d{3,}\] [^\r\n]+\n", markdown
        )[1:]
        known_record_ids = set(
            re.findall(r"(?m)^### \[([A-Z][A-Z0-9_]*-\d{3,})\]", markdown)
        )
        missing_source_refs = sorted(set(source_refs) - known_record_ids)
        authority_refs = [ref for ref in source_refs if ref.startswith("AUTH-")]
        unsupported_source_types = sorted(
            {
                ref
                for ref in source_refs
                if not ref.startswith(("F-", "E-", "AUTH-"))
            }
        )
        missing_required_source_types = [
            prefix for prefix in ("F-", "E-") if not any(ref.startswith(prefix) for ref in source_refs)
        ]
        missing_source_refs.extend(unsupported_source_types)
        missing_source_refs.extend(missing_required_source_types)
        if not authority_refs:
            missing_source_refs.append("authority")
        if missing_source_refs:
            return {
                "confirmed": False,
                "source_refs_invalid": missing_source_refs,
                "assistant": "确认所依据的事实或来源已经无法在当前案情档案中定位，我会先重新核对依据。",
            }
        def record_body(record_id: str) -> str:
            match = re.search(
                rf"(?ms)^### \[{re.escape(record_id)}\] [^\r\n]+\n\n(.*?)(?=^### \[|^## |\Z)",
                markdown,
            )
            return match.group(1) if match else ""

        source_text = "\n".join(record_body(ref) for ref in source_refs)
        if not all(term in source_text for term in ("解除", "通知")):
            return {
                "confirmed": False,
                "source_refs_unrelated": True,
                "assistant": "当前引用的事实、证据或来源与计划解除范围不一致，我会先重新绑定依据。",
            }
        authority_text = "\n".join(record_body(ref) for ref in authority_refs)
        if "解除来源核验状态：过期" in authority_text:
            return {
                "confirmed": False,
                "source_expired": True,
                "assistant": "相关现行依据已经超过复核期限，我会先补做动态核验，再判断是否可以形成后续定稿。",
            }
        if "解除来源核验状态：冲突" in authority_text:
            return {
                "confirmed": False,
                "source_conflict": True,
                "assistant": "相关依据之间存在冲突，我会先完成动态核验，再判断是否可以形成后续定稿。",
            }
        if expected_revision is not None and expected_revision != revision:
            return {
                "confirmed": False,
                "revision_mismatch": True,
                "assistant": "案情档案已有变化，之前的确认不能覆盖当前内容；我会先说明受影响范围。",
            }
        if any(
            has_line(block, "高风险确认标识：", action_type)
            and has_line(block, "action_scope：", scope_text)
            and has_line(block, "archive_revision：", str(revision))
            and has_line(block, "source_refs：", source_refs_text)
            for block in confirmation_blocks
        ):
            return {
                "confirmed": False,
                "duplicate_suppressed": True,
                "assistant": "同一解除动作和当前档案内容已经确认，我不会重复记录或重复询问。",
            }
        stale_confirmation = any(
            has_line(block, "高风险确认标识：", action_type)
            and not has_line(block, "archive_revision：", str(revision))
            for block in confirmation_blocks
        )
        if stale_confirmation and not any(
            word in normalized for word in ("修正后的", "更新后的")
        ):
            return {
                "confirmed": False,
                "stale": True,
                "assistant": "案情事实已经修正，之前的高风险确认已失效；我会先说明受影响范围，再请你确认当前内容。",
            }
        if any(
            word in normalized
            for word in ("拒绝", "不同意", "先不要", "不按", "不确认", "不继续", "不要")
        ):
            return {
                "confirmed": False,
                "refused": True,
                "assistant": "好的，我不会把这次回答当作已确认；可以继续补充事实或暂缓这个动作。",
            }
        if not any(word in normalized for word in ("明确选择", "确认按", "我确认")):
            return {
                "confirmed": False,
                "ambiguous": True,
                "assistant": "我还不能判断你是否作出了明确选择；请直接说明是否确认按刚才解释的范围继续，可以吗？",
            }
        bound_revision = revision + 1
        recorded = self.archive.commit(
            {
                "case_id": self.case_id,
                "expected_revision": revision,
                "change_summary": "记录计划解除的实质知情确认",
                "changes": [
                    {
                        "operation": "append",
                        "record_type": "confirmation",
                        "content_markdown": (
                            f"高风险确认标识：{action_type}\n"
                            f"action_type：{action_type}\n"
                            f"action_scope：{scope_text}。\n"
                            "risk_summary：解除理由、日期、程序、送达、时效和证据不足可能影响后果。\n"
                            "alternatives_presented：先催告履行、补强证据、暂缓解除并保留历史索赔。\n"
                            f"user_choice：{normalized}\n"
                            f"confirmed_at：{date.today().isoformat()}\n"
                            f"archive_revision：{bound_revision}\n"
                            f"source_refs：{source_refs_text}。"
                        ),
                    }
                ],
            }
        )
        assert recorded["ok"], recorded
        return {
            "confirmed": True,
            "bound_revision": bound_revision,
            "assistant": "我已记录你对该计划动作的明确选择；不会自动发送、提交或替你执行解除。",
        }


class ModelLedTerminationAnalysisAndConfirmationTests(unittest.TestCase):
    def test_four_analysis_states_are_available_for_both_termination_domains(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            observed_samples: list[dict[str, object]] = []
            for domain in ("forced", "unlawful"):
                for state in ("supported", "not_supported", "information_insufficient", "conflict"):
                    result = model.analyze(domain=domain, state=state)
                    observed_samples.append(result)
                    self.assertIn(result["judgment"], {"倾向支持", "倾向不支持", "信息不足"})
                    self.assertEqual(result["assistant"].count("？"), 0)
                    if state == "conflict":
                        self.assertIn("情景一", result["assistant"])
                        self.assertIn("情景二", result["assistant"])
                        self.assertIn("只澄清一个当前冲突", result["assistant"])
                        self.assertIsNotNone(result["conflict_fact_id"])
                        self.assertIsNotNone(result["conflict_evidence_id"])
            archive = model._read()["markdown"]
            self.assertEqual(archive.count("### [A-001]"), 1)
            self.assertIn("primary 与 fallback", archive)
            self.assertIn("互斥请求不合并求和", archive)
            self.assertIn("未缴社会保险事实", archive)
            self.assertIn("用人单位通知的主体", archive)
            self.assertIn("跨主张边界", archive)
            self.assertIn("历史回归边界", archive)
            self.assertEqual(
                len({sample["fact_id"] for sample in observed_samples}), 8
            )
            self.assertEqual(
                len({sample["evidence_id"] for sample in observed_samples}), 8
            )
            self.assertEqual(
                len(
                    {
                        sample["conflict_fact_id"]
                        for sample in observed_samples
                        if sample["conflict_fact_id"] is not None
                    }
                ),
                2,
            )
            self.assertIn("相反材料显示", archive)
            self.assertEqual(archive.count("跨主张边界："), 8)
            self.assertEqual(archive.count("历史回归边界："), 8)

    def test_historical_claims_do_not_trigger_a_new_action_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            for action in ("historical", "sent"):
                routed = model.route(
                    actor="劳动者", action=action, delivery="clear", purpose="claim"
                )
                self.assertEqual(routed["route"], "historical_claim_only")
                self.assertFalse(routed["needs_high_risk_confirmation"])
                self.assertNotIn("确认发送", routed["assistant"])
            self.assertNotIn("CONF-", model._read()["markdown"])

    def test_ordinary_unified_confirmation_does_not_satisfy_a_planned_action(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            ordinary = model.archive.commit(
                {
                    "case_id": model.case_id,
                    "expected_revision": model._read()["revision"],
                    "change_summary": "记录普通分析范围确认",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "confirmation",
                            "content_markdown": (
                                "确认类型：普通统一确认；范围：解除历史事实分析；"
                                "主要口径和拟交付内容已向用户说明。来源引用：F-001、E-001。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(ordinary["ok"], ordinary)
            planned = model.route(
                actor="劳动者", action="planned", delivery="planned", purpose="action"
            )
            self.assertTrue(planned["needs_high_risk_confirmation"])
            self.assertNotIn("高风险确认标识：planned_termination", model._read()["markdown"])

    def test_planned_action_requires_material_confirmation_and_writes_only_archive_record(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            routed = model.route(
                actor="劳动者", action="planned", delivery="planned", purpose="action"
            )
            self.assertTrue(routed["needs_high_risk_confirmation"])
            self.assertEqual(routed["assistant"].count("？"), 1)
            for visible in ("主体", "理由", "日期", "程序", "风险", "替代方案"):
                self.assertIn(visible, routed["assistant"])

            before = int(model._read()["revision"])
            ambiguous = model.confirm_high_risk("大概可以吧")
            self.assertTrue(ambiguous["ambiguous"])
            self.assertEqual(int(model._read()["revision"]), before)
            refused = model.confirm_high_risk("我拒绝，先不要")
            self.assertTrue(refused["refused"])
            self.assertEqual(int(model._read()["revision"]), before)
            explicit_negative = model.confirm_high_risk("我确认不按此范围继续")
            self.assertTrue(explicit_negative["refused"])
            self.assertEqual(int(model._read()["revision"]), before)

            confirmed = model.confirm_high_risk("我明确选择确认按刚才解释的范围继续")
            self.assertTrue(confirmed["confirmed"])
            current = model._read()
            self.assertEqual(current["revision"], confirmed["bound_revision"])
            self.assertIn("action_type：", current["markdown"])
            for field in (
                "action_scope：",
                "risk_summary：",
                "alternatives_presented：",
                "user_choice：",
                "confirmed_at：",
                "archive_revision：",
                "source_refs：F-001、E-001、AUTH-001",
            ):
                self.assertIn(field, current["markdown"])
            self.assertEqual(current["markdown"].count("高风险确认标识：planned_termination"), 1)
            self.assertEqual(model.sent_actions, [])

    def test_sent_but_delivery_unknown_checks_facts_before_any_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            routed = model.route(
                actor="劳动者", action="sent", delivery="unknown", purpose="claim"
            )
            self.assertTrue(routed["needs_fact_check"])
            self.assertFalse(routed["needs_high_risk_confirmation"])
            self.assertEqual(routed["assistant"].count("？"), 1)
            for phrase in ("通知内容", "发送时间", "送达事实"):
                self.assertIn(phrase, routed["assistant"])
            self.assertNotIn("CONF-", model._read()["markdown"])

    def test_revision_mismatch_expired_source_and_duplicate_confirmation_are_suppressed(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            prepared_revision = int(model._read()["revision"])
            changed = model.archive.commit(
                {
                    "case_id": model.case_id,
                    "expected_revision": prepared_revision,
                    "change_summary": "记录用户修正的解除日期",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n用户修正了解除日期。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            mismatch = model.confirm_high_risk(
                "我明确选择确认按刚才解释的范围继续",
                expected_revision=prepared_revision,
            )
            self.assertTrue(mismatch["revision_mismatch"])
            self.assertNotIn("高风险确认标识：planned_termination", model._read()["markdown"])

            invalid_refs = model.confirm_high_risk(
                "我明确选择确认按刚才解释的范围继续",
                source_refs=("F-001", "E-001", "AUTH-999"),
            )
            self.assertEqual(invalid_refs["source_refs_invalid"], ["AUTH-999"])
            missing_authority = model.confirm_high_risk(
                "我明确选择确认按刚才解释的范围继续",
                source_refs=("F-001", "E-001"),
            )
            self.assertEqual(missing_authority["source_refs_invalid"], ["authority"])
            expired_model = _NaturalLanguageTerminationScenarioModel(
                Path(temporary_root) / "expired"
            )
            expired_model.start()
            expired_record = expired_model.record_authority_status("过期")
            self.assertTrue(expired_record["ok"], expired_record)
            expired_authority_id = expired_record["result"]["generated_record_ids"][0]
            expired = expired_model.confirm_high_risk(
                "我明确选择确认按刚才解释的范围继续",
                source_refs=("F-001", "E-001", expired_authority_id),
            )
            self.assertTrue(expired["source_expired"])
            self.assertNotIn(
                "高风险确认标识：planned_termination",
                expired_model._read()["markdown"],
            )
            conflict_model = _NaturalLanguageTerminationScenarioModel(
                Path(temporary_root) / "conflict"
            )
            conflict_model.start()
            conflict_record = conflict_model.record_authority_status("冲突")
            self.assertTrue(conflict_record["ok"], conflict_record)
            conflict_authority_id = conflict_record["result"]["generated_record_ids"][0]
            conflicted = conflict_model.confirm_high_risk(
                "我明确选择确认按刚才解释的范围继续",
                source_refs=("F-001", "E-001", conflict_authority_id),
            )
            self.assertTrue(conflicted["source_conflict"])
            self.assertNotIn(
                "高风险确认标识：planned_termination",
                conflict_model._read()["markdown"],
            )

            confirmed = model.confirm_high_risk("我明确选择确认按刚才解释的范围继续")
            self.assertTrue(confirmed["confirmed"])
            changed_again = model.archive.commit(
                {
                    "case_id": model.case_id,
                    "expected_revision": model._read()["revision"],
                    "change_summary": "记录确认后新增的送达事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\n用户补充了送达时间。",
                        }
                    ],
                }
            )
            self.assertTrue(changed_again["ok"], changed_again)
            stale = model.confirm_high_risk("我明确选择确认按刚才解释的范围继续")
            self.assertTrue(stale["stale"])
            self.assertIn("已失效", stale["assistant"])
            self.assertEqual(
                model._read()["markdown"].count("高风险确认标识：planned_termination"),
                1,
            )
            reconfirmed = model.confirm_high_risk(
                "我明确选择按修正后的内容继续"
            )
            self.assertTrue(reconfirmed["confirmed"])
            self.assertGreater(reconfirmed["bound_revision"], confirmed["bound_revision"])
            duplicate = model.confirm_high_risk("我明确选择确认按刚才解释的范围继续")
            self.assertTrue(duplicate["duplicate_suppressed"])
            self.assertEqual(
                model._read()["markdown"].count("高风险确认标识：planned_termination"),
                2,
            )

    def test_unlawful_route_keeps_compensation_and_continuation_as_competing_choices(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            model = _NaturalLanguageTerminationScenarioModel(Path(temporary_root))
            model.start()
            routed = model.route(
                actor="用人单位", action="sent", delivery="clear", purpose="claim"
            )
            self.assertEqual(routed["route"], "unlawful_termination_claim")
            self.assertIn("互斥救济", routed["assistant"])
            self.assertIn("继续履行不换算为赔偿金额", routed["assistant"])
            continuation = model.calculate_relief(
                ["compensation", "continue_performance"]
            )
            self.assertEqual(
                continuation["requested_reliefs"],
                ["compensation", "continue_performance"],
            )
            self.assertEqual(
                continuation["amount_request"]["relief_types"], ["compensation"]
            )
            self.assertEqual(
                continuation["excluded_from_amount"], ["continue_performance"]
            )
            self.assertIn("不进入赔偿金合计", continuation["reason"])
            calculated = model.calculator.calculate(
                {
                    "calculation_label": "违法解除赔偿金条件情景",
                    "formula_id": "unlawful_termination_compensation",
                    "formula_version": "1.0.0",
                    "legal_basis_summary": "模型选择赔偿金口径；计算器只执行算术。",
                    "source_refs": ["F-001", "E-001", "AUTH-001"],
                    "aggregation_policy": "mutually_exclusive",
                    "scenarios": [
                        {
                            "scenario_id": "compensation",
                            "assumptions": ["工资基数和工作年限已由模型另行确认。"],
                            "period": {
                                "start_date": "2024-01-01",
                                "end_date": "2026-01-01",
                                "semantics": "completed_months",
                            },
                            "inputs": {
                                "line_items": [
                                    {
                                        "line_id": "primary",
                                        "basis": {"value": "12000", "unit": "yuan_per_month"},
                                        "quantity": {"value": "2", "unit": "month"},
                                        "multiplier": {"value": "2", "unit": "ratio"},
                                        "paid": {"value": "0", "unit": "yuan"},
                                    }
                                ]
                            },
                        },
                        {
                            "scenario_id": "compensation_fallback",
                            "assumptions": ["工资构成存在争议，保守口径作为互斥备选。"],
                            "period": {
                                "start_date": "2024-01-01",
                                "end_date": "2026-01-01",
                                "semantics": "completed_months",
                            },
                            "inputs": {
                                "line_items": [
                                    {
                                        "line_id": "fallback",
                                        "basis": {"value": "12000", "unit": "yuan_per_month"},
                                        "quantity": {"value": "2", "unit": "month"},
                                        "multiplier": {"value": "2", "unit": "ratio"},
                                        "paid": {"value": "0", "unit": "yuan"},
                                    }
                                ]
                            },
                        },
                    ],
                    "rounding_policy": {
                        "id": "money-independent-unit-half-up-v1",
                        "quantum": "0.01",
                        "mode": "ROUND_HALF_UP",
                        "aggregation": "round_each_line_item_then_sum",
                    },
                }
            )
            self.assertTrue(calculated["ok"], calculated)
            self.assertIsNone(calculated["result"]["grand_total"])
            self.assertNotIn(
                "continue_performance",
                {item["scenario_id"] for item in calculated["result"]["scenario_totals"]},
            )
            self.assertEqual(
                calculated["result"]["grand_total_status"], "not_aggregated_mutually_exclusive"
            )
            economic = model.calculator.calculate(
                {
                    "calculation_label": "被迫解除经济补偿",
                    "formula_id": "economic_compensation",
                    "formula_version": "1.0.0",
                    "legal_basis_summary": "模型选择经济补偿口径；计算器只执行数学。",
                    "source_refs": ["F-001", "E-001", "AUTH-001"],
                    "aggregation_policy": "single_scenario",
                    "scenarios": [
                        {
                            "scenario_id": "primary",
                            "assumptions": ["工资基数和补偿月数已由模型另行确认。"],
                            "period": {
                                "start_date": "2024-01-01",
                                "end_date": "2026-01-01",
                                "semantics": "completed_months",
                            },
                            "inputs": {
                                "line_items": [
                                    {
                                        "line_id": "primary",
                                        "basis": {"value": "12000", "unit": "yuan_per_month"},
                                        "quantity": {"value": "2", "unit": "month"},
                                        "multiplier": {"value": "1", "unit": "ratio"},
                                        "paid": {"value": "0", "unit": "yuan"},
                                    }
                                ]
                            },
                        }
                    ],
                    "rounding_policy": {
                        "id": "money-independent-unit-half-up-v1",
                        "quantum": "0.01",
                        "mode": "ROUND_HALF_UP",
                        "aggregation": "round_each_line_item_then_sum",
                    },
                }
            )
            self.assertTrue(economic["ok"], economic)
            self.assertEqual(economic["result"]["grand_total"], "24000.00")


if __name__ == "__main__":
    unittest.main()
