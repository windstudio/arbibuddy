from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.case_archive import CaseArchive
from scripts.workbuddy_acceptance.service import (
    audit_workspace,
    build_no_delivery_contract,
)


class Rc25Ml03RegressionTests(unittest.TestCase):
    @staticmethod
    def _span(
        trace_id: str,
        session_id: str,
        span_id: str,
        tool_name: str,
        tool_input: object,
        tool_output: object,
        started_at: str,
    ) -> dict[str, object]:
        return {
            "traceId": trace_id,
            "spanId": span_id,
            "sessionId": session_id,
            "kind": "function",
            "toolName": tool_name,
            "toolInput": tool_input,
            "toolOutput": tool_output,
            "status": "success",
            "startedAt": started_at,
            "endedAt": started_at,
        }

    def test_negative_region_example_in_archive_is_canary_only_not_unbound_authority(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            archive = CaseArchive(workspace)
            created = archive.create(
                {
                    "jurisdiction": "待用户确认",
                    "initial_goal": "验证未确认地区的风险文本",
                }
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录地方规则待核验风险",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "risk",
                            "content_markdown": (
                                "适用地区待确认；不得默认套用上海或任何地方规则。"
                            ),
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)

            first_session = "ml03-rc25-first"
            second_session = "ml03-rc25-second"
            trace_root = root / "traces"
            trace_root.mkdir()
            first_trace = {
                "trace": {"traceId": "trace-rc25-first", "sessionId": first_session},
                "spans": [
                    self._span(
                        "trace-rc25-first",
                        first_session,
                        "skill-first",
                        "Skill",
                        {"skill": "arbibuddy"},
                        {"ok": True},
                        "2026-09-21T07:00:00Z",
                    ),
                    self._span(
                        "trace-rc25-first",
                        first_session,
                        "commit-first",
                        "CaseArchive.commit",
                        {
                            "operation": "commit",
                            "case_id": case_id,
                            "changes": [
                                {
                                    "record_type": "risk",
                                    "content_markdown": (
                                        "适用地区待确认；不得默认套用上海或任何地方规则。"
                                    ),
                                }
                            ],
                        },
                        {"ok": True, "result": {"case_id": case_id, "revision": 1}},
                        "2026-09-21T07:01:00Z",
                    ),
                ],
            }
            second_trace = {
                "trace": {"traceId": "trace-rc25-second", "sessionId": second_session},
                "spans": [
                    self._span(
                        "trace-rc25-second",
                        second_session,
                        "skill-second",
                        "Skill",
                        {"skill": "arbibuddy"},
                        {"ok": True},
                        "2026-09-21T08:00:00Z",
                    ),
                    self._span(
                        "trace-rc25-second",
                        second_session,
                        "read-current",
                        "CaseArchive.read",
                        {"operation": "read", "case_id": case_id},
                        {"ok": True, "result": {"case_id": case_id}},
                        "2026-09-21T08:01:00Z",
                    ),
                ],
            }
            (trace_root / "first.json").write_text(
                json.dumps(first_trace, ensure_ascii=False), encoding="utf-8"
            )
            (trace_root / "second.json").write_text(
                json.dumps(second_trace, ensure_ascii=False), encoding="utf-8"
            )

            transcript = root / "transcript.jsonl"
            transcript.write_text('{"type":"metadata"}\n', encoding="utf-8")
            contract_path = root / "contract.json"
            build_no_delivery_contract(
                journey="ML03-v2",
                case_id=case_id,
                output=contract_path,
                transcript_jsonl=transcript,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                allow_no_delivery=True,
                journey_contract=contract_path,
                workbuddy_trace_root=trace_root,
                session_ids=(first_session, second_session),
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("scenario_fact_boundary_pollution", codes, report)
            self.assertNotIn("unbound_local_authority", codes, report)
            self.assertFalse(
                report["transcript_diagnostic"]["unbound_local_authority_observed"],
                report,
            )


    def test_confirmed_region_can_be_used_for_authority_verification(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            created = archive.create(
                {
                    "jurisdiction": "上海市",
                    "initial_goal": "验证已确认地区的法律核验",
                }
            )
            self.assertTrue(created["ok"], created)
            case_id = created["result"]["case_id"]
            committed = archive.commit(
                {
                    "case_id": case_id,
                    "expected_revision": 0,
                    "change_summary": "记录用户明确提供的适用地区",
                    "changes": [
                        {
                            "operation": "append",
                            "record_type": "fact",
                            "content_markdown": "**事实状态：用户陈述**\\n用户明确提供适用地区：上海市。",
                        }
                    ],
                }
            )
            self.assertTrue(committed["ok"], committed)
            transcript = workspace / "transcript.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "sessionId": "confirmed-region",
                        "type": "function_call",
                        "name": "Authority.verify",
                        "arguments": {"jurisdiction": "上海市"},
                    },
                    ensure_ascii=False,
                )
                + "\\n",
                encoding="utf-8",
            )

            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="confirmed-region",
            )

            self.assertNotIn(
                "unbound_local_authority",
                {item["code"] for item in report["violations"]},
            )

if __name__ == "__main__":
    unittest.main()
