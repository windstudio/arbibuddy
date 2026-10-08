"""面向 Codex 与 Claude Code 的模型主导真实 Agent Eval。

该模块只负责平台 CLI 会话、ScenarioResponder 编排、隔离安装、自然语言轮次和文件副作用观察。
它不读取核心内部状态，也不把模型 B 用作判定；最终结果只由确定性 Oracle 判断。
"""

from __future__ import annotations

import ctypes
from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
from time import monotonic, sleep
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.platform_adapters.service import (
    PlatformAdapterError,
    install_skill,
    platform_process_environment,
    probe_capabilities,
    verify_install,
    verify_platform_discovery,
)
from scripts.platform_paths import path_for_io, public_path
from scripts.runtime_context import build_case_archive_invocation
from .artifact_oracle import inspect_public_artifacts
from .scenario_responder import (
    CodexSdkScenarioResponder,
    ReplayScenarioResponder,
    RuleScenarioResponder,
    ScenarioFactOption,
    ScenarioResponseDecision,
    ScenarioResponseError,
    ScenarioResponseLedger,
    ScenarioResponseRequest,
    ScenarioResponder,
    ScenarioResponderRuntimeError,
)
from .scenario_contract import (
    ResponderIdentity,
    ScenarioAttempt,
    ScenarioInteraction,
    ScenarioResponseResult,
    scenario_request_sha256,
)
from .restart_context import RestartContextBuilder
from .journey_oracle import (
    JourneyOracle as _AuthoritativeJourneyOracle,
    _artifacts_satisfied,
)


SUPPORTED_PLATFORMS = ("codex", "claude-code")
DEFAULT_TIMEOUT_PER_TURN_SECONDS = 1200
DEFAULT_SIMULATOR_TIMEOUT_SECONDS = 300
_ALLOWED_ARTIFACTS = frozenset({"case_archive", "docx", "checklist"})
_PUBLIC_DOCUMENT_TYPES = frozenset(
    {
        "employment_obligation_demand_letter",
        "forced_termination_notice",
        "arbitration_application",
        "arbitration_defense",
        "evidence_catalog",
        "company_deregistration_restriction_request_shanghai",
        "property_preservation_application",
        "enforcement_application",
    }
)


def _current_shanghai_date() -> date:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except ZoneInfoNotFoundError:
        # Windows 可能没有 IANA 时区数据库；上海当前使用 UTC+08。
        return datetime.now(timezone(timedelta(hours=8))).date()
_FORBIDDEN_PROMPT_MARKERS = ("$arbibuddy", "agent_task", "PublicTurn")


class JourneyError(ValueError):
    """模型主导 Journey 不符合公开 Eval Schema。"""


class RuntimeAdapterError(RuntimeError):
    """平台 CLI 会话无法启动、继续或解析。"""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class JourneyFact:
    id: str
    text: str
    response_markers: tuple[str, ...] = ()
    max_uses: int = 1
    required_markers: tuple[str, ...] = ()
    exclude_markers: tuple[str, ...] = ()
    requires_sent_facts: tuple[str, ...] = ()
    immediately_after: str | None = None


@dataclass(frozen=True)
class JourneySimulator:
    persona: str = "虚构劳动争议场景中的当事人"
    objective: str = "如实回答模型当前问题，推动案情收集"
    max_facts_per_turn: int = 1
    unavailable_message: str = "这个具体信息我现在说不准，得核对材料；先标成待核项，其他已知部分可以继续。"
    max_unavailable_uses: int | None = 1
    confirmation_fact_id: str | None = None
    confirmation_prerequisite_fact_ids: tuple[str, ...] = ()
    confirmation_request_markers: tuple[str, ...] = ()
    confirmation_conflict_markers: tuple[str, ...] = ()
    confirmation_conflict_fact_ids: tuple[str, ...] = ()
    confirmation_summary_required_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    after_restart_fact_id: str | None = None
    prompt_version: str = "scenario-responder-prompt-v5"
    schema_version: str = "scenario-responder-schema-v3"


@dataclass(frozen=True)
class JourneyMilestone:
    id: str
    response_markers: tuple[str, ...] = ()
    response_all: tuple[str, ...] = ()
    archive_markers: tuple[str, ...] = ()
    after_restart: bool = False


@dataclass(frozen=True)
class JourneyRestart:
    after_milestone: str
    message: str


@dataclass(frozen=True)
class JourneyCompletionPath:
    """一个可公开证明的终态路径。"""

    id: str
    requires_sent_facts: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    required_deliveries: tuple["DeliveryRequirement", ...] = ()
    required_response_groups: tuple[str, ...] = ()
    required_response_groups_after_trigger: tuple[str, ...] = ()
    requires_capabilities_unavailable: tuple[str, ...] = ()
    forbids_artifacts: tuple[str, ...] = ()
    required_milestones: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeliveryRequirement:
    document_type: str
    mode: str
    require_case_archive: bool
    require_checklist: bool
    require_same_case_id: bool
    require_current_archive_revision: bool


@dataclass(frozen=True)
class Journey:
    schema_version: int
    id: str
    title: str
    initial_user_message: str
    fact_pool: tuple[JourneyFact, ...]
    milestones: tuple[JourneyMilestone, ...]
    restart: JourneyRestart | None
    required_artifacts: tuple[str, ...]
    max_turns: int
    required_deliveries: tuple[DeliveryRequirement, ...] = ()
    min_turns: int = 1
    degradation_markers: tuple[str, ...] = ()
    allow_degraded_delivery: bool = False
    allow_extra_deliveries: bool = False
    response_markers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    completion_paths: tuple[JourneyCompletionPath, ...] = ()
    simulator: JourneySimulator = field(default_factory=JourneySimulator)
    initial_fact_ids: tuple[str, ...] = ()
    scenario_as_of_date: str | None = None
    resolved_dates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class JourneyProgress:
    journey_id: str
    platform: str
    turn: int
    phase: str
    elapsed_seconds: float


@dataclass(frozen=True)
class JourneyResult:
    journey_id: str
    platform: str
    passed: bool
    failure_category: str | None
    failure_message: str
    turns: int
    duration_seconds: float
    observation: dict[str, Any]
    evidence_path: Path


@dataclass(frozen=True)
class JourneyPublicState:
    """Oracle 可见的 Journey 状态；不包含隐藏断言或模型 B prompt。"""

    journey: Journey
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    transcripts: tuple[Mapping[str, Any], ...] = ()
    sent_fact_ids: tuple[str, ...] = ()
    observed_milestones: frozenset[str] = frozenset()
    restarted: bool = False
    restart_context: Mapping[str, Any] | None = None
    observation: Mapping[str, Any] = field(default_factory=dict)
    restart_observation: Mapping[str, Any] | None = None
    runtime_completed: bool = True


@dataclass(frozen=True)
class JourneyAssessment:
    passed: bool
    completion_path: str | None = None
    failure_category: str | None = None
    failure_code: str | None = None
    message: str = ""
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "completion_path": self.completion_path,
            "failure_category": self.failure_category,
            "failure_code": self.failure_code,
            "message": self.message,
            "missing": list(self.missing),
        }


JourneyOracle = _AuthoritativeJourneyOracle


@dataclass(frozen=True)
class _AgentResponse:
    text: str
    session_id: str | None
    events: tuple[dict[str, Any], ...]
    command: tuple[str, ...]
    duration_seconds: float


class CliAgentRuntime:
    """用平台原生非交互 CLI 驱动一条自然语言会话。"""

    def __init__(
        self,
        *,
        platform: str,
        command: list[str],
        timeout_seconds: int = 300,
        model: str | None = None,
        runtime_context: Mapping[str, Any] | None = None,
    ) -> None:
        if platform not in SUPPORTED_PLATFORMS:
            raise RuntimeAdapterError(f"不支持的 Agent 平台：{platform}")
        if not command:
            raise RuntimeAdapterError("平台 Agent 命令不能为空")
        self.platform = platform
        self.command = tuple(_resolve_command(command))
        self.timeout_seconds = timeout_seconds
        self.model = (
            model
            if model is not None
            else ("sonnet" if platform == "claude-code" else None)
        )
        self.session_id: str | None = None
        self._runtime_context = _copy_runtime_context(runtime_context)
        self._context_delivery = "none"

    @property
    def context_delivery(self) -> str:
        return self._context_delivery

    def set_runtime_context(self, context: Mapping[str, Any]) -> None:
        """设置首次会话使用的私有运行时上下文，不改变用户消息。"""

        self._runtime_context = _copy_runtime_context(context)

    def turn(self, *, workspace: Path, message: str) -> _AgentResponse:
        if not isinstance(message, str) or not message.strip():
            raise RuntimeAdapterError("Agent 用户消息必须是非空自然语言")
        if any(marker in message for marker in _FORBIDDEN_PROMPT_MARKERS):
            raise RuntimeAdapterError("真实 Journey 用户消息不得注入内部协议或 Skill 命令")
        workspace = _require_runtime_workspace(workspace)
        working_directory_before = _runtime_workspace_state(
            workspace, stage="before_cli_call"
        )

        if self.session_id is None:
            self._prepare_runtime_context(workspace)
            command = self._start_command(workspace, message)
        else:
            command = self._resume_command(workspace, message)
        started = monotonic()
        try:
            result = subprocess.run(
                list(command),
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                env=_runtime_process_environment(self.platform),
            )
        except subprocess.TimeoutExpired as error:
            recovered = _recover_completed_timeout_response(
                command=command,
                error=error,
                duration_seconds=monotonic() - started,
            )
            if recovered is not None:
                self.session_id = recovered.session_id
                return recovered
            raise RuntimeAdapterError(
                "平台 Agent 轮次超时",
                details=_timeout_diagnostic(
                    command=command,
                    error=error,
                    timeout_seconds=self.timeout_seconds,
                    workspace=workspace,
                    working_directory_before=working_directory_before,
                ),
            ) from error
        except OSError as error:
            raise RuntimeAdapterError(
                f"平台 Agent 命令无法执行：{error.__class__.__name__}"
            ) from error
        duration = monotonic() - started
        events = _parse_json_lines(result.stdout)
        installed_root = self._runtime_context.get("installed_skill_root")
        foreign_skill_roots = (
            _foreign_skill_resource_hashes(events, installed_root)
            if self.platform == "codex" and isinstance(installed_root, str)
            else ()
        )
        if foreign_skill_roots:
            raise RuntimeAdapterError(
                "模型读取了本会话安装副本之外的同名 Skill 资源",
                details={
                    "kind": "skill_source_mismatch",
                    "foreign_skill_root_hashes": list(foreign_skill_roots),
                },
            )
        session_id = _session_id(events)
        if session_id is not None:
            self.session_id = session_id
        text = _response_text(events, result.stdout, platform=self.platform)
        if result.returncode != 0:
            detail = (result.stderr or text).strip().splitlines()
            suffix = f"：{detail[-1][:240]}" if detail else ""
            raise RuntimeAdapterError(
                f"平台 Agent 返回非零退出码 {result.returncode}{suffix}"
            )
        if not text.strip():
            raise RuntimeAdapterError("平台 Agent 未返回可观察的自然语言回复")
        if self.session_id is None:
            raise RuntimeAdapterError("平台 Agent 响应缺少可继续会话标识")
        return _AgentResponse(
            text=text,
            session_id=self.session_id,
            events=tuple(events),
            command=tuple(command),
            duration_seconds=duration,
        )

    def probe_version(self, *, workspace: Path) -> str:
        """在真实会话前识别平台 CLI 版本。"""

        workspace = _require_runtime_workspace(workspace)
        command = [*self.command, "--version"]
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(self.timeout_seconds, 30),
                check=False,
                env=_runtime_process_environment(self.platform),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeAdapterError(
                f"平台版本识别失败：{error.__class__.__name__}"
            ) from error
        value = (result.stdout or result.stderr).strip().splitlines()
        if result.returncode != 0 or not value:
            raise RuntimeAdapterError("平台版本识别未返回有效版本")
        return value[0]

    def close(self) -> None:
        self.session_id = None

    def _prepare_runtime_context(self, workspace: Path) -> None:
        if not self._runtime_context:
            return
        declared_workspace = self._runtime_context.get("workspace_root")
        if isinstance(declared_workspace, str) and declared_workspace.strip():
            if Path(declared_workspace).expanduser().resolve() != workspace.resolve():
                raise RuntimeAdapterError("运行时上下文与 Agent 工作区不一致")
        if self.platform == "codex":
            _write_codex_runtime_context(workspace, self._runtime_context)
            self._context_delivery = "codex-project-agents"
        else:
            self._context_delivery = "claude-append-system-prompt"

    def _start_command(self, workspace: Path, message: str) -> list[str]:
        if self.platform == "codex":
            model_args = ["--model", self.model] if self.model else []
            return [
                *self.command,
                "exec",
                "--json",
                *model_args,
                "--approve-for-me",
                "--skip-git-repo-check",
                "-C",
                str(workspace),
                message,
            ]
        return [
            *self.command,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "text",
            "--permission-mode",
            "auto",
            "--allowed-tools",
            "Skill",
            "Read",
            "Bash(python -B -X utf8 -m scripts.case_archive.cli *)",
            "Bash(python -B -X utf8 -m scripts.amount_calculator.public *)",
            "Bash(python -B -X utf8 -m scripts.documents.public *)",
            *(["--model", self.model] if self.model else []),
            "--add-dir",
            str(workspace),
            "--session-id",
            str(uuid4()),
            *(
                ["--append-system-prompt", _render_runtime_context(self._runtime_context)]
                if self._runtime_context
                else []
            ),
            message,
        ]

    def _resume_command(self, workspace: Path, message: str) -> list[str]:
        assert self.session_id is not None
        if self.platform == "codex":
            model_args = ["--model", self.model] if self.model else []
            return [
                *self.command,
                "exec",
                "--approve-for-me",
                "-C",
                str(workspace.resolve()),
                "resume",
                self.session_id,
                "--json",
                *model_args,
                "--skip-git-repo-check",
                message,
            ]
        return [
            *self.command,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "text",
            "--permission-mode",
            "auto",
            "--allowed-tools",
            "Skill",
            "Read",
            "Bash(python -B -X utf8 -m scripts.case_archive.cli *)",
            "Bash(python -B -X utf8 -m scripts.amount_calculator.public *)",
            "Bash(python -B -X utf8 -m scripts.documents.public *)",
            *(["--model", self.model] if self.model else []),
            "--resume",
            self.session_id,
            message,
        ]


def _relative_calendar_values(config: object, as_of_date: date) -> dict[str, str]:
    if not isinstance(config, Mapping) or set(config) != {
        "departure_months_ago", "wage_start_months_ago", "last_demand_months_ago"
    }:
        raise JourneyError("relative_calendar 必须声明离职、工资起月和最后催讨月")
    offsets = list(config.values())
    if any(not isinstance(value, int) or isinstance(value, bool) for value in offsets):
        raise JourneyError("relative_calendar 的月份偏移必须是整数")
    departure_offset = config["departure_months_ago"]
    wage_offset = config["wage_start_months_ago"]
    demand_offset = config["last_demand_months_ago"]
    if not (2 <= departure_offset <= 6 and departure_offset < wage_offset <= departure_offset + 3
            and 0 <= demand_offset < departure_offset):
        raise JourneyError("relative_calendar 日期顺序或一年内离职约束无效")

    def month_ago(offset: int) -> tuple[int, int]:
        month_index = as_of_date.year * 12 + as_of_date.month - 1 - offset
        return divmod(month_index, 12)[0], divmod(month_index, 12)[1] + 1

    departure_year, departure_month = month_ago(departure_offset)
    wage_year, wage_month = month_ago(wage_offset)
    demand_year, demand_month = month_ago(demand_offset)
    departure = date(departure_year, departure_month, monthrange(departure_year, departure_month)[1])
    demand = date(demand_year, demand_month, min(15, monthrange(demand_year, demand_month)[1]))
    wage_period = (
        f"{wage_year} 年 {wage_month} 月到 {departure_month} 月"
        if wage_year == departure_year else
        f"{wage_year} 年 {wage_month} 月到 {departure_year} 年 {departure_month} 月"
    )
    wage_period_short = (
        f"{wage_month} 至 {departure_month} 月"
        if wage_year == departure_year else wage_period
    )
    return {
        "departure_date": f"{departure.year} 年 {departure.month} 月 {departure.day} 日",
        "wage_period": wage_period,
        "wage_period_short": wage_period_short,
        "last_demand_date": f"{demand.year} 年 {demand.month} 月 {demand.day} 日",
    }


def load_journey(path: str | Path, *, as_of_date: date | None = None) -> Journey:
    source = Path(path)
    try:
        import yaml
    except ImportError as error:
        raise JourneyError("读取模型主导 Journey 需要 PyYAML") from error
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise JourneyError(f"Journey 无法读取：{source}") from error
    if not isinstance(value, Mapping):
        raise JourneyError("Journey 顶层必须是对象")
    allowed = {
        "schema_version",
        "id",
        "title",
        "initial_user_message",
        "initial_fact_ids",
        "simulator",
        "fact_pool",
        "milestones",
        "restart",
        "expect",
        "relative_calendar",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise JourneyError(f"Journey 存在未知字段：{', '.join(unknown)}")
    if value.get("schema_version") != 2:
        raise JourneyError("Journey schema_version 必须是 2")
    journey_id = _required_text(value, "id")
    title = _required_text(value, "title")
    initial = _required_text(value, "initial_user_message")
    calendar_date = as_of_date or _current_shanghai_date()
    resolved_dates = (
        _relative_calendar_values(value["relative_calendar"], calendar_date)
        if "relative_calendar" in value else {}
    )
    for token, replacement in resolved_dates.items():
        initial = initial.replace("{" + token + "}", replacement)
    raw_simulator = value.get("simulator", {})
    if not isinstance(raw_simulator, Mapping):
        raise JourneyError("simulator 必须是对象")
    simulator_unknown = sorted(
        set(raw_simulator)
        - {
            "persona",
            "objective",
            "max_facts_per_turn",
            "unavailable_message",
            "max_unavailable_uses",
            "confirmation_fact_id",
            "confirmation_prerequisite_fact_ids",
            "confirmation_request_markers",
            "confirmation_conflict_markers",
            "confirmation_conflict_fact_ids",
            "confirmation_summary_required_groups",
            "after_restart_fact_id",
            "prompt_version",
            "schema_version",
        }
    )
    if simulator_unknown:
        raise JourneyError(
            "simulator 存在未知字段："
            + ", ".join(str(item) for item in simulator_unknown)
        )
    simulator_values = {
        "persona": raw_simulator.get(
            "persona", JourneySimulator.persona
        ),
        "objective": raw_simulator.get(
            "objective", JourneySimulator.objective
        ),
        "max_facts_per_turn": raw_simulator.get(
            "max_facts_per_turn", JourneySimulator.max_facts_per_turn
        ),
        "unavailable_message": raw_simulator.get(
            "unavailable_message", JourneySimulator.unavailable_message
        ),
        "max_unavailable_uses": raw_simulator.get(
            "max_unavailable_uses", JourneySimulator.max_unavailable_uses
        ),
        "confirmation_fact_id": raw_simulator.get(
            "confirmation_fact_id", JourneySimulator.confirmation_fact_id
        ),
        "confirmation_prerequisite_fact_ids": raw_simulator.get(
            "confirmation_prerequisite_fact_ids",
            list(JourneySimulator.confirmation_prerequisite_fact_ids),
        ),
        "confirmation_request_markers": raw_simulator.get(
            "confirmation_request_markers",
            list(JourneySimulator.confirmation_request_markers),
        ),
        "confirmation_conflict_markers": raw_simulator.get(
            "confirmation_conflict_markers",
            list(JourneySimulator.confirmation_conflict_markers),
        ),
        "confirmation_conflict_fact_ids": raw_simulator.get(
            "confirmation_conflict_fact_ids",
            list(JourneySimulator.confirmation_conflict_fact_ids),
        ),
        "confirmation_summary_required_groups": raw_simulator.get(
            "confirmation_summary_required_groups", {}
        ),
        "prompt_version": raw_simulator.get(
            "prompt_version", JourneySimulator.prompt_version
        ),
        "schema_version": raw_simulator.get(
            "schema_version", JourneySimulator.schema_version
        ),
        "after_restart_fact_id": raw_simulator.get("after_restart_fact_id"),
    }
    for key in ("persona", "objective", "unavailable_message", "prompt_version", "schema_version"):
        if not isinstance(simulator_values[key], str) or not simulator_values[key].strip():
            raise JourneyError(f"simulator.{key} 必须是非空字符串")
    if (not isinstance(simulator_values["max_facts_per_turn"], int)
            or isinstance(simulator_values["max_facts_per_turn"], bool)
            or simulator_values["max_facts_per_turn"] < 1):
        raise JourneyError("simulator.max_facts_per_turn 数值无效")
    unavailable_limit = simulator_values["max_unavailable_uses"]
    if unavailable_limit is not None and (
        not isinstance(unavailable_limit, int)
        or isinstance(unavailable_limit, bool)
        or unavailable_limit < 0
    ):
        raise JourneyError("simulator.max_unavailable_uses 必须是非负整数或 null")
    confirmation_fact_id = simulator_values["confirmation_fact_id"]
    if confirmation_fact_id is not None and (
        not isinstance(confirmation_fact_id, str) or not confirmation_fact_id.strip()
    ):
        raise JourneyError("simulator.confirmation_fact_id 必须是非空字符串或 null")
    raw_confirmation_prerequisites = simulator_values[
        "confirmation_prerequisite_fact_ids"
    ]
    if not isinstance(raw_confirmation_prerequisites, list) or any(
        not isinstance(fact_id, str) or not fact_id.strip()
        for fact_id in raw_confirmation_prerequisites
    ):
        raise JourneyError(
            "simulator.confirmation_prerequisite_fact_ids 必须是非空字符串列表"
        )
    confirmation_prerequisites = tuple(
        fact_id.strip() for fact_id in raw_confirmation_prerequisites
    )
    raw_confirmation_request_markers = simulator_values[
        "confirmation_request_markers"
    ]
    raw_confirmation_conflict_markers = simulator_values[
        "confirmation_conflict_markers"
    ]
    for key, markers in (
        ("confirmation_request_markers", raw_confirmation_request_markers),
        ("confirmation_conflict_markers", raw_confirmation_conflict_markers),
    ):
        if not isinstance(markers, list) or any(
            not isinstance(marker, str) or not marker.strip() for marker in markers
        ):
            raise JourneyError(f"simulator.{key} 必须是非空字符串列表")
    confirmation_request_markers = tuple(
        marker.strip() for marker in raw_confirmation_request_markers
    )
    confirmation_conflict_markers = tuple(
        marker.strip() for marker in raw_confirmation_conflict_markers
    )
    raw_confirmation_conflict_fact_ids = simulator_values[
        "confirmation_conflict_fact_ids"
    ]
    if not isinstance(raw_confirmation_conflict_fact_ids, list) or any(
        not isinstance(fact_id, str) or not fact_id.strip()
        for fact_id in raw_confirmation_conflict_fact_ids
    ):
        raise JourneyError(
            "simulator.confirmation_conflict_fact_ids 必须是非空字符串列表"
        )
    confirmation_conflict_fact_ids = tuple(
        fact_id.strip() for fact_id in raw_confirmation_conflict_fact_ids
    )
    raw_confirmation_summary_groups = simulator_values[
        "confirmation_summary_required_groups"
    ]
    if not isinstance(raw_confirmation_summary_groups, Mapping):
        raise JourneyError(
            "simulator.confirmation_summary_required_groups 必须是对象"
        )
    confirmation_summary_required_groups: list[tuple[str, tuple[str, ...]]] = []
    for group_id, raw_markers in raw_confirmation_summary_groups.items():
        if not isinstance(group_id, str) or not group_id.strip():
            raise JourneyError(
                "simulator.confirmation_summary_required_groups 的分组名称必须是非空字符串"
            )
        if not isinstance(raw_markers, list) or not raw_markers or any(
            not isinstance(marker, str) or not marker.strip()
            for marker in raw_markers
        ):
            raise JourneyError(
                "simulator.confirmation_summary_required_groups 每组必须是非空字符串列表"
            )
        markers = tuple(marker.strip() for marker in raw_markers)
        if len(set(markers)) != len(markers):
            raise JourneyError(
                "simulator.confirmation_summary_required_groups 组内标记不得重复"
            )
        confirmation_summary_required_groups.append((group_id.strip(), markers))
    if len(set(confirmation_prerequisites)) != len(confirmation_prerequisites):
        raise JourneyError(
            "simulator.confirmation_prerequisite_fact_ids 不得重复"
        )
    if len(set(confirmation_conflict_fact_ids)) != len(confirmation_conflict_fact_ids):
        raise JourneyError(
            "simulator.confirmation_conflict_fact_ids 不得重复"
        )
    if confirmation_fact_id is None and confirmation_prerequisites:
        raise JourneyError(
            "设置 simulator.confirmation_prerequisite_fact_ids 时必须配置 confirmation_fact_id"
        )
    if confirmation_fact_id is None and confirmation_summary_required_groups:
        raise JourneyError(
            "设置 simulator.confirmation_summary_required_groups 时必须配置 confirmation_fact_id"
        )
    if confirmation_fact_id is None and (
        confirmation_request_markers or confirmation_conflict_markers
    ):
        raise JourneyError(
            "配置确认标记时必须配置 simulator.confirmation_fact_id"
        )
    if (
        len(set(confirmation_request_markers))
        != len(confirmation_request_markers)
        or len(set(confirmation_conflict_markers))
        != len(confirmation_conflict_markers)
    ):
        raise JourneyError("simulator 确认标记列表不得重复")
    simulator = JourneySimulator(
        persona=simulator_values["persona"].strip(),
        objective=simulator_values["objective"].strip(),
        max_facts_per_turn=simulator_values["max_facts_per_turn"],
        unavailable_message=simulator_values["unavailable_message"].strip(),
        max_unavailable_uses=simulator_values["max_unavailable_uses"],
        confirmation_fact_id=(
            confirmation_fact_id.strip()
            if isinstance(confirmation_fact_id, str)
            else None
        ),
        confirmation_prerequisite_fact_ids=confirmation_prerequisites,
        confirmation_request_markers=confirmation_request_markers,
        confirmation_conflict_markers=confirmation_conflict_markers,
        confirmation_conflict_fact_ids=confirmation_conflict_fact_ids,
        confirmation_summary_required_groups=tuple(
            confirmation_summary_required_groups
        ),
        prompt_version=simulator_values["prompt_version"].strip(),
        schema_version=simulator_values["schema_version"].strip(),
        after_restart_fact_id=simulator_values["after_restart_fact_id"],
    )
    raw_fact_pool = value.get("fact_pool", [])
    if not isinstance(raw_fact_pool, list):
        raise JourneyError("fact_pool 必须是列表")
    facts: list[JourneyFact] = []
    fact_ids: set[str] = set()
    for index, item in enumerate(raw_fact_pool, start=1):
        if not isinstance(item, Mapping):
            raise JourneyError(f"fact_pool[{index}] 必须是对象")
        unknown_fields = sorted(
            set(item)
            - {
                "id",
                "message",
                "legacy",
                "max_uses",
                "requires_sent_facts",
                "immediately_after",
            }
        )
        if unknown_fields:
            raise JourneyError(
                f"fact_pool[{index}] 存在未知字段：{', '.join(unknown_fields)}"
            )
        fact_id = _required_text(item, "id")
        if fact_id in fact_ids:
            raise JourneyError(f"fact_pool[{index}].id 不得重复：{fact_id}")
        fact_ids.add(fact_id)
        message = _required_text(item, "message")
        for token, replacement in resolved_dates.items():
            message = message.replace("{" + token + "}", replacement)
        raw_requires = item.get("requires_sent_facts", [])
        if not isinstance(raw_requires, list) or any(
            not isinstance(fact_id, str) or not fact_id.strip() for fact_id in raw_requires
        ) or len(set(raw_requires)) != len(raw_requires):
            raise JourneyError(f"fact_pool[{index}].requires_sent_facts 必须是唯一事实 ID 列表")
        immediately_after = item.get("immediately_after")
        if immediately_after is not None and (
            not isinstance(immediately_after, str) or not immediately_after.strip()
            or immediately_after not in raw_requires
        ):
            raise JourneyError(f"fact_pool[{index}].immediately_after 必须引用本卡前置事实")
        legacy = item.get("legacy", {})
        if not isinstance(legacy, Mapping):
            raise JourneyError(f"fact_pool[{index}].legacy 必须是对象")
        legacy_unknown = sorted(
            set(legacy) - {"when_any", "when_all", "exclude_any"}
        )
        if legacy_unknown:
            raise JourneyError(
                f"fact_pool[{index}].legacy 存在未知字段："
                + ", ".join(str(item) for item in legacy_unknown)
            )
        markers = legacy.get("when_any", [])
        if not isinstance(markers, list) or any(
            not isinstance(marker, str) or not marker.strip() for marker in markers
        ):
            raise JourneyError(f"fact_pool[{index}].legacy.when_any 必须是字符串列表")
        required_markers = legacy.get("when_all", [])
        if not isinstance(required_markers, list) or any(
            not isinstance(marker, str) or not marker.strip()
            for marker in required_markers
        ):
            raise JourneyError(
                f"fact_pool[{index}].legacy.when_all 必须是字符串列表"
            )
        exclude_markers = legacy.get("exclude_any", [])
        if not isinstance(exclude_markers, list) or any(
            not isinstance(marker, str) or not marker.strip()
            for marker in exclude_markers
        ):
            raise JourneyError(
                f"fact_pool[{index}].legacy.exclude_any 必须是字符串列表"
            )
        max_uses = item.get("max_uses", 1)
        if not isinstance(max_uses, int) or isinstance(max_uses, bool) or max_uses < 1:
            raise JourneyError(f"fact_pool[{index}].max_uses 必须是正整数")
        facts.append(
            JourneyFact(
                id=fact_id,
                text=message,
                response_markers=tuple(marker.strip() for marker in markers),
                max_uses=max_uses,
                required_markers=tuple(marker.strip() for marker in required_markers),
                exclude_markers=tuple(marker.strip() for marker in exclude_markers),
                requires_sent_facts=tuple(raw_requires),
                immediately_after=immediately_after,
            )
        )

    for fact in facts:
        if fact.id in fact.requires_sent_facts or not set(fact.requires_sent_facts).issubset(fact_ids):
            raise JourneyError(f"fact_pool.{fact.id}.requires_sent_facts 引用了无效前置事实")
    if simulator.after_restart_fact_id is not None and (
        not isinstance(simulator.after_restart_fact_id, str)
        or simulator.after_restart_fact_id not in fact_ids
        or value.get("restart") is None
    ):
        raise JourneyError("simulator.after_restart_fact_id 必须引用有重启配置的事实")

    if simulator.confirmation_fact_id is not None:
        if simulator.confirmation_fact_id not in fact_ids:
            raise JourneyError(
                "simulator.confirmation_fact_id 必须引用已定义的事实"
            )
        missing_confirmation_prerequisites = set(
            simulator.confirmation_prerequisite_fact_ids
        ) - fact_ids
        if missing_confirmation_prerequisites:
            raise JourneyError(
                "simulator.confirmation_prerequisite_fact_ids 引用未定义事实："
                + ", ".join(sorted(missing_confirmation_prerequisites))
            )
        if (
            simulator.confirmation_fact_id
            in simulator.confirmation_prerequisite_fact_ids
        ):
            raise JourneyError(
                "simulator.confirmation_fact_id 不得出现在确认前置事实中"
            )

    missing_conflict_facts = set(simulator.confirmation_conflict_fact_ids) - fact_ids
    if missing_conflict_facts:
        raise JourneyError(
            "simulator.confirmation_conflict_fact_ids 引用未定义事实："
            + ", ".join(sorted(missing_conflict_facts))
        )

    raw_initial_fact_ids = value.get("initial_fact_ids", [])
    if (
        not isinstance(raw_initial_fact_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in raw_initial_fact_ids)
    ):
        raise JourneyError("initial_fact_ids 必须是事实 ID 列表")
    initial_fact_ids = tuple(item.strip() for item in raw_initial_fact_ids)
    if len(set(initial_fact_ids)) != len(initial_fact_ids):
        raise JourneyError("initial_fact_ids 不得重复")
    if not set(initial_fact_ids).issubset(fact_ids):
        raise JourneyError("initial_fact_ids 引用未定义事实")
    if simulator.confirmation_fact_id in initial_fact_ids:
        raise JourneyError("最终确认事实不得放在开场")

    raw_milestones = value.get("milestones", [])
    if not isinstance(raw_milestones, list):
        raise JourneyError("milestones 必须是列表")
    milestones: list[JourneyMilestone] = []
    milestone_ids: set[str] = set()
    for index, item in enumerate(raw_milestones, start=1):
        if not isinstance(item, Mapping):
            raise JourneyError(f"milestones[{index}] 必须是对象")
        unknown_fields = sorted(
            set(item) - {"id", "response_any", "response_all", "archive_all", "after_restart"}
        )
        if unknown_fields:
            raise JourneyError(
                f"milestones[{index}] 存在未知字段：{', '.join(unknown_fields)}"
            )
        milestone_id = _required_text(item, "id")
        if milestone_id in milestone_ids:
            raise JourneyError(f"milestones[{index}].id 不得重复：{milestone_id}")
        milestone_ids.add(milestone_id)
        response_markers = _marker_list(item.get("response_any", []), f"milestones[{index}].response_any")
        response_all = _marker_list(item.get("response_all", []), f"milestones[{index}].response_all")
        archive_markers = _marker_list(item.get("archive_all", []), f"milestones[{index}].archive_all")
        after_restart = item.get("after_restart", False)
        if not isinstance(after_restart, bool):
            raise JourneyError(f"milestones[{index}].after_restart 必须是布尔值")
        if not response_markers and not response_all and not archive_markers:
            raise JourneyError(f"milestones[{index}] 至少需要 response_any 或 archive_all")
        milestones.append(
            JourneyMilestone(
                id=milestone_id,
                response_markers=response_markers,
                response_all=response_all,
                archive_markers=archive_markers,
                after_restart=after_restart,
            )
        )

    raw_restart = value.get("restart")
    restart: JourneyRestart | None = None
    if raw_restart is not None:
        if not isinstance(raw_restart, Mapping):
            raise JourneyError("restart 必须是对象")
        unknown_fields = sorted(set(raw_restart) - {"after_milestone", "message"})
        if unknown_fields:
            raise JourneyError(
                f"restart 存在未知字段：{', '.join(unknown_fields)}"
            )
        after_milestone = _required_text(raw_restart, "after_milestone")
        if after_milestone not in milestone_ids:
            raise JourneyError("restart.after_milestone 必须引用已声明的里程碑")
        restart = JourneyRestart(
            after_milestone=after_milestone,
            message=_required_text(raw_restart, "message"),
        )

    messages = (
        initial,
        simulator.unavailable_message,
        *(item.text for item in facts),
        *(item.message for item in (restart,) if item is not None),
    )
    if any(marker in message for message in messages for marker in _FORBIDDEN_PROMPT_MARKERS):
        raise JourneyError("Journey 用户消息不得包含内部协议或 Skill 命令")
    expect = value.get("expect")
    if not isinstance(expect, Mapping):
        raise JourneyError("Journey.expect 必须是对象")
    expect_unknown = sorted(
        set(expect)
        - {
            "required_artifacts",
            "required_deliveries",
            "max_turns",
            "min_turns",
            "degradation_markers",
            "allow_degraded_delivery",
            "allow_extra_deliveries",
            "response_markers",
            "completion_paths",
        }
    )
    if expect_unknown:
        raise JourneyError(f"Journey.expect 存在未知字段：{', '.join(expect_unknown)}")
    artifacts = expect.get("required_artifacts", [])
    if not isinstance(artifacts, list) or not all(
        isinstance(item, str) and item in _ALLOWED_ARTIFACTS for item in artifacts
    ):
        raise JourneyError("required_artifacts 只能包含公开文件产物类型")
    raw_deliveries = expect.get("required_deliveries", [])
    if not isinstance(raw_deliveries, list):
        raise JourneyError("required_deliveries 必须是对象列表")
    required_deliveries: list[DeliveryRequirement] = []
    delivery_fields = {
        "document_type",
        "mode",
        "require_case_archive",
        "require_checklist",
        "require_same_case_id",
        "require_current_archive_revision",
    }
    for index, item in enumerate(raw_deliveries, start=1):
        if not isinstance(item, Mapping):
            raise JourneyError(f"required_deliveries[{index}] 必须是对象")
        unknown_delivery_fields = sorted(set(item) - delivery_fields)
        if unknown_delivery_fields:
            raise JourneyError(
                f"required_deliveries[{index}] 存在未知字段：{', '.join(unknown_delivery_fields)}"
            )
        document_type = item.get("document_type")
        if document_type not in _PUBLIC_DOCUMENT_TYPES:
            raise JourneyError(
                f"required_deliveries[{index}].document_type 不是受支持的公开文书类型"
            )
        mode = item.get("mode")
        if mode not in {"candidate", "external_final"}:
            raise JourneyError(
                f"required_deliveries[{index}].mode 必须是 candidate 或 external_final"
            )
        delivery_flags: dict[str, bool] = {}
        for flag in (
            "require_case_archive",
            "require_checklist",
            "require_same_case_id",
            "require_current_archive_revision",
        ):
            flag_value = item.get(flag, True)
            if not isinstance(flag_value, bool):
                raise JourneyError(f"required_deliveries[{index}].{flag} 必须是布尔值")
            delivery_flags[flag] = flag_value
        required_deliveries.append(
            DeliveryRequirement(
                document_type=document_type,
                mode=mode,
                **delivery_flags,
            )
        )
    max_turns = expect.get("max_turns", len(messages))
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
        raise JourneyError("expect.max_turns 必须是正整数")
    min_turns = expect.get("min_turns", 1)
    if (
        not isinstance(min_turns, int)
        or isinstance(min_turns, bool)
        or min_turns < 1
        or min_turns > max_turns
    ):
        raise JourneyError("expect.min_turns 必须是不大于 max_turns 的正整数")
    markers = expect.get("degradation_markers", [])
    if not isinstance(markers, list) or not all(
        isinstance(item, str) and item.strip() for item in markers
    ):
        raise JourneyError("degradation_markers 必须是字符串列表")
    allow_degraded_delivery = expect.get("allow_degraded_delivery", False)
    if not isinstance(allow_degraded_delivery, bool):
        raise JourneyError("allow_degraded_delivery 必须是布尔值")
    allow_extra_deliveries = expect.get("allow_extra_deliveries", False)
    if not isinstance(allow_extra_deliveries, bool):
        raise JourneyError("allow_extra_deliveries 必须是布尔值")
    response_markers_value = expect.get("response_markers", {})
    if not isinstance(response_markers_value, Mapping):
        raise JourneyError("response_markers 必须是对象")
    response_markers: list[tuple[str, tuple[str, ...]]] = []
    for label, markers_value in response_markers_value.items():
        if not isinstance(label, str) or not label.strip():
            raise JourneyError("response_markers 的键必须是非空字符串")
        if not isinstance(markers_value, list) or not all(
            isinstance(marker, str) and marker.strip() for marker in markers_value
        ):
            raise JourneyError(
                f"response_markers.{label} 必须是非空字符串列表"
            )
        response_markers.append(
            (label.strip(), tuple(marker.strip() for marker in markers_value))
        )
    raw_paths = expect.get("completion_paths", [])
    if not isinstance(raw_paths, list):
        raise JourneyError("completion_paths 必须是对象列表")
    completion_paths: list[JourneyCompletionPath] = []
    path_ids: set[str] = set()
    valid_fact_ids = {fact.id for fact in facts}
    valid_milestone_ids = {milestone.id for milestone in milestones}
    for index, item in enumerate(raw_paths, start=1):
        if not isinstance(item, Mapping):
            raise JourneyError(f"completion_paths[{index}] 必须是对象")
        path_fields = {
            "id",
            "requires_sent_facts",
            "required_artifacts",
            "required_deliveries",
            "required_response_groups",
            "required_response_groups_after_trigger",
            "requires_capabilities_unavailable",
            "forbids_artifacts",
            "required_milestones",
        }
        unknown_fields = sorted(set(item) - path_fields)
        if unknown_fields:
            raise JourneyError(
                f"completion_paths[{index}] 存在未知字段：{', '.join(unknown_fields)}"
            )
        path_id = _required_text(item, "id")
        if path_id in path_ids:
            raise JourneyError(f"completion_paths[{index}].id 不得重复：{path_id}")
        path_ids.add(path_id)

        def path_strings(field_name: str) -> tuple[str, ...]:
            values = _marker_list(item.get(field_name, []), f"completion_paths[{index}].{field_name}")
            return values

        requires_sent_facts = path_strings("requires_sent_facts")
        if any(value not in valid_fact_ids for value in requires_sent_facts):
            raise JourneyError(f"completion_paths[{index}].requires_sent_facts 引用了未知事实")
        required_artifacts_values = path_strings("required_artifacts")
        if any(value not in _ALLOWED_ARTIFACTS for value in required_artifacts_values):
            raise JourneyError(f"completion_paths[{index}].required_artifacts 包含未知产物")
        required_groups = path_strings("required_response_groups")
        required_groups_after = path_strings("required_response_groups_after_trigger")
        known_groups = {label for label, _ in response_markers}
        if any(value not in known_groups for value in (*required_groups, *required_groups_after)):
            raise JourneyError(f"completion_paths[{index}] 引用了未声明的 response group")
        unavailable = path_strings("requires_capabilities_unavailable")
        forbids = path_strings("forbids_artifacts")
        if any(value not in _ALLOWED_ARTIFACTS for value in forbids):
            raise JourneyError(f"completion_paths[{index}].forbids_artifacts 包含未知产物")
        required_milestones = path_strings("required_milestones")
        if any(value not in valid_milestone_ids for value in required_milestones):
            raise JourneyError(f"completion_paths[{index}].required_milestones 引用了未知里程碑")
        path_deliveries = item.get(
            "required_deliveries",
            raw_deliveries if path_id == "delivered" else [],
        )
        if not isinstance(path_deliveries, list):
            raise JourneyError(f"completion_paths[{index}].required_deliveries 必须是对象列表")
        parsed_deliveries: list[DeliveryRequirement] = []
        for delivery_index, delivery in enumerate(path_deliveries, start=1):
            if not isinstance(delivery, Mapping):
                raise JourneyError(
                    f"completion_paths[{index}].required_deliveries[{delivery_index}] 必须是对象"
                )
            try:
                parsed_deliveries.append(_parse_delivery_requirement(delivery, f"completion_paths[{index}].required_deliveries[{delivery_index}]"))
            except JourneyError:
                raise
        completion_paths.append(
            JourneyCompletionPath(
                id=path_id,
                requires_sent_facts=requires_sent_facts,
                required_artifacts=required_artifacts_values,
                required_deliveries=tuple(parsed_deliveries),
                required_response_groups=required_groups,
                required_response_groups_after_trigger=required_groups_after,
                requires_capabilities_unavailable=unavailable,
                forbids_artifacts=forbids,
                required_milestones=required_milestones,
            )
        )
    return Journey(
        schema_version=2,
        id=journey_id,
        title=title,
        initial_user_message=initial,
        fact_pool=tuple(facts),
        milestones=tuple(milestones),
        restart=restart,
        required_artifacts=tuple(artifacts),
        max_turns=max_turns,
        required_deliveries=tuple(required_deliveries),
        min_turns=min_turns,
        degradation_markers=tuple(markers),
        allow_degraded_delivery=allow_degraded_delivery,
        allow_extra_deliveries=allow_extra_deliveries,
        response_markers=tuple(response_markers),
        completion_paths=tuple(completion_paths),
        simulator=simulator,
        initial_fact_ids=initial_fact_ids,
        scenario_as_of_date=calendar_date.isoformat() if resolved_dates else None,
        resolved_dates=tuple(sorted(resolved_dates.items())),
    )


def run_journey(
    journey: Journey,
    *,
    platform: str,
    source_root: str | Path,
    temp_root: str | Path,
    platform_command: list[str] | None = None,
    model: str | None = None,
    responder: ScenarioResponder | None = None,
    responder_mode: str = "model",
    simulator_provider: str = "codex-sdk",
    simulator_model: str | None = None,
    simulator_codex_bin: str | Path | None = None,
    simulator_timeout_seconds: int = DEFAULT_SIMULATOR_TIMEOUT_SECONDS,
    simulator_replay: str | Path | None = None,
    timeout_per_turn_seconds: int = DEFAULT_TIMEOUT_PER_TURN_SECONDS,
    keep_workspace_on_failure: bool = False,
    retain_deliveries: bool = False,
    on_progress: Callable[[JourneyProgress], None] | None = None,
) -> JourneyResult:
    """运行一条隔离自然语言 Journey，并将证据写入外部临时根。"""

    if platform not in SUPPORTED_PLATFORMS:
        raise JourneyError(f"不支持的 Agent 平台：{platform}")
    source = public_path(path_for_io(Path(source_root).resolve()))
    temp = public_path(path_for_io(Path(temp_root).resolve()))
    if not source.is_dir():
        raise JourneyError("source_root 必须是目录")
    if temp == source or _is_relative_to(temp, source):
        raise JourneyError("真实 Agent Eval 临时根不得位于源工作树内")
    if timeout_per_turn_seconds < 1:
        raise JourneyError("timeout_per_turn_seconds 必须是正整数")
    if responder_mode not in {"model", "rule", "replay"}:
        raise JourneyError("responder_mode 必须是 model、rule 或 replay")
    if simulator_provider != "codex-sdk":
        raise JourneyError("当前仅支持 simulator_provider=codex-sdk")
    if simulator_timeout_seconds < 1:
        raise JourneyError("simulator_timeout_seconds 必须是正整数")
    if responder_mode == "replay" and simulator_replay is None and responder is None:
        raise JourneyError("replay responder 必须提供 simulator_replay")
    path_for_io(temp).mkdir(parents=True, exist_ok=True)
    run_dir = temp / f"{journey.id}-{platform}-{uuid4().hex[:12]}"
    workspace = run_dir / "workspace"
    responder_workspace = run_dir / "scenario-responder-workspace"
    path_for_io(workspace).mkdir(parents=True)
    path_for_io(responder_workspace).mkdir(parents=True)
    evidence_path = run_dir / "evidence.json"
    started_at = datetime.now(timezone.utc)
    started = monotonic()
    runtime = CliAgentRuntime(
        platform=platform,
        command=platform_command or _default_platform_command(platform),
        timeout_seconds=timeout_per_turn_seconds,
        model=model
        if model is not None
        else ("gpt-6-luna" if platform == "codex" else "sonnet"),
    )
    transcripts: list[dict[str, Any]] = []
    managed_view_receipts: list[dict[str, Any]] = []
    managed_view_status: str | None = None
    observation: dict[str, Any] = {}
    observed_milestones: set[str] = set()
    verification: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    runtime_context: dict[str, Any] = {}
    runtime_diagnostic: dict[str, Any] = {}
    platform_discovery: dict[str, Any] = {}
    scenario_responder_events: list[dict[str, Any]] = []
    responder_instance: ScenarioResponder | None = responder
    responder_ledger = ScenarioResponseLedger()
    oracle = JourneyOracle(journey)
    assessment = JourneyAssessment(False, failure_category="Scenario", failure_code="not_started")
    sent_fact_ids: list[str] = list(journey.initial_fact_ids)
    restart_context_evidence: dict[str, Any] = {}
    restart_observation_evidence: dict[str, Any] | None = None
    unavailable_uses = 0
    failure_category: str | None = None
    failure_code: str | None = None
    failure_message = ""
    runtime_turn_completed = True
    turns = 0
    platform_version: str | None = None
    try:
        installed = install_skill(
            platform=platform,
            scope="project",
            target_root=workspace,
            source=source,
            mode="copy",
        )
        skill_root = Path(installed["skill_root"])
        verification = verify_install(platform=platform, skill_root=skill_root)
        if not (
            verification["discovery_contract_valid"]
            and verification["resources_complete"]
            and verification["runtime_identity_verified"]
        ):
            raise PlatformAdapterError("隔离安装未通过资源、发现或共享核心身份校验")
        if platform_command is None:
            platform_discovery = verify_platform_discovery(
                platform=platform,
                skill_root=skill_root,
                platform_command=list(runtime.command),
            )
        else:
            platform_discovery = {
                "verified": False,
                "runtime_discovered": False,
                "method": "custom-command-not-used-as-runtime-discovery-proof",
            }
        capability_report = probe_capabilities(
            platform=platform,
            skill_root=skill_root,
            platform_command=list(runtime.command),
            workspace=workspace,
            network_check="official-source" if platform_command is None else "skip",
            network_url="https://www.gov.cn/",
        )
        capabilities = {
            "capabilities": capability_report["capabilities"],
            "document_capabilities": capability_report["document_capabilities"],
            "complete_document_pipeline_available": capability_report[
                "complete_document_pipeline_available"
            ],
            "platform_version_probe": capability_report["platform_version_probe"],
            "tested_at": capability_report["tested_at"],
        }
        capabilities["attachment_presentation"] = capability_report[
            "attachment_presentation"
        ]
        runtime_context = _build_runtime_context(
            skill_root=skill_root,
            workspace=workspace,
            capability_report=capability_report,
        )
        runtime.set_runtime_context(runtime_context)
        platform_version = runtime.probe_version(workspace=workspace)

        if responder_instance is None:
            responder_instance = _build_scenario_responder(
                journey,
                mode=responder_mode,
                provider=simulator_provider,
                model=simulator_model or "gpt-6-luna",
                codex_bin=simulator_codex_bin,
                timeout_seconds=simulator_timeout_seconds,
                workspace=responder_workspace,
                agent_workspace=workspace,
                replay_path=simulator_replay,
            )

        used_facts: dict[str, int] = {fact_id: 1 for fact_id in journey.initial_fact_ids}
        pending_message: str | None = None
        restarted = False
        responder_identity: ResponderIdentity | None = None
        for index in range(1, journey.max_turns + 1):
            fact_id: str | None = None
            selected_fact_ids: list[str] = []
            selection_reason: str | None = None
            if index == 1:
                message = journey.initial_user_message
                selection = "scenario-initial-message"
                selected_fact_ids = list(journey.initial_fact_ids)
            elif pending_message is not None:
                message = pending_message
                pending_message = None
                selection = "milestone-restart-message"
            else:
                previous_response = transcripts[-1]["assistant_response"]
                if responder_instance is None:  # pragma: no cover - defensive invariant
                    raise RuntimeAdapterError("ScenarioResponder 未初始化")
                response_request = _build_scenario_response_request(
                    journey,
                    previous_response,
                    transcripts,
                    used_facts,
                    unavailable_uses,
                    turn=index,
                )
                decision_started = monotonic()
                try:
                    responder_result = responder_instance.respond(response_request)
                    if not isinstance(responder_result, ScenarioResponseResult):
                        raise ScenarioResponseError(
                            "ScenarioResponder 必须返回 ScenarioResponseResult"
                        )
                    if not isinstance(responder_result.decision, ScenarioResponseDecision):
                        raise ScenarioResponseError(
                            "ScenarioResponder result 的 decision 类型无效"
                        )
                    if not isinstance(responder_result.identity, ResponderIdentity):
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder 未提供实际 identity",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "identity_missing",
                            },
                        )
                    if not responder_result.identity.identity_verified:
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder 未提供可验证的实际身份",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "identity_missing",
                            },
                        )
                    if (
                        responder_identity is not None
                        and responder_result.identity != responder_identity
                    ):
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder 实际 identity 在 Journey 中发生变化",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "identity_changed",
                            },
                        )
                    responder_identity = responder_result.identity
                    expected_request_hash = scenario_request_sha256(response_request)
                    if responder_result.request_sha256 != expected_request_hash:
                        raise ScenarioResponseError(
                            "ScenarioResponder result 的 request_sha256 不匹配"
                        )
                    if not isinstance(responder_result.trace, Mapping) or not responder_result.trace:
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder 未提供可验证的 trace",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "trace_missing",
                            },
                        )
                    if responder_result.trace.get("request_sha256") != expected_request_hash:
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder trace 的 request_sha256 不匹配",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "trace_binding_mismatch",
                            },
                        )
                    if (
                        not isinstance(responder_result.attempt_count, int)
                        or isinstance(responder_result.attempt_count, bool)
                        or responder_result.attempt_count < 1
                        or not isinstance(responder_result.attempts, tuple)
                        or len(responder_result.attempts) != responder_result.attempt_count
                        or any(
                            not isinstance(attempt, ScenarioAttempt)
                            or attempt.request_sha256 != expected_request_hash
                            for attempt in responder_result.attempts
                        )
                    ):
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder 未提供完整 attempt 绑定信息",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "attempt_metadata_missing",
                            },
                        )
                    if (
                        not isinstance(responder_result.repair_count, int)
                        or isinstance(responder_result.repair_count, bool)
                        or responder_result.repair_count < 0
                        or responder_result.repair_count >= responder_result.attempt_count
                    ):
                        raise ScenarioResponderRuntimeError(
                            "ScenarioResponder repair_count 不符合 attempt 绑定",
                            details={
                                "actor": "scenario_responder",
                                "failure_kind": "attempt_metadata_invalid",
                            },
                        )
                    decision = responder_result.decision
                except ScenarioResponderRuntimeError as error:
                    event = {
                        "turn": index,
                        "status": "runtime_error",
                        "duration_seconds": monotonic() - decision_started,
                        **_sanitize_responder_details(error.details),
                    }
                    scenario_responder_events.append(event)
                    raise _JourneyFailure(
                        "Runtime Adapter",
                        str(error),
                        event,
                    ) from error
                except ScenarioResponseError as error:
                    event = {
                        "turn": index,
                        "status": "invalid_responder_decision",
                        "duration_seconds": monotonic() - decision_started,
                        "error": str(error),
                    }
                    scenario_responder_events.append(event)
                    raise _JourneyFailure("Harness", str(error), event) from error
                try:
                    rendered = responder_ledger.apply(response_request, decision)
                except ScenarioResponseError as error:
                    event = {
                        "turn": index,
                        "status": "rejected_decision",
                        "action": decision.action,
                        "selected_fact_ids": list(decision.selected_fact_ids),
                        "duration_seconds": monotonic() - decision_started,
                        "error": str(error),
                    }
                    scenario_responder_events.append(event)
                    raise _JourneyFailure("Harness", str(error), event) from error
                selected_fact_ids = list(rendered.selected_fact_ids)
                sent_fact_ids.extend(selected_fact_ids)
                fact_id = selected_fact_ids[0] if len(selected_fact_ids) == 1 else None
                selection_reason = decision.reason_code
                event = {
                    "turn": index,
                    "scenario_id": response_request.scenario_id,
                    "prompt_version": response_request.prompt_version,
                    "schema_version": response_request.schema_version,
                    "status": "accepted",
                    "action": rendered.action,
                    "selected_fact_ids": selected_fact_ids,
                    "result_code": rendered.result_code,
                    "rendered_message": rendered.message,
                    "duration_seconds": monotonic() - decision_started,
                    "decision": {
                        "action": rendered.action,
                        "selected_fact_ids": selected_fact_ids,
                        "confidence": decision.confidence,
                        "reason_code": decision.reason_code,
                    },
                    "result": responder_result.as_dict(),
                }
                event["runtime"] = responder_result.identity.as_dict()
                event["request_sha256"] = responder_result.request_sha256
                event["attempt_count"] = responder_result.attempt_count
                event["repair_count"] = responder_result.repair_count
                event["validation"] = responder_result.validation
                event["runtime_source"] = responder_result.identity.runtime_source
                scenario_responder_events.append(event)
                if rendered.action == "stop":
                    raise _JourneyFailure(
                        "Scenario",
                        "ScenarioResponder 请求安全停止 Journey",
                        {**event, "failure_code": "scenario_responder_stop"},
                    )
                message = rendered.message
                if rendered.action == "unavailable":
                    unavailable_uses += 1
                else:
                    for selected_id in selected_fact_ids:
                        used_facts[selected_id] = used_facts.get(selected_id, 0) + 1
                selection = "scenario-responder"
            _notify(
                on_progress,
                JourneyProgress(journey.id, platform, index, "turn_started", monotonic() - started),
            )
            runtime_turn_completed = False
            response = runtime.turn(workspace=workspace, message=message)
            runtime_turn_completed = True
            turns = index
            previous_observation = observation
            session_restarted_turn = selection == "milestone-restart-message"
            turn_managed_view_receipts = _extract_managed_view_receipts(
                response.events,
                platform=platform,
                workspace=workspace,
            )
            runtime_event_summary = (
                _summarize_runtime_events(
                    response.events,
                    workspace=workspace,
                    installed_skill_root=runtime_context.get(
                        "installed_skill_root"
                    ),
                )
            )
            for event in runtime_event_summary:
                status = event.get("managed_view_receipt_status")
                if status in {
                    "passed",
                    "rejected",
                    "unbound_command",
                    "missing_or_unparseable_view_response",
                }:
                    managed_view_status = status
            for receipt in turn_managed_view_receipts:
                if receipt not in managed_view_receipts:
                    managed_view_receipts.append(receipt)
            transcripts.append(
                {
                    "turn": index,
                    "user_message": message,
                    "assistant_response": response.text,
                    "duration_seconds": response.duration_seconds,
                    "session_id": response.session_id,
                    "command": _sanitize_runtime_command(response.command),
                    "session_restarted": session_restarted_turn,
                    "event_count": len(response.events),
                    "selection": selection,
                    "fact_id": fact_id,
                    "selected_fact_ids": selected_fact_ids,
                    "selection_reason": selection_reason,
                    "managed_view_receipts": turn_managed_view_receipts,
                    "runtime_event_summary": runtime_event_summary,
                }
            )
            observation = _observe_workspace(workspace)
            if managed_view_receipts:
                observation["managed_view_receipts"] = list(managed_view_receipts)
            if managed_view_status is not None:
                observation["managed_view_status"] = managed_view_status
            newly_observed = _observed_milestones(
                journey,
                workspace,
                transcripts,
            )
            persistence_claim = _probe_runtime_persistence_claim(response.text)
            archive_changed = _archive_state_changed(
                previous_observation,
                observation,
            )
            persistence_verified = not persistence_claim or archive_changed
            persistence_verification_basis = (
                "no_claim"
                if not persistence_claim
                else "archive_state_changed"
                if archive_changed
                else "unverified"
            )
            if persistence_claim and not persistence_verified:
                persistence_verified = _persistence_claim_references_existing_archive(
                    response.text,
                    workspace,
                )
                if persistence_verified:
                    persistence_verification_basis = (
                        "quoted_reference_in_current_public_archive"
                    )
                elif (
                    session_restarted_turn
                    and _has_successful_case_archive_read(runtime_event_summary)
                    and "archive_recovered_after_restart" in newly_observed
                    and _persistence_claim_matches_read_archive_summary(
                        response.text, workspace
                    )
                ):
                    persistence_verified = True
                    persistence_verification_basis = (
                        "same_case_current_revision_archive_read_receipt"
                    )
            transcripts[-1]["persistence_claim"] = {
                "detected": persistence_claim,
                "verified": persistence_verified,
                "verification_basis": persistence_verification_basis,
            }
            if persistence_claim and not persistence_verified:
                raise _JourneyFailure(
                    "SUT/Skill",
                    "false_persistence_claim：模型声称已保存或已记录，但公开案情档案没有对应内容或可验证的本轮变化",
                )
            observed_milestones.update(newly_observed)
            transcripts[-1]["milestones_observed"] = sorted(observed_milestones)
            _notify(
                on_progress,
                JourneyProgress(journey.id, platform, index, "turn_completed", monotonic() - started),
            )
            if (
                journey.restart is not None
                and not restarted
                and journey.restart.after_milestone in observed_milestones
            ):
                runtime.close()
                pending_message = journey.restart.message
                restarted = True
                restart_observation_evidence = _restart_observation_snapshot(
                    observation
                )
                restart_context_evidence = _build_restart_context(
                    workspace=workspace,
                    observation=observation,
                )
                runtime_context = _with_restart_context(
                    runtime_context,
                    restart_context_evidence,
                )
                runtime.set_runtime_context(runtime_context)
                transcripts[-1]["restart_context_prepared"] = True
            assessment = oracle.evaluate(
                JourneyPublicState(
                    journey=journey,
                    capabilities=capabilities,
                    transcripts=tuple(transcripts),
                    sent_fact_ids=tuple(sent_fact_ids),
                    observed_milestones=frozenset(observed_milestones),
                    restarted=restarted,
                    restart_context=restart_context_evidence or None,
                    observation=observation,
                    restart_observation=restart_observation_evidence,
                )
            )
            if assessment.passed and turns >= journey.min_turns and pending_message is None:
                if assessment.completion_path == "delivery_degraded":
                    observation["delivery_degraded"] = True
                break
        if not observation:
            observation = _observe_workspace(workspace)
        assessment = oracle.evaluate(
            JourneyPublicState(
                journey=journey,
                capabilities=capabilities,
                transcripts=tuple(transcripts),
                sent_fact_ids=tuple(sent_fact_ids),
                observed_milestones=frozenset(observed_milestones),
                restarted=restarted,
                restart_context=restart_context_evidence or None,
                observation=observation,
                restart_observation=restart_observation_evidence,
            )
        )
        if not assessment.passed:
            raise _JourneyFailure(
                assessment.failure_category or "Scenario",
                assessment.message,
                {"failure_code": assessment.failure_code, "missing": list(assessment.missing)},
            )
        if assessment.completion_path == "delivery_degraded":
            observation["delivery_degraded"] = True
    except _JourneyFailure as error:
        failure_category = error.category
        failure_message = str(error)
        if isinstance(error.details, Mapping):
            code = error.details.get("failure_code")
            if isinstance(code, str) and code:
                failure_code = code
        if error.details and error.details not in scenario_responder_events:
            scenario_responder_events.append(error.details)
    except PlatformAdapterError as error:
        failure_category = "Harness"
        failure_message = str(error)
    except RuntimeAdapterError as error:
        failure_category = "Runtime Adapter"
        failure_message = str(error)
        runtime_diagnostic = _sanitize_runtime_diagnostic(error.details)
    except (OSError, ValueError, KeyError, TypeError) as error:
        failure_category = "Harness"
        failure_message = f"Harness 失败：{error.__class__.__name__}"
    finally:
        runtime.close()
        if responder_instance is not None:
            close = getattr(responder_instance, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    scenario_responder_events.append(
                        {
                            "status": "close_error",
                            "failure_kind": "responder_close_failed",
                            "error_type": error.__class__.__name__,
                        }
                    )
                    if failure_category is None:
                        failure_category = "Runtime Adapter"
                        failure_message = "ScenarioResponder 关闭失败"

    try:
        observation = _observe_workspace(workspace)
    except (OSError, ValueError) as error:
        observation = {"inspection_error_count": 1}
        runtime_diagnostic["final_observation_error"] = {
            "error_type": error.__class__.__name__,
            "error_code": getattr(error, "winerror", None) or getattr(error, "errno", None),
        }
        if failure_category is None:
            failure_category = "Runtime Adapter"
            failure_code = "public_artifact_observer_failed"
            failure_message = "最终公开产物观察失败"
    if managed_view_receipts:
        observation["managed_view_receipts"] = list(managed_view_receipts)
    if managed_view_status is not None:
        observation["managed_view_status"] = managed_view_status
    assessment = oracle.evaluate(
        JourneyPublicState(
            journey=journey,
            capabilities=capabilities,
            transcripts=tuple(transcripts),
            sent_fact_ids=tuple(sent_fact_ids),
            observed_milestones=frozenset(observed_milestones),
            restarted=restarted,
            restart_context=restart_context_evidence or None,
            observation=observation,
            restart_observation=restart_observation_evidence,
            runtime_completed=runtime_turn_completed,
        )
    )
    if failure_category is None and not assessment.passed:
        failure_category = assessment.failure_category or "Harness"
        failure_code = assessment.failure_code
        failure_message = assessment.message
    if assessment.passed and assessment.completion_path == "delivery_degraded":
        observation["delivery_degraded"] = True
    try:
        artifact_files = _artifact_files(workspace)
    except (OSError, ValueError) as error:
        artifact_files = []
        runtime_diagnostic["artifact_inventory_error"] = {
            "error_type": error.__class__.__name__,
            "error_code": getattr(error, "winerror", None) or getattr(error, "errno", None),
        }
        if failure_category is None:
            failure_category = "Runtime Adapter"
            failure_code = "public_artifact_inventory_failed"
            failure_message = "最终公开产物清单读取失败"
    retained_deliveries: list[dict[str, Any]] = []
    if (
        retain_deliveries
        and failure_category is None
        and assessment.passed
        and assessment.completion_path == "delivered"
    ):
        try:
            retained_deliveries = _retain_verified_deliveries(
                workspace, run_dir, observation
            )
        except (OSError, ValueError) as error:
            failure_category = "Harness"
            failure_code = "delivery_retention_failed"
            failure_message = f"已验证交付文件的保留失败：{error.__class__.__name__}"
    cleanup_error: OSError | None = None
    passed_before_cleanup = failure_category is None
    if not ((not passed_before_cleanup) and keep_workspace_on_failure):
        cleanup_error = _cleanup_journey_workspaces(run_dir)
        if cleanup_error is not None and passed_before_cleanup:
            failure_category = "Harness"
            failure_message = "成功后隔离 Workspace 清理失败"
    passed = failure_category is None
    if not passed and not failure_message:
        failure_message = "Journey 未通过"
    actual_identity = _actual_responder_identity(
        responder_identity,
        responder_mode=responder_mode,
        provider=simulator_provider,
        model=simulator_model or "gpt-6-luna",
    )
    evidence = {
        "schema_version": 2,
        "journey_id": journey.id,
        "title": journey.title,
        "scenario_as_of_date": journey.scenario_as_of_date,
        "resolved_dates": dict(journey.resolved_dates),
        "platform": platform,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": monotonic() - started,
        "turns": turns,
        "transcript": transcripts,
        "sent_fact_ids": list(sent_fact_ids),
        "installation": {
            "verified": bool(verification.get("runtime_identity_verified")),
            "skill_version": verification.get("skill_version"),
            "platform_version": platform_version,
            "core_identity": verification.get("core_identity"),
        },
        "capabilities": capabilities,
        "scenario_responder": scenario_responder_events,
        "responder": {
            **actual_identity,
            "workspace_relative": "scenario-responder-workspace",
        },
        "runtime_context": {
            "delivered": bool(runtime_context),
            "channel": getattr(runtime, "context_delivery", "unknown"),
            "keys": sorted(runtime_context),
            "restart_recovery": restart_context_evidence or None,
            "restart_observation": restart_observation_evidence,
        },
        "runtime_completed": runtime_turn_completed,
        "oracle": assessment.as_dict(),
        "assertions": {
            "response_markers": {
                label: any(
                    marker in "\n".join(
                        item["assistant_response"] for item in transcripts
                    )
                    for marker in markers
                )
                for label, markers in journey.response_markers
            },
            "degradation_markers": bool(
                journey.degradation_markers
                and any(
                    marker in "\n".join(
                        item["assistant_response"] for item in transcripts
                    )
                    for marker in journey.degradation_markers
                )
            ),
            "milestones": {
                milestone.id: milestone.id in observed_milestones
                for milestone in journey.milestones
            },
        },
        "discovery": {
            "method": "natural-language-cli-session",
            "runtime_discovered": _runtime_discovered(
                platform_command=platform_command,
                transcripts=transcripts,
                observation=observation,
                platform_discovery=platform_discovery,
            ),
            "platform_contract": platform_discovery,
        },
        "observation": observation,
        "artifacts": artifact_files,
        "retained_deliveries": retained_deliveries,
        "host_presentation_verified": False,
        "isolation": {
            "separate_workspace": True,
            "workspace_relative": "workspace",
            "source_worktree_execution": False,
            "internal_state_read": False,
            "second_model_used": bool(
                actual_identity.get("mode") == "model"
                and any(
                    isinstance(item, Mapping)
                    and item.get("status") == "accepted"
                    and isinstance(item.get("runtime"), Mapping)
                    and item["runtime"].get("provider") == "codex-sdk"
                    for item in scenario_responder_events
                )
            ),
            "scenario_responder_workspace": "scenario-responder-workspace",
            "agent_workspace": "workspace",
            "env_policy": actual_identity.get("environment_policy"),
            "read_isolation": actual_identity.get("read_isolation"),
            "isolation_canary": actual_identity.get("isolation_canary"),
        },
        "attribution": {
            "category": "pass" if passed else failure_category,
            "code": None if passed else failure_code,
            "message": failure_message,
        },
    }
    if cleanup_error is not None:
        evidence["cleanup"] = {
            "completed": False,
            "error": cleanup_error.__class__.__name__,
        }
    if runtime_diagnostic:
        evidence["runtime_diagnostic"] = runtime_diagnostic
    path_for_io(evidence_path).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from .conversation_report import write_evidence_report

    write_evidence_report(evidence, path_for_io(evidence_path))
    return JourneyResult(
        journey_id=journey.id,
        platform=platform,
        passed=passed,
        failure_category=failure_category,
        failure_message=failure_message,
        turns=turns,
        duration_seconds=monotonic() - started,
        observation=observation,
        evidence_path=evidence_path,
    )


def run_journey_with_retries(
    journey: Journey,
    *,
    platform: str,
    source_root: str | Path,
    temp_root: str | Path,
    platform_command: list[str] | None = None,
    model: str | None = None,
    responder: ScenarioResponder | None = None,
    responder_mode: str = "model",
    simulator_provider: str = "codex-sdk",
    simulator_model: str | None = None,
    simulator_codex_bin: str | Path | None = None,
    simulator_timeout_seconds: int = DEFAULT_SIMULATOR_TIMEOUT_SECONDS,
    simulator_replay: str | Path | None = None,
    timeout_per_turn_seconds: int = DEFAULT_TIMEOUT_PER_TURN_SECONDS,
    keep_workspace_on_failure: bool = False,
    retain_deliveries: bool = False,
    on_progress: Callable[[JourneyProgress], None] | None = None,
    retries: int = 1,
) -> JourneyResult:
    """先重跑模型主导 Journey，再对不一致结果标记环境/模型波动。"""

    if retries < 0:
        raise JourneyError("retries 必须是不小于零的整数")
    attempts: list[JourneyResult] = []
    for _ in range(retries + 1):
        result = run_journey(
            journey,
            platform=platform,
            source_root=source_root,
            temp_root=temp_root,
            platform_command=platform_command,
            model=model,
            responder=responder,
            responder_mode=responder_mode,
            simulator_provider=simulator_provider,
            simulator_model=simulator_model,
            simulator_codex_bin=simulator_codex_bin,
            simulator_timeout_seconds=simulator_timeout_seconds,
            simulator_replay=simulator_replay,
            timeout_per_turn_seconds=timeout_per_turn_seconds,
            keep_workspace_on_failure=keep_workspace_on_failure,
            retain_deliveries=retain_deliveries,
            on_progress=on_progress,
        )
        attempts.append(result)
        if result.passed:
            break
    if len(attempts) == 1:
        return attempts[0]

    passing = next((item for item in attempts if item.passed), None)
    final = passing or attempts[-1]
    signatures = {
        (item.failure_category, item.failure_message) for item in attempts
    }
    if passing is not None:
        final_state = (True, None, "")
    elif len(signatures) > 1:
        final_state = (
            False,
            "环境/模型波动",
            "重跑结果不一致，未将单次失败直接定性为 Skill 缺陷",
        )
    else:
        final_state = (False, final.failure_category, final.failure_message)

    retry_summary = [
        {
            "attempt": index,
            "passed": item.passed,
            "failure_category": item.failure_category,
            "failure_message": item.failure_message,
            "turns": item.turns,
            "duration_seconds": item.duration_seconds,
            "evidence_path": str(item.evidence_path),
        }
        for index, item in enumerate(attempts, start=1)
    ]
    evidence = json.loads(
        path_for_io(final.evidence_path).read_text(encoding="utf-8")
    )
    evidence["retries"] = retry_summary
    evidence["attribution"] = {
        "category": "pass" if final_state[0] else final_state[1],
        "message": final_state[2],
    }
    path_for_io(final.evidence_path).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from .conversation_report import write_evidence_report

    write_evidence_report(evidence, path_for_io(final.evidence_path))
    return replace(
        final,
        passed=final_state[0],
        failure_category=final_state[1],
        failure_message=final_state[2],
    )


class _JourneyFailure(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.details = dict(details or {})


def _build_scenario_responder(
    journey: Journey,
    *,
    mode: str,
    provider: str,
    model: str,
    codex_bin: str | Path | None,
    timeout_seconds: int,
    workspace: Path,
    agent_workspace: Path,
    replay_path: str | Path | None,
) -> ScenarioResponder:
    if provider != "codex-sdk":
        raise JourneyError("当前仅支持 simulator_provider=codex-sdk")
    if mode == "model":
        return CodexSdkScenarioResponder(
            workspace=workspace,
            agent_workspace=agent_workspace,
            model=model,
            codex_bin=codex_bin,
            timeout_seconds=timeout_seconds,
        )
    if mode == "replay":
        if replay_path is None:
            raise JourneyError("replay responder 必须提供 simulator_replay")
        return ReplayScenarioResponder(replay_path)
    if mode == "rule":
        return RuleScenarioResponder(
            marker_rules={
                fact.id: fact.response_markers
                for fact in journey.fact_pool
                if fact.response_markers
            }
        )
    raise JourneyError("responder_mode 必须是 model、rule 或 replay")


def _build_scenario_response_request(
    journey: Journey,
    latest_response: str,
    transcripts: list[dict[str, Any]],
    used_facts: Mapping[str, int],
    unavailable_uses: int,
    *,
    turn: int = 0,
) -> ScenarioResponseRequest:
    recent_messages: list[str] = []
    for transcript in transcripts[-4:]:
        user_message = transcript.get("user_message")
        assistant_response = transcript.get("assistant_response")
        if isinstance(user_message, str) and user_message.strip():
            recent_messages.append("用户：" + user_message)
        if isinstance(assistant_response, str) and assistant_response.strip():
            recent_messages.append("模型 A：" + assistant_response)
    used_summary = "、".join(
        f"{fact_id}({used_facts.get(fact_id, 0)})"
        for fact_id in sorted(used_facts)
        if used_facts.get(fact_id, 0) > 0
    ) or "无"
    required_next_fact_id = None
    if transcripts:
        last = transcripts[-1]
        if (
            last.get("session_restarted")
            and journey.simulator.after_restart_fact_id
        ):
            required_next_fact_id = journey.simulator.after_restart_fact_id
        else:
            last_sent = set(last.get("selected_fact_ids") or ())
            required_next_fact_id = next((
                fact.id for fact in journey.fact_pool
                if fact.immediately_after in last_sent
                and used_facts.get(fact.id, 0) < fact.max_uses
            ), None)
    return ScenarioResponseRequest(
        scenario_id=journey.id,
        turn=turn,
        persona=journey.simulator.persona,
        objective=journey.simulator.objective,
        latest_agent_response=latest_response,
        recent_messages=tuple(recent_messages),
        state_summary=f"已使用事实：{used_summary}",
        available_facts=tuple(
            ScenarioFactOption(
                id=fact.id,
                message=fact.text,
                max_uses=fact.max_uses,
                used_count=used_facts.get(fact.id, 0),
                requires_sent_facts=fact.requires_sent_facts,
            )
            for fact in journey.fact_pool
        ),
        max_facts_per_turn=journey.simulator.max_facts_per_turn,
        remaining_unavailable_uses=(
            None
            if journey.simulator.max_unavailable_uses is None
            else max(0, journey.simulator.max_unavailable_uses - unavailable_uses)
        ),
        max_unavailable_uses=journey.simulator.max_unavailable_uses,
        unavailable_used_count=unavailable_uses,
        unavailable_message=journey.simulator.unavailable_message,
        confirmation_fact_id=journey.simulator.confirmation_fact_id,
        required_next_fact_id=required_next_fact_id,
        confirmation_prerequisite_fact_ids=(
            journey.simulator.confirmation_prerequisite_fact_ids
        ),
        confirmation_request_markers=journey.simulator.confirmation_request_markers,
        confirmation_conflict_markers=journey.simulator.confirmation_conflict_markers,
        confirmation_conflict_fact_ids=(
            journey.simulator.confirmation_conflict_fact_ids
        ),
        confirmation_summary_required_groups=(
            journey.simulator.confirmation_summary_required_groups
        ),
        prompt_version=journey.simulator.prompt_version,
        schema_version=journey.simulator.schema_version,
    )


def _actual_responder_identity(
    identity: object | None,
    *,
    responder_mode: str,
    provider: str,
    model: str,
) -> dict[str, object]:
    if isinstance(identity, ResponderIdentity) and identity.identity_verified:
        return identity.as_dict()
    return {
        "mode": "unknown",
        "provider": "unknown",
        "model": None,
        "sdk_version": None,
        "runtime_source": "unknown",
        "binary_version": None,
        "sandbox": None,
        "approval_mode": None,
        "read_isolation": "unknown",
        "environment_policy": "unknown",
        "isolation_canary": "not_run",
        "identity_verified": False,
        "canary_results": {},
    }


def _sanitize_responder_details(details: Mapping[str, Any] | None) -> dict[str, object]:
    if not isinstance(details, Mapping):
        return {}
    allowed = {
        "actor",
        "mode",
        "provider",
        "model",
        "sdk_version",
        "runtime_source",
        "binary_version",
        "sandbox",
        "approval_mode",
        "read_isolation",
        "environment_policy",
        "isolation_canary",
        "canary_results",
        "thread_id",
        "prompt_version",
        "schema_version",
        "input_sha256",
        "request_sha256",
        "turn",
        "attempt_count",
        "repair_count",
        "retry_count",
        "duration_seconds",
        "result_code",
        "validation",
        "action",
        "selected_fact_ids",
        "attempts",
        "failure_kind",
        "error_type",
        "error_summary",
    }
    sanitized: dict[str, object] = {}
    for key, value in details.items():
        if key not in allowed:
            continue
        if key == "attempts" and isinstance(value, list):
            sanitized[key] = [
                _sanitize_responder_details(item)
                for item in value[:4]
                if isinstance(item, Mapping)
            ]
        elif key == "selected_fact_ids" and isinstance(value, (list, tuple)):
            sanitized[key] = [str(item)[:128] for item in value[:32]]
        elif key == "canary_results" and isinstance(value, Mapping):
            sanitized[key] = {
                str(item_key)[:128]: (
                    {
                        str(nested_key)[:128]: nested_value
                        for nested_key, nested_value in nested.items()
                        if isinstance(nested_value, (str, bool, int, float))
                        or nested_value is None
                    }
                    if isinstance(nested, Mapping)
                    else nested
                )
                for item_key, nested in list(value.items())[:16]
            }
        elif isinstance(value, str):
            sanitized[key] = value[:512]
        elif isinstance(value, (bool, int, float)) or value is None:
            sanitized[key] = value
    return sanitized


_PERSISTENCE_COMPLETION_PATTERN = re.compile(
    r"(?:已(?:经)?|成功地|现已)\s*(?:将)?[^。！？?!\n]{0,36}?"
    r"(?:保存|记录|写入|提交(?!前)|创建|建立)[^。！？?!\n]{0,24}"
    r"(?:案情档案|档案|事实|材料)?"
    r"|(?:案情档案|档案|事实|材料)[^。！？?!\n]{0,6}"
    r"(?:已(?:经)?|成功(?:地)?)[^。！？?!\n]{0,12}"
    r"(?:保存|记录|写入|提交(?!前)|创建|建立)"
    r"|(?:保存|记录|写入|提交(?!前)|创建|建立)\s*(?:成功|完成)",
)
_PERSISTENCE_NEGATIVE_PATTERN = re.compile(
    r"(?:已读|已签收|已送达)[^。！？?!\n]{0,16}(?:记录|凭证|回执)"
    r"|(?:保存|记录|写入|提交|创建|建立)[^。！？?!\n]{0,10}"
    r"(?:失败|未成功|未完成|尚未|未能|无法|不能|不可)"
    r"|(?:是否|能否|可否)[^。！？?!\n]{0,36}"
    r"(?:已(?:经)?|现已)?[^。！？?!\n]{0,18}"
    r"(?:保存|记录|写入|提交|创建|建立)"
    r"|(?:不是|并非|并不是|不属于)[^。！？?!\n]{0,8}"
    r"(?:已(?:经)?\s*)?(?:保存|记录|写入|提交|创建|建立)"
    r"|(?:将要?|会|建议|准备|计划|尚未|未|未能|无法|不能|不可|不要|不应|不|没有|没)"
    r"[^。！？?!\n]{0,8}(?:保存|记录|写入|提交|创建|建立)",
)
_PERSISTENCE_REFERENCE_PATTERN = re.compile(
    r"已从[^。！？?!\n]{0,36}保存的(?:案情)?档案(?:接续|继续|恢复|处理)|"
    r"已从[^。！？?!\n]{0,48}(?:案情)?档案(?:接续|继续|恢复|处理)|"
    r"已(?:经)?按[^。！？?!\n]{0,24}(?:更正|修正)[^。！？?!\n]{0,12}记录|"
    r"(?:已(?:经)?\s*)?(?:保存|记录|写入|提交|创建|建立)\s*(?:的)?"
    r"[^。！？?!\n]{0,12}(?:内容|信息|事项|事实|材料|档案)"
    r"\s*(?:一致|相同|重复|在案|在档|如上|如下)"
    r"|(?:内容|信息|事项|事实|材料|档案|案件|案情)[^。！？?!\n]{0,6}"
    r"已(?:经)?\s*(?:在|于)[^。！？?!\n]{0,16}"
    r"(?:保存|记录|写入|提交|创建|建立)"
    r"|(?:档案|案件|案情)[^。！？?!\n]{0,8}"
    r"已(?:经)?[^。！？?!\n]{0,16}"
    r"(?:保存|记录|写入|提交|创建|建立)"
    r"|(?:读取|查看|查阅|恢复)[^。！？?!\n]{0,24}"
    r"(?:之前|此前|保存的|保存过|已保存)"
    r"|(?:当前|目前|现有|已有)?已(?:经)?记录(?:的)?"
    r"(?:事实|内容|信息|事项)"
    r"|(?:读取|查看|查阅|定位|查找|找到|依据|根据|按照|基于|沿用)"
    r"[^。！？?!\n]{0,24}已(?:经)?\s*(?:保存|记录|写入|提交|创建|建立)"
    r"[^。！？?!\n]{0,24}(?:内容|信息|事项|事实|材料|档案|案件|案情)"
    r"|(?:已|曾|此前|先前|刚才)\s*保存过"
    r"[^。！？?!\n]{0,24}"
    r"|[^。！？?!\n]{0,16}(?:已(?:经)?|曾|此前|先前|刚才)"
    r"[^。！？?!\n]{0,12}(?:保存|记录|写入|提交|创建|建立)过"
    r"|(?:确认|读取|查看|查阅|定位|查找|找到|恢复|从|中断前|工作区|当前)"
    r"[^。！？?!\n]{0,36}已(?:经)?\s*保存(?:过|到|的)?"
    r"[^。！？?!\n]{0,24}"
    r"|(?:前面|之前|先前|此前|上轮|上一轮|刚才)"
    r"[^。！？?!\n]{0,36}已(?:经)?(?:按[^。！？?!\n]{0,16})?\s*保存"
    r"[^。！？?!\n]{0,24}"
    r"|已(?:经)?\s*(?:保存|记录|写入|提交|创建|建立)"
    r"[^。！？?!\n]{0,48}(?:本轮|本次|这一轮)"
    r"[^。！？?!\n]{0,20}(?:没有|无|未)"
    r"[^。！？?!\n]{0,16}新增|"
    r"(?:保存|记录|写入|提交|创建|建立)[^。！？?!\n]{0,40}"
    r"(?:重复(?:内容|部分)?(?:不再|无需|不必)?重复|不再重复|无需重复|不重复|"
    r"不另行(?:记录|记载|保存|写入))"
)
_PERSISTENCE_ARCHIVE_REFERENCE_PATTERN = re.compile(
    r"[“\"](?P<reference>[^”\"]{1,80})[”\"]"
    r"[^。！？?!\n]{0,12}(?:状态|事实|内容|信息|事项)"
    r"[^。！？?!\n]{0,6}(?:已(?:经)?|成功(?:地)?)(?:保存|记录|写入|提交|创建|建立)"
)
_PERSISTENCE_ARCHIVE_QUOTED_VALUE_PATTERN = re.compile(
    r"档案(?:里|中)(?:现在)?(?:写的是|写着|记载为|记录为|保存为|标注为)"
    r"[“\"](?P<reference>[^”\"]{1,80})[”\"]"
)
_PERSISTENCE_ARCHIVE_CORRECTION_PATTERN = re.compile(
    r"(?P<field>相关工资争议期间|工资争议期间|相关期间|欠薪月数|欠薪月份|拖欠月数|拖欠月份)"
    r"[^。！？?!\n；;]{0,12}(?:已)?(?:更正|修正)(?:为|成|到)"
    r"(?P<value>[^“”\"'，,；;。！？?!\n]{1,24})"
)
_PERSISTENCE_ARCHIVE_PARAPHRASE_CORRECTION_PATTERN = re.compile(
    r"(?P<field>相关工资争议期间|工资争议期间|相关期间|欠薪月数|欠薪月份|拖欠月数|拖欠月份)"
    r"(?:是|为)(?P<value>[^“”\"'，,；;。！？?!\n]{1,24}?)(?:而不是|但|$)"
)
_PERSISTENCE_ARCHIVE_FACT_LIST_PATTERN = re.compile(
    r"(?P<reference>.+?)[，,]?\s*(?:也)?已(?:经)?(?:保存|记录|写入)\s*$"
)
_PERSISTENCE_THREE_REQUEST_ANALYSIS_PATTERN = re.compile(
    r"(?:三项|这三项)[^。！？?!\n；;]{0,96}(?:条件化)?分析"
    r"[^。！？?!\n；;]{0,72}(?:已|都已|均已|已全部|已经)"
    r"(?:记录|保存|写入|产出|完成)"
)


def _existing_archive_fact_list(clause: str, archive_text: str) -> bool:
    """核对一次旧事实列表复述；不把泛称或本轮新写入当作旧档案证明。"""

    if re.search(r"本轮|本次|刚刚|新增|新补充", clause):
        return False
    match = _PERSISTENCE_ARCHIVE_FACT_LIST_PATTERN.fullmatch(clause.strip())
    if match is None:
        return False
    terms = re.split(r"[、，,]|以及|和", match.group("reference"))
    terms = [
        re.sub(r"^(?:你|我|用户)?(?:已有|现有|已提供)(?:的)?", "", term.strip())
        .replace("尚未", "未")
        .strip()
        for term in terms
        if term.strip()
    ]
    return (
        len(terms) >= 2
        and all(len(term) >= 2 and term.casefold() in archive_text for term in terms)
    )


def _existing_three_request_analysis(clause: str, archive_text: str) -> bool:
    """核对三项请求的既有条件化分析摘要，不要求重复提交相同分析。"""

    if not _PERSISTENCE_THREE_REQUEST_ANALYSIS_PATTERN.search(clause):
        return False
    section_match = re.search(
        r"## 分析与假设(.*?)(?:## 计算结果|$)",
        archive_text,
        re.DOTALL,
    )
    if section_match is None:
        return False
    entries = re.split(
        r"(?=### \[A-[0-9]{3,}\] )",
        section_match.group(1),
        flags=re.IGNORECASE,
    )
    required_topics = (
        ("工资差额",),
        ("季度绩效奖金", "季度奖金"),
        ("加班费", "加班工资"),
    )
    return all(
        any(
            any(topic in entry for topic in topic_group)
            and re.search(r"(?:当前判断|初步判断)\s*[：:]\s*信息不足", entry)
            for entry in entries
        )
        for topic_group in required_topics
    )


def _existing_archive_correction(
    clause: str, archive_text: str, *, prior_archive_context: bool = False
) -> bool:
    """核对带明确领域字段和值的既有更正。"""

    if re.search(r"本轮|本次|刚刚|新增|新补充", clause):
        return False
    match = _PERSISTENCE_ARCHIVE_CORRECTION_PATTERN.search(clause)
    if match is None and prior_archive_context and "更正过" in clause:
        match = _PERSISTENCE_ARCHIVE_PARAPHRASE_CORRECTION_PATTERN.search(clause)
    if match is None or (
        not re.search(r"档案|保存|记录|接续", clause)
        and not (prior_archive_context and "更正过" in clause)
    ):
        return False
    field = match.group("field")
    value = re.sub(r"\s+", " ", match.group("value")).strip()
    field_terms = ("期间",) if "期间" in field else ("欠薪", "拖欠", "月数", "月份")
    return bool(value) and any(
        value.casefold() in sentence
        and any(term in sentence for term in field_terms)
        for sentence in re.split(r"[。；;\n]+", archive_text)
    )


def _probe_runtime_persistence_claim(response: str) -> bool:
    """识别完成式持久化表述，不把未来式或否定式当成完成。"""

    if not isinstance(response, str) or not response.strip():
        return False
    if "ARGUMENTS:" in response:
        response = response.rsplit("ARGUMENTS:", 1)[1]
    # 比较或查看既有内容不主张本轮写入；仅移除该引用短语，保留
    # 同句里另一个写入断言。独立的“当前已记录的情况：…”仍需核实。
    response = re.sub(
        r"(?:与|和|核对|查看|查阅)\s*已(?:经)?\s*"
        r"(?:保存|记录|写入|提交|创建|建立)的"
        r"(?:情况|内容|信息|事项|事实|材料|档案)",
        "既有内容",
        response,
    )
    # 从已保存案情接续是读取引用；只移除该短语，不能遮掉同句新增写入。
    read_reference_pattern = (
        r"已(?:经)?从[^。！？?!\n；;]{0,36}?保存的"
        r"(?:案情|案件)(?:档案)?(?:接续|继续|恢复|处理)"
    )
    read_reference = re.search(read_reference_pattern, response)
    # 明确复述更正字段和值时仍须与档案核对，不被纯读取引用豁免。
    if read_reference and (
        _PERSISTENCE_ARCHIVE_CORRECTION_PATTERN.search(response)
        or _PERSISTENCE_ARCHIVE_PARAPHRASE_CORRECTION_PATTERN.search(response)
    ):
        return True
    response = re.sub(read_reference_pattern, "", response)
    # “已将”是完成式；不能被“将…”的未来式分支误否定。
    response = re.sub(r"(已(?:经)?\s*)将", r"\1把", response)
    clauses = re.split(r"[。！？?!\n；;]", response)
    for clause in clauses:
        # 文书内容持久化由产物Oracle检查；不能把明确的正文/文书
        # 写入误判为档案写入。混合档案或新增事实断言仍保留校验。
        if not re.search(
            r"档案|案情|(?:新增|新补充|新提供|新)(?:事实|信息)", clause
        ):
            clause = re.sub(
                r"(?:文书(?:正文)?|正文|DOCX|Word文档|申请书|答辩书|"
                r"通知书|催告函|证据目录|核验清单)"
                r"[^，,。！？?!\n；;]{0,16}?"
                r"(?:已(?:经)?|成功地|现已)\s*"
                r"[^，,。！？?!\n；;]{0,16}?"
                r"(?:保存|记录|写入|提交(?!前)|创建|建立)",
                "",
                clause,
            )
            clause = re.sub(
                r"(?:已(?:经)?|成功地|现已)\s*"
                r"[^，,。！？?!\n；;]{0,24}?"
                r"(?:保存|记录|写入|提交(?!前)|创建|建立)"
                r"\s*(?:到|至|进)?\s*"
                r"(?:DOCX|Word文档|文书(?:正文)?|正文|"
                r"(?:申请书|答辩书|通知书|催告函|证据目录|核验清单)(?:正文)?)",
                "",
                clause,
            )
        if not _PERSISTENCE_COMPLETION_PATTERN.search(clause):
            continue
        if _PERSISTENCE_NEGATIVE_PATTERN.search(clause):
            continue
        if _PERSISTENCE_REFERENCE_PATTERN.search(clause):
            continue
        return True
    return False


def _persistence_claim_references_existing_archive(
    response: str,
    workspace: Path,
) -> bool:
    """接受能与唯一公开档案中既有引用内容核对的状态陈述。"""

    if not isinstance(response, str) or not response.strip():
        return False
    archives = _read_case_archives(workspace)
    if len(archives) != 1:
        return False
    archive_text = re.sub(r"\s+", " ", archives[0]).casefold()
    if "ARGUMENTS:" in response:
        response = response.rsplit("ARGUMENTS:", 1)[1]
    clauses = re.split(r"[。！？?!\n；;]", response)
    # 一个正确的旧值引用不能替同次回复中的新增写入作证。
    # 否定本轮变更的分句不提供新增写入语境；只去掉该分句，
    # 保留同句其他完成式写入，避免旧状态与“本轮没有新材料”误关联。
    write_claim_clauses = [
        re.sub(
            r"(?:本轮|本次)\s*(?:没有|无需|不需要)[^，,。！？?!\n；;]*",
            "",
            clause,
        )
        for clause in clauses
    ]
    if any(
        _probe_runtime_persistence_claim(clause)
        and re.search(r"本轮|本次|刚刚|新增|新补充|新提供", clause)
        for clause in write_claim_clauses
    ):
        return False
    prior_archive_context = False
    for clause in clauses:
        preceding_archive_context = prior_archive_context
        prior_archive_context = bool(
            re.search(r"已从[^。！？?!\n；;]{0,48}保存的案情接续", clause)
        )
        if _PERSISTENCE_NEGATIVE_PATTERN.search(clause):
            continue
        match = _PERSISTENCE_ARCHIVE_REFERENCE_PATTERN.search(clause)
        if match is not None:
            if re.search(r"本轮|本次|刚刚|新增|新补充", clause):
                continue
            reference = re.sub(r"\s+", " ", match.group("reference")).strip(
                " \t\r\n，,。；;：:"
            )
            if reference and reference.casefold() in archive_text:
                return True
        if _existing_archive_fact_list(clause, archive_text):
            return True
        if _existing_three_request_analysis(clause, archive_text):
            return True
        quoted_value = _PERSISTENCE_ARCHIVE_QUOTED_VALUE_PATTERN.search(clause)
        if quoted_value is not None and not re.search(
            r"本轮|本次|刚刚|新增|新补充", clause
        ):
            reference = re.sub(r"\s+", " ", quoted_value.group("reference")).strip()
            if reference and reference.casefold() in archive_text:
                return True
        if _existing_archive_correction(
            clause, archive_text, prior_archive_context=preceding_archive_context
        ):
            return True
    return False


def _persistence_claim_matches_read_archive_summary(
    response: str,
    workspace: Path,
) -> bool:
    """读取回执只证明当前事实摘要，不证明本轮发生了新写入。"""

    archive_text = re.sub(r"[\s*`]+", "", _current_case_fact_text(workspace)).casefold()
    if not archive_text:
        return False
    if "ARGUMENTS:" in response:
        response = response.rsplit("ARGUMENTS:", 1)[1]
    claims = [
        clause.strip()
        for clause in re.split(r"[。！？?!\n；;]", response)
        if _probe_runtime_persistence_claim(clause)
    ]
    for clause in claims:
        match = re.fullmatch(
            r"(?:当前|目前|现有|已有)已(?:经)?记录(?:的)?"
            r"(?:情况|事实|内容|信息|事项)\s*[：:]\s*(?P<reference>.+)",
            clause,
        )
        if match is None:
            return False
        reference = re.sub(r"[\s*`]+", "", match.group("reference")).casefold()
        if len(reference) < 4 or reference not in archive_text:
            return False
    return bool(claims)


def _archive_state_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    """判断本轮是否新建档案或使当前公开 revision/记录发生变化。"""

    before_archives = _archive_state(before)
    after_archives = _archive_state(after)
    if not after_archives:
        return False
    if not before_archives:
        return True
    for case_id, revision, record_ids in after_archives:
        previous = next(
            (
                item
                for item in before_archives
                if item[0] == case_id
            ),
            None,
        )
        if previous is None:
            return True
        if revision > previous[1] or record_ids != previous[2]:
            return True
    return False


def _cleanup_journey_workspaces(run_dir: Path) -> OSError | None:
    """分别、有限重试清理 Harness 自己创建的两个临时 Workspace。"""

    first_error: OSError | None = None
    for relative in ("workspace", "scenario-responder-workspace"):
        raw_target = run_dir / relative
        targets = (raw_target, path_for_io(raw_target))
        last_error: OSError | None = None
        for attempt in range(6):
            cleaned = False
            for target in targets:
                if not target.exists():
                    last_error = None
                    cleaned = True
                    break
                try:
                    shutil.rmtree(target)
                    last_error = None
                    cleaned = True
                    break
                except OSError as error:
                    last_error = error
            if cleaned:
                break
            if attempt < 5:
                sleep(0.5 * (attempt + 1))
        if last_error is not None and raw_target.exists() and first_error is None:
            first_error = last_error
    return first_error


def _archive_state(
    observation: Mapping[str, Any],
) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    archives = observation.get("case_archives", [])
    if not isinstance(archives, list):
        return ()
    state: list[tuple[str, int, tuple[str, ...]]] = []
    for archive in archives:
        if not isinstance(archive, Mapping):
            continue
        case_id = archive.get("case_id")
        revision = archive.get("archive_revision")
        record_ids = archive.get("record_ids", [])
        if not isinstance(case_id, str) or not isinstance(revision, int):
            continue
        if not isinstance(record_ids, list) or not all(
            isinstance(item, str) for item in record_ids
        ):
            continue
        state.append((case_id, revision, tuple(record_ids)))
    return tuple(sorted(state))


def _observed_milestones(
    journey: Journey,
    workspace: Path,
    transcripts: list[dict[str, Any]],
) -> set[str]:
    response_text = "\n".join(
        str(item.get("assistant_response", "")) for item in transcripts
    ).casefold()
    restart_index = next(
        (
            index
            for index, item in enumerate(transcripts)
            if bool(item.get("session_restarted"))
        ),
        None,
    )
    active_fact_text = _current_case_fact_text(workspace).casefold()
    observed: set[str] = set()
    for milestone in journey.milestones:
        milestone_response_text = response_text
        milestone_transcripts = transcripts
        if milestone.after_restart:
            if restart_index is None:
                continue
            milestone_transcripts = transcripts[restart_index:]
            milestone_response_text = "\n".join(
                str(item.get("assistant_response", "")) for item in milestone_transcripts
            ).casefold()
        response_ok = not milestone.response_markers or any(
            marker.casefold() in milestone_response_text
            for marker in milestone.response_markers
        )
        if milestone.after_restart and milestone.id == "archive_recovered_after_restart":
            semantic_recovery = (
                ("案情" in milestone_response_text and "接续" in milestone_response_text)
                or ("档案" in milestone_response_text and "接续" in milestone_response_text)
                or ("档案" in milestone_response_text and "接上" in milestone_response_text)
                or ("档案" in milestone_response_text and "读取" in milestone_response_text)
            )
            response_ok = response_ok or semantic_recovery
            if milestone.archive_markers and not all(
                marker.casefold() in milestone_response_text
                for marker in milestone.archive_markers
            ):
                response_ok = False
            archive_cli_events = [
                event
                for transcript in milestone_transcripts
                for event in transcript.get("runtime_event_summary", [])
                if isinstance(event, Mapping)
                and event.get("item_type") == "command_execution"
                and "scripts.case_archive.cli" in event.get("python_module_names", [])
            ]
            if archive_cli_events and not any(
                event.get("case_archive_read_succeeded") is True
                and event.get("exit_code") == 0
                for event in archive_cli_events
            ):
                response_ok = False
        if milestone.response_all and not all(
            marker.casefold() in milestone_response_text
            for marker in milestone.response_all
        ):
            response_ok = False
        archive_ok = not milestone.archive_markers or all(
            marker.casefold() in active_fact_text
            for marker in milestone.archive_markers
        )
        if response_ok and archive_ok:
            observed.add(milestone.id)
    return observed


def _has_successful_case_archive_read(
    runtime_event_summary: Iterable[Mapping[str, Any]],
) -> bool:
    return any(
        event.get("item_type") == "command_execution"
        and "scripts.case_archive.cli" in event.get("python_module_names", [])
        and event.get("case_archive_read_succeeded") is True
        and event.get("exit_code") == 0
        for event in runtime_event_summary
        if isinstance(event, Mapping)
    )


def _current_case_fact_text(workspace: Path) -> str:
    """Read active facts only; update history cannot satisfy a corrected value."""
    archives = _read_case_archives(workspace)
    if len(archives) != 1:
        return ""
    match = re.search(
        r"(?ms)^## 事实\s*\n(?P<facts>.*?)(?=^## |\Z)", archives[0]
    )
    return match.group("facts") if match is not None else ""


def _read_case_archives(workspace: Path) -> list[str]:
    root = workspace / ".arbibuddy" / "cases"
    if not root.is_dir():
        return []
    archives = sorted(root.glob("*/案情档案.md"))
    # A Journey owns one current case.  Never let a second case satisfy a
    # milestone; the resulting missing milestone is safer than cross-case
    # evidence being treated as current-case evidence.
    if len(archives) != 1:
        return []
    try:
        return [archives[0].read_text(encoding="utf-8")]
    except (OSError, UnicodeError):
        return []


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise JourneyError(f"Journey.{key} 必须是非空字符串")
    return result.strip()


def _marker_list(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise JourneyError(f"{path} 必须是字符串列表")
    return tuple(item.strip() for item in value)


def _parse_delivery_requirement(
    item: Mapping[str, Any],
    path: str,
) -> DeliveryRequirement:
    fields = {
        "document_type",
        "mode",
        "require_case_archive",
        "require_checklist",
        "require_same_case_id",
        "require_current_archive_revision",
    }
    unknown = sorted(set(item) - fields)
    if unknown:
        raise JourneyError(f"{path} 存在未知字段：{', '.join(unknown)}")
    document_type = item.get("document_type")
    if document_type not in _PUBLIC_DOCUMENT_TYPES:
        raise JourneyError(f"{path}.document_type 不是受支持的公开文书类型")
    mode = item.get("mode")
    if mode not in {"candidate", "external_final"}:
        raise JourneyError(f"{path}.mode 必须是 candidate 或 external_final")
    flags: dict[str, bool] = {}
    for flag in (
        "require_case_archive",
        "require_checklist",
        "require_same_case_id",
        "require_current_archive_revision",
    ):
        value = item.get(flag, True)
        if not isinstance(value, bool):
            raise JourneyError(f"{path}.{flag} 必须是布尔值")
        flags[flag] = value
    return DeliveryRequirement(document_type=document_type, mode=mode, **flags)


def _default_platform_command(platform: str) -> list[str]:
    name = "codex" if platform == "codex" else "claude"
    if os.name == "nt":
        native_executable = shutil.which(f"{name}.exe")
        if native_executable:
            return [native_executable]
    located = shutil.which(name) or shutil.which(f"{name}.ps1")
    if located and located.casefold().endswith(".ps1"):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", located]
    return [located or name]


def _copy_runtime_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise RuntimeAdapterError("运行时上下文必须是对象")
    try:
        value = json.loads(json.dumps(context, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise RuntimeAdapterError("运行时上下文必须可序列化") from error
    if not isinstance(value, dict):
        raise RuntimeAdapterError("运行时上下文必须是对象")
    return value


def _runtime_process_environment(platform: str = "codex") -> dict[str, str]:
    """隔离模型主导 CLI，并使 Skill 的 ``python`` 命令复用 Harness 解释器。"""

    environment = platform_process_environment()
    environment.pop("PYTHONPATH", None)
    python_directory = str(Path(sys.executable).parent)
    path_key = next(
        (key for key in environment if key.casefold() == "path"),
        "PATH",
    )
    current_path = environment.get(path_key, "")
    entries = current_path.split(os.pathsep) if current_path else []
    if not entries or os.path.normcase(os.path.normpath(entries[0])) != os.path.normcase(
        os.path.normpath(python_directory)
    ):
        environment[path_key] = os.pathsep.join(
            part for part in (python_directory, current_path) if part
        )
    if platform == "codex" and not environment.get("CODEX_HOME", "").strip():
        account_home = (
            environment.get("USERPROFILE", "").strip()
            or environment.get("HOME", "").strip()
        )
        if account_home:
            environment["CODEX_HOME"] = str(Path(account_home) / ".codex")
    return environment


def _foreign_skill_resource_hashes(
    events: Iterable[Mapping[str, Any]], installed_root: str,
) -> tuple[str, ...]:
    """Detect observable absolute reads of a different named installation.

    This checks common shell content-read/search commands, not model prose or
    business facts. Relative resources continue to resolve in the selected
    workspace. It is a source identity check, not an OS read sandbox.
    """
    read_command_pattern = re.compile(
        r"(?i)(?:^|[\s;&|])(?:&\s*)?"
        r"(?:get-content|gc|cat|type|more|head|tail|less|sed|awk|"
        r"select-string|sls|grep|rg|findstr)(?=\s|$)"
    )
    root_pattern = re.compile(
        r"(?:[a-z]:[\\/]+|/)[^\"'\r\n]*?[\\/]+"
        r"(?:\.agents|\.codex|\.claude)[\\/]+skills[\\/]+arbibuddy"
        r"(?=[\\/\"'\s]|$)",
        re.IGNORECASE,
    )
    expected = str(Path(installed_root).resolve()).casefold()
    foreign: set[str] = set()
    for event in events:
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str) or not read_command_pattern.search(command):
            continue
        for match in root_pattern.finditer(command):
            root = str(Path(match.group(0)).resolve()).casefold()
            if root != expected:
                foreign.add(hashlib.sha256(root.encode("utf-8")).hexdigest())
    return tuple(sorted(foreign))


_RUNTIME_DIAGNOSTIC_LIMIT = 4096
_RUNTIME_DIAGNOSTIC_COMMAND_ITEM_LIMIT = 512
_RUNTIME_DIAGNOSTIC_EVENT_LIMIT = 32
_RUNTIME_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:token|secret|password|passwd|api[_-]?key|authorization|bearer)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_RUNTIME_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:[\\/]+|\\\\+|/(?!/))[^\r\n\s\"'<>]+"
)


def _runtime_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate_runtime_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _sanitize_runtime_text(value: object, *, limit: int = _RUNTIME_DIAGNOSTIC_LIMIT) -> str:
    """保留诊断形状，同时移除用户路径、令牌和其他凭据值。"""

    text = _runtime_text(value)
    text = _RUNTIME_SECRET_PATTERN.sub("<REDACTED>", text)
    text = _RUNTIME_ABSOLUTE_PATH_PATTERN.sub("<PATH>", text)
    sanitized, _ = _truncate_runtime_text(text, limit)
    return sanitized


def _sanitize_runtime_command(command: object) -> list[str]:
    if isinstance(command, (list, tuple)):
        values = list(command)[:64]
    else:
        values = [command]
    if values:
        # CLI 最后一个参数是当前用户消息；诊断不应重复保存用户输入。
        values[-1] = "<USER_MESSAGE>"
    sanitized: list[str] = []
    redact_next = False
    sensitive_flags = {
        "--token",
        "--secret",
        "--password",
        "--passwd",
        "--api-key",
        "--apikey",
        "--authorization",
        "--bearer",
    }
    for value in values:
        item = _runtime_text(value)
        lowered = item.casefold()
        if redact_next:
            sanitized.append("<REDACTED>")
            redact_next = False
            continue
        if lowered in sensitive_flags:
            sanitized.append(
                _sanitize_runtime_text(
                    item,
                    limit=_RUNTIME_DIAGNOSTIC_COMMAND_ITEM_LIMIT,
                )
            )
            redact_next = True
            continue
        sanitized.append(
            _sanitize_runtime_text(
                item,
                limit=_RUNTIME_DIAGNOSTIC_COMMAND_ITEM_LIMIT,
            )
        )
    return sanitized


def _recover_completed_timeout_response(
    *,
    command: object,
    error: subprocess.TimeoutExpired,
    duration_seconds: float,
) -> _AgentResponse | None:
    """Codex 可能已发出 turn.completed，但 CLI 外壳仍未退出。"""

    partial_stdout = getattr(error, "stdout", None)
    if not partial_stdout:
        partial_stdout = getattr(error, "output", None)
    stdout = _runtime_text(partial_stdout)
    events = _parse_json_lines(stdout)
    if not any(event.get("type") == "turn.completed" for event in events):
        return None
    session_id = _session_id(events)
    text = _response_text(events, stdout)
    if not session_id or not text.strip():
        return None
    return _AgentResponse(
        text=text,
        session_id=session_id,
        events=tuple(events),
        command=tuple(str(item) for item in command),
        duration_seconds=duration_seconds,
    )


def _timeout_diagnostic(
    *,
    command: object,
    error: subprocess.TimeoutExpired,
    timeout_seconds: int,
    workspace: Path,
    working_directory_before: Mapping[str, Any],
) -> dict[str, Any]:
    partial_stdout = getattr(error, "stdout", None)
    if not partial_stdout:
        partial_stdout = getattr(error, "output", None)
    partial_stderr = getattr(error, "stderr", None)
    stdout_text = _runtime_text(partial_stdout)
    stderr_text = _runtime_text(partial_stderr)
    events = _parse_json_lines(stdout_text)
    parsed_events: list[dict[str, Any]] = []
    for event in events[:_RUNTIME_DIAGNOSTIC_EVENT_LIMIT]:
        item = event.get("item")
        parsed_events.append(
            {
                "type": (
                    _sanitize_runtime_text(event.get("type"), limit=128)
                    if isinstance(event.get("type"), str)
                    else None
                ),
                "event_keys": sorted(str(key) for key in event)[:20],
                "item_type": (
                    _sanitize_runtime_text(item.get("type"), limit=128)
                    if isinstance(item, Mapping) and isinstance(item.get("type"), str)
                    else None
                ),
                "has_text": bool(_response_text([event], "").strip()),
            }
        )
    stdout, stdout_truncated = _truncate_runtime_text(
        _sanitize_runtime_text(stdout_text),
        _RUNTIME_DIAGNOSTIC_LIMIT,
    )
    stderr, stderr_truncated = _truncate_runtime_text(
        _sanitize_runtime_text(stderr_text),
        _RUNTIME_DIAGNOSTIC_LIMIT,
    )
    runtime_activity = _runtime_activity_diagnostic(events)
    return {
        "kind": "timeout",
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": (
            float(error.timeout)
            if isinstance(error.timeout, (int, float))
            else float(timeout_seconds)
        ),
        "command": _sanitize_runtime_command(command),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "working_directory": {
            "before_call": dict(working_directory_before),
            "at_timeout": _runtime_workspace_state(
                workspace, stage="at_timeout"
            ),
        },
        "partial_event_count": len(events),
        "parsed_events": parsed_events,
        "partial_runtime_event_summary": _summarize_runtime_events(
            tuple(events),
            workspace=workspace,
        ),
        "runtime_activity": runtime_activity,
        "partial_session_id_present": _session_id(events) is not None,
    }


def _runtime_activity_diagnostic(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize runtime activity without persisting tool arguments or message text."""

    tool_types = {
        "command_execution",
        "web_search",
        "file_change",
        "custom_tool_call",
        "function_call",
        "local_shell_call",
        "mcp_tool_call",
    }
    started_ids: set[tuple[str, str]] = set()
    completed_ids: set[tuple[str, str]] = set()
    started_without_id = 0
    completed_without_id = 0
    summarized: list[dict[str, Any]] = []
    last_event: Mapping[str, Any] | None = None
    last_item: Mapping[str, Any] | None = None

    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        item_mapping = item if isinstance(item, Mapping) else None
        item_type = item_mapping.get("type") if item_mapping is not None else None
        if not isinstance(item_type, str) or item_type not in tool_types:
            continue
        event_type = event.get("type")
        item_id = item_mapping.get("id")
        if isinstance(item_id, str) and item_id:
            key = (item_type, item_id)
            if event_type == "item.started":
                started_ids.add(key)
            elif event_type == "item.completed":
                completed_ids.add(key)
        elif event_type == "item.started":
            started_without_id += 1
        elif event_type == "item.completed":
            completed_without_id += 1

        raw_timestamp: object = event.get("timestamp")
        timestamp_source = "event.timestamp" if raw_timestamp is not None else None
        if raw_timestamp is None and item_mapping is not None:
            raw_timestamp = item_mapping.get("completed_at_ms") or item_mapping.get(
                "started_at_ms"
            )
            if raw_timestamp is not None:
                timestamp_source = "item.*_at_ms"
        timestamp_utc = _runtime_event_timestamp(raw_timestamp)
        summarized.append(
            {
                "index": index,
                "timestamp_utc": timestamp_utc,
                "timestamp_source": timestamp_source if timestamp_utc else None,
                "event_type": _sanitize_runtime_text(event_type, limit=80),
                "item_type": item_type,
            }
        )
        last_event = event
        last_item = item_mapping

    started_count = len(started_ids) + started_without_id
    completed_count = len(completed_ids) + completed_without_id
    pending_ids = started_ids - completed_ids
    pending_count = len(pending_ids) + max(
        started_without_id - completed_without_id, 0
    )
    last_timestamp: str | None = None
    last_timestamp_source: str | None = None
    if last_event is not None:
        raw_timestamp = last_event.get("timestamp")
        if raw_timestamp is not None:
            last_timestamp_source = "event.timestamp"
        elif last_item is not None:
            raw_timestamp = last_item.get("completed_at_ms") or last_item.get(
                "started_at_ms"
            )
            if raw_timestamp is not None:
                last_timestamp_source = "item.*_at_ms"
        last_timestamp = _runtime_event_timestamp(raw_timestamp)
    if pending_count:
        progress_state = "tool_in_flight_at_timeout"
    elif completed_count:
        progress_state = "tools_completed_no_tool_in_flight_at_timeout"
    else:
        progress_state = "no_tool_activity_observed_before_timeout"
    return {
        "timeout_observed_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "event_count": len(events),
        "tool_call_started_count": started_count,
        "completed_tool_call_count": completed_count,
        "pending_tool_call_count": pending_count,
        "last_event_type": (
            _sanitize_runtime_text(last_event.get("type"), limit=80)
            if last_event is not None
            else None
        ),
        "last_item_type": (
            _sanitize_runtime_text(last_item.get("type"), limit=80)
            if last_item is not None
            else None
        ),
        "last_event_timestamp_utc": last_timestamp,
        "last_event_timestamp_source": last_timestamp_source,
        "last_activity_state": progress_state,
        "events": summarized[-_RUNTIME_DIAGNOSTIC_EVENT_LIMIT:],
    }


def _runtime_event_timestamp(value: object) -> str | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            numeric_value = float(value)
            timestamp_seconds = (
                numeric_value / 1000
                if abs(numeric_value) >= 10_000_000_000
                else numeric_value
            )
            return datetime.fromtimestamp(
                timestamp_seconds, timezone.utc
            ).isoformat(timespec="milliseconds")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _require_runtime_workspace(workspace: str | Path) -> Path:
    """Resolve and validate the isolated Agent cwd before invoking a CLI."""

    resolved = Path(workspace).resolve()
    state = _runtime_workspace_state(resolved, stage="preflight")
    if not state["exists"] or not state["is_directory"]:
        raise RuntimeAdapterError(
            "Agent 工作区 cwd 不存在或不是目录",
            details={
                "kind": "working_directory_invalid",
                "working_directory": state,
            },
        )
    return resolved


def _runtime_workspace_state(
    workspace: str | Path,
    *,
    stage: str,
) -> dict[str, Any]:
    """Record cwd identity and liveness without persisting the absolute path."""

    raw_path = Path(workspace)
    resolved = raw_path.resolve()
    try:
        exists = resolved.exists()
        is_directory = resolved.is_dir()
        parent_exists = resolved.parent.is_dir()
    except OSError:
        exists = False
        is_directory = False
        parent_exists = False
    try:
        path_stat = resolved.stat()
        modified_time_ns: int | None = path_stat.st_mtime_ns
    except OSError:
        modified_time_ns = None
    return {
        "role": "journey_agent_workspace",
        "stage": stage,
        "exists": exists,
        "is_directory": is_directory,
        "parent_exists": parent_exists,
        "path_length": len(str(resolved)),
        "path_sha256": hashlib.sha256(
            str(resolved).encode("utf-8", errors="replace")
        ).hexdigest(),
        "modified_time_ns": modified_time_ns,
        "installed_skill_exists": any(
            candidate.is_dir()
            for candidate in (
                resolved / ".agents" / "skills" / "arbibuddy",
                resolved / ".claude" / "skills" / "arbibuddy",
            )
        ),
    }


def _sanitize_runtime_diagnostic(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """再次清洗 Runtime Adapter 细节，避免 fake/第三方适配器绕过边界。"""

    if not isinstance(details, Mapping):
        return {}

    def sanitize(value: object, key: str = "", depth: int = 0) -> object:
        if depth > 3:
            return "<TRUNCATED>"
        if key == "command":
            return _sanitize_runtime_command(value)
        if key in {"stdout", "stderr", "output", "partial_stdout", "partial_stderr"}:
            return _sanitize_runtime_text(value)
        if isinstance(value, Mapping):
            return {
                str(item_key): sanitize(item_value, str(item_key), depth + 1)
                for item_key, item_value in list(value.items())[:64]
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item, key, depth + 1) for item in list(value)[:64]]
        if isinstance(value, str):
            return _sanitize_runtime_text(value, limit=512)
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return _sanitize_runtime_text(value, limit=512)

    return {
        str(key): sanitize(value, str(key))
        for key, value in list(details.items())[:64]
    }


def _render_runtime_context(context: Mapping[str, Any]) -> str:
    payload = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    skill_root = context.get("installed_skill_root")
    skill_binding = (
        f"本会话 ArbiBuddy 的安装入口为 {(Path(skill_root) / 'SKILL.md').as_posix()}；"
        "加载该入口，并相对同一安装根读取参考与执行工具。其他目录中的同名 Skill 不属于本会话安装副本。\n"
        if isinstance(skill_root, str) and skill_root.strip()
        else ""
    )
    return (
        "ArbiBuddy 私有运行时上下文（仅用于工具事实与能力边界；不得向用户展示或复述）：\n"
        f"{skill_binding}"
        "<arbibuddy-runtime-context>\n"
        f"{payload}\n"
        "</arbibuddy-runtime-context>\n"
        "原始用户消息仍是唯一的用户输入；不要改变、拼接或替换它。"
    )


def _write_codex_runtime_context(
    workspace: Path,
    context: Mapping[str, Any],
) -> None:
    """通过隔离工作区的项目指令文件给 Codex 提供隐藏初始化上下文。"""

    target = workspace / "AGENTS.md"
    begin = "<!-- arbibuddy-runtime-context:begin -->"
    end = "<!-- arbibuddy-runtime-context:end -->"
    block = f"{begin}\n{_render_runtime_context(context)}\n{end}\n"
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing:
            if begin not in existing or end not in existing:
                raise RuntimeAdapterError(
                    "Agent 工作区已有 AGENTS.md，无法安全追加私有运行时上下文"
                )
            start = existing.index(begin)
            finish = existing.index(end, start) + len(end)
            updated = existing[:start] + block.rstrip("\n") + existing[finish:]
        else:
            updated = block
        target.write_text(updated, encoding="utf-8")
    except RuntimeAdapterError:
        raise
    except (OSError, UnicodeError) as error:
        raise RuntimeAdapterError(
            f"无法准备 Codex 私有运行时上下文：{error.__class__.__name__}"
        ) from error


def _build_runtime_context(
    *,
    skill_root: Path,
    workspace: Path,
    capability_report: Mapping[str, Any],
) -> dict[str, Any]:
    invocation = build_case_archive_invocation(
        skill_root=skill_root,
        workspace_root=workspace,
        python_executable=sys.executable,
    )
    raw_capabilities = capability_report.get("capabilities", {})
    if not isinstance(raw_capabilities, Mapping):
        raise RuntimeAdapterError("能力预检结果缺少 capabilities 对象")
    context_capabilities = dict(raw_capabilities)
    context_capabilities.update(
        {
            "document_capabilities": capability_report.get(
                "document_capabilities", {}
            ),
            "complete_document_pipeline_available": bool(
                capability_report.get("complete_document_pipeline_available")
            ),
            "attachment_presentation": capability_report.get(
                "attachment_presentation", {}
            ),
            "platform_version": capability_report.get("platform_version"),
            "platform_version_probe": capability_report.get(
                "platform_version_probe", {}
            ),
        }
    )
    complete_document_pipeline = bool(
        context_capabilities["complete_document_pipeline_available"]
    )
    return {
        "schema_version": 1,
        "workspace_root": str(workspace.resolve()),
        "installed_skill_root": str(skill_root.resolve()),
        "case_archive": invocation.as_dict(),
        "capabilities": context_capabilities,
        "degradation": {
            "case_archive": {
                "allowed": True,
                "required_for_case_facts": True,
            },
            "docx": {
                "allowed": complete_document_pipeline,
                "must_not_claim_success": not complete_document_pipeline,
            },
            "checklist": {
                "allowed": complete_document_pipeline,
                "must_not_claim_success": not complete_document_pipeline,
            },
            "plain_text": {"allowed": True},
            "unverified_current_rules": {
                "allowed": True,
                "must_mark_as_pending_verification": not bool(
                    isinstance(raw_capabilities.get("network"), Mapping)
                    and raw_capabilities["network"].get("available")
                ),
            },
        },
    }


def _build_restart_context(
    *,
    workspace: Path,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    return RestartContextBuilder().build(
        workspace=workspace,
        observation=observation,
    )


def _restart_observation_snapshot(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture public archive metadata at restart-context injection time."""

    archives = observation.get("case_archives", [])
    return {
        "case_archive": bool(observation.get("case_archive")),
        "case_archives": [
            {
                key: archive[key]
                for key in (
                    "case_id",
                    "archive_revision",
                    "archive_path",
                    "archive_sha256",
                    "confirmed_fact_summary_sha256",
                )
                if key in archive
            }
            for archive in archives
            if isinstance(archive, Mapping)
        ]
        if isinstance(archives, list)
        else [],
    }


def _with_restart_context(
    runtime_context: Mapping[str, Any],
    restart_context: Mapping[str, Any],
) -> dict[str, Any]:
    updated = _copy_runtime_context(runtime_context)
    updated["restart_recovery"] = dict(restart_context)
    return updated


def _runtime_discovered(
    *,
    platform_command: list[str] | None,
    transcripts: list[dict[str, Any]],
    observation: Mapping[str, Any],
    platform_discovery: Mapping[str, Any],
) -> bool:
    """Require a real platform command plus public Skill-shaped behavior."""

    if platform_command is not None or not transcripts:
        return False
    if platform_discovery.get("runtime_discovered"):
        return True
    return bool(
        observation.get("case_archive")
        or observation.get("docx")
        or observation.get("checklist")
    )


def _resolve_command(command: list[str]) -> list[str]:
    resolved = list(command)
    executable = resolved[0]
    if not Path(executable).is_absolute() and not any(
        separator in executable for separator in ("/", "\\")
    ):
        located = shutil.which(executable)
        if located:
            resolved[0] = located
    return resolved


def _parse_json_lines(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _extract_managed_view_receipts(
    events: tuple[dict[str, Any], ...],
    *,
    platform: str,
    workspace: Path,
) -> list[dict[str, Any]]:
    """Extract only successful Codex command-execution receipts for public view."""

    if platform != "codex":
        return []
    receipts: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "command_execution":
            continue
        exit_code = item.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
            continue
        tokens = _managed_view_command_tokens(
            item.get("command"),
            installed_skill_root=workspace / ".agents" / "skills" / "arbibuddy",
        )
        if tokens is None:
            continue
        request = _managed_view_command_request(tokens, workspace)
        if request is None:
            continue
        output = item.get("aggregated_output")
        if not isinstance(output, str):
            continue
        parsed = []
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping) and value.get("schema_version") == 1:
                parsed.append(value)
        if len(parsed) != 1:
            continue
        receipt = _normalize_managed_view_receipt(
            parsed[0],
            request=request,
            workspace=workspace,
        )
        if receipt is not None and receipt not in receipts:
            receipts.append(receipt)
    return receipts


def _summarize_runtime_events(
    events: tuple[dict[str, Any], ...],
    *,
    workspace: Path,
    installed_skill_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Summarize observed platform tool outcomes without raw payloads."""

    if any(
        event.get("type") in {"assistant", "user"}
        and isinstance(event.get("message"), Mapping)
        for event in events
        if isinstance(event, Mapping)
    ):
        return _summarize_claude_archive_read_events(
            events,
            workspace=workspace,
            installed_skill_root=installed_skill_root,
        )

    summaries: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        if (
            not isinstance(item, Mapping)
            or event.get("type")
            not in {"item.started", "item.updated", "item.completed"}
        ):
            continue
        raw_item_type = item.get("type")
        item_type = (
            raw_item_type
            if isinstance(raw_item_type, str)
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", raw_item_type)
            else "other"
        )
        item_name_value = item.get("tool_name", item.get("name"))
        item_name = (
            item_name_value
            if isinstance(item_name_value, str)
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", item_name_value)
            else None
        )
        summary: dict[str, Any] = {
            "event_type": event.get("type"),
            "item_type": item_type,
            "item_name": item_name,
        }
        if item_type != "command_execution":
            if len(summaries) < 64:
                summaries.append(summary)
            continue

        raw_command = item.get("command")
        if isinstance(raw_command, str):
            command_kind = "string"
            command_text = raw_command
            try:
                loose_tokens = [
                    _unquote_managed_view_token(token)
                    for token in shlex.split(raw_command, posix=False)
                ]
            except ValueError:
                loose_tokens = []
        elif isinstance(raw_command, (list, tuple)) and all(
            isinstance(token, str) for token in raw_command
        ):
            command_kind = "argument_list"
            loose_tokens = [
                _unquote_managed_view_token(token) for token in raw_command
            ]
            command_text = " ".join(loose_tokens)
        else:
            command_kind = "other"
            loose_tokens = []
            command_text = ""

        strict_tokens = _managed_view_command_tokens(
            raw_command,
            installed_skill_root=installed_skill_root
            or workspace / ".agents" / "skills" / "arbibuddy",
        )
        observed_tokens = strict_tokens or loose_tokens
        request = (
            _managed_view_command_request(strict_tokens, workspace)
            if strict_tokens is not None
            else None
        )
        has_runtime_module = "scripts.documents.runtime_cli" in command_text
        has_view_token = any(
            token.casefold() == "view" for token in observed_tokens
        )
        python_modules = sorted(
            set(re.findall(r"\bscripts(?:\.[A-Za-z_]\w*)+\b", command_text))
        )[:8]
        inline_python = any(token == "-c" for token in loose_tokens)
        output = item.get("aggregated_output")
        output_lines = output.splitlines() if isinstance(output, str) else []
        parsed_objects: list[Mapping[str, Any]] = []
        for line in output_lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                parsed_objects.append(value)
        view_responses = [
            value
            for value in parsed_objects
            if value.get("schema_version") == 1
            and isinstance(value.get("case_id"), str)
            and isinstance(value.get("delivery_state"), str)
            and isinstance(value.get("stopped"), bool)
        ]
        response = view_responses[0] if len(view_responses) == 1 else None
        case_archive_receipts = [
            value
            for value in parsed_objects
            if value.get("contract_version") == "case-archive-v1"
            and value.get("operation") in {"create", "read", "commit"}
            and value.get("ok") is True
            and isinstance(value.get("result"), Mapping)
        ]
        archive_read_succeeded = False
        if (
            type(item.get("exit_code")) is int
            and item["exit_code"] == 0
            and "scripts.case_archive.cli" in python_modules
            and len(case_archive_receipts) == 1
            and case_archive_receipts[0].get("operation") == "read"
        ):
            result = case_archive_receipts[0]["result"]
            result_case_id = result.get("case_id")
            result_revision = result.get("revision")
            current_archives = inspect_public_artifacts(workspace).get(
                "case_archives", []
            )
            archive_read_succeeded = (
                isinstance(result_case_id, str)
                and isinstance(result_revision, int)
                and not isinstance(result_revision, bool)
                and len(current_archives) == 1
                and current_archives[0].get("case_id") == result_case_id
                and current_archives[0].get("archive_revision") == result_revision
            )
        receipt_status = "not_a_view_response"
        if response is not None:
            if request is None:
                receipt_status = "unbound_command"
            else:
                receipt_status = (
                    "passed"
                    if _normalize_managed_view_receipt(
                        response,
                        request=request,
                        workspace=workspace,
                    )
                    is not None
                    else "rejected"
                )
        elif has_runtime_module and has_view_token:
            receipt_status = "missing_or_unparseable_view_response"
        error_code = response.get("error_code") if response is not None else None
        if not isinstance(error_code, str) or re.fullmatch(
            r"[a-z0-9_]{1,64}", error_code
        ) is None:
            error_code = None
        presentation_files = (
            response.get("presentation_files") if response is not None else None
        )
        summary.update(
            {
                "command_kind": command_kind,
                "command_token_count": len(loose_tokens),
                "python_module_names": python_modules,
                "inline_python": inline_python,
                "runtime_cli_module_seen": has_runtime_module,
                "view_token_seen": has_view_token,
                "workspace_flag_seen": "--workspace" in observed_tokens,
                "json_flag_seen": "--json" in observed_tokens,
                "strict_command_match": strict_tokens is not None,
                "request_workspace_match": request is not None,
                "exit_code": (
                    item.get("exit_code")
                    if isinstance(item.get("exit_code"), int)
                    and not isinstance(item.get("exit_code"), bool)
                    else None
                ),
                "output_line_count": len(output_lines),
                "output_json_object_count": len(parsed_objects),
                "case_archive_read_succeeded": archive_read_succeeded,
                "managed_view_response_count": len(view_responses),
                "managed_view_receipt_status": receipt_status,
                "managed_view_stopped": (
                    response.get("stopped")
                    if response is not None
                    and isinstance(response.get("stopped"), bool)
                    else None
                ),
                "managed_view_error_code": error_code,
                "presentation_file_count": (
                    len(presentation_files)
                    if isinstance(presentation_files, list)
                    else None
                ),
            }
        )
        # A long document turn can exceed the diagnostic budget before its
        # final managed view completes. Keep every view outcome even then:
        # the Oracle's delivery status is derived from these events.
        if len(summaries) < 64 or (has_runtime_module and has_view_token):
            summaries.append(summary)
    return summaries


def _summarize_claude_archive_read_events(
    events: tuple[dict[str, Any], ...],
    *,
    workspace: Path,
    installed_skill_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Bind Claude Bash read receipts to the current public case revision."""

    tool_calls: dict[str, tuple[str, list[str]]] = {}
    summaries: list[dict[str, Any]] = []
    current_archives = inspect_public_artifacts(workspace).get("case_archives", [])

    for event in events:
        if not isinstance(event, Mapping):
            continue
        message = event.get("message")
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        if event.get("type") == "assistant":
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Bash":
                    continue
                call_id = block.get("id")
                tool_input = block.get("input")
                command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
                tokens = _claude_case_archive_read_command_tokens(
                    command,
                    workspace=workspace,
                    installed_skill_root=installed_skill_root,
                )
                if isinstance(call_id, str) and tokens is not None:
                    tool_calls[call_id] = (command, tokens)
            continue

        if event.get("type") != "user":
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id")
            call = tool_calls.pop(call_id, None) if isinstance(call_id, str) else None
            if call is None:
                continue
            command, tokens = call
            result_text = _claude_tool_result_text(block.get("content"))
            receipt_objects = _json_objects_from_tool_result(result_text)
            receipt = receipt_objects[0] if len(receipt_objects) == 1 else None
            result = receipt.get("result") if isinstance(receipt, Mapping) else None
            case_id = result.get("case_id") if isinstance(result, Mapping) else None
            revision = result.get("revision") if isinstance(result, Mapping) else None
            tool_succeeded = block.get("is_error") is False
            archive_read_succeeded = bool(
                tool_succeeded
                and isinstance(receipt, Mapping)
                and receipt.get("contract_version") == "case-archive-v1"
                and receipt.get("operation") == "read"
                and receipt.get("ok") is True
                and isinstance(case_id, str)
                and type(revision) is int
                and len(current_archives) == 1
                and current_archives[0].get("case_id") == case_id
                and current_archives[0].get("archive_revision") == revision
            )
            exit_match = re.search(r"(?m)^Exit code (\d+)\s*$", result_text)
            exit_code = (
                int(exit_match.group(1))
                if exit_match is not None
                else 0
                if tool_succeeded
                else None
            )
            summary: dict[str, Any] = {
                "event_type": "item.completed",
                "item_type": "command_execution",
                "item_name": "Bash",
                "command_kind": "string",
                "command_token_count": len(tokens),
                "python_module_names": ["scripts.case_archive.cli"],
                "exit_code": exit_code,
                "output_json_object_count": len(receipt_objects),
                "case_archive_read_succeeded": archive_read_succeeded,
            }
            if isinstance(case_id, str):
                summary["case_id"] = case_id[:128]
            if type(revision) is int:
                summary["archive_revision"] = revision
            summaries.append(summary)

    return summaries


def _claude_case_archive_read_command_tokens(
    command: object,
    *,
    workspace: Path,
    installed_skill_root: str | Path | None,
) -> list[str] | None:
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if "&&" in tokens:
        if tokens.count("&&") != 1 or len(tokens) < 4 or tokens[:1] != ["cd"]:
            return None
        expected_skill_root = (
            Path(installed_skill_root).expanduser().resolve()
            if isinstance(installed_skill_root, (str, Path))
            else None
        )
        command_skill_root = Path(tokens[1]).expanduser().resolve()
        if expected_skill_root is not None and command_skill_root != expected_skill_root:
            return None
        if tokens[2] != "&&":
            return None
        tokens = tokens[3:]
    if any(token in {";", "|", "||", "&&", "&", ">", ">>", "<"} for token in tokens):
        return None
    if len(tokens) != 9:
        return None
    executable = re.split(r"[\\/]", tokens[0])[-1].casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    if tokens[1:7] != [
        "-B",
        "-X",
        "utf8",
        "-m",
        "scripts.case_archive.cli",
        "--root",
    ]:
        return None
    if Path(tokens[7]).expanduser().resolve() != workspace.resolve():
        return None
    if tokens[8] != "read-current":
        return None
    return tokens


def _claude_tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        )
    return ""


def _json_objects_from_tool_result(content: str) -> list[Mapping[str, Any]]:
    if not content.strip():
        return []
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        values: list[Any] = []
        for line in content.splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    else:
        values = [value]
    return [value for value in values if isinstance(value, Mapping)]


def _managed_view_command_tokens(
    command: object,
    *,
    installed_skill_root: str | Path | None = None,
) -> list[str] | None:
    if isinstance(command, (list, tuple)):
        if not all(isinstance(item, str) for item in command):
            return None
        raw_tokens = list(command)
    elif isinstance(command, str):
        windows_tokens = (
            _windows_command_line_tokens(command) if os.name == "nt" else None
        )
        if windows_tokens and Path(windows_tokens[0]).name.casefold() in {
            "powershell",
            "powershell.exe",
            "pwsh",
            "pwsh.exe",
        }:
            # CommandLineToArgvW handles nested JSON quoting in Codex's
            # PowerShell wrapper; shlex raises on this valid Windows command.
            raw_tokens = windows_tokens
        else:
            try:
                raw_tokens = shlex.split(command, posix=False)
            except ValueError:
                return None
        if raw_tokens is None:
            return None
    else:
        return None
    if raw_tokens and raw_tokens[0] == "&":
        raw_tokens = raw_tokens[1:]
    if raw_tokens and Path(raw_tokens[0]).name.casefold() in {
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }:
        raw_tokens = _codex_powershell_command_tokens(raw_tokens)
        if raw_tokens is None:
            return None
        if raw_tokens and raw_tokens[0] == "&":
            raw_tokens = raw_tokens[1:]
    tokens = [_unquote_managed_view_token(item) for item in raw_tokens]
    if tokens and tokens[0].casefold() == "set-location":
        # Accept only a literal, bound installation-directory prefix. Keep
        # rejecting arbitrary shell composition even when its output looks valid.
        if len(tokens) < 4 or tokens[2] != "&&" or installed_skill_root is None:
            return None
        directory = tokens[1]
        if any(char in directory for char in "$`\n\r"):
            return None
        try:
            if (
                not Path(directory).is_absolute()
                or Path(directory).resolve() != Path(installed_skill_root).resolve()
            ):
                return None
        except (OSError, RuntimeError, ValueError):
            return None
        tokens = tokens[3:]
        if tokens[:1] == ["&"]:
            tokens = tokens[1:]
    if any(item in {";", "|", "||", "&&", "&", ">", ">>", "<"} for item in tokens):
        return None
    if len(tokens) != 11:
        return None
    executable = Path(tokens[0]).name.casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    if tokens[1:7] != [
        "-B",
        "-X",
        "utf8",
        "-m",
        "scripts.documents.runtime_cli",
        "--workspace",
    ]:
        return None
    if tokens[8:10] != ["view", "--json"]:
        return None
    return tokens


def _windows_command_line_tokens(command: str) -> list[str] | None:
    """Parse the argv string Codex emits on Windows without shell heuristics."""

    if os.name != "nt":
        return None
    try:
        argument_count = ctypes.c_int()
        command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
        command_line_to_argv.argtypes = (
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_int),
        )
        command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = command_line_to_argv(command, ctypes.byref(argument_count))
        if not argv:
            return None
        try:
            return [argv[index] for index in range(argument_count.value)]
        finally:
            local_free = ctypes.windll.kernel32.LocalFree
            local_free.argtypes = (ctypes.c_void_p,)
            local_free.restype = ctypes.c_void_p
            local_free(ctypes.cast(argv, ctypes.c_void_p))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _codex_powershell_command_tokens(
    outer_tokens: list[str],
) -> list[str] | None:
    """Unwrap only Codex's simple PowerShell -Command transport."""

    index = 1
    while index < len(outer_tokens):
        option = outer_tokens[index].casefold()
        if option in {"-nologo", "-noprofile", "-noninteractive"}:
            index += 1
            continue
        if option in {"-command", "-c"} and index == len(outer_tokens) - 2:
            try:
                return shlex.split(outer_tokens[index + 1], posix=False)
            except ValueError:
                return None
        return None
    return None


def _unquote_managed_view_token(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace(r"\\", "\\").replace(r"\"", '"')
    return value


def _managed_view_command_request(
    tokens: list[str],
    workspace: Path,
) -> dict[str, str] | None:
    requested_workspace = tokens[7]
    try:
        actual_path = Path(requested_workspace).expanduser().resolve()
        expected_path = workspace.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        not actual_path.is_absolute()
        or os.path.normcase(str(actual_path)) != os.path.normcase(str(expected_path))
    ):
        return None
    try:
        request = json.loads(tokens[10])
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(request, Mapping)
        or set(request) != {"case_id", "delivery_set_id"}
        or not isinstance(request.get("case_id"), str)
        or not isinstance(request.get("delivery_set_id"), str)
        or not request["case_id"]
        or not request["delivery_set_id"]
    ):
        return None
    return {
        "case_id": request["case_id"],
        "delivery_set_id": request["delivery_set_id"],
    }


def _normalize_managed_view_receipt(
    view: Mapping[str, Any],
    *,
    request: Mapping[str, str],
    workspace: Path | None = None,
) -> dict[str, Any] | None:
    case_id = request.get("case_id")
    delivery_set_id = request.get("delivery_set_id")
    if (
        not isinstance(case_id, str)
        or re.fullmatch(r"case-[0-9a-f]{24}", case_id) is None
        or view.get("case_id") != case_id
        or view.get("stopped") is not False
    ):
        return None
    delivery_set = view.get("delivery_set")
    summaries = view.get("delivery_summaries")
    files = view.get("presentation_files")
    if not isinstance(delivery_set, Mapping) or not isinstance(summaries, list) or not isinstance(files, list):
        return None
    revision = delivery_set.get("archive_revision")
    expected = delivery_set.get("expected_document_types")
    completed = delivery_set.get("completed_document_types")
    missing = delivery_set.get("missing_document_types")
    if (
        delivery_set.get("contract_version") != "document.delivery-set-v1"
        or delivery_set.get("delivery_set_id") != delivery_set_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or not isinstance(expected, list)
        or not expected
        or not all(
            isinstance(item, str) and item in _PUBLIC_DOCUMENT_TYPES
            for item in expected
        )
        or len(set(expected)) != len(expected)
        or completed != expected
        or missing != []
        or len(summaries) != len(expected)
        or len(files) != len(expected) * 2
    ):
        return None

    normalized_deliveries: list[dict[str, Any]] = []
    modes: set[str] = set()
    for index, document_type in enumerate(expected):
        summary = summaries[index]
        document_file = files[index * 2]
        checklist_file = files[index * 2 + 1]
        if not all(isinstance(item, Mapping) for item in (summary, document_file, checklist_file)):
            return None
        mode = summary.get("mode")
        expected_state = "candidate_ready" if mode == "candidate" else "final_ready"
        if (
            summary.get("case_id") != case_id
            or summary.get("archive_revision") != revision
            or summary.get("document_type") != document_type
            or mode not in {"candidate", "external_final"}
            or summary.get("delivery_state") != expected_state
            or view.get("delivery_state") != expected_state
        ):
            return None
        validation = summary.get("validation_summary")
        checks = validation.get("checks") if isinstance(validation, Mapping) else None
        if (
            not isinstance(validation, Mapping)
            or validation.get("status") != "passed"
            or not isinstance(checks, list)
            or not checks
            or not all(isinstance(item, str) and item.strip() for item in checks)
        ):
            return None
        modes.add(mode)
        normalized_files: dict[str, Mapping[str, Any]] = {}
        for offset, (kind, file_item, suffix) in enumerate(
            (
                ("document", document_file, ".docx"),
                ("checklist", checklist_file, ".txt"),
            )
        ):
            expected_order = index * 2 + offset + 1
            path_value = file_item.get("path")
            digest = file_item.get("sha256")
            normalized_path = _normalize_managed_view_path(
                path_value,
                case_id=case_id,
                document_type=document_type,
                suffix=suffix,
                workspace=workspace,
            )
            if (
                file_item.get("delivery_label") != document_type
                or file_item.get("kind") != kind
                or file_item.get("order") != expected_order
                or file_item.get("state") != expected_state
                or normalized_path is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                return None
            normalized_file = dict(file_item)
            normalized_file["path"] = normalized_path
            normalized_files[kind] = normalized_file
        normalized_deliveries.append(
            {
                "case_id": case_id,
                "document_type": document_type,
                "mode": mode,
                "archive_revision": revision,
                "current_archive_revision": revision,
                "case_archive_path": (
                    f".arbibuddy/cases/{case_id}/案情档案.md"
                ),
                "document_path": normalized_files["document"]["path"],
                "checklist_path": normalized_files["checklist"]["path"],
                "document_sha256": normalized_files["document"]["sha256"],
                "checklist_sha256": normalized_files["checklist"]["sha256"],
                "validation_status": "passed",
            }
        )
    if len(modes) != 1:
        return None
    return {
        "schema_version": 1,
        "source": "codex_cli_managed_view",
        "case_id": case_id,
        "archive_revision": revision,
        "delivery_set_id": delivery_set_id,
        "expected_document_types": list(expected),
        "completed_document_types": list(completed),
        "missing_document_types": list(missing),
        "deliveries": normalized_deliveries,
    }


def _normalize_managed_view_path(
    value: object,
    *,
    case_id: str,
    document_type: str,
    suffix: str,
    workspace: Path | None,
) -> str | None:
    if _valid_managed_view_path(
        value,
        case_id=case_id,
        document_type=document_type,
        suffix=suffix,
    ):
        return value
    if not isinstance(value, str) or workspace is None:
        return None
    candidate = Path(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        root_path = Path(os.path.abspath(os.fspath(workspace)))
        candidate_path = Path(os.path.abspath(os.fspath(candidate)))
        common_path = os.path.commonpath((str(root_path), str(candidate_path)))
        if os.path.normcase(common_path) != os.path.normcase(str(root_path)):
            return None
        relative_path = Path(os.path.relpath(candidate_path, root_path)).as_posix()
    except (OSError, TypeError, ValueError):
        return None
    if not _valid_managed_view_path(
        relative_path,
        case_id=case_id,
        document_type=document_type,
        suffix=suffix,
    ):
        return None
    return relative_path


def _valid_managed_view_path(
    value: object,
    *,
    case_id: str,
    document_type: str,
    suffix: str,
) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and len(path.parts) == 6
        and path.parts[:5]
        == (".arbibuddy", "cases", case_id, "output", document_type)
        and bool(path.parts[5])
        and path.parts[5] not in {".", ".."}
        and path.parts[5].casefold().endswith(suffix)
    )


def _session_id(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        for key in ("thread_id", "session_id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        item = event.get("item")
        if isinstance(item, dict):
            for key in ("thread_id", "session_id"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _response_text(
    events: list[dict[str, Any]],
    stdout: str,
    *,
    platform: str | None = None,
) -> str:
    if platform == "claude-code":
        # Claude stream-json emits assistant messages while the turn is running,
        # then a terminal result event with the complete final response. Using
        # both surfaces repeats final text and can promote tool-use progress to
        # a user-visible completion claim.
        terminal_result = next(
            (event for event in reversed(events) if event.get("type") == "result"),
            None,
        )
        if terminal_result is None:
            return ""
        if (
            terminal_result.get("subtype") not in {"success", "completion"}
            or terminal_result.get("is_error") is True
        ):
            return ""
        value = terminal_result.get("result")
        return value.strip() if isinstance(value, str) else ""

    texts: list[str] = []
    for event in events:
        for key in ("result", "text", "message"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
        item = event.get("item")
        if isinstance(item, dict):
            value = item.get("text")
            if isinstance(value, str) and value.strip():
                texts.append(value)
            content = item.get("content")
            if isinstance(content, list):
                texts.extend(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                texts.extend(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
    if texts:
        return "\n".join(text for text in texts if text.strip())
    return stdout.strip()


def _observe_workspace(workspace: Path) -> dict[str, Any]:
    snapshot = inspect_public_artifacts(workspace)
    archives = snapshot["case_archives"]
    deliveries = snapshot["deliveries"]
    return {
        "case_archive": bool(archives),
        "docx": bool(deliveries),
        "checklist": any(item.get("checklist_path") for item in deliveries),
        "case_archive_count": len(archives),
        "docx_count": len(deliveries),
        "invalid_docx_count": snapshot["invalid_docx_count"],
        "invalid_delivery_count": snapshot["invalid_delivery_count"],
        "permission_denied_archive_count": snapshot[
            "permission_denied_archive_count"
        ],
        "permission_denied_delivery_count": snapshot[
            "permission_denied_delivery_count"
        ],
        "permission_denied_docx_count": snapshot["permission_denied_docx_count"],
        "inspection_error_count": snapshot["inspection_error_count"],
        "checklist_count": sum(bool(item.get("checklist_path")) for item in deliveries),
        "case_archives": archives,
        "deliveries": deliveries,
    }


def _artifact_files(workspace: Path) -> list[dict[str, Any]]:
    archives, docx, _, checklists = _public_artifact_paths(workspace)
    files: list[dict[str, Any]] = []
    for path in sorted({*archives, *docx, *checklists}):
        relative = path.relative_to(workspace).as_posix()
        data = path_for_io(path).read_bytes()
        files.append(
            {
                "path": relative,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return files


def _retain_verified_deliveries(
    workspace: Path,
    run_dir: Path,
    observation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Copy only verified public delivery files for later host presentation checks."""

    deliveries = observation.get("deliveries")
    if not isinstance(deliveries, list) or not deliveries:
        raise ValueError("没有可保留的已验证交付")
    workspace_root = workspace.resolve(strict=True)
    retained_root = run_dir / "retained-deliveries"
    planned: list[tuple[Path, Path, str, str]] = []
    destinations: set[Path] = set()
    for delivery in deliveries:
        if not isinstance(delivery, Mapping):
            raise ValueError("交付记录格式无效")
        document_type = delivery.get("document_type")
        case_id = delivery.get("case_id")
        if (
            document_type not in _PUBLIC_DOCUMENT_TYPES
            or not isinstance(case_id, str)
            or re.fullmatch(r"case-[a-z0-9]{24}", case_id) is None
        ):
            raise ValueError("交付记录缺少有效案件或文书类型")
        for key, kind, suffix in (
            ("document_path", "docx", ".docx"),
            ("checklist_path", "checklist", ".txt"),
        ):
            value = delivery.get(key)
            if not isinstance(value, str) or "\\" in value:
                raise ValueError("交付文件路径无效")
            relative = PurePosixPath(value)
            parts = relative.parts
            if (
                relative.is_absolute()
                or ".." in parts
                or len(parts) != 6
                or parts[:5]
                != (".arbibuddy", "cases", case_id, "output", document_type)
                or relative.suffix.casefold() != suffix
            ):
                raise ValueError("交付文件越过 canonical 输出目录")
            source = workspace.joinpath(*parts)
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(workspace_root) or not source.is_file():
                raise ValueError("交付文件不可读取或越过隔离工作区")
            destination = retained_root / case_id / document_type / source.name
            if destination in destinations:
                raise ValueError("交付文件目标重复")
            destinations.add(destination)
            planned.append((source, destination, kind, value))

    retained: list[dict[str, Any]] = []
    for source, destination, kind, relative in planned:
        path_for_io(destination.parent).mkdir(parents=True, exist_ok=True)
        shutil.copy2(path_for_io(source), path_for_io(destination))
        data = path_for_io(destination).read_bytes()
        retained.append(
            {
                "kind": kind,
                "source_path": relative,
                "path": str(destination.resolve()),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return retained


def _public_artifact_paths(
    workspace: Path,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    snapshot = inspect_public_artifacts(workspace)

    def local_path(relative: str) -> Path:
        return Path(workspace).joinpath(*relative.split("/"))

    archives = [
        local_path(item["archive_path"])
        for item in snapshot["case_archives"]
    ]
    docx = [
        local_path(item["document_path"])
        for item in snapshot["deliveries"]
    ]
    checklists = [
        local_path(item["checklist_path"])
        for item in snapshot["deliveries"]
    ]
    invalid_docx = [
        local_path(relative) for relative in snapshot["invalid_docx_paths"]
    ]
    return archives, docx, invalid_docx, checklists


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        return path.is_relative_to(parent)
    except AttributeError:  # pragma: no cover
        return str(path).startswith(str(parent) + os.sep)


def _notify(
    callback: Callable[[JourneyProgress], None] | None,
    progress: JourneyProgress,
) -> None:
    if callback is not None:
        callback(progress)


__all__ = [
    "CliAgentRuntime",
    "Journey",
    "JourneyError",
    "JourneyFact",
    "JourneyMilestone",
    "JourneyCompletionPath",
    "JourneyOracle",
    "JourneyPublicState",
    "JourneyAssessment",
    "JourneyProgress",
    "JourneyRestart",
    "JourneyResult",
    "JourneySimulator",
    "CodexSdkScenarioResponder",
    "ReplayScenarioResponder",
    "RuleScenarioResponder",
    "ScenarioFactOption",
    "ScenarioResponseDecision",
    "ScenarioResponseError",
    "ScenarioResponseLedger",
    "ScenarioResponseRequest",
    "ScenarioResponderRuntimeError",
    "ScenarioResponseResult",
    "ResponderIdentity",
    "ScenarioInteraction",
    "RuntimeAdapterError",
    "load_journey",
    "run_journey",
    "run_journey_with_retries",
]
