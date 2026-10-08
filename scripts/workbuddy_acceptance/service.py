from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

from scripts.documents.view import managed_delivery_view as _managed_delivery_view
from scripts.case_archive import CaseArchive
from scripts.documents.receipts import (
    validate_archive_read as _validate_model_archive_read,
    read_delivery_entries as _model_public_delivery_entries,
)
from scripts.case_archive.service import CONTRACT_VERSION as CASE_ARCHIVE_CONTRACT
from scripts.documents.ooxml_check import INTERNAL_LEAK_TERMS, audit_docx
from scripts.documents.public import (
    CONTRACT_VERSION as DOCUMENT_RENDER_CONTRACT,
    DELIVERY_SET_CONTRACT_VERSION,
    DOCUMENT_TYPES,
    OPERATION as DOCUMENT_RENDER_OPERATION,
    TEMPLATE_VERSION as DOCUMENT_TEMPLATE_VERSION,
    load_delivery_set,
)
from scripts.platform_paths import (
    absolute_path_for_io,
    normalize_platform_path,
    path_for_io,
    public_path,
)
from scripts.workbuddy_acceptance.native_trace import adapt_native_traces


PROHIBITED_GENERATION_SCRIPTS = frozenset(
    {
        "_gen_docs.py",
        "build_docx.py",
        "build_docs.py",
        "gen_docx.py",
        "generate_docs.py",
        "make_docs.py",
    }
)
REQUIRED_DOCX_PARTS = frozenset(
    {
        "[Content_Types].xml",
        "word/document.xml",
        "word/styles.xml",
    }
)
MEMORY_POLICY_MODE = "post_hoc_audit_only"
MEMORY_POLICY_NEXT_ACTION = "obtain_workbuddy_memory_isolation_evidence"
MEMORY_POLICY_BLOCK_REASON = (
    "WorkBuddy 宿主的 memory 事前禁写或案件数据隔离尚未可验证，严格模式已停止。"
)
MODEL_CASE_ID_PATTERN = re.compile(r"^case-[a-f0-9]{24}$")
MODEL_DELIVERY_NEXT_ACTIONS = {
    "candidate_ready": "present_candidate_deliveries_and_end_turn",
    "final_ready": "status_only",
}
NO_DELIVERY_CONTRACT_SOURCE = "workbuddy-journey-harness"
NO_DELIVERY_CONTRACT_JOURNEY = "ML03-v2"
_TRANSCRIPT_PATH_ARGUMENT_KEYS = frozenset(
    {
        "path",
        "file_path",
        "filepath",
        "filename",
        "cwd",
        "workspace",
        "workspace_root",
        "root",
        "case_root",
        "case_dir",
        "archive_path",
        "output",
        "output_path",
        "output_dir",
        "canonical_location",
        "canonical_docx",
        "verification_checklist",
        "machine_manifest",
        "files",
        "paths",
    }
)
_TRANSCRIPT_NON_PATH_ARGUMENT_KEYS = frozenset(
    {
        "content",
        "text",
        "message",
        "description",
        "explanation",
        "old_string",
        "new_string",
        "change_summary",
        "result",
        "response",
        "stdout",
        "stderr",
    }
)
_USER_VISIBLE_INTERNAL_PATTERN_GROUPS = {
    "record_identity": (
        re.compile(r"\bcase-[0-9a-f]{24}\b", re.IGNORECASE),
        re.compile(r"\b(?:F|CL|E|A|CAL|AUTH|R|CONF|N|HR|HE|PF|FF)-\d{3,}\b"),
        re.compile(r"\b[A-Z]{1,8}-\d{3,}\b"),
        re.compile(
            r"(?:archive_revision|record_id|记录号|档案版本|版本号)\s*[:：=]?\s*[A-Za-z0-9_.-]+",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:revision|record[_ ]?id|case[_ ]?id)\s*[:：=]?\s*[A-Za-z0-9_.-]+",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:内部(?:记录|档案)?(?:编号|ID)|档案内部编号)\s*[:：=]?\s*[A-Za-z0-9_.-]+",
            re.IGNORECASE,
        ),
    ),
    "revision": (
        re.compile(r"(?:最新|档案)?修订版本\s*\d+"),
        re.compile(r"(?:档案|记录|当前状态|版本).{0,4}(?:第\s*)?\d+\s*版"),
        re.compile(r"版本\s*\d+\s*(?:→|到)\s*\d+"),
    ),
    "path_protocol": (
        re.compile(
            r"(?:case-archive-v1|document\.render-v1|amount\.calculate-v1|"
            r"scripts\.[a-z0-9_.-]+|present_files)",
            re.IGNORECASE,
        ),
        re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|\.arbibuddy[\\/]|\.workbuddy[\\/])"),
        re.compile(r"\b(?:CLI|JSON|OOXML|SHA-256|sha256)\b", re.IGNORECASE),
    ),
    "implementation_process": (
        # 只匹配带实现语境的英文词，避免误伤正常的“替换方案”等业务表达。
        re.compile(r"\breplace\s*(?:通道|操作|记录|接口|请求)\b", re.IGNORECASE),
        re.compile(
            r"(?:输入路径(?:类)?|传输层|内部实现|工具调用|函数调用|错误码|返回码)"
            r".{0,8}(?:失败|错误|成功|完成|重试|重放|读取|调用)"
        ),
        re.compile(r"(?:原样|自动|重新)?重放(?:后|成功|完成)"),
        re.compile(r"(?:可恢复|不可恢复)(?:的)?(?:失败|错误)"),
        re.compile(
            r"(?:显式|直接|按)\s*(?:案件编号|case[_ -]?id)\s*(?:再)?"
            r"(?:读|读取|查询|调用|恢复)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:replace|retry|replay|transport|runtime|function_call)\b",
            re.IGNORECASE,
        ),
    ),
    "validation_process": (
        re.compile(r"(?:免编号|按编号)(?:读取|读)|(?:两种|多个)读取路径"),
        re.compile(r"(?:通道|契约)(?:自述|描述)|(?:门禁行为|验证动作|验证结论|验证项)"),
        re.compile(
            r"(?:network_access|fail[- ]closed|形状校验|并发保护|重放安全|内容漂移|"
            r"档案未改动|可恢复错误)",
            re.IGNORECASE,
        ),
        re.compile(r"(?:过期|当前)的?版本号|(?:模拟|演练).{0,12}(?:规则|落档|请求|错误)"),
    ),
}
_REGION_MARKERS = (
    "上海",
    "北京",
    "杭州",
    "广州",
    "深圳",
    "南京",
    "苏州",
    "成都",
    "武汉",
    "重庆",
    "天津",
    "浙江",
    "江苏",
    "广东",
    "四川",
    "湖北",
    "山东",
    "全国",
)
_LOCAL_AUTHORITY_REGION_MARKERS = tuple(
    marker for marker in _REGION_MARKERS if marker != "全国"
)
_UNTRUSTED_CANARY_PATTERN = re.compile(
    r"\b(?:arbibuddy[_-])?canary[_-][a-z0-9_-]+\b", re.IGNORECASE
)
_UNTRUSTED_AMOUNT_PATTERN = re.compile(
    r"(?<!\d)\d{3,8}(?:\.\d{1,2})?\s*(?:元|万元)(?!\w)"
)
_UNTRUSTED_DATE_PATTERN = re.compile(
    r"(?<!\d)20\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?"
)
_UNTRUSTED_DURATION_PATTERN = re.compile(
    r"(?<![\d零〇一二两三四五六七八九十百])"
    r"(?:(?:\d+|[零〇一二两三四五六七八九十百]+)(?:个月|年|天|周)|半年)"
    r"(?![\w零〇一二两三四五六七八九十百])"
)
_DURATION_FACT_CONTEXT = (
    "欠薪",
    "拖欠",
    "未支付",
    "工资",
    "入职",
    "任职",
    "工作",
    "加班",
    "合同",
    "在职",
    "工龄",
    "服务期",
    "试用期",
)
_DURATION_RULE_CONTEXT = (
    "时效",
    "起算",
    "申请仲裁",
    "提出",
    "劳动关系终止",
    "终止之日起",
    "期限",
)


def _validate_no_delivery_contract(
    path: Path | None,
    *,
    case_id: str,
    transcript_jsonl: Path | None,
    native_trace_evidence: Mapping[str, Any] | None = None,
    session_ids: tuple[str, ...] = (),
    require_v2_contract: bool = False,
) -> str | None:
    if path is None:
        return "--allow-no-delivery 必须提供结构化 Journey contract"
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "无文书 Journey contract 不可读取或解析"
    if not isinstance(contract, Mapping):
        return "无文书 Journey contract 必须是对象"
    if (
        (require_v2_contract or native_trace_evidence is not None)
        and contract.get("schema_version") != 2
    ):
        return "ML03-v2 必须使用 workbuddy-journey-contract-v2"
    if contract.get("schema_version") == 2:
        required = {
            "schema_version",
            "contract_version",
            "source",
            "journey_id",
            "case_id",
            "session_ids",
            "trace_files",
            "trace_summary",
            "scenario_spec_sha256",
            "scenario_summary",
            "expected_deliveries",
            "milestones",
            "fact_boundary",
            "transcript_sha256",
        }
        if set(contract) != required:
            return "workbuddy-journey-contract-v2 字段集合无效"
        if contract.get("contract_version") != "workbuddy-journey-contract-v2":
            return "workbuddy-journey-contract-v2 contract_version 无效"
        if contract.get("source") != NO_DELIVERY_CONTRACT_SOURCE:
            return "无文书 Journey contract 来源无效"
        if contract.get("journey_id") != NO_DELIVERY_CONTRACT_JOURNEY:
            return "无文书 Journey contract 不是 ML03-v2"
        if contract.get("case_id") != case_id:
            return "无文书 Journey contract 案件编号不一致"
        if transcript_jsonl is None:
            return "workbuddy-journey-contract-v2 必须绑定 transcript"
        transcript_digest = contract.get("transcript_sha256")
        if (
            not isinstance(transcript_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", transcript_digest)
        ):
            return "workbuddy-journey-contract-v2 缺少 transcript SHA-256"
        try:
            transcript_bytes = transcript_jsonl.read_bytes()
        except (OSError, IOError):
            return "workbuddy-journey-contract-v2 绑定的 transcript 不可读取"
        if not transcript_bytes.strip():
            return "workbuddy-journey-contract-v2 绑定的 transcript 为空"
        actual_transcript_digest = sha256(transcript_bytes).hexdigest()
        if transcript_digest != actual_transcript_digest:
            return "workbuddy-journey-contract-v2 与 transcript SHA-256 不一致"
        declared_sessions = contract.get("session_ids")
        if (
            not isinstance(declared_sessions, list)
            or len(declared_sessions) != 2
            or not all(isinstance(item, str) and item for item in declared_sessions)
        ):
            return "workbuddy-journey-contract-v2 必须绑定有序的两个 session"
        if session_ids and list(session_ids) != declared_sessions:
            return "workbuddy-journey-contract-v2 session 顺序与验收参数不一致"
        if native_trace_evidence is None:
            return "workbuddy-journey-contract-v2 必须绑定原生 WorkBuddy trace"
        if native_trace_evidence.get("diagnostic_codes"):
            return "workbuddy-journey-contract-v2 原生 trace 存在未解决诊断"
        if list(native_trace_evidence.get("session_ids", [])) != declared_sessions:
            return "workbuddy-journey-contract-v2 session 集合与原生 trace 不一致"
        if contract.get("expected_deliveries") != []:
            return "无文书 Journey contract 必须声明空交付列表"
        trace_files = contract.get("trace_files")
        if not isinstance(trace_files, list) or len(trace_files) != 2:
            return "workbuddy-journey-contract-v2 trace_files 必须覆盖两个 session"
        trace_file_sessions: list[object] = []
        for item in trace_files:
            if not isinstance(item, Mapping):
                return "workbuddy-journey-contract-v2 trace_files 项无效"
            item_session = item.get("session_id")
            if item_session in trace_file_sessions:
                return "workbuddy-journey-contract-v2 trace_files 不得重复绑定 session"
            trace_file_sessions.append(item_session)
        if trace_file_sessions != declared_sessions:
            return "workbuddy-journey-contract-v2 trace_files 必须按 session 顺序各绑定一次"
        actual_by_session: dict[str, list[dict[str, str]]] = {
            item: [] for item in declared_sessions
        }
        for item in native_trace_evidence.get("trace_files", []):
            if not isinstance(item, Mapping):
                continue
            file_path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(file_path, str) or not isinstance(digest, str):
                continue
            for item_session in item.get("session_ids", []):
                if item_session in actual_by_session:
                    actual_by_session[item_session].append(
                        {"path": file_path, "sha256": digest}
                    )
        for item in trace_files:
            item_session = item.get("session_id")
            files = item.get("files")
            if item_session not in actual_by_session or not isinstance(files, list):
                return "workbuddy-journey-contract-v2 trace_files session 无效"
            declared_files: list[dict[str, str]] = []
            for file in files:
                if (
                    not isinstance(file, Mapping)
                    or set(file) != {"path", "sha256"}
                    or not isinstance(file.get("path"), str)
                    or not file.get("path")
                    or Path(file["path"]).is_absolute()
                    or ".." in Path(file["path"]).parts
                    or not isinstance(file.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", file["sha256"]) is None
                ):
                    return "workbuddy-journey-contract-v2 trace 文件摘要项无效"
                declared_files.append({
                    "path": file["path"],
                    "sha256": file["sha256"],
                })
            declared_files.sort(key=lambda value: value["path"])
            if declared_files != sorted(actual_by_session[item_session], key=lambda value: value["path"]):
                return "workbuddy-journey-contract-v2 trace 文件摘要不一致"
        expected_trace_summary = {
            "sessions": [
                item
                for item in native_trace_evidence.get("sessions", [])
                if isinstance(item, Mapping)
                and item.get("session_id") in declared_sessions
            ],
            "aggregate": native_trace_evidence.get("summary", {}),
        }
        if contract.get("trace_summary") != expected_trace_summary:
            return "workbuddy-journey-contract-v2 trace 摘要不一致"
        digest = contract.get("scenario_spec_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "workbuddy-journey-contract-v2 缺少 scenario 规范摘要"
        scenario_summary = contract.get("scenario_summary")
        if not isinstance(scenario_summary, Mapping):
            return "workbuddy-journey-contract-v2 scenario 摘要无效"
        milestones = contract.get("milestones")
        expected_milestones = [
            "first_session_correction_commit",
            "second_session_skill_reactivated",
            "second_session_first_substantive_action_current_case_read",
            "no_render_or_present",
        ]
        if milestones != expected_milestones:
            return "workbuddy-journey-contract-v2 必须声明恢复里程碑"
        boundary = contract.get("fact_boundary")
        if (
            not isinstance(boundary, Mapping)
            or set(scenario_summary) != {
                "scenario_id",
                "provided_fact_ids",
                "forbidden_marker_ids",
            }
            or scenario_summary.get("scenario_id") != "ML03-v2"
            or not isinstance(scenario_summary.get("provided_fact_ids"), list)
            or not isinstance(scenario_summary.get("forbidden_marker_ids"), list)
            or not isinstance(boundary.get("provided_fact_ids"), list)
            or not isinstance(boundary.get("forbidden_markers"), list)
            or scenario_summary.get("provided_fact_ids")
            != boundary.get("provided_fact_ids")
            or not all(isinstance(item, str) and item for item in boundary.get("provided_fact_ids", []))
            or not all(isinstance(item, str) and item for item in boundary.get("forbidden_markers", []))
            or scenario_summary.get("forbidden_marker_ids")
            != [
                f"forbidden-marker-{index + 1}"
                for index in range(len(boundary.get("forbidden_markers", [])))
            ]
        ):
            return "workbuddy-journey-contract-v2 事实边界摘要无效"
        return None
    if transcript_jsonl is None:
        return "无文书 Journey contract 必须绑定 transcript"
    if set(contract) != {
        "schema_version",
        "source",
        "journey_id",
        "case_id",
        "expected_deliveries",
        "transcript_sha256",
    }:
        return "无文书 Journey contract 字段集合无效"
    if contract.get("schema_version") != 1:
        return "无文书 Journey contract schema_version 无效"
    if contract.get("source") != NO_DELIVERY_CONTRACT_SOURCE:
        return "无文书 Journey contract 来源无效"
    if contract.get("journey_id") != NO_DELIVERY_CONTRACT_JOURNEY:
        return "无文书 Journey contract 不是 ML03-v2"
    if contract.get("case_id") != case_id:
        return "无文书 Journey contract 案件编号不一致"
    if contract.get("expected_deliveries") != []:
        return "无文书 Journey contract 必须声明空交付列表"
    digest = contract.get("transcript_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return "无文书 Journey contract 缺少 transcript SHA-256"
    try:
        actual_digest = sha256(transcript_jsonl.read_bytes()).hexdigest()
    except (OSError, IOError):
        return "无文书 Journey contract 绑定的 transcript 不可读取"
    if digest != actual_digest:
        return "无文书 Journey contract 与 transcript SHA-256 不一致"
    return None


def build_no_delivery_contract(
    *,
    journey: str,
    case_id: str,
    transcript_jsonl: Path | None = None,
    output: Path,
    workbuddy_trace_root: Path | None = None,
    session_ids: tuple[str, ...] = (),
    scenario_spec: Path | None = None,
) -> dict[str, Any]:
    """Seal the registered no-document Journey evidence for later acceptance."""
    if journey != NO_DELIVERY_CONTRACT_JOURNEY:
        raise ValueError(f"不支持生成无文书契约：{journey}")
    if MODEL_CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValueError("无文书契约只能绑定模型主导案件编号")
    output_path = Path(os.path.abspath(output))
    repository_root = Path(__file__).resolve().parents[2]
    try:
        output_path.resolve().relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "无文书 Journey contract 必须写入源码树外的测试临时根"
        )
    if output_path.exists():
        raise FileExistsError(f"契约目标已存在：{output_path}")
    if workbuddy_trace_root is not None:
        if transcript_jsonl is None:
            raise ValueError(
                "workbuddy-journey-contract-v2 必须绑定公开 transcript"
            )
        transcript_path = Path(os.path.abspath(transcript_jsonl))
        if not transcript_path.is_file():
            raise FileNotFoundError(
                f"绑定的 transcript 不存在：{transcript_path}"
            )
        transcript_bytes = transcript_path.read_bytes()
        if not transcript_bytes.strip():
            raise ValueError("workbuddy-journey-contract-v2 绑定的 transcript 为空")
        transcript_sha256 = sha256(transcript_bytes).hexdigest()
        if len(session_ids) != 2 or len(set(session_ids)) != 2:
            raise ValueError("workbuddy-journey-contract-v2 必须绑定两个不同 session")
        trace_root = Path(os.path.abspath(workbuddy_trace_root))
        if not trace_root.is_dir():
            raise FileNotFoundError(f"原生 trace 根目录不存在：{trace_root}")
        evidence = adapt_native_traces(trace_root, session_ids=session_ids)
        if evidence.get("diagnostic_codes"):
            raise ValueError(
                "原生 trace 不可用于生成 contract："
                + ",".join(evidence["diagnostic_codes"])
            )
        scenario_path = (
            Path(os.path.abspath(scenario_spec))
            if scenario_spec is not None
            else Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "workbuddy"
            / "ml03-v2-scenario.json"
        )
        try:
            scenario_bytes = scenario_path.read_bytes()
            scenario_value = json.loads(scenario_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("ML03-v2 scenario 规范不可读取") from error
        if not isinstance(scenario_value, Mapping):
            raise ValueError("ML03-v2 scenario 规范必须是对象")
        forbidden_markers = scenario_value.get("forbidden_fact_markers", [])
        provided_fact_ids = scenario_value.get("provided_fact_ids", [])
        if not isinstance(forbidden_markers, list) or not all(
            isinstance(item, str) and item for item in forbidden_markers
        ):
            raise ValueError("ML03-v2 scenario 缺少禁止污染标记")
        if not isinstance(provided_fact_ids, list) or not all(
            isinstance(item, str) and item for item in provided_fact_ids
        ):
            raise ValueError("ML03-v2 scenario 提供事实摘要无效")
        session_summaries = evidence.get("sessions", [])
        files_by_session = {
            session_id: [
                {
                    "path": item["path"],
                    "sha256": item["sha256"],
                }
                for item in evidence.get("trace_files", [])
                if session_id in item.get("session_ids", [])
            ]
            for session_id in session_ids
        }
        contract = {
            "schema_version": 2,
            "contract_version": "workbuddy-journey-contract-v2",
            "source": NO_DELIVERY_CONTRACT_SOURCE,
            "journey_id": journey,
            "case_id": case_id,
            "transcript_sha256": transcript_sha256,
            "session_ids": list(session_ids),
            "trace_files": [
                {"session_id": session_id, "files": files_by_session[session_id]}
                for session_id in session_ids
            ],
            "trace_summary": {
                "sessions": [
                    item
                    for item in session_summaries
                    if item.get("session_id") in session_ids
                ],
                "aggregate": evidence.get("summary", {}),
            },
            "scenario_spec_sha256": sha256(scenario_bytes).hexdigest(),
            "scenario_summary": {
                "scenario_id": scenario_value.get("scenario_id"),
                "provided_fact_ids": list(provided_fact_ids),
                "forbidden_marker_ids": [
                    f"forbidden-marker-{index + 1}"
                    for index in range(len(forbidden_markers))
                ],
            },
            "expected_deliveries": [],
            "milestones": [
                "first_session_correction_commit",
                "second_session_skill_reactivated",
                "second_session_first_substantive_action_current_case_read",
                "no_render_or_present",
            ],
            "fact_boundary": {
                "provided_fact_ids": list(provided_fact_ids),
                "forbidden_markers": list(forbidden_markers),
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return contract
    if transcript_jsonl is None:
        raise ValueError("v1 无文书契约必须提供 transcript_jsonl")
    transcript_path = Path(os.path.abspath(transcript_jsonl))
    if not transcript_path.is_file():
        raise FileNotFoundError(f"transcript 不存在：{transcript_path}")
    contract = {
        "schema_version": 1,
        "source": NO_DELIVERY_CONTRACT_SOURCE,
        "journey_id": journey,
        "case_id": case_id,
        "expected_deliveries": [],
        "transcript_sha256": sha256(transcript_path.read_bytes()).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return contract


def _memory_policy_summary() -> dict[str, Any]:
    return {
        "mode": MEMORY_POLICY_MODE,
        "prewrite_enforced": False,
        "case_data_isolated": False,
        "verified": False,
    }


def audit_workspace(
    *,
    workspace: Path,
    case_id: str,
    expected_deliveries: tuple[str, ...] = (),
    audit_log: Path | None = None,
    session_id: str | None = None,
    transcript_jsonl: Path | None = None,
    client_log: Path | None = None,
    allow_plan_subset: bool = False,
    current_case_only: bool = False,
    require_memory_isolation: bool = False,
    allow_no_delivery: bool = False,
    no_delivery_contract: Path | None = None,
    workbuddy_trace_root: Path | None = None,
    session_ids: tuple[str, ...] = (),
    journey_contract: Path | None = None,
) -> dict[str, Any]:
    """验证 WorkBuddy 文书是否全部来自受控案件交付事务。"""

    root = Path(os.path.abspath(normalize_platform_path(workspace)))
    violations: list[dict[str, str]] = []
    presentation_files: list[dict[str, Any]] = []
    memory_policy = _memory_policy_summary()
    managed_view = _managed_delivery_view(
        workspace=root,
        case_id=case_id,
    )
    authorized_presentation_paths = _authorized_presentation_paths(
        tuple(
            str(item.get("path"))
            for item in managed_view.get("presentation_files", [])
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        ),
        root,
    )
    authorized_presentation_values = tuple(
        str(item.get("path"))
        for item in managed_view.get("presentation_files", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    )
    if require_memory_isolation:
        _add(
            violations,
            "workbuddy_memory_isolation_unverified",
            "未提供 WorkBuddy memory 宿主状态证据；此项仅作显式运行时诊断，不改变 Skill Journey 判定",
            root / ".workbuddy" / "memory",
        )
    if not root.is_dir():
        _add(violations, "workspace_missing", "工作区不存在", root)
        return _report(
            root,
            case_id,
            expected_deliveries,
            violations,
            memory_policy=memory_policy,
        )
    if not case_id or case_id in {".", ".."} or any(
        separator in case_id for separator in ("/", "\\")
    ):
        _add(violations, "invalid_case_id", "案件编号无效", root)
        return _report(root, case_id, expected_deliveries, violations)

    case_dir = root / ".arbibuddy" / "cases" / case_id
    output_root = case_dir / "output"
    case_file = case_dir / "案情档案.md"
    declared = tuple(dict.fromkeys(expected_deliveries))
    model_case = (
        MODEL_CASE_ID_PATTERN.fullmatch(case_id) is not None
        and "delivery_summaries" in managed_view
    )
    model_delivery_summaries: list[dict[str, Any]] = []
    if model_case:
        if not case_file.is_file():
            _add(violations, "case_file_missing", "权威案情档案缺失", case_dir)
        elif managed_view.get("delivery_state") == "unavailable":
            if not allow_no_delivery:
                delivery_error_code = str(
                    managed_view.get("error_code") or "invalid_model_delivery"
                )
                _add(
                    violations,
                    delivery_error_code,
                    str(managed_view.get("reason") or "document.render-v1 canonical 交付无效"),
                    output_root,
                )
        else:
            raw_summaries = managed_view.get("delivery_summaries")
            if isinstance(raw_summaries, list) and all(
                isinstance(item, dict)
                and isinstance(item.get("document_type"), str)
                for item in raw_summaries
            ):
                model_delivery_summaries = list(raw_summaries)
            else:
                _add(
                    violations,
                    "invalid_model_delivery",
                    "document.render-v1 canonical 交付摘要缺失",
                    output_root,
                )
        planned = tuple(
            item["document_type"] for item in model_delivery_summaries
        )
        expected = declared if allow_plan_subset and declared else planned
        if declared and (
            (not allow_plan_subset and declared != planned)
            or (allow_plan_subset and not set(declared) <= set(planned))
        ):
            _add(
                violations,
                "expected_delivery_plan_mismatch",
                "命令声明的预期交付不属于 document.render-v1 canonical 交付清单",
                case_file,
            )
        if not expected and not managed_view.get("stopped") and not allow_no_delivery:
            _add(
                violations,
                "missing_expected_deliveries",
                "document.render-v1 canonical 交付清单没有预期文书",
                root,
            )
    else:
        expected = declared
        _add(violations, "invalid_case_file", "案情档案不符合当前公共契约", case_file)

    audit_files = tuple(
        _workspace_files(case_dir / "input")
        if current_case_only
        else _workspace_files(root)
    )
    for path in audit_files:
        case_input_script = (
            _is_relative_to(path.resolve(), (case_dir / "input").resolve())
            and path.suffix.casefold() in {".py", ".js"}
        )
        ad_hoc_docx_script = False
        if path.suffix.casefold() in {".py", ".js"}:
            try:
                script_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                script_text = ""
            folded = script_text.casefold()
            ad_hoc_docx_script = (
                ("import docx" in folded or "from docx import" in folded)
                and (".save(" in folded or "document(" in folded)
            )
        if (
            path.name.casefold() in PROHIBITED_GENERATION_SCRIPTS
            or case_input_script
            or ad_hoc_docx_script
        ):
            _add(
                violations,
                "prohibited_generation_script",
                "发现被禁止的旁路文书生成脚本",
                path,
            )
        relative_parts = tuple(
            part.casefold() for part in path.relative_to(root).parts[:-1]
        )
        if (
            path.suffix.casefold() == ".json"
            and any(
                part in {
                    "render-requests",
                    "runtime-requests",
                    "document-requests",
                    "runtime-input",
                }
                for part in relative_parts
            )
        ):
            _add(
                violations,
                "persistent_runtime_request_copy",
                "文书工具请求是瞬时数据，完成后不得在工作区留下事实副本",
                path,
            )

    canonical_output = output_root.resolve()
    for html in case_dir.rglob("*.html"):
        if not _is_relative_to(html.resolve(), canonical_output):
            _add(
                violations,
                "unmanaged_html_delivery",
                "HTML 不属于统一文书生成器的受管交付物",
                html,
            )

    for path in audit_files:
        if not _looks_like_unmanaged_text_delivery(path, case_dir):
            continue
        code = (
            "unmanaged_pdf_delivery"
            if path.suffix.casefold() == ".pdf"
            else "unmanaged_markdown_delivery"
        )
        _add(
            violations,
            code,
            "文件不在核心状态信封授权的受管交付清单中",
            path,
        )

    safe_expected: list[str] = []
    for label in expected:
        if not label or label in {".", ".."} or Path(label).name != label:
            _add(
                violations,
                "invalid_delivery_label",
                "预期交付目录必须是安全的直接子目录名",
                output_root,
            )
            continue
        safe_expected.append(label)

    visible_prefix = f"ArbiBuddy-交付-{case_id}-"
    valid_visible_roots: set[Path] = set()
    if model_case:
        if not managed_view.get("stopped"):
            presentation_files.extend(
                dict(item)
                for item in managed_view.get("presentation_files", [])
            )
    allowed_presentation_paths = {
        str(Path(item["path"]).resolve())
        for item in managed_view["presentation_files"]
    }
    for item in presentation_files:
        if str(Path(item["path"]).resolve()) not in allowed_presentation_paths:
            _add(
                violations,
                "presentation_not_in_state_envelope",
                "用户可见文件未获核心状态信封的受控交付清单授权",
                Path(item["path"]),
            )

    docx_roots = (
        [output_root, *(root / f"{visible_prefix}{label}" for label in safe_expected)]
        if current_case_only
        else [root]
    )
    for docx in (
        path
        for scan_root in docx_roots
        if scan_root.is_dir()
        for path in scan_root.rglob("*.docx")
    ):
        resolved = docx.resolve()
        if not _is_relative_to(resolved, root.resolve()):
            _add(
                violations,
                "docx_path_escape",
                "DOCX 路径链接到工作区之外",
                docx,
            )
            continue
        if not _is_relative_to(resolved, canonical_output) and not any(
            _is_relative_to(resolved, visible) for visible in valid_visible_roots
        ):
            _add(
                violations,
                "unmanaged_docx",
                "DOCX 不属于权威案件输出或受管可见交付",
                docx,
            )
        if not _valid_docx_package(docx):
            _add(
                violations,
                "invalid_docx_package",
                "DOCX 缺少必要 OOXML 结构或页脚",
                docx,
            )

    native_trace_evidence: dict[str, Any] | None = None
    native_records: list[dict[str, Any]] | None = None
    strict_recovery = False
    if workbuddy_trace_root is not None:
        strict_recovery = journey_contract is not None or (
            allow_no_delivery and no_delivery_contract is not None
        )
        native_trace_evidence = adapt_native_traces(
            Path(os.path.abspath(workbuddy_trace_root)),
            session_ids=session_ids,
        )
        native_records = list(native_trace_evidence.get("records", []))
        for code in native_trace_evidence.get("diagnostic_codes", []):
            _add(
                violations,
                str(code),
                "WorkBuddy 原生 trace 结构化证据无法完整验证",
                Path(os.path.abspath(workbuddy_trace_root)),
            )
    trace_diagnostic = {
        "acknowledgement_after_controlled_failure": False,
        "prohibited_present_files": False,
    }
    if audit_log is not None:
        trace_diagnostic = _audit_command_trace(
            Path(os.path.abspath(audit_log)),
            session_id,
            violations,
            expected_document_count=len(safe_expected),
        )

    transcript_diagnostic: dict[str, Any] | None = None
    if transcript_jsonl is not None or native_records is not None:
        try:
            trusted_case_text = case_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            trusted_case_text = ""
        transcript_diagnostic = _audit_transcript_jsonl(
            (
                Path(os.path.abspath(transcript_jsonl))
                if transcript_jsonl is not None
                else Path("<workbuddy-native-trace>")
            ),
            None if native_records is not None else session_id,
            violations,
            workspace=root,
            case_id=case_id,
            authorized_presentation_paths=authorized_presentation_paths,
            authorized_presentation_values=authorized_presentation_values,
            require_historical_managed_view=model_case,
            trusted_case_text=trusted_case_text,
            records_override=native_records,
            strict_recovery=strict_recovery,
        )
        if native_trace_evidence is not None:
            session_diagnostics: list[dict[str, Any]] = []
            for native_session_id in native_trace_evidence.get("session_ids", []):
                session_diagnostic = _audit_transcript_jsonl(
                    Path("<workbuddy-native-trace>"),
                    str(native_session_id),
                    [],
                    workspace=root,
                    case_id=case_id,
                    authorized_presentation_paths=authorized_presentation_paths,
                    authorized_presentation_values=authorized_presentation_values,
                    require_historical_managed_view=model_case,
                    trusted_case_text=trusted_case_text,
                    records_override=native_records,
                    strict_recovery=strict_recovery,
                )
                session_diagnostics.append(session_diagnostic)
            transcript_diagnostic["sessions"] = session_diagnostics
            transcript_diagnostic["native_trace"] = {
                "session_ids": native_trace_evidence.get("session_ids", []),
                "trace_files": native_trace_evidence.get("trace_files", []),
                "summary": native_trace_evidence.get("summary", {}),
                "diagnostic_codes": native_trace_evidence.get("diagnostic_codes", []),
            }
        if (
            trace_diagnostic["acknowledgement_after_controlled_failure"]
            and not transcript_diagnostic.get("user_round_observed")
        ):
            _add(
                violations,
                "failure_acknowledgement_requires_user_round",
                "失败围栏解除命令缺少可核验的后续用户轮次；模型不得自行 acknowledge",
                Path(os.path.abspath(audit_log)) if audit_log is not None else root,
            )

        if model_case and transcript_diagnostic is not None:
            timeline = transcript_diagnostic.get("revision_timeline", {})
            historical = timeline.get("historical_presentations", [])
            historical_types = next(
                (
                    tuple(item.get("delivery_expected_types", ()))
                    for item in reversed(historical)
                    if item.get("delivery_expected_types")
                ),
                (),
            )
            expected_history_matches = not declared or (
                bool(historical_types) and historical_types == declared
            )
            current_view_error = str(managed_view.get("error_code") or "")
            recoverable_current_view = current_view_error in {
                "invalid_model_delivery",
                "stale_managed_delivery",
                "delivery_set_incomplete",
            }
            historical_view_is_authoritative = bool(
                recoverable_current_view
                and historical
                and timeline.get("valid") is True
                and timeline.get("present_count") == len(historical)
                and expected_history_matches
            )
            if historical_view_is_authoritative:
                suppressed_current_codes = {
                    current_view_error,
                    "invalid_model_delivery",
                    "stale_managed_delivery",
                    "delivery_set_incomplete",
                    "missing_expected_deliveries",
                }
                if expected_history_matches:
                    suppressed_current_codes.add("expected_delivery_plan_mismatch")
                violations[:] = [
                    item
                    for item in violations
                    if item["code"] not in suppressed_current_codes
                ]
                historical_files: list[dict[str, Any]] = []
                latest = historical[-1]
                values = latest.get("authorized_values", [])
                hashes = latest.get("file_hashes", [])
                delivery_types = list(latest.get("delivery_expected_types", []))
                for index, value in enumerate(values):
                    digest = (
                        hashes[index].get("sha256")
                        if index < len(hashes)
                        and isinstance(hashes[index], Mapping)
                        else None
                    )
                    delivery_label = (
                        delivery_types[index // 2]
                        if delivery_types and index // 2 < len(delivery_types)
                        else "historical_managed_delivery"
                    )
                    historical_files.append(
                        {
                            "delivery_label": delivery_label,
                            "kind": "document"
                            if str(value).casefold().endswith(".docx")
                            else "checklist",
                            "order": index + 1,
                            "path": str(value),
                            "sha256": digest,
                            "state": "historical_valid_at_event",
                        }
                    )
                presentation_files = historical_files
                managed_view = dict(managed_view)
                managed_view["current_state_diagnostic"] = {
                    "error_code": current_view_error,
                    "reason": managed_view.get("reason"),
                    "not_used_to_revoke_historical_presentation": True,
                }
                managed_view["historical_presentation_authorized"] = True
                managed_view["historical_presentation_count"] = len(historical_files)

    if allow_no_delivery:
        no_delivery_mode_error: str | None = None
        contract_path = journey_contract or no_delivery_contract
        if not model_case:
            no_delivery_mode_error = "--allow-no-delivery 只适用于模型主导案件"
        elif transcript_jsonl is None and native_trace_evidence is None:
            no_delivery_mode_error = "--allow-no-delivery 必须提供 transcript 或原生 trace 作为无文书 Journey 证据"
        else:
            no_delivery_mode_error = _validate_no_delivery_contract(
                contract_path,
                case_id=case_id,
                transcript_jsonl=(
                    Path(os.path.abspath(transcript_jsonl))
                    if transcript_jsonl is not None
                    else None
                ),
                native_trace_evidence=native_trace_evidence,
                session_ids=session_ids,
                require_v2_contract=journey_contract is not None,
            )
        if no_delivery_mode_error is None and declared:
            no_delivery_mode_error = "已有命令预期文书时不得使用 --allow-no-delivery"
        if no_delivery_mode_error is None and model_delivery_summaries:
            no_delivery_mode_error = "已有 document.render-v1 交付摘要时不得使用 --allow-no-delivery"
        if no_delivery_mode_error is None and output_root.is_dir():
            try:
                has_output = any(output_root.iterdir())
            except OSError:
                has_output = True
            if has_output:
                no_delivery_mode_error = "案件已有文书输出时不得使用 --allow-no-delivery"
        if no_delivery_mode_error is None and native_trace_evidence is not None:
            trace_path = Path(os.path.abspath(workbuddy_trace_root))
            if len(session_ids) != 2 or len(set(session_ids)) != 2:
                no_delivery_mode_error = (
                    "workbuddy-journey-contract-v2 必须通过两个有序 --session-id 绑定 session"
                )
            else:
                if _native_sessions_overlap(native_records or [], session_ids):
                    _add(
                        violations,
                        "journey_session_overlap",
                        "ML03 两个会话的原生事件时间线发生交叠，无法证明第二会话是在第一会话之后开始",
                        trace_path,
                    )
                session_diagnostics = (
                    transcript_diagnostic.get("sessions", [])
                    if transcript_diagnostic
                    else []
                )
                if len(session_diagnostics) != 2:
                    no_delivery_mode_error = "workbuddy-journey-contract-v2 缺少两个 session 的结构化证据"
                else:
                    native_records_for_audit = native_records or []
                    if not all(
                        item.get("skill_invocation_observed")
                        and item.get("skill_result_observed")
                        for item in session_diagnostics
                    ):
                        _add(
                            violations,
                            "restart_skill_invocation_missing",
                            "ML03 第二会话必须重新激活并成功调用 ArbiBuddy Skill",
                            trace_path,
                        )

                    first_session_records = [
                        item
                        for item in native_records_for_audit
                        if item.get("sessionId") == session_ids[0]
                        and item.get("event") == "function_call"
                    ]
                    first_commit = next(
                        (
                            item
                            for item in first_session_records
                            if _transcript_case_archive_operation(item) == "commit"
                            and _transcript_case_archive_bound(
                                item, workspace=root, case_id=case_id
                            )
                        ),
                        None,
                    )
                    if first_commit is None or not _transcript_call_has_successful_result(
                        native_records_for_audit, _transcript_call_id(first_commit)
                    ):
                        _add(
                            violations,
                            "first_session_correction_commit_missing",
                            "ML03 第一会话必须完成当前案件的事实纠正 commit",
                            trace_path,
                        )

                    second_session_id = session_ids[1]
                    second_session_evidence = [
                        item
                        for item in native_records_for_audit
                        if item.get("sessionId") == second_session_id
                    ]
                    second_records = [
                        item
                        for item in second_session_evidence
                        if item.get("event") == "function_call"
                    ]
                    first_second_tool = second_records[0] if second_records else None
                    if first_second_tool is None or not _transcript_skill_call(
                        first_second_tool
                    ):
                        _add(
                            violations,
                            "restart_first_tool_not_skill",
                            "ML03 第二会话的第一个模型工具调用必须是 ArbiBuddy Skill",
                            trace_path,
                        )
                    skill_call_index = next(
                        (
                            index
                            for index, item in enumerate(second_session_evidence)
                            if item.get("event") == "function_call"
                            and _transcript_skill_call(item)
                        ),
                        None,
                    )
                    (
                        recoverable_retry_ids,
                        _exact_recovery_index,
                    ) = _recoverable_case_archive_retry_call_ids(
                        second_session_evidence,
                        workspace=root,
                    )
                    read_call_index = next(
                        (
                            index
                            for index, item in enumerate(second_session_evidence)
                            if item.get("event") == "function_call"
                            and _transcript_case_archive_operation(item) == "read"
                            and _transcript_case_archive_bound(
                                item, workspace=root, case_id=case_id
                            )
                            and _transcript_call_has_successful_result(
                                second_session_evidence,
                                _transcript_call_id(item),
                            )
                        ),
                        None,
                    )
                    recovery_reference_call_ids = {
                        call_id
                        for item in second_records
                        for call_id in (_transcript_call_id(item),)
                        if call_id is not None
                        and "/references/model-led-case-archive.md"
                        in " ".join(_transcript_path_texts(item)).replace("\\", "/")
                    }
                    recovery_exempt_ids = (
                        frozenset()
                        if strict_recovery
                        else recoverable_retry_ids | recovery_reference_call_ids
                    )
                    if (
                        skill_call_index is not None
                        and read_call_index is not None
                        and any(
                            item.get("event") == "function_call"
                            and index > skill_call_index
                            and index < read_call_index
                            and _transcript_call_id(item) not in recovery_exempt_ids
                            and not _transcript_untrusted_context_path(item)
                            for index, item in enumerate(second_session_evidence)
                        )
                    ):
                        _add(
                            violations,
                            "restart_pre_read_probe",
                            "ML03 第二会话在当前案件 read 前不得执行目录、参考、源码或环境探查",
                            trace_path,
                        )
                    substantive = [
                        item
                        for item in second_records
                        if "skill" not in _transcript_call_names(item)
                        and not _transcript_is_read_only_preparation(item)
                        and _transcript_call_id(item) not in recovery_exempt_ids
                    ]
                    first_substantive = substantive[0] if substantive else None
                    first_operation = (
                        _transcript_case_archive_operation(first_substantive)
                        if first_substantive is not None
                        else None
                    )
                    first_arguments = (
                        _transcript_argument_object(first_substantive)
                        if first_substantive is not None
                        else {}
                    )
                    if (
                        first_operation != "read"
                        or first_arguments.get("case_id") not in {None, case_id}
                        or first_substantive is None
                        or not _transcript_call_has_successful_result(
                            native_records_for_audit,
                            _transcript_call_id(first_substantive),
                        )
                    ):
                        _add(
                            violations,
                            "restart_first_action_not_current_case_read",
                            "ML03 第二会话首个实质案件动作必须是当前案件的公开 read",
                            trace_path,
                        )
            if transcript_diagnostic and (
                transcript_diagnostic.get("render_observed")
                or transcript_diagnostic.get("present_files_observed")
            ):
                _add(
                    violations,
                    "no_delivery_document_activity",
                    "ML03 无文书 contract 不得出现 render 或 present_files",
                    Path(os.path.abspath(workbuddy_trace_root)),
                )
            try:
                contract_value = json.loads(
                    Path(contract_path).read_text(encoding="utf-8")
                ) if contract_path is not None else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                contract_value = None
            boundary = (
                contract_value.get("fact_boundary")
                if isinstance(contract_value, Mapping)
                and contract_value.get("schema_version") == 2
                else None
            )
            forbidden_markers = (
                boundary.get("forbidden_markers", [])
                if isinstance(boundary, Mapping)
                else []
            )
            if isinstance(forbidden_markers, list) and any(
                isinstance(marker, str)
                and marker
                and (
                    marker.casefold() in trusted_case_text.casefold()
                    or any(
                        _native_trace_uses_forbidden_fact(item, marker)
                        for item in native_records or []
                    )
                )
                for marker in forbidden_markers
            ):
                _add(
                    violations,
                    "scenario_fact_boundary_pollution",
                    "ML03 出现 scenario 明确禁止的未提供事实污染",
                    Path(os.path.abspath(workbuddy_trace_root)),
                )
            if no_delivery_mode_error is None:
                transcript_diagnostic["explicit_no_document_goal"] = True
        if (
            no_delivery_mode_error is None
            and (
                transcript_diagnostic is None
                or transcript_diagnostic.get("document_delivery_observed")
            )
        ):
            no_delivery_mode_error = "transcript 已出现文书交付或展示调用"
        if (
            no_delivery_mode_error is None
            and not transcript_diagnostic.get("explicit_no_document_goal")
        ):
            no_delivery_mode_error = "transcript 未明确记录本轮不生成文书的用户目标"
        if no_delivery_mode_error is not None:
            _add(
                violations,
                "invalid_no_delivery_mode",
                no_delivery_mode_error,
            contract_path or transcript_jsonl or case_file,
            )
    elif trace_diagnostic["acknowledgement_after_controlled_failure"]:
        _add(
            violations,
            "failure_acknowledgement_requires_user_round",
            "失败围栏解除命令缺少 transcript 用户轮次凭据；模型不得自行 acknowledge",
            Path(os.path.abspath(audit_log)) if audit_log is not None else root,
        )

    skill_lifecycle = _skill_lifecycle(
        client_log=client_log,
        transcript_diagnostic=transcript_diagnostic,
        violations=violations,
    )
    root_causes: list[dict[str, Any]] = []
    bypass_related_codes = {
        "prohibited_document_command",
        "prohibited_generation_script",
        "prohibited_present_files",
        "prohibited_runtime_dependency_install",
        "prohibited_runtime_environment_creation",
        "prohibited_temporary_script",
        "runtime_skill_mutation",
        "split_managed_presentation",
        "extra_unmanaged_presentation",
        "incomplete_managed_presentation",
        "amount_calculation_bypass",
        "cross_workspace_case_access",
        "destructive_case_workspace_command",
        "unmanaged_docx",
        "loaded_then_contract_discovery_bypass",
        "prohibited_case_archive_transport",
        "duplicate_managed_presentation",
        "render_evidence_missing",
    }
    if transcript_diagnostic is not None:
        related_codes = sorted(
            set(transcript_diagnostic.get("direct_bypass_codes", ()))
            & bypass_related_codes
        )
        if not transcript_diagnostic.get("skill_invocation_observed"):
            if related_codes:
                _add(
                    violations,
                    "skill_not_in_execution_chain",
                    "未观察到真实 Skill(arbibuddy) 调用；劳动争议文书、脚本或 present_files 旁路统一归因于 Skill 未进入执行链",
                    transcript_jsonl or root,
                )
                root_causes.append({
                    "code": "skill_not_in_execution_chain",
                    "message": "Skill 未进入执行链；后续旁路不能解释为 Skill 内状态机已执行。",
                    "related_violations": related_codes,
                    "next_action": "重载、重启或重新导入 WorkBuddy Skill 后重新验证真实调用链。",
                })
        elif related_codes:
            if "skill_invocation_result_missing" in {
                item["code"] for item in violations
            }:
                related_codes = sorted(
                    set(related_codes) | {"skill_invocation_result_missing"}
                )
            _add(
                violations,
                "skill_invoked_but_bypassed",
                "真实 Skill 已调用，但随后出现直接可观察的工具或产物旁路",
                transcript_jsonl or root,
            )
            root_causes.append({
                "code": "skill_invoked_but_bypassed",
                "message": "Skill 已调用但后续出现直接可观察的工具或产物旁路。",
                "related_violations": related_codes,
                "next_action": "停止当前回合；只通过公开 Skill 工具按同一用户决定受控恢复。",
            })

    return _report(
        root,
        case_id,
        expected,
        violations,
        presentation_files=tuple(presentation_files),
        managed_view=managed_view,
        memory_policy=memory_policy,
        transcript_diagnostic=transcript_diagnostic,
        skill_lifecycle=skill_lifecycle,
        root_causes=tuple(root_causes),
        transcript_path=(
            Path(os.path.abspath(transcript_jsonl))
            if transcript_jsonl is not None
            else None
        ),
    )


def _audit_command_trace(
    audit_log: Path,
    session_id: str | None,
    violations: list[dict[str, str]],
    *,
    expected_document_count: int = 0,
) -> dict[str, bool]:
    diagnostic = {
        "acknowledgement_after_controlled_failure": False,
        "prohibited_present_files": False,
    }
    events: list[tuple[int, str, int | None, str]] = []
    try:
        for line in audit_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if session_id is not None and value.get("sessionId") != session_id:
                continue
            command = value.get("commandPreview")
            timestamp = value.get("timestamp")
            returncode = value.get("returncode")
            if isinstance(command, str) and isinstance(timestamp, int):
                response = value.get("response")
                events.append((
                    timestamp,
                    command.casefold(),
                    returncode if isinstance(returncode, int) else None,
                    json.dumps(response, ensure_ascii=False, sort_keys=True).casefold()
                    if response is not None
                    else "",
                ))
    except (OSError, json.JSONDecodeError):
        _add(
            violations,
            "invalid_audit_trace",
            "WorkBuddy 命令审计轨迹无法读取",
            audit_log,
        )
        return diagnostic
    if not events:
        _add(
            violations,
            "audit_trace_session_missing",
            "WorkBuddy 命令审计轨迹中没有目标会话",
            audit_log,
        )
        return diagnostic

    document_events: list[int] = []
    supplement_events: list[int] = []
    confirmation_events: list[int] = []
    controlled_document_commands: list[str] = []
    controlled_failures = [
        timestamp for timestamp, command, returncode, _response in events
        if "scripts.core_workflow.cli" in command
        and returncode is not None
        and returncode != 0
    ]
    acknowledgement_after_failure = bool(controlled_failures) and any(
        timestamp > min(controlled_failures)
        and any(
            marker in command
            for marker in (
                "acknowledge-preflight-failure",
                "acknowledge-failure",
            )
        )
        for timestamp, command, _returncode, _response in events
    )
    diagnostic["acknowledgement_after_controlled_failure"] = (
        acknowledgement_after_failure
    )
    if controlled_failures and any(
        timestamp > min(controlled_failures)
        for timestamp, _command, _returncode, _response in events
    ):
        _add(
            violations,
            "continued_after_controlled_failure",
            "统一入口失败后客户端仍继续执行当前交付流程",
            audit_log,
        )
    safe_delete_failures = [
        timestamp
        for timestamp, command, returncode, response in events
        if returncode is not None
        and returncode != 0
        and any(
            marker in command or marker in response
            for marker in (
                "safe_delete_fail_closed",
                "safe_delete_failure",
                "safe-delete-fail-closed",
                "safedeletefailclosed",
                "runtime_temp_unavailable",
                "runtimetemperror",
            )
        )
    ]
    if safe_delete_failures:
        first_safe_delete_failure = min(safe_delete_failures)
        later_events = [
            (timestamp, command)
            for timestamp, command, _returncode, _response in events
            if timestamp > first_safe_delete_failure
        ]
        if later_events:
            _add(
                violations,
                "safe_delete_failure_requires_stop",
                "安全删除失败后必须保持失败关闭，不得继续当前交付流程",
                audit_log,
            )
            if any(
                any(
                    marker in command
                    for marker in (
                        ".docx",
                        ".markdown",
                        ".md",
                        ".html",
                        ".pdf",
                        "present_files",
                        "from docx",
                        "import docx",
                    )
                )
                for _timestamp, command in later_events
            ):
                _add(
                    violations,
                    "bypass_after_controlled_failure",
                    "安全删除失败后不得创建、展示或声称可直接使用任何旁路文书",
                    audit_log,
                )

    for timestamp, command, _returncode, _response in events:
        prohibited = any(name in command for name in PROHIBITED_GENERATION_SCRIPTS)
        inline_docx = "import docx" in command or "from docx import" in command
        controlled_document = (
            "scripts.core_workflow.cli" in command
            and (" run-package " in command or " run-document " in command)
        )
        bottom_level_entry = any(
            module in command
            for module in (
                "scripts.documents.cli",
                "scripts.case_pipeline.cli",
                "scripts.high_risk_action.cli",
            )
        )
        if "scripts.case_archive.cli" in command:
            _add(
                violations,
                "prohibited_case_archive_transport",
                "非结构化审计日志不能证明 runtime context 的精确 CaseArchive transport",
                audit_log,
            )
        if bottom_level_entry:
            _add(
                violations,
                "prohibited_bottom_level_entry",
                "WorkBuddy 必须只通过统一核心工作流入口推进",
                audit_log,
            )
        if _command_masks_business_exit_status(command):
            _add(
                violations,
                "masked_exit_status",
                "命令不得附加会掩盖或绕过真实非零退出状态的片段",
                audit_log,
            )
        if prohibited or inline_docx:
            _add(
                violations,
                "prohibited_document_command",
                "命令轨迹包含旁路 DOCX 生成或探测命令",
                audit_log,
            )
        if "present_files" in command:
            diagnostic["prohibited_present_files"] = True
            _add(
                violations,
                "prohibited_present_files",
                "客户端不得绕过核心状态信封调用 present_files 展示文书",
                audit_log,
            )
        if re.search(r"\b(?:pip|pip\d*)\b[^\r\n]*\binstall\b", command):
            _add(
                violations,
                "prohibited_runtime_dependency_install",
                "文书阶段不得临时安装 python-docx",
                audit_log,
            )
        if re.search(r"\bpython(?:\.exe)?\s+-m\s+venv\b", command):
            _add(
                violations,
                "prohibited_runtime_environment_creation",
                "文书阶段不得临时创建虚拟环境",
                audit_log,
            )
        if re.search(
            r"\bpython(?:\.exe)?\s+(?!-m\b)[^\r\n]*\.py\b|\bpython(?:\.exe)?\s+-\s*<",
            command,
        ):
            _add(
                violations,
                "prohibited_temporary_script",
                "命令轨迹包含临时 Python 脚本或内联脚本执行",
                audit_log,
            )
        if re.search(
            r"(?:\brg\b|\bget-content\b|\bselect-string\b|\bsed\b|\btype\b)"
            r"[^\r\n]*(?:scripts[/\\]|references[/\\]|skill\.md)",
            command,
        ):
            _add(
                violations,
                "prohibited_runtime_source_inspection",
                "运行期不得检查技能源码来猜测公共命令用法",
                audit_log,
            )
        if re.search(
            r"(?:\bremove-item\b|\bshutil\.rmtree\b|\.unlink\s*\(|"
            r"\brm\s+-[a-z]*r|\brmdir\s+/s\b|\bdel\s+/s\b)",
            command,
        ):
            _add(
                violations,
                "prohibited_manual_cleanup",
                "命令轨迹包含人工删除或递归清理；文书重试必须使用统一 CLI 恢复",
                audit_log,
            )
        document_command = controlled_document or (
            "scripts.documents.cli" in command and " generate " in command
        )
        if prohibited or document_command:
            document_events.append(timestamp)
        if document_command:
            controlled_document_commands.append(command)
        if "record-supplement-answer" in command:
            supplement_events.append(timestamp)
        if "confirm-snapshot" in command or (
            "scripts.core_workflow.cli" in command and " confirm " in command
        ):
            confirmation_events.append(timestamp)

    if document_events and (
        not supplement_events or min(document_events) < min(supplement_events)
    ):
        _add(
            violations,
            "document_before_final_supplement",
            "文书命令早于最终补充答复持久化命令",
            audit_log,
        )
    first_document = min(document_events) if document_events else None
    valid_confirmations = (
        [
            timestamp
            for timestamp in confirmation_events
            if supplement_events
            and min(supplement_events) < timestamp < first_document
        ]
        if first_document is not None
        else []
    )
    if first_document is not None and not valid_confirmations:
        _add(
            violations,
            "document_before_confirmation",
            "文书命令前没有位于最终补充之后的关键事实确认命令",
            audit_log,
        )
    if expected_document_count and len(controlled_document_commands) > expected_document_count:
        _add(
            violations,
            "document_generation_retry_loop",
            "文书生成命令次数超过权威交付计划，存在重复生成或重试风暴",
            audit_log,
        )
    if len(events) > 60:
        _add(
            violations,
            "excessive_command_count",
            "单次交付流程命令数超过 60 次预算",
            audit_log,
        )
    return diagnostic


_BUSINESS_COMMAND_MARKERS = (
    "scripts.core_workflow.cli",
    "scripts.case_archive.cli",
    "scripts.documents.public",
    "scripts.documents.cli",
    "scripts.amount_calculator.public",
    "scripts.amount_calculator.cli",
    "scripts.case_pipeline.cli",
    "scripts.high_risk_action.cli",
)


def _command_masks_business_exit_status(command: str) -> bool:
    folded = command.casefold()
    if not any(marker in folded for marker in _BUSINESS_COMMAND_MARKERS):
        return False
    if re.search(r"\|\|\s*(?:true|:|exit\s+0)\b", folded):
        return True
    if re.search(r";\s*(?:true|:|exit\s+0)\b", folded):
        return True
    if re.search(
        r"2>&1\s*\|\s*(?:head|tail|grep(?:\s+-v)?|select-string|select-object)\b",
        folded,
    ):
        return True
    segments = re.split(r"\|", folded)
    if len(segments) > 1 and any(
        any(marker in segment for marker in _BUSINESS_COMMAND_MARKERS)
        for segment in segments[:-1]
    ) and re.search(
        r"\b(?:head|tail|grep|select-string|select-object)\b", segments[-1]
    ):
        return True
    semicolon_segments = [segment.strip() for segment in folded.split(";")]
    return any(
        any(marker in segment for marker in _BUSINESS_COMMAND_MARKERS)
        for segment in semicolon_segments[:-1]
    )


def _case_archive_module_present(record: Mapping[str, Any]) -> bool:
    serialized = _transcript_serialized_text(dict(record)).replace("\\", "/")
    return re.search(
        r"scripts[./]case_archive[./](?:cli|runtime_cli)(?:\.py)?\b",
        serialized,
        flags=re.IGNORECASE,
    ) is not None


def _managed_runtime_input_transcript_path(
    value: str,
    *,
    workspace: Path,
) -> Path | None:
    normalized = normalize_platform_path(value.strip().strip('"\''))
    if any(part == ".." for part in normalized.parts):
        return None
    candidate = _resolve_transcript_path(str(normalized), workspace)
    runtime_root = (workspace / ".arbibuddy" / "runtime-input").resolve()
    if candidate is None or candidate.parent != runtime_root:
        return None
    if candidate.suffix.casefold() != ".json":
        return None
    try:
        if candidate.is_symlink() or not candidate.is_relative_to(workspace.resolve()):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _case_archive_runtime_transport_allowed(
    record: Mapping[str, Any],
    *,
    workspace: Path,
) -> bool:
    """验证 runtime_context 生成的精确公开 CaseArchive transport。"""

    arguments = _transcript_argument_object(dict(record))
    argv = arguments.get("argv")
    raw_command = next(
        (
            value
            for key in ("command", "cmd", "script")
            if isinstance((value := arguments.get(key)), str) and value.strip()
        ),
        "",
    )
    skill_root_from_wrapper: Path | None = None
    if not isinstance(argv, list):
        if not raw_command:
            return False
        wrapper = re.fullmatch(
            r"\s*cd\s+([\"'])(?P<cwd>.+?)\1\s*&&\s*(?P<command>.+?)\s*",
            raw_command,
            flags=re.IGNORECASE | re.DOTALL,
        )
        command = raw_command
        if wrapper is not None:
            skill_root_from_wrapper = _resolve_transcript_path(
                wrapper.group("cwd"), workspace
            )
            command = wrapper.group("command")
        if re.search(r"[|;&<>]", command) or "pythonpath" in command.casefold():
            return False
        try:
            argv = shlex.split(command, posix=False)
        except ValueError:
            return False
        argv = [str(item).strip('"\'') for item in argv]
    if not argv or not all(isinstance(item, str) and item for item in argv):
        return False
    if len(argv) < 9:
        return False
    python_name = Path(argv[0].replace("\\", "/")).name.casefold()
    if python_name not in {"python", "python.exe", "py", "py.exe"}:
        return False
    if tuple(argv[1:7]) != ("-B", "-X", "utf8", "-m", "scripts.case_archive.cli", "--root"):
        return False
    root_value = argv[7]
    root_candidate = _resolve_transcript_path(root_value, workspace)
    if root_candidate is None or root_candidate != workspace.resolve():
        return False
    operation = argv[8]
    if operation not in {"create", "read", "read-current", "commit"}:
        return False
    remainder = argv[9:]
    if operation == "read":
        if len(remainder) != 1 or not re.fullmatch(r"case-[0-9a-f]{24}", remainder[0]):
            return False
    elif operation == "read-current":
        if remainder:
            return False
    elif operation == "create":
        allowed = {"--case-label", "--jurisdiction", "--initial-goal"}
        index = 0
        while index < len(remainder):
            if remainder[index] not in allowed or index + 1 >= len(remainder):
                return False
            index += 2
    else:
        if len(remainder) != 2 or remainder[0] not in {"--json", "--input"}:
            return False
        if remainder[0] == "--input" and _managed_runtime_input_transcript_path(
            remainder[1], workspace=workspace
        ) is None:
            return False
    cwd_value = arguments.get("cwd")
    skill_root = (
        _resolve_transcript_path(cwd_value, workspace)
        if isinstance(cwd_value, str) and cwd_value.strip()
        else skill_root_from_wrapper
    )
    if skill_root is None or skill_root == workspace.resolve():
        return False
    if _is_relative_to(skill_root, workspace.resolve()) or not (
        skill_root / "scripts" / "case_archive" / "cli.py"
    ).is_file():
        return False
    environment = arguments.get("env") or arguments.get("environment")
    if isinstance(environment, Mapping) and any(
        str(key).casefold() == "pythonpath" for key in environment
    ):
        return False
    return "pythonpath" not in " ".join(argv).casefold()


_TRANSCRIPT_EVENT_KEYS = ("type", "event", "kind")
_TRANSCRIPT_SESSION_KEYS = ("sessionId", "session_id", "session")
_TRANSCRIPT_CALL_KEYS = (
    "tool",
    "tool_name",
    "toolName",
    "function",
    "function_name",
    "functionName",
    "call",
    "call_name",
    "callName",
    "name",
    "skill",
)
_TRANSCRIPT_CALL_EVENTS = frozenset({
    "tool_call",
    "tool_use",
    "function_call",
    "function_use",
    "skill_call",
    "call_tool",
})
_TRANSCRIPT_RESULT_EVENTS = frozenset({
    "function_call_result",
    "function_result",
    "tool_result",
    "tool_response",
    "function_call_output",
})
_TRANSCRIPT_USER_ROUND_EVENTS = frozenset({
    "user_turn",
    "user_message",
    "user_round",
    "turn_boundary",
})
_TRANSCRIPT_TURN_END_EVENTS = frozenset({
    "turn_end",
    "end_turn",
    "user_turn_end",
    "round_end",
    "turn_completed",
    "response_completed",
    "run_completed",
    "message_stop",
    "assistant_turn_end",
})
_TRANSCRIPT_CALL_ID_KEYS = (
    "callId",
    "call_id",
    "tool_call_id",
    "toolCallId",
    "function_call_id",
    "functionCallId",
    "id",
)
_TRANSCRIPT_RESULT_KEYS = (
    "result",
    "output",
    "response",
    "data",
    "structuredContent",
    "stdout",
    "content",
)


def _transcript_text(value: object) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _transcript_event(record: dict[str, Any]) -> str:
    for key in _TRANSCRIPT_EVENT_KEYS:
        value = _transcript_text(record.get(key))
        if value:
            return value.replace("-", "_").replace(" ", "_")
    return ""


def _transcript_json_value(value: object) -> object | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _transcript_argument_object(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("arguments", "input", "parameters", "params"):
        value = _transcript_json_value(record.get(key))
        if isinstance(value, dict):
            return value
    return {}


def _transcript_path_values(value: object, key_name: str | None = None) -> tuple[str, ...]:
    """Extract path-bearing fields without treating free-form tool text as a path."""
    if isinstance(value, str):
        if key_name is None:
            return ()
        folded_key = key_name.casefold()
        if (
            folded_key in _TRANSCRIPT_PATH_ARGUMENT_KEYS
            or folded_key.endswith("_path")
            or folded_key.endswith("_dir")
        ):
            return (value,)
        return ()
    if isinstance(value, Mapping):
        values: list[str] = []
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            if key.casefold() in _TRANSCRIPT_NON_PATH_ARGUMENT_KEYS:
                continue
            values.extend(_transcript_path_values(child, key))
        return tuple(values)
    if isinstance(value, list):
        values: list[str] = []
        for child in value:
            values.extend(_transcript_path_values(child, key_name))
        return tuple(values)
    return ()


def _transcript_path_texts(record: dict[str, Any]) -> tuple[str, ...]:
    values = list(_transcript_command_texts(record))
    values.extend(_transcript_path_values(_transcript_argument_object(record)))
    return tuple(dict.fromkeys(value for value in values if value.strip()))


def _resolve_transcript_path(value: str, workspace: Path) -> Path | None:
    try:
        normalized = normalize_platform_path(value.strip().strip('"\''))
        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        return candidate.resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _authorized_presentation_paths(
    paths: tuple[str, ...] | list[str],
    workspace: Path,
) -> tuple[Path, ...]:
    resolved = [
        candidate
        for value in paths
        if (candidate := _resolve_transcript_path(str(value), workspace)) is not None
    ]
    return tuple(dict.fromkeys(resolved))


def _transcript_presented_paths(
    record: dict[str, Any],
    workspace: Path,
) -> tuple[Path, ...]:
    arguments = _transcript_argument_object(record)
    values = _transcript_presented_values(record)
    if not values:
        return ()
    cwd = arguments.get("cwd")
    base = workspace
    if isinstance(cwd, str):
        resolved_cwd = _resolve_transcript_path(cwd, workspace)
        if resolved_cwd is not None:
            base = resolved_cwd
    return tuple(
        candidate
        for value in values
        if (candidate := _resolve_transcript_path(value, base)) is not None
    )


def _transcript_presented_values(record: dict[str, Any]) -> tuple[str, ...]:
    arguments = _transcript_argument_object(record)
    raw_values: object = arguments.get("files") or arguments.get("paths")
    if not isinstance(raw_values, list) or not all(
        isinstance(item, str) for item in raw_values
    ):
        return ()
    return tuple(raw_values)


def _transcript_mutates_installed_skill(record: dict[str, Any]) -> bool:
    if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
        return False
    if not _transcript_call_names(record) & {
        "edit",
        "edit_file",
        "multiedit",
        "save",
        "write",
        "write_file",
    }:
        return False
    for value in _transcript_path_values(_transcript_argument_object(record)):
        folded = value.replace("\\\\", "/").replace("\\", "/").casefold()
        if re.search(r"(?:^|/)(?:\.workbuddy/)?skills/arbibuddy(?:/|$)", folded):
            return True
    return False


def _present_files_matches_authorized(
    record: dict[str, Any],
    workspace: Path,
    authorized_paths: tuple[Path, ...],
    authorized_values: tuple[str, ...] = (),
) -> bool:
    if not authorized_paths:
        return False
    if authorized_values and _transcript_presented_values(record) != authorized_values:
        return False
    presented = _transcript_presented_paths(record, workspace)
    return bool(presented) and presented == authorized_paths


def _managed_presentation_diagnostic(
    events: list[tuple[int, tuple[str, ...]]],
    *,
    authorized_values: tuple[str, ...],
) -> dict[str, Any]:
    """Classify structured present_files calls without echoing file paths."""

    if not events or not authorized_values:
        return {"codes": [], "events": []}
    codes: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for index, values in events:
        exact = values == authorized_values
        has_extra = any(value not in authorized_values for value in values)
        is_subset = bool(values) and all(value in authorized_values for value in values)
        if has_extra:
            codes.add("extra_unmanaged_presentation")
            reason = "extra_unmanaged"
        elif not exact and is_subset:
            codes.add("incomplete_managed_presentation")
            reason = "incomplete_managed"
        elif not exact:
            codes.add("incomplete_managed_presentation")
            reason = "rewritten_or_reordered"
        else:
            reason = "complete"
        summaries.append(
            {
                "index": index,
                "file_count": len(values),
                "authorized_count": len(authorized_values),
                "exact": exact,
                "reason": reason,
            }
        )
    flattened = tuple(value for _index, values in events for value in values)
    if (
        len(events) > 1
        and not any(values == authorized_values for _index, values in events)
        and all(
            bool(values) and all(value in authorized_values for value in values)
            for _index, values in events
        )
        and flattened == authorized_values
    ):
        codes.add("split_managed_presentation")
    return {"codes": sorted(codes), "events": summaries}


def _transcript_mapping_objects(value: object):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _transcript_mapping_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _transcript_mapping_objects(child)


def _transcript_payload_objects(value: object):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _transcript_payload_objects(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _transcript_payload_objects(child)
        return
    if not isinstance(value, str) or not value.strip():
        return
    parsed = _transcript_json_value(value)
    if parsed is not None:
        yield from _transcript_payload_objects(parsed)
        return
    decoder = json.JSONDecoder()
    for offset, character in enumerate(value):
        if character not in "[{":
            continue
        try:
            embedded, end = decoder.raw_decode(value, offset)
        except (TypeError, json.JSONDecodeError):
            continue
        if end > offset:
            yield from _transcript_payload_objects(embedded)


def _transcript_result_mappings(record: Mapping[str, Any]):
    for key in _TRANSCRIPT_RESULT_KEYS:
        yield from _transcript_payload_objects(record.get(key))


def _timeline_render_metadata(
    record: Mapping[str, Any],
    *,
    fallback_arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    case_id: str | None = None
    revision: int | None = None
    manifest: str | None = None
    manifest_sha256: str | None = None
    manifest_id: str | None = None
    delivery_expected_types: tuple[str, ...] = ()
    delivery_completed_types: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    file_hashes: tuple[tuple[str, str], ...] = ()
    current_revision: int | None = None
    dependencies_verified = False
    for mapping in _transcript_result_mappings(record):
        current = mapping.get("current_archive_revision")
        if type(current) is int and mapping.get("archive_dependencies_verified") is True:
            current_revision = current
            dependencies_verified = True
        candidate_case_id = mapping.get("case_id")
        if isinstance(candidate_case_id, str) and candidate_case_id.strip():
            case_id = candidate_case_id
        for key in ("archive_revision", "revision"):
            value = mapping.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                revision = value
                break
        candidate_manifest = mapping.get("machine_manifest")
        if isinstance(candidate_manifest, str) and candidate_manifest.strip():
            manifest = candidate_manifest
            manifest_id = candidate_manifest
        for key in (
            "machine_manifest_id",
            "manifest_id",
            "render_id",
            "delivery_set_id",
        ):
            candidate_manifest_id = mapping.get(key)
            if isinstance(candidate_manifest_id, str) and candidate_manifest_id.strip():
                manifest_id = candidate_manifest_id
        candidate_manifest_sha = mapping.get("machine_manifest_sha256")
        if isinstance(candidate_manifest_sha, str) and candidate_manifest_sha.strip():
            manifest_sha256 = candidate_manifest_sha
        delivery_set = mapping.get("delivery_set")
        if isinstance(delivery_set, Mapping):
            expected_types = delivery_set.get("expected_document_types")
            completed_types = delivery_set.get("completed_document_types")
            if isinstance(expected_types, list) and all(
                isinstance(item, str) and item for item in expected_types
            ):
                delivery_expected_types = tuple(expected_types)
            if isinstance(completed_types, list) and all(
                isinstance(item, str) and item for item in completed_types
            ):
                delivery_completed_types = tuple(completed_types)
        candidate_files = mapping.get("presentation_files")
        if not isinstance(candidate_files, list):
            candidate_files = mapping.get("artifact_files")
        if isinstance(candidate_files, list):
            entries = tuple(
                (item.get("path"), item.get("sha256"))
                for item in candidate_files
                if isinstance(item, Mapping)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("sha256"), str)
            )
            if entries and len(entries) == len(candidate_files):
                paths = tuple(item[0] for item in entries)
                file_hashes = entries
    if revision is None and fallback_arguments is not None:
        value = fallback_arguments.get("archive_revision")
        if isinstance(value, int) and not isinstance(value, bool):
            revision = value
    if case_id is None and fallback_arguments is not None:
        value = fallback_arguments.get("case_id")
        if isinstance(value, str) and value.strip():
            case_id = value
    return {
        "case_id": case_id,
        "revision": revision,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "manifest_id": manifest_id,
        "delivery_expected_types": delivery_expected_types,
        "delivery_completed_types": delivery_completed_types,
        "paths": paths,
        "file_hashes": file_hashes,
        "current_revision": current_revision,
        "dependencies_verified": dependencies_verified,
    }


def _audit_revision_timeline(
    records: list[tuple[int, dict[str, Any]]],
    *,
    workspace: Path | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    snapshot_calls: dict[str, tuple[int, Mapping[str, Any], str]] = {}
    render_snapshots: list[dict[str, Any]] = []
    managed_view_snapshots: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    presentations: list[dict[str, Any]] = []
    for index, record in records:
        event = _transcript_event(record)
        call_id = _transcript_call_id(record)
        render_signal = _transcript_render_signal(record)
        managed_view_signal = _transcript_managed_view_signal(record)
        if event in _TRANSCRIPT_CALL_EVENTS and (render_signal or managed_view_signal):
            snapshot_calls[call_id or f"snapshot@{index}"] = (
                index,
                _transcript_argument_object(record),
                "managed_view" if managed_view_signal else "render",
            )
        fallback = snapshot_calls.get(call_id or "")
        if event in _TRANSCRIPT_RESULT_EVENTS and (
            render_signal or managed_view_signal or fallback is not None
        ) and _transcript_result_is_success(record):
            metadata = _timeline_render_metadata(
                record,
                fallback_arguments=fallback[1] if fallback else None,
            )
            if metadata["paths"]:
                raw_snapshot_paths = metadata["paths"]
                snapshot_paths = raw_snapshot_paths
                snapshot_file_hashes = metadata["file_hashes"]
                snapshot_manifest = metadata["manifest"]
                path_error = None
                if workspace is not None:
                    try:
                        if isinstance(snapshot_manifest, str):
                            snapshot_manifest = _timeline_relative(
                                snapshot_manifest, workspace=workspace
                            )
                        snapshot_paths = tuple(
                            _timeline_relative(value, workspace=workspace)
                            for value in snapshot_paths
                        )
                        snapshot_file_hashes = tuple(
                            (
                                _timeline_relative(value, workspace=workspace),
                                digest,
                            )
                            for value, digest in snapshot_file_hashes
                        )
                    except (OSError, ValueError):
                        path_error = "render_path_invalid"
                snapshot = {
                    "index": index,
                    "snapshot_kind": fallback[2] if fallback else (
                        "managed_view" if managed_view_signal else "render"
                    ),
                    "call_id": call_id,
                    "result_id": record.get("id") or record.get("result_id"),
                    "case_id": metadata["case_id"],
                    "revision": metadata["revision"],
                    "manifest": snapshot_manifest,
                    "manifest_sha256": metadata["manifest_sha256"],
                    "manifest_id": metadata["manifest_id"],
                    "delivery_expected_types": metadata["delivery_expected_types"],
                    "delivery_completed_types": metadata["delivery_completed_types"],
                    "raw_paths": raw_snapshot_paths,
                    "paths": snapshot_paths,
                    "file_hashes": snapshot_file_hashes,
                    "current_revision": metadata["current_revision"],
                    "dependencies_verified": metadata["dependencies_verified"],
                    "path_error": path_error,
                }
                if snapshot["snapshot_kind"] == "managed_view":
                    managed_view_snapshots.append(snapshot)
                else:
                    render_snapshots.append(snapshot)
        if _transcript_case_archive_operation(record) == "commit":
            metadata = _timeline_render_metadata(
                record,
                fallback_arguments=_transcript_argument_object(record),
            )
            commits.append(
                {
                    "index": index,
                    "case_id": metadata["case_id"],
                    "revision": metadata["revision"],
                }
            )
        if _transcript_present_files_call(record):
            values = _transcript_presented_values(record)
            canonical_values = values
            path_error = None
            if workspace is not None:
                try:
                    canonical_values = tuple(
                        _timeline_relative(value, workspace=workspace)
                        for value in values
                    )
                except (OSError, ValueError):
                    path_error = "presentation_path_invalid"
            presentations.append(
                {
                    "index": index,
                    "raw_paths": values,
                    "paths": canonical_values,
                    "path_error": path_error,
                }
            )

    historical: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    current_artifact_diagnostics: list[dict[str, Any]] = []

    def snapshot_matches_case(snapshot: Mapping[str, Any]) -> bool:
        if snapshot.get("case_id") == case_id:
            return True
        if snapshot.get("case_id") is not None or workspace is None:
            return False
        case_root = workspace / ".arbibuddy" / "cases" / str(case_id)
        try:
            case_root = case_root.resolve()
            return all(
                _timeline_candidate(path, workspace=workspace).is_relative_to(case_root)
                for path in snapshot.get("paths", ())
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def snapshot_structure_valid(
        snapshot: Mapping[str, Any], *, require_render_receipt: bool
    ) -> tuple[bool, str | None]:
        paths = snapshot.get("paths", ())
        file_hashes = snapshot.get("file_hashes", ())
        if (
            len(file_hashes) != len(paths)
            or snapshot.get("path_error")
            or any(not _timeline_sha256(digest) for _, digest in file_hashes)
            or (
                require_render_receipt
                and (
                    not snapshot.get("manifest_id")
                    or not snapshot.get("manifest")
                    or not _timeline_sha256(snapshot.get("manifest_sha256"))
                )
            )
        ):
            return False, "render_receipt_missing_manifest_or_file_sha256"
        return True, None

    def render_candidates_for_view(view: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for snapshot in render_snapshots:
            if snapshot["index"] > view["index"]:
                continue
            if not snapshot_matches_case(snapshot):
                continue
            if (
                view.get("revision") is not None
                and snapshot.get("revision") != view.get("revision")
            ):
                continue
            valid, _reason = snapshot_structure_valid(
                snapshot, require_render_receipt=True
            )
            if valid:
                result.append(snapshot)
        return result

    def verify_current_hashes(snapshot: Mapping[str, Any]) -> None:
        if workspace is None:
            return

        def replaced_by_later_render(path: str, digest: str) -> bool:
            return any(
                later["index"] > snapshot["index"]
                and snapshot_matches_case(later)
                and snapshot_structure_valid(
                    later, require_render_receipt=True
                )[0]
                and (
                    (
                        later.get("manifest") == path
                        and later.get("manifest_sha256") == digest
                    )
                    or (path, digest) in later["file_hashes"]
                )
                for later in render_snapshots
            )

        try:
            if snapshot.get("snapshot_kind") != "managed_view":
                manifest = _timeline_path(snapshot["manifest"], workspace=workspace)
                current_digest = _digest(manifest)
                if (
                    current_digest != snapshot["manifest_sha256"]
                    and not replaced_by_later_render(
                        snapshot["manifest"], current_digest
                    )
                ):
                    invalid.append(
                        {
                            "index": snapshot["index"],
                            "render_index": snapshot["index"],
                            "revision": snapshot["revision"],
                            "reason": "render_manifest_sha256_mismatch",
                        }
                    )
                    return
            for file_path, expected_sha256 in snapshot["file_hashes"]:
                path = _timeline_path(file_path, workspace=workspace)
                current_digest = _digest(path)
                if (
                    current_digest != expected_sha256
                    and not replaced_by_later_render(file_path, current_digest)
                ):
                    invalid.append(
                        {
                            "index": snapshot["index"],
                            "render_index": snapshot["index"],
                            "revision": snapshot["revision"],
                            "reason": "render_file_sha256_mismatch",
                        }
                    )
                    return
        except (OSError, ValueError):
            current_artifact_diagnostics.append(
                {
                    "index": snapshot["index"],
                    "revision": snapshot["revision"],
                    "reason": "current_artifact_unreadable",
                }
            )

    for presentation in presentations:
        presentation_index = presentation["index"]
        presented = tuple(presentation["paths"])
        candidates = [
            snapshot
            for snapshot in managed_view_snapshots
            if snapshot["index"] <= presentation_index
            and tuple(snapshot["paths"]) == presented
            and snapshot_matches_case(snapshot)
        ]
        if not candidates:
            invalid.append(
                {
                    "index": presentation_index,
                    "reason": presentation.get("path_error")
                    or "managed_view_snapshot_not_found",
                }
            )
            continue
        snapshot = candidates[-1]
        snapshot_valid, snapshot_reason = snapshot_structure_valid(
            snapshot, require_render_receipt=False
        )
        if not snapshot_valid:
            invalid.append(
                {
                    "index": presentation_index,
                    "view_index": snapshot["index"],
                    "revision": snapshot["revision"],
                    "reason": snapshot_reason,
                }
            )
            continue
        render_candidates = render_candidates_for_view(snapshot)
        rendered_hashes: dict[str, tuple[str, int]] = {}
        for render in render_candidates:
            for path, digest in render["file_hashes"]:
                rendered_hashes[path] = (digest, render["index"])
        missing_render_receipt = any(
            rendered_hashes.get(path, (None, None))[0] != digest
            for path, digest in snapshot["file_hashes"]
        )
        if missing_render_receipt:
            invalid.append(
                {
                    "index": presentation_index,
                    "view_index": snapshot["index"],
                    "revision": snapshot["revision"],
                    "reason": "managed_view_without_prior_render_receipt",
                }
            )
            continue
        commits_after_render = [
            commit
            for commit in commits
            if commit["case_id"] == case_id
            and commit["index"] < presentation_index
            and any(
                commit["index"] > rendered_hashes[path][1]
                for path, _digest in snapshot["file_hashes"]
            )
        ]
        if commits_after_render:
            uncovered_commits = [commit for commit in commits_after_render if (
                not snapshot["dependencies_verified"]
                or snapshot["current_revision"] is None
                or commit["index"] > snapshot["index"]
                or (commit["revision"] is not None
                    and commit["revision"] > snapshot["current_revision"])
            )]
        else:
            uncovered_commits = []
        if uncovered_commits:
            invalid.append(
                {
                    "index": presentation_index,
                    "view_index": snapshot["index"],
                    "revision": snapshot["revision"],
                    "reason": "render_commit_present_same_turn",
                }
            )
            continue
        for render in render_candidates:
            verify_current_hashes(render)
        verify_current_hashes(snapshot)
        historical.append(
            {
                "index": presentation_index,
                "view_index": snapshot["index"],
                "render_indices": sorted(
                    {rendered_hashes[path][1] for path, _digest in snapshot["file_hashes"]}
                ),
                "call_id": snapshot["call_id"],
                "result_id": snapshot["result_id"],
                "case_id": snapshot["case_id"],
                "revision": snapshot["revision"],
                "manifest_id": snapshot["manifest_id"],
                "manifest": snapshot["manifest"],
                "manifest_sha256": snapshot["manifest_sha256"],
                "delivery_expected_types": list(
                    snapshot.get("delivery_expected_types", ())
                ),
                "delivery_completed_types": list(
                    snapshot.get("delivery_completed_types", ())
                ),
                "paths": list(snapshot["paths"]),
                "authorized_values": list(snapshot["raw_paths"]),
                "file_hashes": [
                    {"path": path, "sha256": digest}
                    for path, digest in snapshot["file_hashes"]
                ],
                "valid_at_event": True,
            }
        )
    return {
        "render_count": len(render_snapshots),
        "managed_view_count": len(managed_view_snapshots),
        "commit_count": len(commits),
        "present_count": len(presentations),
        "historical_presentations": historical,
        "invalid_events": invalid,
        "current_artifact_diagnostics": current_artifact_diagnostics,
        "valid": not invalid,
    }


def _timeline_path(value: str, *, workspace: Path) -> Path:
    resolved = _timeline_candidate(value, workspace=workspace)
    if not resolved.is_file():
        raise OSError("timeline file missing")
    return resolved


def _timeline_candidate(value: str, *, workspace: Path) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = Path(str(candidate).replace("\\\\?\\", "")).resolve()
    comparable_workspace = Path(
        str(workspace.resolve()).replace("\\\\?\\", "")
    ).resolve()
    if not resolved.is_relative_to(comparable_workspace):
        raise ValueError("timeline path escapes workspace")
    return resolved


def _timeline_relative(value: str, *, workspace: Path) -> str:
    path = _timeline_candidate(value, workspace=workspace)
    comparable_workspace = Path(
        str(workspace.resolve()).replace("\\\\?\\", "")
    ).resolve()
    return path.relative_to(comparable_workspace).as_posix()


def _timeline_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _transcript_message_texts(record: dict[str, Any]) -> tuple[str, ...]:
    def collect(value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Mapping):
            values: list[str] = []
            if isinstance(value.get("text"), str):
                values.append(value["text"])
            for key in ("content", "parts", "message"):
                values.extend(collect(value.get(key)))
            return tuple(values)
        if isinstance(value, list):
            values: list[str] = []
            for item in value:
                values.extend(collect(item))
            return tuple(values)
        return ()

    return collect(record.get("content"))


def _transcript_public_texts(record: dict[str, Any]) -> tuple[str, ...]:
    role = _transcript_text(record.get("role"))
    event = _transcript_event(record)
    if role not in {"assistant", "model"} and event not in {
        "assistant_output",
        "assistant_message",
        "model_output",
    }:
        return ()
    return _transcript_message_texts(record)


def _public_text_has_internal_leak(text: str) -> bool:
    return bool(_public_text_internal_leak_categories(text))


def _public_text_internal_leak_categories(text: str) -> tuple[str, ...]:
    """按内部信息类别判断公开文本是否越过用户界面边界。"""

    return tuple(
        category
        for category, patterns in _USER_VISIBLE_INTERNAL_PATTERN_GROUPS.items()
        if any(pattern.search(text) for pattern in patterns)
    )


def _audit_public_transcript_messages(
    transcript_jsonl: Path,
    session_id: str | None,
    violations: list[dict[str, str]],
) -> None:
    """在使用原生 trace 时，补审 transcript 中模型真正展示给用户的文本。"""

    try:
        with transcript_jsonl.open("r", encoding="utf-8") as stream:
            for raw_line in stream:
                if not raw_line.strip():
                    continue
                value = json.loads(raw_line)
                if not isinstance(value, dict):
                    raise ValueError("transcript JSONL 行必须是对象")
                record_session = next(
                    (
                        value.get(key)
                        for key in _TRANSCRIPT_SESSION_KEYS
                        if isinstance(value.get(key), str)
                    ),
                    None,
                )
                if session_id is not None and record_session != session_id:
                    continue
                if _public_text_has_internal_leak(
                    " ".join(_transcript_public_texts(value))
                ):
                    _add(
                        violations,
                        "user_visible_internal_leak",
                        "用户可见回复包含内部记录、路径、协议或实现细节",
                        transcript_jsonl,
                    )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        _add(
            violations,
            "invalid_transcript_diagnostic",
            "WorkBuddy transcript JSONL 只读诊断无法读取",
            transcript_jsonl,
        )


def _timestamp_value(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _native_sessions_overlap(
    records: list[dict[str, Any]],
    session_ids: tuple[str, ...],
) -> bool:
    """确认 ML03 两个真实会话的事件时间线没有交叠。"""

    if len(session_ids) != 2 or len(set(session_ids)) != 2:
        return False
    ranges: list[tuple[float, float]] = []
    for session_id in session_ids:
        timestamps = [
            parsed
            for item in records
            if item.get("sessionId") == session_id
            for parsed in (_timestamp_value(item.get("timestamp")),)
            if parsed is not None
        ]
        if not timestamps:
            return False
        ranges.append((min(timestamps), max(timestamps)))
    return ranges[0][1] > ranges[1][0]


def _transcript_has_explicit_nonzero_exit(record: dict[str, Any]) -> bool:
    for key in ("returncode", "return_code", "exit_code", "exitCode"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value != 0
    return _transcript_text(record.get("status")) in {
        "error",
        "failed",
        "failure",
    }


def _transcript_call_names(record: dict[str, Any]) -> set[str]:
    names = {
        _transcript_text(record.get(key))
        for key in _TRANSCRIPT_CALL_KEYS
        if isinstance(record.get(key), str)
    }
    arguments = _transcript_argument_object(record)
    names.update(
        _transcript_text(arguments.get(key))
        for key in ("skill", "skill_name", "name", "target", "tool")
        if isinstance(arguments.get(key), str)
    )
    return {name for name in names if name}


def _transcript_serialized_text(record: dict[str, Any]) -> str:
    """Return a case-folded structural view used only for evidence matching."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True).casefold()


def _untrusted_fact_markers(text: str) -> set[str]:
    """Extract comparison tokens without returning source facts to the caller."""
    folded = text.casefold()
    markers = {
        marker.casefold()
        for marker in _REGION_MARKERS
        # 全国是通用法律规则层级，不是可用于识别历史案件的地区事实。
        if marker != "全国"
        if marker.casefold() in folded
    }
    markers.update(match.group(0).casefold() for match in _UNTRUSTED_CANARY_PATTERN.finditer(text))
    markers.update(match.group(0).casefold() for match in _UNTRUSTED_AMOUNT_PATTERN.finditer(text))
    markers.update(match.group(0).casefold() for match in _UNTRUSTED_DATE_PATTERN.finditer(text))
    for match in _UNTRUSTED_DURATION_PATTERN.finditer(text):
        context = text[max(0, match.start() - 16) : min(len(text), match.end() + 16)]
        # “终止之日起一年”“时效起算一年”等是规则表达；只有带有
        # 欠薪、入职、加班等案件事实语境的期间才可作为污染标记。
        if any(marker in context for marker in _DURATION_RULE_CONTEXT):
            continue
        if any(marker in context for marker in _DURATION_FACT_CONTEXT):
            markers.add(match.group(0).casefold())
    return markers


def _trusted_case_fact_markers(text: str) -> set[str]:
    """Use only user/evidence-facing archive sections as trusted fact context."""
    sections = re.findall(
        r"^## (?:案件元数据|事实|证据材料)\s*$([\s\S]*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE,
    )
    trusted_text = "\n".join(sections)
    markers = _untrusted_fact_markers(trusted_text)
    # 当前档案常用中文年月，宿主 memory/日志常用 ISO 年月；两者指向
    # 同一当前事实时不应被误判为跨源事实。仅从受信档案派生别名，
    # 不会放宽未知历史事实的金额、地区或期限标记。
    for match in re.finditer(
        r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月",
        trusted_text,
    ):
        year = match.group("year")
        month = int(match.group("month"))
        markers.add(f"{year}-{month:02d}".casefold())
        markers.add(f"{year}-{month}".casefold())
    return markers


def _transcript_untrusted_context_path(record: dict[str, Any]) -> bool:
    return _command_mentions_untrusted_memory(
        " ".join((*_transcript_command_texts(record), *_transcript_path_texts(record)))
    )


def _transcript_authority_region(record: dict[str, Any]) -> set[str]:
    names = _transcript_call_names(record)
    commands = " ".join(_transcript_command_texts(record))
    arguments = _transcript_argument_object(record)
    serialized = " ".join((commands, json.dumps(arguments, ensure_ascii=False)))
    authority_signal = bool(
        names
        & {"authority", "authority.verify", "verify_authority", "legal_authority"}
    ) or any(
        marker in serialized.casefold()
        for marker in (
            "legal_verification",
            "controlled_authority",
            "source_access",
        )
    )
    return {
        marker.casefold()
        for marker in _LOCAL_AUTHORITY_REGION_MARKERS
        if authority_signal and marker.casefold() in serialized.casefold()
    }


def _transcript_activation_evidence(record: dict[str, Any]) -> dict[str, bool]:
    """Recognize Claude activation evidence without treating prose as execution."""
    text = _transcript_serialized_text(record)
    normalized_text = text.replace("\\\\", "/")
    return {
        "slash_command": bool(
            re.search(r"<command-name>\s*/arbibuddy\s*</command-name>", text)
        ),
        "skill_path": ".claude/skills/arbibuddy/skill.md" in normalized_text,
        "skill_body_injected": bool(
            re.search(r"name\s*:\s*arbibuddy", text)
            and "description:" in text
            and any(marker in text for marker in ("劳动争议", "labor dispute"))
        ),
        "structured_skill_call": _transcript_skill_call(record),
    }


def _transcript_case_archive_action(record: dict[str, Any]) -> bool:
    """Identify a public CaseArchive action, not a source-code inspection."""
    names = _transcript_call_names(record)
    commands = _transcript_command_texts(record)
    arguments = _transcript_argument_object(record)
    action_names = {
        "casearchive.create",
        "casearchive.read",
        "casearchive.commit",
        "case_archive.create",
        "case_archive.read",
        "case_archive.commit",
    }
    if names & action_names:
        return True
    operation = _transcript_text(arguments.get("operation"))
    if (
        operation in {"create", "read", "read-current", "commit"}
        and any(name in {"casearchive", "case_archive"} for name in names)
    ):
        return True
    combined = " ".join(
        (*sorted(names), *commands, json.dumps(arguments, ensure_ascii=False))
    )
    return bool(
        re.search(
            r"\bcase[_-]?archive\b[^\r\n]{0,240}\b(?:create|read(?:-current)?|commit)\b",
            combined,
        )
    )


def _transcript_case_archive_operation(record: dict[str, Any]) -> str | None:
    names = _transcript_call_names(record)
    arguments = _transcript_argument_object(record)
    operation = _transcript_text(arguments.get("operation"))
    if operation in {"create", "read", "read-current", "commit"} and any(
        name in {"casearchive", "case_archive"} or name.startswith("casearchive.")
        or name.startswith("case_archive.")
        for name in names
    ):
        return operation
    for name in names:
        if name.startswith("casearchive.") or name.startswith("case_archive."):
            candidate = name.rsplit(".", 1)[-1]
            if candidate in {"create", "read", "read-current", "commit"}:
                return "read" if candidate == "read-current" else candidate
    transport_command = " ".join(_transcript_command_texts(record)).replace(
        "\\", "/"
    )
    transport_match = re.search(
        r"scripts[./]case_archive[./](?:cli|runtime_cli)(?:\.py)?\b"
        r"[^\r\n]*\b(create|read(?:-current)?|commit)\b",
        transport_command,
    )
    if transport_match is not None:
        return "read" if transport_match.group(1) == "read-current" else transport_match.group(1)
    combined = " ".join(
        (*sorted(names), *_transcript_command_texts(record))
    )
    match = re.search(
        r"\bcase[_-]?archive\b[^\r\n]{0,240}\b(create|read(?:-current)?|commit)\b",
        combined,
    )
    if match is None:
        return None
    return "read" if match.group(1) == "read-current" else match.group(1)


def _transcript_render_signal(record: dict[str, Any]) -> bool:
    names = _transcript_call_names(record)
    if names & {
        "document.render-v1",
        "document.render",
        "scripts.documents.public.render",
    }:
        return True
    if any(
        marker in command
        for command in _transcript_command_texts(record)
        for marker in (
            "document.render-v1",
            "document.render",
            "scripts.documents.public.render",
        )
    ):
        return True
    return any(
        re.search(
            r"scripts\.documents\.runtime_cli\b[^\r\n]*\brender\b",
            command,
        )
        is not None
        for command in _transcript_command_texts(record)
    )


def _transcript_case_archive_bound(
    record: dict[str, Any], *, workspace: Path, case_id: str
) -> bool:
    """Require an archive call to bind to the audited workspace and case."""
    arguments = _transcript_argument_object(record)
    requested_case_id = arguments.get("case_id")
    if requested_case_id is not None and requested_case_id != case_id:
        return False
    workspace_root = workspace.resolve()
    for value in _transcript_path_values(arguments):
        candidate = _resolve_transcript_path(value, workspace)
        if candidate is None:
            return False
        if not _is_relative_to(candidate, workspace_root):
            return False
    return True


def _transcript_contract_discovery_bypass(record: dict[str, Any]) -> bool:
    """Detect reading implementation/source to discover a public contract."""
    names = _transcript_call_names(record)
    command = " ".join(_transcript_command_texts(record))
    paths = " ".join(_transcript_path_texts(record)).casefold()
    source_path = bool(
        re.search(
            r"(?:^|[/\\\s])scripts[/\\]case_archive[/\\](?:cli|service)\.py\b",
            paths,
        )
    )
    source_command = bool(
        re.search(
            r"(?:\brg\b|\bgrep\b|\bget-content\b|\bcat\b|\bread\b|"
            r"\bselect-string\b|\btype\b)[^\r\n]*scripts[/\\]case_archive[/\\]",
            command,
        )
    )
    return source_path or source_command or (
        bool(names & {"read", "get-content", "cat", "rg", "grep"})
        and "scripts/case_archive/" in paths
    )


def _transcript_has_document_delivery_signal(record: dict[str, Any]) -> bool:
    names = _transcript_call_names(record)
    if names & {
        "present_files",
        "document.render-v1",
        "document.render",
        "scripts.documents.public.render",
    }:
        return True
    commands = _transcript_command_texts(record)
    return any(
        marker in command
        for command in commands
        for marker in (
            "present_files",
            "document.render-v1",
            "scripts.documents.public.render",
            "scripts.documents.runtime_cli",
        )
    )


def _transcript_runtime_source_inspection(record: dict[str, Any]) -> bool:
    """Detect implementation inspection while keeping routed references readable."""

    command = " ".join(_transcript_command_texts(record))
    paths = " ".join(_transcript_path_texts(record)).replace("\\", "/").casefold()
    names = _transcript_call_names(record)
    source_path = bool(
        re.search(r"(?:^|/)scripts/(?:[^/]+/)*[^/]+\.py(?:$|[\s\"'])", paths)
    )
    source_reader = bool(
        names & {"read", "get-content", "cat", "rg", "grep", "select-string", "type"}
    ) or bool(
        re.search(
            r"(?:\bread\b|\bget-content\b|\bcat\b|\brg\b|\bgrep\b|"
            r"\bselect-string\b|\btype\b)[^\r\n]*scripts[/\\][^\r\n]*\.py\b",
            command,
        )
    )
    python_introspection = bool(
        re.search(r"\binspect\.(?:getsource|signature|getmembers)\s*\(", command)
        or (
            "scripts." in command
            and re.search(r"\bdir\s*\(", command)
        )
    )
    return python_introspection or (source_path and source_reader)


def _transcript_managed_view_signal(record: dict[str, Any]) -> bool:
    names = _transcript_call_names(record)
    if names & {
        "managed_delivery_view",
        "managed-view",
        "workbuddy_acceptance.managed_view",
    }:
        return True
    command = " ".join(_transcript_command_texts(record))
    return (
        "--managed-view" in command
        or "managed_delivery_view" in command
        or bool(
            re.search(
                r"scripts\.documents\.runtime_cli\b[^\r\n]*\bview\b",
                command,
            )
        )
    )


def _transcript_present_files_call(record: dict[str, Any]) -> bool:
    if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
        return False
    return "present_files" in _transcript_call_names(record) or any(
        "present_files" in command
        for command in _transcript_command_texts(record)
    )


def _transcript_has_explicit_no_document_goal(record: dict[str, Any]) -> bool:
    role = _transcript_text(record.get("role"))
    event = _transcript_event(record)
    if role != "user" and event not in {"user_message", "user_turn"}:
        return False
    text = " ".join(_transcript_message_texts(record))
    return bool(
        re.search(
            r"(?:没有(?:任何)?(?:文书|文件)目标|(?:本轮|这次|当前)?\s*"
            r"(?:不需要|无需|不生成|不制作|不输出|不做)[^。\n]{0,20}"
            r"(?:文书|文件|通知书|申请书|DOCX))",
            text,
            re.IGNORECASE,
        )
    )


def _transcript_call_id(record: dict[str, Any]) -> str | None:
    for key in _TRANSCRIPT_CALL_ID_KEYS:
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


def _transcript_command_texts(record: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("commandPreview", "command_preview", "command", "cmd"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.casefold())
    arguments = _transcript_argument_object(record)
    for key in ("commandPreview", "command_preview", "command", "cmd", "script"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.casefold())
    argv = arguments.get("argv")
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        values.append(" ".join(argv).casefold())
    return tuple(dict.fromkeys(values))


def _transcript_result_values(record: dict[str, Any]) -> tuple[object, ...]:
    values: list[object] = []
    for key in _TRANSCRIPT_RESULT_KEYS:
        if key in record:
            values.append(record[key])
    return tuple(values)


def _transcript_result_is_success(record: dict[str, Any]) -> bool:
    status_mappings: list[Mapping[str, Any]] = [record]
    for value in _transcript_result_values(record):
        status_mappings.extend(_transcript_mapping_objects(value))
    for provider_key in ("providerData", "provider_data"):
        provider = record.get(provider_key)
        if not isinstance(provider, Mapping):
            continue
        tool_result = provider.get("toolResult", provider.get("tool_result"))
        if not isinstance(tool_result, Mapping):
            continue
        raw_response = tool_result.get(
            "rawResponse", tool_result.get("raw_response")
        )
        if isinstance(raw_response, Mapping):
            status_mappings.append(raw_response)

    for mapping in status_mappings:
        if mapping.get("is_error") is True or mapping.get("isError") is True:
            return False
        if mapping.get("error") not in (None, False, "", []):
            return False
        if mapping.get("ok") is False or mapping.get("success") is False:
            return False
        for key in ("returncode", "return_code", "exit_code", "exitCode"):
            return_code = mapping.get(key)
            if (
                isinstance(return_code, int)
                and not isinstance(return_code, bool)
                and return_code != 0
            ):
                return False
        status = _transcript_text(mapping.get("status"))
        if status in {"error", "failed", "failure", "cancelled", "canceled"}:
            return False
    return True


def _recoverable_case_archive_retry_call_ids(
    records: list[dict[str, Any]], *, workspace: Path
) -> tuple[frozenset[str], int | None]:
    """Find failed read-only transport guesses recovered by an exact public read."""

    outcomes: dict[str, bool] = {}
    for record in records:
        if _transcript_event(record) not in _TRANSCRIPT_RESULT_EVENTS:
            continue
        call_id = _transcript_call_id(record)
        if call_id is not None:
            outcomes[call_id] = _transcript_result_is_success(record)

    successful_recovery_index: int | None = None
    for index, record in enumerate(records):
        if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
            continue
        call_id = _transcript_call_id(record)
        if outcomes.get(call_id or "") is not True:
            continue
        if not _case_archive_runtime_transport_allowed(record, workspace=workspace):
            continue
        if _transcript_case_archive_operation(record) != "read":
            continue
        successful_recovery_index = index
        break

    if successful_recovery_index is None:
        return frozenset(), None

    retry_ids: set[str] = set()
    for record in records[:successful_recovery_index]:
        if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
            continue
        call_id = _transcript_call_id(record)
        if call_id is None or outcomes.get(call_id) is not False:
            continue
        command = " ".join(_transcript_command_texts(record)).replace("\\", "/")
        if not re.search(
            r"scripts[./]case_archive[./]runtime_cli(?:\.py)?\b[^\r\n]*\bread-current\b",
            command,
        ):
            continue
        if re.search(r"(?:^|\s)(?:create|commit)(?:\s|$)", command):
            continue
        retry_ids.add(call_id)
    return frozenset(retry_ids), successful_recovery_index


def _read_recovery_reference_before_success(
    records: list[dict[str, Any]], successful_recovery_index: int | None
) -> bool:
    if successful_recovery_index is None:
        return False
    for record in records[:successful_recovery_index]:
        if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
            continue
        paths = " ".join(_transcript_path_texts(record)).replace("\\", "/")
        if "/references/model-led-case-archive.md" in paths:
            return True
    return False


def _transcript_call_has_successful_result(
    records: list[dict[str, Any]], call_id: str | None
) -> bool:
    if call_id is None:
        return False
    return any(
        _transcript_event(record) in _TRANSCRIPT_RESULT_EVENTS
        and _transcript_call_id(record) == call_id
        and _transcript_result_is_success(record)
        for record in records
    )


def _native_trace_uses_forbidden_fact(
    record: dict[str, Any], forbidden_marker: str
) -> bool:
    """只在原生 trace 的结构化业务写入/计算/文书输入中判定事实使用。"""

    if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
        return False
    names = _transcript_call_names(record)
    commands = " ".join(_transcript_command_texts(record))
    business_sink = bool(
        names
        & {
            "casearchive.commit",
            "case_archive.commit",
            "amount.calculate",
            "amount.calculate-v1",
            "document.render",
            "document.render-v1",
            "scripts.documents.public.render",
        }
    ) or bool(
        re.search(
            r"(?:scripts\.case_archive\.cli[^\r\n]*\bcommit\b|"
            r"scripts\.amount_calculator\.runtime_cli[^\r\n]*\bcalculate\b|"
            r"scripts\.documents\.runtime_cli[^\r\n]*\brender\b)",
            commands,
        )
    )
    if not business_sink:
        return False
    arguments = json.dumps(
        _transcript_argument_object(record), ensure_ascii=False, sort_keys=True
    )
    return forbidden_marker.casefold() in (arguments + commands).casefold()


def _transcript_envelope(value: object) -> dict[str, Any] | None:
    candidate = _transcript_json_value(value)
    if candidate is None and isinstance(value, str):
        decoder = json.JSONDecoder()
        for offset, character in enumerate(value):
            if character not in "[{":
                continue
            try:
                embedded, _end = decoder.raw_decode(value, offset)
            except (TypeError, json.JSONDecodeError):
                continue
            found = _transcript_envelope(embedded)
            if found is not None:
                return found
        return None
    if isinstance(candidate, dict):
        if (
            ("phase" in candidate or "core_phase" in candidate)
            and any(
                key in candidate
                for key in (
                    "status",
                    "allowed_actions",
                    "next_action",
                    "next_user_action",
                    "awaiting_user_answer",
                )
            )
        ):
            return candidate
        if (
            "status" in candidate
            and any(
                key in candidate
                for key in ("allowed_actions", "next_action", "next_user_action")
            )
        ):
            return candidate
        for key in (
            "result", "output", "response", "data", "structuredContent",
            "stdout", "operation", "text",
        ):
            found = _transcript_envelope(candidate.get(key))
            if found is not None:
                return found
    elif isinstance(candidate, list):
        for item in candidate:
            found = _transcript_envelope(item)
            if found is not None:
                return found
    return None


def _transcript_is_skill_result(record: dict[str, Any], skill_call_ids: set[str]) -> bool:
    if _transcript_event(record) not in _TRANSCRIPT_RESULT_EVENTS:
        return False
    call_id = _transcript_call_id(record)
    if call_id is not None and call_id not in skill_call_ids:
        return False
    names = _transcript_call_names(record)
    if call_id is None and not ({"skill", "arbibuddy"} <= names or "skill" in names):
        return False
    return _transcript_result_is_success(record)


def _transcript_skill_call(record: object) -> bool:
    """识别结构化调用元数据，不读取 content/message/output 正文。"""
    if not isinstance(record, dict):
        return False
    event = _transcript_event(record)
    if event == "skill(arbibuddy)":
        return True
    names = _transcript_call_names(record)
    arguments = _transcript_argument_object(record)
    skill_name = _transcript_text(
        arguments.get("skill") or arguments.get("skill_name")
    )
    if event not in _TRANSCRIPT_CALL_EVENTS and not any(
        value in {"skill(arbibuddy)", "skill:arbibuddy"} for value in names
    ):
        return False
    return event in _TRANSCRIPT_CALL_EVENTS and (
        skill_name == "arbibuddy"
        or (
            "skill" in names
            and "arbibuddy" in names
        )
        or "skill(arbibuddy)" in names
        or "skill:arbibuddy" in names
    )


def _transcript_is_core_call(record: dict[str, Any]) -> bool:
    commands = _transcript_command_texts(record)
    names = _transcript_call_names(record)
    return any("scripts.core_workflow.cli" in command for command in commands) or any(
        name in {
            "scripts.core_workflow.cli",
            "core_workflow",
            "core_workflow_cli",
        }
        for name in names
    )


_TRANSCRIPT_BARRIER_ACTIONS = frozenset({
    "resolve-right",
    "record-answer",
    "record-rights-scan-answer",
    "record-supplement-answer",
    "confirm",
    "plan-deliveries",
    "preflight-package",
    "run-package",
    "run-document",
    "publish-plan",
    "record-high-risk-gate",
    "acknowledge-failure",
    "acknowledge-preflight-failure",
})


def _transcript_is_read_only_preparation(record: dict[str, Any]) -> bool:
    """Ignore source/context inspection when locating the first business call."""
    if _transcript_event(record) not in _TRANSCRIPT_CALL_EVENTS:
        return False
    names = _transcript_call_names(record)
    if names & {
        "read", "find", "search", "list", "glob", "rg", "grep", "ls",
        "dir", "pwd", "get-content", "select-string", "cat",
    }:
        return True
    command = " ".join(_transcript_command_texts(record))
    if re.fullmatch(
        r"\s*(?:python(?:\.exe)?|py(?:\.exe)?)\s+(?:-v|--version)"
        r"(?:\s*;\s*echo\s+[a-z0-9._-]+)?\s*",
        command,
        flags=re.IGNORECASE,
    ):
        return True
    if any(marker in command for marker in _BUSINESS_COMMAND_MARKERS):
        return False
    return bool(re.search(
        r"(?:^|\s)(?:pwd|dir|ls|rg|grep|find|select-string|get-content|"
        r"get-childitem|test-path)\b",
        command,
    ))


def _transcript_core_action(record: dict[str, Any]) -> str | None:
    if not _transcript_is_core_call(record):
        return None
    command = " ".join(_transcript_command_texts(record))
    for action in sorted(_TRANSCRIPT_BARRIER_ACTIONS, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9_-]){re.escape(action)}(?![a-z0-9_-])", command):
            return action
    return None


def _transcript_is_turn_end(record: dict[str, Any]) -> bool:
    event = _transcript_event(record)
    if event in _TRANSCRIPT_TURN_END_EVENTS:
        return True
    if any(
        record.get(key) is True
        for key in ("turn_end", "turn_ended", "end_turn", "round_end", "completed_turn")
    ):
        return True
    finish_reason = _transcript_text(record.get("finish_reason"))
    return finish_reason in {"stop", "end_turn", "completed"}


def _transcript_envelope_waits_for_user(envelope: dict[str, Any]) -> bool:
    return (
        envelope.get("awaiting_user_answer") is True
        or _transcript_text(envelope.get("status")) == "awaiting_user_answer"
        or _transcript_text(
            envelope.get("next_action") or envelope.get("next_user_action")
        )
        in {"ask_user_and_end_turn", "ask_user"}
        or (
            isinstance(envelope.get("barrier"), dict)
            and envelope.get("same_turn_allowed_actions") == []
        )
        or (
            _transcript_text(envelope.get("phase"))
            in {"failure_blocked", "preflight_blocked"}
            and _transcript_text(
                envelope.get("next_user_action") or envelope.get("next_action")
            ) in {"acknowledge-failure", "acknowledge-preflight-failure"}
        )
    )


def _add_transcript_bypass_signals(
    record: dict[str, Any],
    transcript_jsonl: Path,
    violations: list[dict[str, str]],
    *,
    workspace: Path,
    authorized_presentation_paths: tuple[Path, ...] = (),
    authorized_presentation_values: tuple[str, ...] = (),
    diagnostic_observations: set[str] | None = None,
    recoverable_case_archive_retry_call_ids: frozenset[str] = frozenset(),
    strict_recovery: bool = False,
) -> None:
    names = _transcript_call_names(record)
    commands = _transcript_command_texts(record)
    path_texts = _transcript_path_texts(record)
    command = " ".join(commands)
    path_text = " ".join(path_texts)
    recoverable_archive_retry = (
        not strict_recovery
        and _transcript_call_id(record) in recoverable_case_archive_retry_call_ids
    )
    if (
        _transcript_event(record) in _TRANSCRIPT_CALL_EVENTS
        and _case_archive_module_present(record)
        and not _case_archive_runtime_transport_allowed(record, workspace=workspace)
        and not recoverable_archive_retry
    ):
        _add(
            violations,
            "prohibited_case_archive_transport",
            "CaseArchive CLI 只能使用 runtime context 生成的精确公开 transport",
            transcript_jsonl,
        )
    present_files_call = (
        _transcript_event(record) in _TRANSCRIPT_CALL_EVENTS
        and ("present_files" in names or "present_files" in command)
    )
    authorized_presentation = (
        present_files_call
        and "present_files" in names
        and _present_files_matches_authorized(
            record,
            workspace,
            authorized_presentation_paths,
            authorized_presentation_values,
        )
    )
    if (
        _command_masks_business_exit_status(command)
        and _transcript_has_explicit_nonzero_exit(record)
    ):
        _add(
            violations,
            "masked_exit_status",
            "命令不得使用 2>&1 或 head、tail、grep 等管道掩盖真实退出状态",
            transcript_jsonl,
        )
    if present_files_call and not authorized_presentation:
        _add(
            violations,
            "prohibited_present_files",
            "客户端不得绕过核心状态信封调用 present_files 展示文书",
            transcript_jsonl,
        )
    if re.search(r"\bpython(?:\.exe)?\s+-m\s+venv\b", command):
        _add(
            violations,
            "prohibited_runtime_environment_creation",
            "文书阶段不得临时创建虚拟环境",
            transcript_jsonl,
        )
    if re.search(r"\b(?:pip|pip\d*)\b[^\r\n]*\binstall\b", command):
        _add(
            violations,
            "prohibited_runtime_dependency_install",
            "文书阶段不得临时安装 python-docx",
            transcript_jsonl,
        )
    if _transcript_mutates_installed_skill(record):
        _add(
            violations,
            "runtime_skill_mutation",
            "运行中的案件会话不得改写 WorkBuddy 已安装 Skill 树",
            transcript_jsonl,
        )
    if any(
        name in command
        for name in PROHIBITED_GENERATION_SCRIPTS
    ) or re.search(r"\b(?:gen|generate|build|make)[^\s/\\]*\.py\b", command):
        _add(
            violations,
            "prohibited_generation_script",
            "发现被禁止的旁路文书生成脚本",
            transcript_jsonl,
        )
    if re.search(
        r"\bpython(?:\.exe)?\s+(?!-m\b)[^\r\n]*\.py\b",
        command,
    ) and not recoverable_archive_retry:
        _add(
            violations,
            "prohibited_temporary_script",
            "命令轨迹包含临时 Python 脚本或内联脚本执行",
            transcript_jsonl,
        )
    if _transcript_runtime_source_inspection(record):
        _add(
            violations,
            "prohibited_runtime_source_inspection",
            "运行期应使用公开契约，不得读取或反射实现源码来猜测调用方式",
            transcript_jsonl,
        )
    if re.search(r"\bpython(?:\.exe)?\s+(?:-c|-)$", command) or re.search(
        r"\bpython(?:\.exe)?\s+-(?:c|exec)\b",
        command,
    ):
        _add(
            violations,
            "prohibited_temporary_script",
            "命令轨迹包含内联 Python 脚本执行",
            transcript_jsonl,
        )
    if (
        ".arbibuddy/cases" in command.replace("\\", "/").casefold()
        and names & {"write", "write_file", "edit", "edit_file", "save"}
        and any(
            marker in command.casefold()
            for marker in ("案情档案.md", ".arbibuddy/cases")
        )
    ):
        _add(
            violations,
            "prohibited_case_archive_transport",
            "不得手工编辑 canonical 案情档案或其工作区控制文件",
            transcript_jsonl,
        )
    if (
        ".docx" in command
        and "scripts.core_workflow.cli" not in command
        and not authorized_presentation
    ):
        _add(
            violations,
            "unmanaged_docx",
            "DOCX 命令未来自核心状态信封授权的受管交付",
            transcript_jsonl,
        )
    if _command_reads_outside_workspace_case(
        " ".join((command, path_text)), workspace
    ):
        _add(
            violations,
            "cross_workspace_case_access",
            "模型访问了当前工作区之外的案情档案或 .arbibuddy 路径",
            transcript_jsonl,
        )
    if _command_mentions_untrusted_memory(" ".join((command, path_text))):
        write_memory = bool(
            names & {"write", "edit", "write_file", "edit_file", "save"}
            or re.search(r"(?:>|>>|set-content|add-content|tee\b|write_text|write_bytes)", command)
        )
        if diagnostic_observations is not None:
            diagnostic_observations.add(
                "model_wrote_workbuddy_memory"
                if write_memory
                else "model_read_untrusted_context"
            )
    if _destructive_case_workspace_command(command):
        _add(
            violations,
            "destructive_case_workspace_command",
            "模型不得删除或重建当前 .arbibuddy 案件目录",
            transcript_jsonl,
        )
    if (
        "scripts.amount_calculator.cli" in command
        or "core.calculate_batch" in command
        or re.search(r"(?:from|import)\s+decimal\b|\bdecimal\.decimal\b", command)
    ):
        _add(
            violations,
            "amount_calculation_bypass",
            "金额结果必须来自公开 amount.calculate-v1 接口，不得使用旧 CLI 或内嵌 Decimal 旁路",
            transcript_jsonl,
        )


def _command_reads_outside_workspace_case(command: str, workspace: Path) -> bool:
    normalized = command.replace("\\\\", "/").replace("\\", "/")
    workspace_root = workspace.resolve()
    tokens = re.findall(
        r"(?:[a-z]:/|/|\.\.?/)[^\"'`\s,;]+",
        normalized,
        flags=re.IGNORECASE,
    )
    for token in tokens:
        token = token.rstrip(")]}>'")
        folded = token.casefold()
        if ".arbibuddy" not in folded and "案情档案.md" not in token:
            continue
        try:
            candidate = Path(token)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (workspace_root / candidate).resolve()
            )
        except (OSError, RuntimeError, ValueError):
            return True
        if not _is_relative_to(resolved, workspace_root):
            return True
    return False


def _command_mentions_untrusted_memory(command: str) -> bool:
    folded = command.replace("\\\\", "/").replace("\\", "/").casefold()
    return ".workbuddy/memory" in folded or re.search(
        r"(?:^|[/ ])user\.md(?:$|[^a-z0-9_])",
        folded,
    ) is not None


def _destructive_case_workspace_command(command: str) -> bool:
    """Detect deletion of canonical case state, not disposable runtime input."""

    normalized = command.replace("\\\\", "/").replace("\\", "/").casefold()
    if ".arbibuddy" not in normalized or not re.search(
        r"(?:remove-item|rmdir|rm\s+-[a-z]*r|shutil\.rmtree|\.unlink\s*\()",
        normalized,
    ):
        return False
    if ".arbibuddy/cases" in normalized or "案情档案.md" in normalized:
        return True
    return re.search(
        r"\.arbibuddy[\"']?(?:\s|$|[,;)])",
        normalized,
    ) is not None


def _transcript_user_round(record: object) -> bool:
    """读取用户轮次的结构化标记，不读取正文内容。"""
    if not isinstance(record, dict):
        return False
    event = _transcript_event(record)
    if event in _TRANSCRIPT_USER_ROUND_EVENTS:
        return True
    if any(
        record.get(key) is True
        for key in ("user_turn", "user_round", "new_user_message", "turn_boundary")
    ):
        return True
    return _transcript_text(record.get("role")) == "user"


def _transcript_uses_case_facts_in_business_result(record: dict[str, Any]) -> bool:
    """Only public text or business sink inputs can prove memory fact reuse."""

    event = _transcript_event(record)
    if event in {"assistant_output", "assistant_message", "model_output"}:
        return True
    if event not in _TRANSCRIPT_CALL_EVENTS:
        return False
    operation = _transcript_case_archive_operation(record)
    if operation == "commit":
        return True
    names = _transcript_call_names(record)
    commands = " ".join(_transcript_command_texts(record))
    return bool(
        names
        & {
            "amount.calculate",
            "amount.calculate-v1",
            "document.render",
            "document.render-v1",
            "scripts.documents.public.render",
            "legal_verification",
            "legal_verification.verify",
            "Authority.verify",
            "authority.verify",
            "verify_authority",
            "legal_authority",
            "scripts.legal_verification.runtime_cli",
        }
        or "scripts.legal_verification.runtime_cli" in commands
        or re.search(
            r"scripts[.]amount_calculator[.]runtime_cli[^\r\n]*\bcalculate\b|"
            r"scripts[.]documents[.]runtime_cli[^\r\n]*\brender\b",
            commands,
        )
    )


def _audit_transcript_jsonl(
    transcript_jsonl: Path,
    session_id: str | None,
    violations: list[dict[str, str]],
    *,
    workspace: Path,
    case_id: str,
    authorized_presentation_paths: tuple[Path, ...] = (),
    authorized_presentation_values: tuple[str, ...] = (),
    require_historical_managed_view: bool = False,
    trusted_case_text: str = "",
    records_override: list[dict[str, Any]] | None = None,
    strict_recovery: bool = False,
) -> dict[str, Any]:
    """只读解析原生调用/返回结构，不回显聊天正文或敏感值。"""
    records = 0
    matched_session = session_id is None
    skill_call_observed = False
    skill_result_observed = False
    user_round_observed = False
    compliance_claim_observed = False
    present_files_observed = False
    core_call_observed = False
    core_envelope_observed = False
    first_business_action_is_core = False
    first_core_call_id: str | None = None
    first_core_envelope_observed = False
    waiting_for_user = False
    waiting_turn_ended = False
    skill_call_ids: set[str] = set()
    call_kinds: dict[str, str] = {}
    first_business_action_seen = False
    waiting_envelope_index: int | None = None
    pending_barrier = False
    pending_barrier_index: int | None = None
    barrier_count = 0
    barrier_user_round_observed = False
    barrier_bypass_observed = False
    same_turn_barrier_actions: list[str] = []
    direct_bypass_codes: set[str] = set()
    diagnostic_observations: set[str] = set()
    document_delivery_observed = False
    explicit_no_document_goal = False
    activation_evidence = {
        "slash_command": False,
        "skill_path": False,
        "skill_body_injected": False,
        "structured_skill_call": False,
    }
    case_archive_action_observed = False
    current_case_archive_action_observed = False
    case_archive_action_succeeded = False
    case_archive_call_ids: set[str] = set()
    contract_discovery_bypass_observed = False
    untrusted_context_observed = False
    cross_source_fact_pollution_observed = False
    unbound_local_authority_observed = False
    untrusted_context_call_ids: set[str] = set()
    untrusted_markers: set[str] = set()
    confirmed_markers: set[str] = _trusted_case_fact_markers(trusted_case_text)
    render_observed = False
    render_success_observed = False
    unauthorized_render_observed = False
    managed_view_observed = False
    archive_change_after_render_observed = False
    first_render_index: int | None = None
    last_user_round_index: int | None = None
    first_archive_change_after_render_index: int | None = None
    archive_change_after_render_kind: str | None = None
    presentation_events: list[tuple[int, tuple[str, ...]]] = []
    presentation_diagnostic: dict[str, Any] = {"codes": [], "events": []}
    timeline_records: list[tuple[int, dict[str, Any]]] = []
    revision_timeline: dict[str, Any] = {
        "render_count": 0,
        "commit_count": 0,
        "present_count": 0,
        "historical_presentations": [],
        "invalid_events": [],
        "valid": True,
    }
    if records_override is not None and transcript_jsonl.name != "<workbuddy-native-trace>":
        _audit_public_transcript_messages(
            transcript_jsonl,
            session_id,
            violations,
        )
    try:
        prepass_records: list[dict[str, Any]] = []
        if records_override is not None:
            for item in records_override:
                if not isinstance(item, dict):
                    raise ValueError("native trace 规范化记录必须是对象")
                prepass_records.append(item)
        else:
            with transcript_jsonl.open("r", encoding="utf-8") as prepass_stream:
                for item in prepass_stream:
                    if not item.strip():
                        continue
                    value = json.loads(item)
                    if not isinstance(value, dict):
                        raise ValueError("transcript JSONL 行必须是对象")
                    prepass_records.append(value)
        selected_prepass_records = [
            value
            for value in prepass_records
            if session_id is None
            or next(
                (
                    value.get(key)
                    for key in _TRANSCRIPT_SESSION_KEYS
                    if isinstance(value.get(key), str)
                ),
                None,
            )
            == session_id
        ]
        (
            recoverable_archive_retry_ids,
            successful_recovery_index,
        ) = _recoverable_case_archive_retry_call_ids(
            selected_prepass_records,
            workspace=workspace,
        )
        if recoverable_archive_retry_ids:
            diagnostic_observations.add(
                "recoverable_case_archive_transport_retry"
            )
        if _read_recovery_reference_before_success(
            selected_prepass_records, successful_recovery_index
        ):
            diagnostic_observations.add(
                "recovery_reference_read_before_current_archive"
            )

        source = nullcontext(records_override) if records_override is not None else transcript_jsonl.open("r", encoding="utf-8")
        with source as stream:
            for index, item in enumerate(stream):
                if records_override is not None:
                    value = item
                    if not isinstance(value, dict):
                        raise ValueError("native trace 规范化记录必须是对象")
                else:
                    if not item.strip():
                        continue
                    value = json.loads(item)
                    if not isinstance(value, dict):
                        raise ValueError("transcript JSONL 行必须是对象")
                record_session = next(
                    (
                        value.get(key)
                        for key in _TRANSCRIPT_SESSION_KEYS
                        if isinstance(value.get(key), str)
                    ),
                    None,
                )
                if session_id is not None and record_session != session_id:
                    continue
                matched_session = True
                records += 1
                timeline_records.append((index, value))
                is_user_round = _transcript_user_round(value)
                role = _transcript_text(value.get("role"))
                event = _transcript_event(value)
                if _transcript_render_signal(value):
                    render_observed = True
                    if event in _TRANSCRIPT_RESULT_EVENTS and any(
                        mapping.get("contract_version") == "document.render-v1"
                        and mapping.get("ok") is True
                        for mapping in _transcript_result_mappings(value)
                    ):
                        render_success_observed = True
                    if first_render_index is None:
                        first_render_index = index
                if _transcript_managed_view_signal(value):
                    managed_view_observed = True
                if (
                    first_render_index is not None
                    and index > first_render_index
                    and _transcript_case_archive_operation(value) == "commit"
                ):
                    archive_change_after_render_observed = True
                    if first_archive_change_after_render_index is None:
                        first_archive_change_after_render_index = index
                        archive_change_after_render_kind = (
                            "user_follow_up"
                            if last_user_round_index is not None
                            and last_user_round_index > first_render_index
                            else "unprompted_or_delivery_status"
                        )
                message_text = " ".join(_transcript_message_texts(value))
                if (
                    is_user_round
                    or role == "user"
                    or event in _TRANSCRIPT_USER_ROUND_EVENTS
                ):
                    confirmed_markers.update(_untrusted_fact_markers(message_text))
                if _transcript_untrusted_context_path(value):
                    untrusted_context_observed = True
                    memory_names = _transcript_call_names(value)
                    memory_command = " ".join(_transcript_command_texts(value))
                    memory_write = bool(
                        memory_names
                        & {"write", "edit", "write_file", "edit_file", "save"}
                    ) or bool(
                        re.search(
                            r"(?:>|>>|set-content|add-content|tee\b|write_text|write_bytes)",
                            memory_command,
                        )
                    )
                    memory_call_id = _transcript_call_id(value)
                    if not memory_write and memory_call_id is not None:
                        untrusted_context_call_ids.add(memory_call_id)
                record_call_id = _transcript_call_id(value)
                if (
                    event in _TRANSCRIPT_RESULT_EVENTS
                    and record_call_id in untrusted_context_call_ids
                ):
                    source_text = " ".join(
                        item if isinstance(item, str)
                        else json.dumps(item, ensure_ascii=False)
                        for item in _transcript_result_values(value)
                    )
                    untrusted_markers.update(_untrusted_fact_markers(source_text))
                elif (
                    untrusted_markers
                    and role != "user"
                    and event not in _TRANSCRIPT_RESULT_EVENTS
                    and _transcript_uses_case_facts_in_business_result(value)
                ):
                    used_markers = (
                        _untrusted_fact_markers(_transcript_serialized_text(value))
                        & untrusted_markers
                        - confirmed_markers
                    )
                    if used_markers:
                        cross_source_fact_pollution_observed = True
                        _add(
                            violations,
                            "cross_source_case_fact_pollution",
                            "模型使用了未经本轮用户确认的 WorkBuddy 历史上下文案件事实",
                            transcript_jsonl,
                        )
                authority_regions = _transcript_authority_region(value)
                if authority_regions and not (authority_regions & confirmed_markers):
                    unbound_local_authority_observed = True
                    _add(
                        violations,
                        "unbound_local_authority",
                        "地方性法律核验缺少当前用户或当前案情档案确认的适用地区",
                        transcript_jsonl,
                    )
                if is_user_round:
                    user_round_observed = True
                    last_user_round_index = index
                    if pending_barrier and (
                        pending_barrier_index is None or index > pending_barrier_index
                    ):
                        pending_barrier = False
                        pending_barrier_index = None
                        barrier_user_round_observed = True
                skill_call = _transcript_skill_call(value)
                if skill_call:
                    skill_call_observed = True
                    call_id = _transcript_call_id(value)
                    if call_id is not None:
                        skill_call_ids.add(call_id)
                        call_kinds[call_id] = "skill"

                names = _transcript_call_names(value)
                if "present_files" in names or any(
                    "present_files" in command
                    for command in _transcript_command_texts(value)
                ):
                    present_files_observed = True
                    if event in _TRANSCRIPT_CALL_EVENTS:
                        presentation_events.append(
                            (index, _transcript_presented_values(value))
                        )
                document_delivery_observed = (
                    document_delivery_observed
                    or _transcript_has_document_delivery_signal(value)
                )
                if _transcript_render_signal(value) and any(
                    mapping.get("presentation_authorized") is False
                    for mapping in _transcript_result_mappings(value)
                ):
                    unauthorized_render_observed = True
                explicit_no_document_goal = (
                    explicit_no_document_goal
                    or _transcript_has_explicit_no_document_goal(value)
                )
                record_activation = _transcript_activation_evidence(value)
                for key, observed in record_activation.items():
                    activation_evidence[key] = (
                        activation_evidence[key] or observed
                    )
                case_archive_action_observed = (
                    case_archive_action_observed
                    or _transcript_case_archive_action(value)
                )
                archive_operation = _transcript_case_archive_operation(value)
                if archive_operation is not None and _transcript_case_archive_bound(
                    value, workspace=workspace, case_id=case_id
                ):
                    current_case_archive_action_observed = True
                    archive_call_id = _transcript_call_id(value)
                    if archive_call_id is not None:
                        case_archive_call_ids.add(archive_call_id)
                if _transcript_contract_discovery_bypass(value):
                    contract_discovery_bypass_observed = True
                    _add(
                        violations,
                        "loaded_then_contract_discovery_bypass",
                        "Skill 加载后读取 scripts/case_archive 实现来猜测契约；应直接使用公开 CaseArchive 接缝",
                        transcript_jsonl,
                    )
                if _public_text_has_internal_leak(
                    " ".join(_transcript_public_texts(value))
                ):
                    _add(
                        violations,
                        "user_visible_internal_leak",
                        "用户可见回复包含内部记录、路径、协议或实现细节",
                        transcript_jsonl,
                    )
                violation_count = len(violations)
                _add_transcript_bypass_signals(
                    value,
                    transcript_jsonl,
                    violations,
                    workspace=workspace,
                    authorized_presentation_paths=(
                        ()
                        if require_historical_managed_view
                        else authorized_presentation_paths
                    ),
                    authorized_presentation_values=(
                        ()
                        if require_historical_managed_view
                        else authorized_presentation_values
                    ),
                    diagnostic_observations=diagnostic_observations,
                    recoverable_case_archive_retry_call_ids=(
                        recoverable_archive_retry_ids
                    ),
                    strict_recovery=strict_recovery,
                )
                direct_bypass_codes.update(
                    item["code"] for item in violations[violation_count:]
                )
                if _transcript_contract_discovery_bypass(value):
                    direct_bypass_codes.add("loaded_then_contract_discovery_bypass")

                core_call = event in _TRANSCRIPT_CALL_EVENTS and _transcript_is_core_call(value)
                if core_call:
                    core_call_observed = True
                    call_id = _transcript_call_id(value)
                    if call_id is not None:
                        call_kinds[call_id] = "core"
                    if not first_business_action_seen:
                        first_business_action_seen = True
                        first_business_action_is_core = True
                        first_core_call_id = call_id
                elif (
                    event in _TRANSCRIPT_CALL_EVENTS
                    and not skill_call
                    and not _transcript_is_read_only_preparation(value)
                ):
                    if not first_business_action_seen:
                        first_business_action_seen = True
                        first_business_action_is_core = False

                if core_call and pending_barrier:
                    action = _transcript_core_action(value)
                    if action is not None:
                        barrier_bypass_observed = True
                        same_turn_barrier_actions.append(action)
                        # 旧 core ask_* / barrier 只作历史诊断，不是模型主导
                        # Skill 的验收协议；不得因其缺失或旁路单独阻断 Journey。

                if event in _TRANSCRIPT_RESULT_EVENTS and _transcript_result_is_success(value):
                    call_id = _transcript_call_id(value)
                    kind = call_kinds.get(call_id) if call_id is not None else None
                    if kind == "skill" or _transcript_is_skill_result(value, skill_call_ids):
                        skill_result_observed = True
                    if kind == "core" or (
                        call_id is None
                        and _transcript_is_core_call(value)
                    ):
                        envelope = next(
                            (
                                _transcript_envelope(item)
                                for item in _transcript_result_values(value)
                                if _transcript_envelope(item) is not None
                            ),
                            None,
                        )
                        if envelope is not None:
                            core_envelope_observed = True
                            if call_id == first_core_call_id:
                                first_core_envelope_observed = True
                            if _transcript_envelope_waits_for_user(envelope):
                                barrier_count += 1
                                pending_barrier = True
                                pending_barrier_index = index
                                waiting_for_user = True
                                waiting_envelope_index = index
                    if call_id in case_archive_call_ids:
                        case_archive_action_succeeded = True
                    elif (
                        _transcript_case_archive_operation(value) is not None
                        and _transcript_case_archive_bound(
                            value, workspace=workspace, case_id=case_id
                        )
                    ):
                        case_archive_action_succeeded = True

                if waiting_for_user and _transcript_is_turn_end(value):
                    if waiting_envelope_index is None or index > waiting_envelope_index:
                        waiting_turn_ended = True
                        waiting_for_user = False
                compliance_claim_observed = compliance_claim_observed or any(
                    value.get(key) is True
                    for key in ("compliance_claim", "claimed_compliant", "output_claimed_compliant")
                )
        revision_timeline = _audit_revision_timeline(
            timeline_records,
            workspace=workspace,
            case_id=case_id,
        )
        historical_present_indices = {
            int(item["index"])
            for item in revision_timeline["historical_presentations"]
        }
        if require_historical_managed_view:
            violations[:] = [
                item
                for item in violations
                if item["code"] != "prohibited_present_files"
            ]
            direct_bypass_codes.discard("prohibited_present_files")
            for presentation_index, _values in presentation_events:
                if presentation_index not in historical_present_indices:
                    _add(
                        violations,
                        "prohibited_present_files",
                        "客户端不得绕过核心状态信封调用 present_files 展示文书",
                        transcript_jsonl,
                    )
                    direct_bypass_codes.add("prohibited_present_files")
        diagnostic_authorized_values = authorized_presentation_values
        if require_historical_managed_view:
            historical_values = next(
                (
                    tuple(item.get("authorized_values", ()))
                    for item in reversed(revision_timeline["historical_presentations"])
                    if item.get("authorized_values")
                ),
                (),
            )
            if historical_values:
                diagnostic_authorized_values = historical_values
        presentation_diagnostic = _managed_presentation_diagnostic(
            presentation_events,
            authorized_values=diagnostic_authorized_values,
        )
        if records_override is not None and render_success_observed and not revision_timeline["render_count"]:
            _add(
                violations,
                "render_evidence_missing",
                "原生 trace 没有可绑定的结构化 document.render 返回证据",
                transcript_jsonl,
            )
            direct_bypass_codes.add("render_evidence_missing")
        if unauthorized_render_observed and presentation_events and not managed_view_observed:
            _add(
                violations,
                "incomplete_managed_presentation",
                "单份 render 的非授权 artifact 列表不得直接驱动 present_files",
                transcript_jsonl,
            )
            direct_bypass_codes.add("incomplete_managed_presentation")
        historical_presentations = revision_timeline["historical_presentations"]
        duplicate_present = any(
            left.get("revision") is not None
            and left.get("revision") == right.get("revision")
            and left.get("paths") == right.get("paths")
            for index, left in enumerate(historical_presentations)
            for right in historical_presentations[index + 1:]
        )
        if duplicate_present:
            _add(
                violations,
                "duplicate_managed_presentation",
                "同一 archive revision 的受管文书集合不得重复展示",
                transcript_jsonl,
            )
            direct_bypass_codes.add("duplicate_managed_presentation")
        if (
            "split_managed_presentation" in presentation_diagnostic["codes"]
            and revision_timeline["valid"]
            and len(revision_timeline["historical_presentations"])
            == len(presentation_events)
            and revision_timeline["render_count"] >= len(presentation_events)
        ):
            presentation_diagnostic["codes"].remove("split_managed_presentation")
        for code in presentation_diagnostic["codes"]:
            _add(
                violations,
                code,
                {
                    "split_managed_presentation": "受管文件必须在一次 present_files 调用中完整展示",
                    "extra_unmanaged_presentation": "present_files 参数包含受管清单之外的文件",
                    "incomplete_managed_presentation": "present_files 参数未按受管视图原样完整传递",
                }[code],
                transcript_jsonl,
            )
            direct_bypass_codes.add(code)
        for invalid_event in revision_timeline["invalid_events"]:
            _add(
                violations,
                "invalid_revision_timeline",
                "文书展示发生在档案提交之后，且没有基于新 revision 的成功渲染",
                transcript_jsonl,
            )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        _add(
            violations,
            "invalid_transcript_diagnostic",
            "WorkBuddy transcript JSONL 只读诊断无法读取",
            transcript_jsonl,
        )
        return {
            "provided": True,
            "records": records,
            "session_matched": matched_session,
            "skill_invocation_observed": False,
            "skill_result_observed": False,
            "user_round_observed": False,
            "compliance_claim_observed": False,
            "present_files_observed": False,
            "core_call_observed": False,
            "core_envelope_observed": False,
            "first_business_action_is_core": False,
            "first_barrier_obeyed": False,
            "barrier_count": 0,
            "barrier_user_round_observed": False,
            "barrier_bypass_observed": False,
            "same_turn_barrier_actions": [],
            "direct_bypass_codes": [],
            "diagnostic_observations": [],
            "document_delivery_observed": False,
            "explicit_no_document_goal": False,
            "activation_evidence": activation_evidence,
            "case_archive_action_observed": False,
            "current_case_archive_action_observed": False,
            "case_archive_action_succeeded": False,
            "contract_discovery_bypass_observed": False,
            "untrusted_context_observed": untrusted_context_observed,
            "cross_source_fact_pollution_observed": False,
            "unbound_local_authority_observed": False,
            "render_observed": render_observed,
            "unauthorized_render_observed": unauthorized_render_observed,
            "managed_view_observed": managed_view_observed,
            "archive_change_after_render_observed": archive_change_after_render_observed,
            "first_archive_change_after_render_index": first_archive_change_after_render_index,
            "archive_change_after_render_kind": archive_change_after_render_kind,
            "presentation": presentation_diagnostic,
            "revision_timeline": revision_timeline,
        }
    if not matched_session:
        _add(
            violations,
            "transcript_session_missing",
            "WorkBuddy transcript JSONL 中没有目标会话的结构化记录",
            transcript_jsonl,
        )
    elif not skill_call_observed:
        _add(
            violations,
            "skill_invocation_missing",
            "未观察到结构化 Skill(arbibuddy) 调用；输出自述不能替代真实 Skill 调用",
            transcript_jsonl,
        )
    elif not skill_result_observed:
        _add(
            violations,
            "skill_invocation_result_missing",
            "Skill 调用没有关联到成功的 function_call_result",
            transcript_jsonl,
        )
    if compliance_claim_observed and violations:
        _add(
            violations,
            "unsubstantiated_compliance_claim",
            "客户端合规自述不能覆盖轨迹或产物中的硬失败",
            transcript_jsonl,
        )
    return {
        "provided": True,
        "records": records,
        "session_matched": matched_session,
        "skill_invocation_observed": skill_call_observed,
        "skill_result_observed": skill_result_observed,
        "user_round_observed": user_round_observed,
        "compliance_claim_observed": compliance_claim_observed,
        "present_files_observed": present_files_observed,
        "core_call_observed": core_call_observed,
        "core_envelope_observed": core_envelope_observed,
        "first_business_action_is_core": first_business_action_is_core,
        "barrier_count": barrier_count,
        "barrier_user_round_observed": barrier_user_round_observed,
        "barrier_bypass_observed": barrier_bypass_observed,
        "same_turn_barrier_actions": list(same_turn_barrier_actions),
        "direct_bypass_codes": sorted(direct_bypass_codes),
        "diagnostic_observations": sorted(diagnostic_observations),
        "document_delivery_observed": document_delivery_observed,
        "explicit_no_document_goal": explicit_no_document_goal,
        "activation_evidence": activation_evidence,
        "case_archive_action_observed": case_archive_action_observed,
        "current_case_archive_action_observed": current_case_archive_action_observed,
        "case_archive_action_succeeded": case_archive_action_succeeded,
        "contract_discovery_bypass_observed": contract_discovery_bypass_observed,
        "untrusted_context_observed": untrusted_context_observed,
        "cross_source_fact_pollution_observed": cross_source_fact_pollution_observed,
        "unbound_local_authority_observed": unbound_local_authority_observed,
        "render_observed": render_observed,
        "unauthorized_render_observed": unauthorized_render_observed,
        "managed_view_observed": managed_view_observed,
        "archive_change_after_render_observed": archive_change_after_render_observed,
        "first_archive_change_after_render_index": first_archive_change_after_render_index,
        "archive_change_after_render_kind": archive_change_after_render_kind,
        "presentation": presentation_diagnostic,
        "revision_timeline": revision_timeline,
        "first_barrier_obeyed": bool(
            skill_call_observed
            and skill_result_observed
            and first_business_action_is_core
            and first_core_envelope_observed
            and (waiting_envelope_index is None or waiting_turn_ended)
        ),
    }


def _skill_lifecycle(
    *,
    client_log: Path | None,
    transcript_diagnostic: dict[str, Any] | None,
    violations: list[dict[str, str]],
) -> dict[str, Any]:
    """Summarize observable host-to-model Skill loading stages without log echo."""
    activation = (
        transcript_diagnostic.get("activation_evidence", {})
        if transcript_diagnostic
        else {}
    )
    lifecycle = {
        "installed": False,
        "reload_observed": False,
        "registered": False,
        "exposed_to_model": False,
        "invoked": bool(
            transcript_diagnostic
            and transcript_diagnostic.get("skill_invocation_observed")
        ),
    }
    if transcript_diagnostic and transcript_diagnostic.get("skill_result_observed"):
        # WorkBuddy 的成功 Skill 返回已经是宿主实际完成加载并交给模型的
        # 机械证据；不再要求独立宿主日志重复标记 registered/exposed/invoked。
        lifecycle.update({
            "installed": True,
            "registered": True,
            "exposed_to_model": True,
            "invoked": True,
        })
    host_noise: list[str] = []
    if client_log is not None:
        path = Path(os.path.abspath(client_log))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            _add(
                violations,
                "invalid_client_lifecycle_log",
                "WorkBuddy 客户端生命周期日志无法读取",
                path,
            )
            lines = []
        for raw_line in lines:
            folded = raw_line.casefold()
            event = ""
            try:
                parsed = json.loads(raw_line)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                event = _transcript_text(
                    parsed.get("event") or parsed.get("kind") or parsed.get("type")
                ).replace("-", "_").replace(" ", "_")
            if event in {"skill_installed", "installed", "skill_install_observed"}:
                lifecycle["installed"] = True
            if event in {"hot_reload_completed", "reload_completed", "hotreload_completed"}:
                lifecycle["reload_observed"] = True
            if event in {"skill_registered", "registered"}:
                lifecycle["registered"] = True
            if event in {"skill_exposed", "skill_exposed_to_model", "exposed_to_model"}:
                lifecycle["exposed_to_model"] = True

            if (
                ("hotreload" in folded or "hot reload" in folded)
                and any(marker in folded for marker in ("completed", "success", "成功"))
            ):
                lifecycle["reload_observed"] = True
            if (
                ("skill" in folded or "arbibuddy" in folded)
                and "installed" in folded
            ):
                lifecycle["installed"] = True
            if (
                ("skill" in folded or "arbibuddy" in folded)
                and "registered" in folded
            ):
                lifecycle["registered"] = True
            if (
                ("skill" in folded or "arbibuddy" in folded)
                and "exposed" in folded
            ):
                lifecycle["exposed_to_model"] = True

            if "sessionrunstatemachine" in folded and "invalid transition" in folded:
                host_noise.append("session_run_state_machine_invalid_transition")
            if "fileversionstore" in folded and "enoent" in folded:
                host_noise.append("file_version_store_enoent")
            if "tencent-docx" in folded and (
                "yaml" in folded or "warning" in folded
            ):
                host_noise.append("builtin_tencent_docx_yaml_warning")

    lifecycle["host_noise"] = sorted(set(host_noise))
    lifecycle["activation_evidence"] = {
        key: bool(activation.get(key))
        for key in (
            "slash_command",
            "skill_path",
            "skill_body_injected",
            "structured_skill_call",
        )
    }
    lifecycle["case_archive_action_observed"] = bool(
        transcript_diagnostic
        and transcript_diagnostic.get("case_archive_action_observed")
    )
    lifecycle["contract_discovery_bypass_observed"] = bool(
        transcript_diagnostic
        and transcript_diagnostic.get("contract_discovery_bypass_observed")
    )
    lifecycle["current_case_archive_action_observed"] = bool(
        transcript_diagnostic
        and transcript_diagnostic.get("current_case_archive_action_observed")
    )
    lifecycle["case_archive_action_succeeded"] = bool(
        transcript_diagnostic
        and transcript_diagnostic.get("case_archive_action_succeeded")
    )
    slash_loaded = all(
        lifecycle["activation_evidence"].get(key) is True
        for key in ("slash_command", "skill_path", "skill_body_injected")
    )
    structured_loaded = bool(
        lifecycle["activation_evidence"].get("structured_skill_call")
        and transcript_diagnostic
        and transcript_diagnostic.get("skill_result_observed")
    )
    activation_observed = slash_loaded or structured_loaded
    lifecycle["loaded_observed"] = activation_observed
    direct_bypass_codes = set(
        transcript_diagnostic.get("direct_bypass_codes", ())
        if transcript_diagnostic
        else ()
    )
    if lifecycle["contract_discovery_bypass_observed"] and activation_observed:
        lifecycle["status"] = "loaded_then_contract_discovery_bypass"
    elif (
        lifecycle["case_archive_action_succeeded"]
        and activation_observed
        and direct_bypass_codes
    ):
        lifecycle["status"] = "executed_then_bypassed"
    elif lifecycle["case_archive_action_succeeded"] and activation_observed:
        lifecycle["status"] = "executed"
    elif activation_observed:
        lifecycle["status"] = "loaded_not_executed"
    elif lifecycle["installed"]:
        lifecycle["status"] = "not_invoked"
    else:
        lifecycle["status"] = "not_discovered"
    missing = [
        key
        for key in (
            "installed",
            "reload_observed",
            "registered",
            "exposed_to_model",
            "invoked",
        )
        if not lifecycle[key]
    ]
    lifecycle["fail_closed"] = bool(missing)
    lifecycle["missing_stages"] = missing
    lifecycle["next_action"] = (
        "重载、重启或重新导入 WorkBuddy Skill 后重新验证生命周期证据；缺少完整链路前不得计入真实 Journey。"
        if missing
        else "允许按 Skill 公开工具继续。"
    )
    return lifecycle


def _workspace_files(workspace: Path):
    for path in workspace.rglob("*"):
        try:
            relative = path.relative_to(workspace)
        except ValueError:
            continue
        if ".git" in relative.parts:
            continue
        if path.is_file():
            yield path


def _looks_like_unmanaged_text_delivery(path: Path, case_dir: Path) -> bool:
    suffix = path.suffix.casefold()
    if suffix not in {".md", ".pdf"}:
        return False
    if path.resolve() == (case_dir / "案情档案.md").resolve():
        return False
    folded = path.name.casefold()
    delivery_terms = ("申请书", "通知书", "证据", "文书", "仲裁", "delivery")
    return any(term in folded for term in delivery_terms)


def _valid_docx_package(path: Path) -> bool:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except (BadZipFile, OSError):
        return False
    return REQUIRED_DOCX_PARTS <= names and any(
        name.startswith("word/footer") and name.endswith(".xml") for name in names
    )


def _direct_name(value: object) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ValueError("产物文件名无效")
    return value


def _digest(path: Path) -> str:
    return sha256(path_for_io(path).read_bytes()).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _add(
    violations: list[dict[str, str]],
    code: str,
    message: str,
    path: Path,
) -> None:
    item = {"code": code, "message": message, "path": str(path)}
    if item not in violations:
        violations.append(item)


def _report(
    workspace: Path,
    case_id: str,
    expected_deliveries: tuple[str, ...],
    violations: list[dict[str, str]],
    *,
    presentation_files: tuple[dict[str, Any], ...] = (),
    managed_view: dict[str, Any] | None = None,
    memory_policy: dict[str, Any] | None = None,
    transcript_diagnostic: dict[str, Any] | None = None,
    skill_lifecycle: dict[str, Any] | None = None,
    root_causes: tuple[dict[str, Any], ...] = (),
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        violations, key=lambda item: (item["code"], item["path"], item["message"])
    )
    artifact_codes = {
        "case_file_missing",
        "invalid_case_file",
        "invalid_delivery_plan",
        "missing_delivery_plan",
        "missing_expected_deliveries",
        "expected_delivery_plan_mismatch",
        "missing_hidden_delivery",
        "incomplete_hidden_delivery",
        "missing_visible_delivery",
        "invalid_visible_delivery",
        "unmanaged_html_delivery",
        "unmanaged_markdown_delivery",
        "unmanaged_pdf_delivery",
        "unmanaged_docx",
        "invalid_docx_package",
        "docx_path_escape",
        "presentation_not_in_state_envelope",
        "invalid_model_delivery",
        "stale_managed_delivery",
        "incomplete_canonical_delivery",
        "canonical_delivery_invalid",
        "canonical_delivery_state_mismatch",
        "persistent_runtime_request_copy",
    }
    bypass_codes = {
        "prohibited_present_files",
        "prohibited_document_command",
        "prohibited_generation_script",
        "prohibited_bottom_level_entry",
        "prohibited_runtime_dependency_install",
        "prohibited_runtime_environment_creation",
        "prohibited_temporary_script",
        "prohibited_runtime_source_inspection",
        "runtime_skill_mutation",
        "split_managed_presentation",
        "extra_unmanaged_presentation",
        "incomplete_managed_presentation",
        "amount_calculation_bypass",
        "cross_workspace_case_access",
        "destructive_case_workspace_command",
        "skill_not_in_execution_chain",
        "skill_invoked_but_bypassed",
        "loaded_then_contract_discovery_bypass",
        "prohibited_case_archive_transport",
        "duplicate_managed_presentation",
        "render_evidence_missing",
    }
    transcript_resolved = (
        transcript_path.resolve() if transcript_path is not None else None
    )
    artifact_violations = [
        item
        for item in ordered
        if item["code"] in artifact_codes
        and not (
            item["code"] == "unmanaged_docx"
            and transcript_resolved is not None
            and Path(item["path"]).resolve() == transcript_resolved
        )
    ]
    bypass_violations = [
        item for item in ordered if item["code"] in bypass_codes
    ]
    return {
        "schema_version": 2,
        "workspace": str(workspace),
        "case_id": case_id,
        "expected_deliveries": list(expected_deliveries),

        "presentation_count": len(presentation_files),
        "presentation_files": list(presentation_files),
        "memory_policy": memory_policy or _memory_policy_summary(),
        "skill_lifecycle": skill_lifecycle or _skill_lifecycle(
            client_log=None,
            transcript_diagnostic=transcript_diagnostic,
            violations=[],
        ),
        "root_causes": list(root_causes),
        "delivery_validity": {
            "passed": not artifact_violations,
            "violations": artifact_violations,
            "presentation_files": list(presentation_files),
        },
        "agent_bypass": {
            "observed": bool(bypass_violations),
            "violations": bypass_violations,
            "root_causes": list(root_causes),
        },
        "transcript_diagnostic": transcript_diagnostic or {
            "provided": False,
            "records": 0,
            "session_matched": False,
            "skill_invocation_observed": False,
            "skill_result_observed": False,
            "user_round_observed": False,
            "compliance_claim_observed": False,
            "present_files_observed": False,
            "core_call_observed": False,
            "core_envelope_observed": False,
            "first_business_action_is_core": False,
            "first_barrier_obeyed": False,
            "direct_bypass_codes": [],
            "activation_evidence": {
                "slash_command": False,
                "skill_path": False,
                "skill_body_injected": False,
                "structured_skill_call": False,
            },
            "case_archive_action_observed": False,
            "contract_discovery_bypass_observed": False,
            "untrusted_context_observed": False,
            "cross_source_fact_pollution_observed": False,
            "unbound_local_authority_observed": False,
            "render_observed": False,
            "unauthorized_render_observed": False,
            "managed_view_observed": False,
            "archive_change_after_render_observed": False,
        },
        "managed_delivery_view": managed_view or {
            "delivery_state": "unavailable",
            "stopped": True,
            "presentation_files": [],
            "memory_policy": _memory_policy_summary(),
        },
        "passed": not ordered,
        "violations": ordered,
    }
