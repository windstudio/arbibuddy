"""Public-boundary regressions for the 2026-10-13 Skill static review.

These tests use the existing archive, legal-verification, document-render,
runtime-transport, and platform-probe entry points. They avoid copying their
validation logic so each case exercises the same seams available to callers.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.amount_calculator.runtime_cli import main as amount_runtime_main
from scripts.case_archive import CaseArchive
from scripts.case_archive.cli import main as archive_runtime_main
from scripts.case_archive.service import _ArchiveLockTimeout
from scripts.documents.public import render
from scripts.documents.view import managed_delivery_view
from scripts.documents import delivery as delivery_module
from scripts.documents.runtime_cli import main as document_runtime_main
from scripts.legal_verification import LegalVerificationTracer
from scripts.legal_verification.runtime_cli import main as legal_runtime_main
from scripts.platform_adapters.service import (
    PlatformAdapterError,
    probe_capabilities,
)
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as create_forced_case,
    forced_request,
)
from tests.documents.test_model_led_document_render import (
    _application_request,
    _create_archive,
)
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings


ROOT = Path(__file__).resolve().parents[1]


class SkillStaticReviewRegressionTests(unittest.TestCase):
    def _authority_observation(
        self, *, outcome: str, scope: str = "劳动者解除相关现行规则"
    ) -> dict[str, object]:
        observation: dict[str, object] = {
            "scope": scope,
            "trigger": "high_risk_finalization",
            "outcome": outcome,
            "official_sources": [],
            "accessed_on": "2026-10-13",
            "jurisdiction": "全国",
            "impact": (
                "旧基线过期，本次已完成现行来源核验；影响被迫解除通知使用的现行口径。"
                if outcome == "verified"
                else "影响被迫解除通知使用的现行口径。"
            ),
        }
        if outcome == "verified":
            source_url = "https://rsj.sh.gov.cn/example/termination"
            observation["official_sources"] = [
                {
                    "title": "上海市人力资源和社会保障局解除劳动合同规则",
                    "url": source_url,
                    "final_url": source_url,
                    "jurisdiction": "全国",
                    "content_sha256": "a" * 64,
                }
            ]
        return observation

    @staticmethod
    def _refreshed_confirmation(archive: CaseArchive, case_id: str, revision: int) -> int:
        next_revision = revision + 1
        content = (
            "高风险确认标识：planned_termination\n"
            "action_type：planned_termination\n"
            "action_scope：按当前事实准备被迫解除通知书，劳动者张三向示例公司发出，理由为未及时足额支付劳动报酬，使用欠薪流水，通知日期为2026年9月8日，解除生效日期为2026年9月20日，不包含自动发送或提交。\n"
            "risk_summary：解除理由、日期、程序、送达、时效和证据不足可能影响后果。\n"
            "alternatives_presented：先催告履行、补强证据、暂缓解除并保留历史索赔。\n"
            "user_choice：我明确选择按已说明范围继续准备被迫解除通知书。\n"
            "confirmed_at：2026-09-08\n"
            f"archive_revision：{next_revision}\n"
            "source_refs：F-001、F-002、F-003、F-004、CL-001、E-001、AUTH-001、AUTH-002。"
        )
        updated = archive.commit(
            {
                "case_id": case_id,
                "expected_revision": revision,
                "change_summary": "将确认绑定到新增核验记录后的当前档案版本",
                "changes": [
                    {
                        "operation": "replace",
                        "record_type": "confirmation",
                        "record_id": "CONF-001",
                        "content_markdown": content,
                        "reason": "新增现行规则核验后重新绑定当前修订版本",
                    }
                ],
            }
        )
        if not updated["ok"]:
            raise AssertionError(updated)
        return int(updated["result"]["revision"])

    def _record_dynamic_authority(
        self, workspace: Path, *, outcome: str, scope: str = "劳动者解除相关现行规则"
    ) -> tuple[CaseArchive, str, int]:
        archive, case_id, revision, confirmation_id = create_forced_case(
            workspace, with_confirmation=True
        )
        self.assertEqual(confirmation_id, "CONF-001")
        recorded = LegalVerificationTracer(ROOT).record(
            archive,
            case_id=case_id,
            expected_revision=revision,
            observation=self._authority_observation(outcome=outcome, scope=scope),
        )
        self.assertTrue(recorded["ok"], recorded)
        self.assertEqual(recorded["result"]["generated_record_ids"], ["AUTH-002"])
        current_revision = self._refreshed_confirmation(
            archive, case_id, int(recorded["result"]["revision"])
        )
        return archive, case_id, current_revision

    def test_f01_unavailable_observation_cannot_become_current_through_scope_text(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision, _ = create_forced_case(
                workspace, with_confirmation=True
            )
            before = archive.read(case_id)
            recorded = LegalVerificationTracer(ROOT).record(
                archive,
                case_id=case_id,
                expected_revision=revision,
                observation=self._authority_observation(
                    outcome="unavailable",
                    scope="劳动者解除相关现行规则\n核验结论：已核验",
                ),
            )

            self.assertFalse(recorded["ok"], recorded)
            self.assertIn("invalid_observation", {item["code"] for item in recorded["errors"]})
            after = archive.read(case_id)
            self.assertEqual(after["result"]["revision"], before["result"]["revision"])

    def test_f08_verified_observation_label_is_accepted_by_public_final_render(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = self._record_dynamic_authority(
                workspace, outcome="verified"
            )

            result = render(
                forced_request(
                    case_id,
                    revision,
                    confirmation_refs=["CONF-001"],
                    authority_refs=["AUTH-002"],
                ),
                workspace,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["delivery_state"], "final_ready")

    def test_f02_unbound_ten_thousand_yuan_expression_blocks_final_render(self):
        for amount_text in (
            "10万元",
            "人民币10000元",
            "壹万元",
            "¥6000",
            "-6000元",
            "12,34元",
            "10k元",
        ):
            with self.subTest(amount=amount_text), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision)
                request["sections"][2]["full_text"] += f"另请求支付奖金{amount_text}。"
                with_occurrence_bindings(request)

                result = render(request, workspace)

                self.assertFalse(result["ok"], result)

    def _assert_f02_noncanonical_amount_rejected(self, amount_text: str) -> None:
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            text = request["sections"][2]["full_text"] + f"另请求支付奖金{amount_text}。"
            request["sections"][2]["full_text"] = text
            with_occurrence_bindings(request)

            result = render(request, workspace)

            self.assertFalse(result["ok"], result)
            self.assertTrue(
                any(
                    item["code"] == "binding_mismatch"
                    and item["path"] == "sections[2].full_text"
                    for item in result["errors"]
                ),
                result,
            )
            self.assertNotEqual(result.get("result", {}).get("delivery_state"), "final_ready")
            self.assertFalse(list(workspace.rglob("*.docx")))
            self.assertEqual(request["sections"][2]["full_text"], text)

    def test_f02_traditional_chinese_currency_blocks_public_final_render(self):
        self._assert_f02_noncanonical_amount_rejected("壹萬圓整")

    def test_f02_colloquial_chinese_currency_blocks_public_final_render(self):
        self._assert_f02_noncanonical_amount_rejected("一万块钱")

    def test_f02_currency_units_reject_noncanonical_numeric_forms(self):
        # Exercise the public gate across numeral/unit combinations, rather
        # than only the two words that escaped the original review.
        for number in ("10000", "一万", "壹萬"):
            for unit in ("圓", "块", "块钱", "塊", "塊錢", "美元", "港元", "日元", "歐元", "人民幣"):
                with self.subTest(number=number, unit=unit):
                    self._assert_f02_noncanonical_amount_rejected(number + unit)
        for expression in (
            "貳萬圓整", "兩萬圆正", "陸仟元", "一万塊钱", "一万块錢",
            "1万圓", "１萬元", "６０００元", "一萬點五元", "壹萬RMB",
            "人民幣壹萬", "人民币一万", "￥壹萬圓", "6000.00元五角",
            "6000.00元 五 角", "6000.00元 零伍分", "6000.00元整",
            "－6000.00元", ".5元", "10000元",
        ):
            with self.subTest(expression=expression):
                self._assert_f02_noncanonical_amount_rejected(expression)

    def test_f02_canonical_amount_requires_every_occurrence_binding(self):
        for bind_all in (False, True):
            with self.subTest(bind_all=bind_all), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision)
                request["sections"][3]["full_text"] += "上述工资差额合计6000.00元。"
                if bind_all:
                    with_occurrence_bindings(request)

                result = render(request, workspace)

                if bind_all:
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(result["result"]["delivery_state"], "final_ready")
                    self.assertTrue(Path(result["result"]["canonical_docx"]).is_file())
                else:
                    self.assertFalse(result["ok"], result)
                    self.assertTrue(
                        any(
                            item["code"] == "binding_mismatch"
                            and item["path"] == "sections[3].full_text"
                            for item in result["errors"]
                        ),
                        result,
                    )
                    self.assertFalse(list(workspace.rglob("*.docx")))

    def test_f03_party_name_prefix_does_not_satisfy_locked_identity(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][0]["full_text"] = (
                "申请人：张三丰，身份证号：310000199001010000。"
            )

            result = render(request, workspace)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {item["code"] for item in result["errors"]})

    def test_f03_archive_name_prefix_does_not_satisfy_locked_identity(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            next_revision = revision + 1
            updated = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "纠正申请人姓名并重新绑定确认版本",
                    "changes": [
                        {
                            "operation": "replace",
                            "record_type": "fact",
                            "record_id": "F-001",
                            "content_markdown": (
                                "申请人：张三丰；身份证号：310000199001010000；"
                                "联系地址：上海市示例路1号；联系电话：13800000000。"
                            ),
                            "reason": "测试档案主体姓名的完整值匹配",
                        },
                        {
                            "operation": "replace",
                            "record_type": "confirmation",
                            "record_id": "CONF-001",
                            "content_markdown": (
                                "用户自然语言确认摘要：确认按当前工资差额范围准备仲裁材料。\n"
                                f"绑定档案修订版本：{next_revision}\n"
                                "关键事实：劳动关系和工资差额已陈述。\n"
                                "权益范围：本次处理工资差额。\n"
                                "主要口径：以档案中的工资差额计算为准。\n"
                                "重要假设：未核实事项留待核对。\n"
                                "拟交付内容：仲裁申请书和证据目录。"
                            ),
                            "reason": "测试确认记录重新绑定当前档案版本",
                        },
                    ],
                }
            )
            self.assertTrue(updated["ok"], updated)
            current_revision = int(updated["result"]["revision"])
            request = _application_request(case_id, current_revision)
            request["confirmation_refs"] = ["CONF-001"]

            result = render(request, workspace)

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "binding_mismatch",
                {item["code"] for item in result["errors"]},
            )

    def test_f03_company_identity_requires_full_source_value_and_allows_spaces_punctuation(self):
        full_identity = "ACME & Sons (Shanghai) Co., Ltd."
        for requested_identity, accepted in (
            ("ACME", False),
            (full_identity, True),
        ):
            with self.subTest(requested_identity=requested_identity), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                archive, case_id, revision = _create_archive(workspace)
                updated = archive.commit(
                    {
                        "case_id": case_id,
                        "expected_revision": revision,
                        "change_summary": "记录带空格标点的完整英文单位名称",
                        "changes": [
                            {
                                "operation": "replace",
                                "record_type": "fact",
                                "record_id": "F-002",
                                "content_markdown": (
                                    f"被申请人：{full_identity}；住所：上海市示例路2号；"
                                    "法定代表人：李四。"
                                ),
                                "reason": "测试被申请人完整身份来源",
                            }
                        ],
                    }
                )
                self.assertTrue(updated["ok"], updated)
                request = _application_request(
                    case_id, int(updated["result"]["revision"])
                )
                request["sections"][1]["full_text"] = (
                    f"被申请人：{requested_identity}，法定代表人：李四。"
                )
                request["locked_bindings"][2]["rendered_value"] = (
                    f"被申请人：{requested_identity}"
                )

                result = render(request, workspace)

                with self.subTest(expected_acceptance=accepted):
                    self.assertEqual(result["ok"], accepted, result)
                    if not accepted:
                        self.assertIn(
                            "binding_mismatch",
                            {item["code"] for item in result["errors"]},
                        )

    def test_f03_unbound_explicit_party_name_blocks_final_render(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            request["sections"][0]["full_text"] += "\n申请人：张四。"

            result = render(request, workspace)

            self.assertFalse(result["ok"], result)
            self.assertIn("binding_mismatch", {item["code"] for item in result["errors"]})

    def test_f03_missing_or_unlabelled_party_binding_cannot_bypass_body_coverage(self):
        for variant in (
            "missing_applicant_role",
            "missing_respondent_role",
            "unlabelled_binding",
        ):
            with self.subTest(variant=variant), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision)
                if variant == "missing_applicant_role":
                    request["sections"][0]["full_text"] = (
                        "张三，身份证号：310000199001010000。"
                    )
                elif variant == "missing_respondent_role":
                    request["sections"][1]["full_text"] = "示例公司，法定代表人：李四。"
                else:
                    request["locked_bindings"][1]["rendered_value"] = "张三"

                result = render(request, workspace)

                self.assertFalse(result["ok"], result)
                self.assertIn(
                    "binding_mismatch",
                    {item["code"] for item in result["errors"]},
                )

    def test_f05_archive_read_rejects_cases_directory_link_outside_workspace(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            workspace = root / "workspace"
            external = root / "external"
            external.mkdir()
            created = CaseArchive(external).create({"initial_goal": "外部案件"})
            self.assertTrue(created["ok"], created)
            case_id = str(created["result"]["case_id"])
            cases_path = workspace / ".arbibuddy" / "cases"
            cases_path.parent.mkdir(parents=True)
            try:
                cases_path.symlink_to(
                    external / ".arbibuddy" / "cases", target_is_directory=True
                )
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink unavailable in this environment: {error}")

            result = CaseArchive(workspace).read(case_id)

            self.assertFalse(result["ok"], result)

    def test_f06_amount_transport_does_not_follow_runtime_input_directory_link(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            workspace = root / "workspace"
            external_input = root / "external-input"
            workspace_arbibuddy = workspace / ".arbibuddy"
            workspace_arbibuddy.mkdir(parents=True)
            external_input.mkdir()
            runtime_link = workspace_arbibuddy / "runtime-input"
            try:
                runtime_link.symlink_to(external_input, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink unavailable in this environment: {error}")
            external_file = external_input / "amount.json"
            external_file.write_text("{}", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                code = amount_runtime_main(
                    [
                        "--workspace",
                        str(workspace),
                        "calculate",
                        "--input",
                        str(runtime_link / external_file.name),
                    ]
                )

            self.assertEqual(code, 2, output.getvalue())
            self.assertTrue(external_file.is_file())
            self.assertEqual(json.loads(output.getvalue())["errors"][0]["code"], "invalid_runtime_input_path")

    def test_f10_invalid_enum_type_returns_aggregate_failure_from_public_archive_api(self):
        with TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create()
            self.assertTrue(created["ok"], created)

            result = archive.commit(
                {
                    "case_id": created["result"]["case_id"],
                    "expected_revision": 0,
                    "change_summary": "非法枚举类型应返回公开错误",
                    "changes": [
                        {
                            "operation": [],
                            "record_type": "fact",
                            "content_markdown": "用户提交的事实。",
                        }
                    ],
                }
            )

            self.assertIsInstance(result, dict)
            self.assertFalse(result["ok"], result)
            self.assertTrue(result["errors"], result)

    def test_f11_failure_to_reclaim_dead_archive_lock_still_times_out(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive = CaseArchive(workspace)
            created = archive.create()
            case_id = str(created["result"]["case_id"])
            archive_path = Path(created["result"]["canonical_location"])
            stale_lock = archive_path.parent / ".archive.lock"
            stale_lock.mkdir()
            (stale_lock / "owner").write_text("99999999", encoding="ascii")
            script = r"""
import json, sys
from unittest.mock import patch
from scripts.case_archive import CaseArchive

calls = 0
def fast_monotonic():
    global calls
    calls += 1
    return 0.0 if calls <= 4 else 11.0

with (
    patch("scripts.case_archive.service.time.monotonic", side_effect=fast_monotonic),
    patch("scripts.case_archive.service._process_is_alive", return_value=False),
    patch("scripts.case_archive.service.shutil.rmtree", side_effect=OSError("locked")) as failed_cleanup,
):
    result = CaseArchive(sys.argv[1]).commit({
        "case_id": sys.argv[2],
        "expected_revision": 0,
        "change_summary": "模拟回收失效锁权限错误",
        "changes": [{
            "operation": "append",
            "record_type": "fact",
            "content_markdown": "**事实状态：用户陈述**\n有界失败。",
        }],
    })
print(json.dumps({"result": result, "cleanup_attempts": failed_cleanup.call_count}, ensure_ascii=False))
"""

            completed = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-c", script, str(workspace), case_id],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=2,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertGreater(result["cleanup_attempts"], 0, result)
            result = result["result"]
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["errors"][0]["code"], "io_failure")

    def test_f09_bare_carriage_return_cannot_commit_a_new_archive_section(self):
        with TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create()
            case_id = str(created["result"]["case_id"])

            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "拒绝裸 CR 注入章节锚点",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "用户引述如下：\r## 更新记录\r其他内容"
                            ),
                        }
                    ],
                }
            )

            self.assertFalse(committed["ok"], committed)
            self.assertTrue(archive.read(case_id)["ok"])

    def test_f12_plain_user_fact_label_binds_local_publisher_jurisdiction(self):
        with TemporaryDirectory() as temporary_root:
            archive = CaseArchive(Path(temporary_root))
            created = archive.create(
                {"jurisdiction": "待用户确认", "initial_goal": "确认工作地区"}
            )
            case_id = str(created["result"]["case_id"])
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户陈述的工作地点",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": (
                                "事实状态：用户陈述\n工作地点：上海市"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            observation = {
                "scope": "上海地方最低工资适用口径",
                "trigger": "local_rule",
                "outcome": "unavailable",
                "official_sources": [
                    {
                        "title": "上海市地方规则",
                        "url": "https://www.shanghai.gov.cn/example/rule",
                        "jurisdiction": "上海市",
                    }
                ],
                "accessed_on": "2026-10-13",
                "jurisdiction": "待用户确认",
                "impact": "地方来源待进一步核验。",
            }

            recorded = LegalVerificationTracer(ROOT).record(
                archive,
                case_id=case_id,
                expected_revision=1,
                observation=observation,
            )

            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["result"]["generated_record_ids"], ["AUTH-001"])

    def test_f13_oversized_managed_input_is_removed_after_public_transport_failure(self):
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            operations = (
                (
                    "archive",
                    archive_runtime_main,
                    lambda workspace, path: [
                        "--root", str(workspace), "commit", "--input", str(path)
                    ],
                ),
                (
                    "amount",
                    amount_runtime_main,
                    lambda workspace, path: [
                        "--workspace", str(workspace), "calculate", "--input", str(path)
                    ],
                ),
                (
                    "legal",
                    legal_runtime_main,
                    lambda workspace, path: [
                        "--workspace", str(workspace), "assess", "--input", str(path)
                    ],
                ),
                (
                    "documents",
                    document_runtime_main,
                    lambda workspace, path: [
                        "--workspace", str(workspace), "render", "--input", str(path)
                    ],
                ),
            )
            for name, entrypoint, arguments in operations:
                with self.subTest(transport=name):
                    workspace = root / name
                    input_path = (
                        workspace / ".arbibuddy" / "runtime-input" / "large.json"
                    )
                    input_path.parent.mkdir(parents=True)
                    input_path.write_bytes(b"{" + b" " * (2 * 1024 * 1024))
                    output = StringIO()

                    if name == "documents":
                        with (
                            patch(
                                "scripts.documents.runtime_cli.bootstrap_document_runtime",
                                return_value=False,
                            ),
                            redirect_stdout(output),
                        ):
                            code = entrypoint(arguments(workspace, input_path))
                    else:
                        with redirect_stdout(output):
                            code = entrypoint(arguments(workspace, input_path))

                    self.assertEqual(code, 2, output.getvalue())
                    self.assertFalse(input_path.exists())
                    self.assertEqual(
                        json.loads(output.getvalue())["errors"][0]["code"],
                        "runtime_input_too_large",
                    )

    def test_f07_new_archive_revision_can_recover_after_failed_public_render(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            original_request = _application_request(
                case_id, revision, mode="candidate"
            )
            first = render(original_request, workspace)
            self.assertTrue(first["ok"], first)
            failed_request = _application_request(
                case_id, revision, mode="candidate"
            )
            failed_request["sections"][3]["full_text"] += "\n本次重生成用于恢复验证。"

            original_replace = delivery_module._replace_generation_child

            def fail_old_output_move(source: Path, target: Path) -> None:
                if source == Path(first["result"]["canonical_docx"]).parent:
                    raise PermissionError("模拟旧稿被占用")
                original_replace(source, target)

            with patch(
                "scripts.documents.delivery._replace_generation_child",
                side_effect=fail_old_output_move,
            ):
                failed = render(failed_request, workspace)

            self.assertFalse(failed["ok"], failed)
            self.assertIn(
                "permission_denied", {item["code"] for item in failed["errors"]}
            )
            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "记录用户修正后重新生成候选文书",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户补充：补充材料仍待核对。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current_revision = int(changed["result"]["revision"])
            replacement = _application_request(
                case_id, current_revision, mode="candidate"
            )

            recovered = render(replacement, workspace)

            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(recovered["result"]["archive_revision"], current_revision)

    def test_f07_new_request_restores_proven_old_generation_after_interrupted_rollback(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            archive, case_id, revision = _create_archive(workspace)
            original_request = _application_request(
                case_id, revision, mode="candidate"
            )
            original = render(original_request, workspace)
            self.assertTrue(original["ok"], original)
            canonical = Path(original["result"]["canonical_docx"]).parent
            original_files = {
                path.relative_to(canonical).as_posix(): path.read_bytes()
                for path in canonical.rglob("*")
                if path.is_file()
            }

            interrupted_request = _application_request(
                case_id, revision, mode="candidate"
            )
            interrupted_request["sections"][3]["full_text"] += (
                "\n此版本触发旧稿回滚中断。"
            )
            real_replace = delivery_module._replace_generation_child
            calls = {"old_to_backup": 0, "staging_to_canonical": 0, "rollback": 0}

            def interrupt_rollback(source: Path, target: Path) -> None:
                if source == canonical and ".regeneration-" in target.name:
                    calls["old_to_backup"] += 1
                elif ".staging-" in source.name and target == canonical:
                    calls["staging_to_canonical"] += 1
                    raise OSError("模拟 staging 晋级失败")
                elif ".regeneration-" in source.name and target == canonical:
                    calls["rollback"] += 1
                    raise PermissionError("模拟 backup 回滚失败")
                real_replace(source, target)

            with patch(
                "scripts.documents.delivery._replace_generation_child",
                side_effect=interrupt_rollback,
            ):
                failed = render(interrupted_request, workspace)

            self.assertFalse(failed["ok"], failed)
            self.assertEqual(
                calls,
                {"old_to_backup": 1, "staging_to_canonical": 1, "rollback": 1},
            )
            self.assertFalse(canonical.exists())
            self.assertTrue(
                list(canonical.parent.glob(f".{canonical.name}.regeneration-*"))
            )

            changed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": revision,
                    "change_summary": "中断恢复期间补充一项用户事实",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "用户补充：旧稿事实仍需保留。",
                        }
                    ],
                }
            )
            self.assertTrue(changed["ok"], changed)
            current_revision = int(changed["result"]["revision"])

            real_replace = delivery_module._replace_generation_child
            restored_generations: list[dict[str, bytes]] = []
            replacements: list[tuple[str, str]] = []

            def observe_old_generation_restore(source: Path, target: Path) -> None:
                replacements.append((source.name, target.name))
                real_replace(source, target)
                if ".regeneration-" in source.name and target.name == canonical.name:
                    restored_generations.append(
                        {
                            path.relative_to(canonical).as_posix(): path.read_bytes()
                            for path in canonical.rglob("*")
                            if path.is_file()
                        }
                    )

            with patch(
                "scripts.documents.delivery._replace_generation_child",
                side_effect=observe_old_generation_restore,
            ):
                recovered = render(
                    _application_request(case_id, current_revision, mode="candidate"),
                    workspace,
                )

            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(recovered["result"]["archive_revision"], current_revision)
            self.assertTrue(canonical.is_dir())
            self.assertEqual(len(restored_generations), 1, replacements)
            self.assertEqual(
                {
                    name: sha256(data).hexdigest()
                    for name, data in restored_generations[0].items()
                },
                {
                    name: sha256(data).hexdigest()
                    for name, data in original_files.items()
                },
            )

    def test_f14_failed_backup_archive_does_not_block_delivery_view_or_replay(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision, mode="candidate")
            original = render(request, workspace)
            self.assertTrue(original["ok"], original)
            changed = _application_request(case_id, revision, mode="candidate")
            changed["sections"][3]["full_text"] += "\n本次正文有明确的新补充。"
            original_replace = delivery_module._replace_generation_child

            def fail_backup_archive(source: Path, target: Path) -> None:
                if ".regeneration-" in source.name and ".committed-backup-" in target.name:
                    raise OSError("模拟旧备份归档失败")
                original_replace(source, target)

            with patch(
                "scripts.documents.delivery._replace_generation_child",
                side_effect=fail_backup_archive,
            ):
                published = render(changed, workspace)

            self.assertTrue(published["ok"], published)
            replay = render(changed, workspace)
            self.assertTrue(replay["ok"], replay)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            output_parent = Path(published["result"]["canonical_docx"]).parent.parent
            self.assertEqual(list(output_parent.glob("*.regeneration-*")), [])

    @staticmethod
    def _probe_verification(*, runtime_identity_verified: bool = True) -> dict[str, object]:
        return {
            "discovery_contract_valid": True,
            "resources_complete": True,
            "renderer_dependencies_verified": True,
            "runtime_identity_verified": runtime_identity_verified,
            "installation": {"scope": "project", "mode": "copy"},
        }

    def _run_public_capability_probe(self, workspace: Path) -> dict[str, object]:
        with (
            patch(
                "scripts.platform_adapters.service.verify_install",
                return_value=self._probe_verification(),
            ),
            patch(
                "scripts.platform_adapters.service._probe_platform_version",
                return_value="codex 0.test",
            ),
            patch(
                "scripts.platform_adapters.service._probe_filesystem",
                return_value={"available": True},
            ),
            patch(
                "scripts.platform_adapters.service._probe_scripts",
                return_value={"available": True},
            ),
            patch(
                "scripts.platform_adapters.service._probe_network",
                return_value={"available": False},
            ),
            patch(
                "scripts.platform_adapters.service._probe_word",
                return_value={"available": True},
            ),
            patch(
                "scripts.platform_adapters.service._probe_structure_inspection",
                return_value={"available": True},
            ),
        ):
            return probe_capabilities(
                platform="codex",
                skill_root=ROOT,
                platform_command=["codex", "--version"],
                workspace=workspace,
                network_check="skip",
                network_url="https://www.gov.cn/",
            )

    def test_f16_capability_probe_preserves_preexisting_same_name_user_file(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            probe_path = workspace / "附件展示探测-中文.txt"
            probe_path.write_text("用户已有内容", encoding="utf-8")

            self._run_public_capability_probe(workspace)

            self.assertTrue(probe_path.is_file())
            self.assertEqual(probe_path.read_text(encoding="utf-8"), "用户已有内容")

    def test_f17_capability_probe_stops_when_runtime_identity_is_invalid(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            with (
                patch(
                    "scripts.platform_adapters.service.verify_install",
                    return_value=self._probe_verification(
                        runtime_identity_verified=False
                    ),
                ),
                patch(
                    "scripts.platform_adapters.service._probe_platform_version",
                    return_value="codex 0.test",
                ),
                patch(
                    "scripts.platform_adapters.service._probe_word"
                ) as probe_word,
                patch(
                    "scripts.platform_adapters.service._probe_structure_inspection"
                ) as probe_structure,
            ):
                with self.assertRaises(PlatformAdapterError):
                    probe_capabilities(
                        platform="codex",
                        skill_root=ROOT,
                        platform_command=["codex", "--version"],
                        workspace=workspace,
                        network_check="skip",
                        network_url="https://www.gov.cn/",
                    )

            probe_word.assert_not_called()
            probe_structure.assert_not_called()

if __name__ == "__main__":
    unittest.main()
