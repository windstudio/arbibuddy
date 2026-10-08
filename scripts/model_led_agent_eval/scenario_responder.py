"""模型 B 场景响应器的窄接口与结构化契约。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import tomllib
from time import monotonic
from types import SimpleNamespace
from typing import Callable, Mapping, Protocol, Sequence

from .scenario_contract import (
    ResponderIdentity,
    ScenarioAttempt,
    ScenarioResponseResult,
    raw_response_sha256,
    scenario_request_sha256,
)
from .scenario_validation import (
    ResponseAction,
    ResponseConfidence,
    RenderedScenarioResponse,
    ScenarioFactOption,
    ScenarioResponseDecision,
    ScenarioResponseError,
    ScenarioResponseLedger,
    ScenarioResponseRequest,
    ValidatedScenarioResponse,
    _bounded_text,
    build_scenario_responder_prompt,
    decision_json_schema,
    render_scenario_response,
    validate_scenario_response,
)
from .scenario_worker import (
    BASE_INSTRUCTIONS as _BASE_INSTRUCTIONS,
    SdkWorkerProcess as _SdkWorkerProcess,
    WorkerRuntimeError as _WorkerRuntimeError,
    WorkerThreadProxy as _WorkerThreadProxy,
    WorkerTimeout as _WorkerTimeout,
    _safe_error_summary,
    _windows_process_tree,
    sdk_environment as _sdk_environment,
    sdk_worker_main as _sdk_worker_main,
    terminate_process_tree as _terminate_process_tree,
)


class ScenarioResponder(Protocol):
    """读取有限上下文并返回一个受约束的 Scenario 决定。"""

    def respond(self, request: ScenarioResponseRequest) -> ScenarioResponseResult:
        ...

    @property
    def identity(self) -> ResponderIdentity:
        ...


class ReplayScenarioResponder:
    """从已记录的决定顺序重放，不调用任何模型。"""

    def __init__(self, source: Sequence[object] | str | Path | Mapping[str, object]):
        records: object = source
        if isinstance(source, (str, Path)):
            try:
                records = json.loads(Path(source).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ScenarioResponseError("回放证据无法读取") from error
        if isinstance(records, Mapping):
            evidence_schema = records.get("schema_version")
            if evidence_schema == 1:
                raise ScenarioResponseError(
                    "v1 evidence 不可直接回放；请使用带 request_sha256 的 v2 记录"
                )
            for key in ("responder_decisions", "scenario_responder", "decisions", "replay"):
                if key in records:
                    records = records[key]
                    break
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise ScenarioResponseError("回放证据必须是决定列表")
        self._records = tuple(records)
        self._decisions: tuple[ScenarioResponseDecision, ...] = tuple(
            self._parse_record(record) for record in self._records
        )
        self._index = 0
        self._last_trace: dict[str, object] = {}

    @staticmethod
    def _parse_record(record: object) -> ScenarioResponseDecision:
        if not isinstance(record, Mapping):
            raise ScenarioResponseError("回放记录必须是带绑定字段的对象")
        required = {
            "scenario_id",
            "turn",
            "prompt_version",
            "schema_version",
            "request_sha256",
            "decision",
        }
        missing = sorted(required - set(record))
        if missing:
            raise ScenarioResponseError(
                "回放记录缺少绑定字段：" + ",".join(missing)
            )
        decision = record.get("decision")
        try:
            return ScenarioResponseDecision.from_mapping(decision)
        except (TypeError, ValueError) as error:
            raise ScenarioResponseError("回放记录的 decision 无效") from error

    @property
    def identity(self) -> ResponderIdentity:
        return ResponderIdentity(
            mode="replay",
            provider="replay",
            model=None,
            runtime_source="replay-record",
            read_isolation="not_applicable",
            environment_policy="not_applicable",
            isolation_canary="not_applicable",
        )

    @property
    def last_trace(self) -> dict[str, object]:
        return dict(self._last_trace)

    def respond(self, request: ScenarioResponseRequest) -> ScenarioResponseResult:
        if self._index >= len(self._decisions):
            raise ScenarioResponseError("回放决策已耗尽")
        record = self._records[self._index]
        decision = self._decisions[self._index]
        assert isinstance(record, Mapping)
        for key, actual in (
            ("scenario_id", request.scenario_id),
            ("turn", request.turn),
            ("prompt_version", request.prompt_version),
            ("schema_version", request.schema_version),
            ("request_sha256", scenario_request_sha256(request)),
        ):
            if record.get(key) != actual:
                raise ScenarioResponseError(f"回放契约字段不匹配：{key}")
        # Replay 不是模型，非法决定属于 Harness/replay contract failure。
        validate_scenario_response(request, decision)
        self._index += 1
        self._last_trace = {
            "actor": "scenario_responder",
            "mode": "replay",
            "provider": "replay",
            "request_sha256": scenario_request_sha256(request),
            "turn": request.turn,
            "attempt_count": 1,
            "repair_count": 0,
            "validation": "passed",
            "result_code": "decision_received",
        }
        return ScenarioResponseResult(
            decision=decision,
            identity=self.identity,
            request_sha256=scenario_request_sha256(request),
            attempt_count=1,
            repair_count=0,
            duration_seconds=0.0,
            result_code="decision_received",
            attempts=(
                ScenarioAttempt(
                    attempt=1,
                    request_sha256=scenario_request_sha256(request),
                    validation="passed",
                    action=decision.action,
                    selected_fact_ids=decision.selected_fact_ids,
                ),
            ),
            trace=dict(self._last_trace),
        )


class RuleScenarioResponder:
    """显式 legacy 基线；不会被模型模式自动调用。"""

    def __init__(self, *, marker_rules: Mapping[str, Sequence[str]]):
        self._marker_rules = {
            str(fact_id): tuple(str(marker) for marker in markers if str(marker).strip())
            for fact_id, markers in marker_rules.items()
        }
        self._last_trace: dict[str, object] = {}

    @property
    def identity(self) -> ResponderIdentity:
        return ResponderIdentity(
            mode="rule",
            provider="rule",
            runtime_source="in-process-rule",
            read_isolation="not_applicable",
            environment_policy="not_applicable",
            isolation_canary="not_applicable",
        )

    @property
    def last_trace(self) -> dict[str, object]:
        return dict(self._last_trace)

    def respond(self, request: ScenarioResponseRequest) -> ScenarioResponseResult:
        folded = request.latest_agent_response.casefold()
        available = {
            fact.id
            for fact in request.available_facts
            if fact.used_count < fact.max_uses
        }
        candidates = [
            fact_id
            for fact_id, markers in self._marker_rules.items()
            if fact_id in available
            and any(marker.casefold() in folded for marker in markers)
        ]
        if len(candidates) == 1:
            decision = ScenarioResponseDecision(
                action="answer",
                selected_fact_ids=(candidates[0],),
                confidence="high",
                reason_code="direct_question",
            )
            self._record_trace(request, decision)
            return ScenarioResponseResult(
                decision=decision,
                identity=self.identity,
                request_sha256=scenario_request_sha256(request),
                attempt_count=1,
                repair_count=0,
                duration_seconds=0.0,
                result_code="decision_received",
                attempts=(
                    ScenarioAttempt(
                        attempt=1,
                        request_sha256=scenario_request_sha256(request),
                        validation="passed",
                        action=decision.action,
                        selected_fact_ids=decision.selected_fact_ids,
                    ),
                ),
                trace=dict(self._last_trace),
            )
        if len(candidates) > 1:
            decision = ScenarioResponseDecision(
                action="stop",
                selected_fact_ids=(),
                confidence="low",
                reason_code="insufficient_context",
            )
            self._record_trace(request, decision)
            return ScenarioResponseResult(
                decision=decision,
                identity=self.identity,
                request_sha256=scenario_request_sha256(request),
                attempt_count=1,
                repair_count=0,
                duration_seconds=0.0,
                result_code="decision_received",
                attempts=(
                    ScenarioAttempt(
                        attempt=1,
                        request_sha256=scenario_request_sha256(request),
                        validation="passed",
                        action=decision.action,
                        selected_fact_ids=decision.selected_fact_ids,
                    ),
                ),
                trace=dict(self._last_trace),
            )
        decision = ScenarioResponseDecision(
            action=("stop" if request.remaining_unavailable_uses is not None
                    and request.remaining_unavailable_uses <= 0 else "unavailable"),
            selected_fact_ids=(),
            confidence="low",
            reason_code="no_matching_fact",
        )
        self._record_trace(request, decision)
        return ScenarioResponseResult(
            decision=decision,
            identity=self.identity,
            request_sha256=scenario_request_sha256(request),
            attempt_count=1,
            repair_count=0,
            duration_seconds=0.0,
            result_code="decision_received",
            attempts=(
                ScenarioAttempt(
                    attempt=1,
                    request_sha256=scenario_request_sha256(request),
                    validation="passed",
                    action=decision.action,
                    selected_fact_ids=decision.selected_fact_ids,
                ),
            ),
            trace=dict(self._last_trace),
        )

    def _record_trace(
        self,
        request: ScenarioResponseRequest,
        decision: ScenarioResponseDecision,
    ) -> None:
        self._last_trace = {
            "actor": "scenario_responder",
            "mode": "rule",
            "provider": "rule",
            "request_sha256": scenario_request_sha256(request),
            "turn": request.turn,
            "attempt_count": 1,
            "repair_count": 0,
            "validation": "not_applicable",
            "result_code": "decision_received",
            "action": decision.action,
            "selected_fact_ids": list(decision.selected_fact_ids),
        }


class ScenarioResponderRuntimeError(RuntimeError):
    """模型 B Runtime Adapter 失败，不能静默切换到其他 responder。"""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class _RepairConstraint:
    action: str
    original_selected_fact_ids: tuple[str, ...]
    legal_selected_fact_ids: tuple[str, ...]
    invalid_selected_fact_ids: tuple[str, ...]
    allow_fact_reselection: bool = False
    allow_action_reselection: bool = False
    required_repair_action: str | None = None


class CodexSdkScenarioResponder:
    """通过独立 Codex SDK thread 实现模型 B。"""

    def __init__(
        self,
        *,
        workspace: str | Path,
        model: str,
        timeout_seconds: int = 180,
        effort: str | None = None,
        codex_bin: str | Path | None = None,
        codex_factory: Callable[[object], object] | None = None,
        sdk_factory: Callable[[], object] | None = None,
        agent_workspace: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("模型 B workspace 必须是已存在的目录")
        if agent_workspace is not None:
            resolved_agent_workspace = Path(agent_workspace).expanduser().resolve()
            if (
                resolved_agent_workspace == self.workspace
                or self.workspace.is_relative_to(resolved_agent_workspace)
                or resolved_agent_workspace.is_relative_to(self.workspace)
            ):
                raise ValueError("模型 B workspace 必须与模型 A workspace 隔离")
            self.agent_workspace = resolved_agent_workspace
        else:
            self.agent_workspace = None
        if not isinstance(model, str) or not model.strip():
            raise ValueError("模型 B model 必须是非空字符串")
        if timeout_seconds < 1:
            raise ValueError("模型 B timeout_seconds 必须是正整数")
        if codex_factory is not None and sdk_factory is not None:
            raise ValueError("codex_factory 与 sdk_factory 不能同时提供")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.effort = effort
        (
            self.effective_reasoning_effort,
            self.reasoning_effort_source,
        ) = _resolve_reasoning_effort_metadata(self.effort, _sdk_environment())
        # 默认由 openai-codex SDK 自己选择 pinned runtime；只有显式参数才允许
        # 覆盖。不能用 PATH 中恰好存在的 codex 版本冒充 SDK runtime。
        self.codex_bin = (
            str(Path(codex_bin).expanduser().resolve())
            if codex_bin is not None
            else None
        )
        self.runtime_source = "explicit" if self.codex_bin else "sdk-pinned"
        self._codex_factory = codex_factory
        self._sdk_factory = sdk_factory
        self._codex: object | None = None
        self._thread: object | None = None
        self._sdk_module: object | None = None
        self._sdk_version: str | None = None
        self._binary_version: str | None = None
        self._canary_results: dict[str, object] = {
            "secret_env": "not_run",
            "workspace": "not_run",
        }
        self._identity_verified = False
        self._last_trace: dict[str, object] = {}
        self._last_result: ScenarioResponseResult | None = None
        self._worker: _SdkWorkerProcess | None = None
        self._worker_thread_id: str | None = None

    @property
    def last_trace(self) -> dict[str, object]:
        return dict(self._last_trace)

    @property
    def identity(self) -> ResponderIdentity:
        canary = self._worker.isolation_canary if self._worker is not None else (
            "test_double" if self._codex_factory is not None or self._sdk_factory is not None else "not_run"
        )
        return ResponderIdentity(
            mode="model",
            provider="codex-sdk",
            model=self.model,
            sdk_version=self._sdk_version,
            runtime_source=self.runtime_source,
            binary_version=self._binary_version,
            sandbox="read-only",
            approval_mode="auto_review",
            requested_reasoning_effort=self.effort,
            effective_reasoning_effort=self.effective_reasoning_effort,
            reasoning_effort_source=self.reasoning_effort_source,
            # Codex 的 read-only 是能力级别，不等于目录级读取隔离。
            read_isolation="read_only_not_path_confined",
            environment_policy="explicit-allowlist",
            isolation_canary=canary,
            identity_verified=self._identity_verified,
            canary_results=dict(self._canary_results),
        )

    @property
    def last_result(self) -> ScenarioResponseResult | None:
        return self._last_result

    @property
    def worker_alive(self) -> bool:
        worker = self._worker
        process = getattr(worker, "_process", None) if worker is not None else None
        return bool(process is not None and process.is_alive())

    def respond(self, request: ScenarioResponseRequest) -> ScenarioResponseResult:
        result = self.respond_result(request)
        return result

    def respond_result(self, request: ScenarioResponseRequest) -> ScenarioResponseResult:
        prompt = build_scenario_responder_prompt(request)
        self._ensure_thread()
        thread = self._thread
        if thread is None:  # pragma: no cover - defensive invariant
            raise ScenarioResponderRuntimeError("Codex SDK 模型 B thread 未建立")
        retry_count = 0
        attempts: list[ScenarioAttempt] = []
        request_hash = scenario_request_sha256(request)
        repair_error_code = "invalid_json"
        repair_raw: object = None
        repair_constraint: _RepairConstraint | None = None
        started = monotonic()
        while True:
            call_prompt = (
                prompt
                if retry_count == 0
                else _repair_prompt(
                    prompt,
                    error_code=repair_error_code,
                    raw_decision=repair_raw,
                )
            )
            try:
                result = self._run_thread(thread, call_prompt)
            except ScenarioResponderRuntimeError:
                raise
            except Exception as error:
                raise self._runtime_error(
                    "Codex SDK 模型 B 调用失败",
                    failure_kind="sdk_call_failed",
                    error=error,
                    retry_count=retry_count,
                ) from error
            raw = getattr(result, "final_response", None)
            try:
                decision = ScenarioResponseDecision.from_json(raw)
            except (TypeError, ValueError) as error:
                attempts.append(
                    ScenarioAttempt(
                        attempt=retry_count + 1,
                        request_sha256=request_hash,
                        raw_response_sha256=raw_response_sha256(raw),
                        validation="failed",
                        validation_error_code="invalid_json",
                    )
                )
                if retry_count == 0:
                    retry_count = 1
                    repair_error_code = "invalid_json"
                    repair_raw = raw
                    continue
                raise self._runtime_error(
                    "Codex SDK 模型 B 输出不是严格 JSON",
                    failure_kind="invalid_json",
                    error=error,
                    retry_count=retry_count,
                    attempts=attempts,
                ) from error
            try:
                validate_scenario_response(request, decision)
            except ScenarioResponseError as error:
                attempts.append(
                    ScenarioAttempt(
                        attempt=retry_count + 1,
                        request_sha256=request_hash,
                        raw_response_sha256=raw_response_sha256(raw),
                        validation="failed",
                        validation_error_code=_semantic_error_code(str(error)),
                        action=decision.action,
                        selected_fact_ids=decision.selected_fact_ids,
                    )
                )
                if retry_count == 0:
                    retry_count = 1
                    repair_error_code = _semantic_error_code(str(error))
                    repair_raw = raw
                    repair_constraint = _repair_constraint(
                        request, decision, error_code=repair_error_code
                    )
                    continue
                raise self._runtime_error(
                    "Codex SDK 模型 B 决定未通过语义校验",
                    failure_kind="semantic_invalid",
                    error=error,
                    retry_count=retry_count,
                    attempts=attempts,
                ) from error
            if repair_constraint is not None:
                try:
                    _enforce_repair_constraint(repair_constraint, decision)
                except ScenarioResponseError as error:
                    attempts.append(
                        ScenarioAttempt(
                            attempt=retry_count + 1,
                            request_sha256=request_hash,
                            raw_response_sha256=raw_response_sha256(raw),
                            validation="failed",
                            validation_error_code="repair_selection_changed",
                            action=decision.action,
                            selected_fact_ids=decision.selected_fact_ids,
                        )
                    )
                    raise self._runtime_error(
                        "Codex SDK 模型 B 修复改写了原事实选择",
                        failure_kind="semantic_invalid",
                        error=error,
                        retry_count=retry_count,
                        attempts=attempts,
                    ) from error
            attempts.append(
                ScenarioAttempt(
                    attempt=retry_count + 1,
                    request_sha256=request_hash,
                    raw_response_sha256=raw_response_sha256(raw),
                    validation="passed",
                    action=decision.action,
                    selected_fact_ids=decision.selected_fact_ids,
                )
            )
            duration = monotonic() - started
            self._last_trace = {
                "actor": "scenario_responder",
                "mode": "model",
                "provider": "codex-sdk",
                "model": self.model,
                "sdk_version": self._sdk_version,
                "runtime_source": self.runtime_source,
                "binary_version": self._binary_version,
                "requested_reasoning_effort": self.effort,
                "effective_reasoning_effort": self.effective_reasoning_effort,
                "reasoning_effort_source": self.reasoning_effort_source,
                "sandbox": "read-only",
                "approval_mode": "auto_review",
                "read_isolation": "read_only_not_path_confined",
                "environment_policy": "explicit-allowlist",
                "isolation_canary": self.identity.isolation_canary,
                "identity_verified": self._identity_verified,
                "canary_results": dict(self._canary_results),
                "thread_id": self._worker_thread_id or str(getattr(thread, "id", "")),
                "prompt_version": request.prompt_version,
                "schema_version": request.schema_version,
                "input_sha256": request_hash,
                "request_sha256": request_hash,
                "attempt_count": len(attempts),
                "repair_count": retry_count,
                "retry_count": retry_count,
                "attempts": [item.as_dict() for item in attempts],
                "duration_seconds": duration,
                "result_code": "decision_received",
                "canary_results": dict(self._canary_results),
            }
            self._last_result = ScenarioResponseResult(
                decision=decision,
                identity=self.identity,
                request_sha256=request_hash,
                attempt_count=len(attempts),
                repair_count=retry_count,
                duration_seconds=duration,
                result_code="decision_received",
                attempts=tuple(attempts),
                trace=dict(self._last_trace),
            )
            return self._last_result

    def close(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.close()
        codex = self._codex
        self._thread = None
        self._codex = None
        if codex is not None:
            close = getattr(codex, "close", None)
            if callable(close):
                close()

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        sdk_module = self._sdk_module
        if sdk_module is None and self._codex_factory is None and self._sdk_factory is None:
            try:
                import openai_codex as sdk_module  # type: ignore[import-not-found]
            except ImportError as error:
                raise self._runtime_error(
                    "Codex SDK 不可用；未安装 openai-codex",
                    failure_kind="dependency_missing",
                    error=error,
                    retry_count=0,
                ) from error
            self._sdk_module = sdk_module
            self._sdk_version = str(getattr(sdk_module, "__version__", "")) or None
        if self._codex_factory is not None:
            config = SimpleNamespace(
                cwd=str(self.workspace),
                env=_sdk_environment(),
                codex_bin=self.codex_bin,
                runtime_source=self.runtime_source,
            )
            try:
                codex = self._codex_factory(config)
            except Exception as error:
                raise self._runtime_error(
                    "Codex SDK 初始化失败",
                    failure_kind="sdk_init_failed",
                    error=error,
                    retry_count=0,
                ) from error
            sandbox = "read-only"
            approval_mode = "auto_review"
        elif self._sdk_factory is not None:
            try:
                codex = self._sdk_factory()
            except Exception as error:
                raise self._runtime_error(
                    "Codex SDK 初始化失败",
                    failure_kind="dependency_missing",
                    error=error,
                    retry_count=0,
                ) from error
            sandbox = "read-only"
            approval_mode = "auto_review"
        else:
            # 真实 SDK 必须在可终止的 worker process 内创建，parent 不持有 SDK
            # 调用线程。这样 timeout 后可以证明底层调用已经停止。
            try:
                import openai_codex as module  # type: ignore[import-not-found]
            except ImportError as error:
                raise self._runtime_error(
                    "Codex SDK 不可用；未安装 openai-codex",
                    failure_kind="dependency_missing",
                    error=error,
                    retry_count=0,
                ) from error
            self._sdk_module = module
            self._sdk_version = str(getattr(module, "__version__", "")) or None
            self._worker = _SdkWorkerProcess(
                workspace=self.workspace,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                effort=self.effort,
                codex_bin=self.codex_bin,
                env=_sdk_environment(),
                agent_workspace=self.agent_workspace,
            )
            secret_name = f"ARBI_B_SECRET_CANARY_{secrets.token_hex(8)}"
            secret_value = secrets.token_hex(24)
            workspace_canary: Path | None = None
            if self.agent_workspace is not None:
                self.agent_workspace.mkdir(parents=True, exist_ok=True)
                workspace_canary = (
                    self.agent_workspace
                    / f".arbibuddy-workspace-canary-{secrets.token_hex(8)}"
                )
                workspace_canary.write_text(secret_value, encoding="utf-8")
            self._worker.configure_canaries(
                secret_name=secret_name,
                secret_value=secret_value,
                workspace_canary=workspace_canary,
            )
            try:
                self._worker.start()
            except _WorkerTimeout as error:
                self._worker.close()
                self._worker = None
                raise self._runtime_error(
                    "Codex SDK 模型 B worker 启动超时",
                    failure_kind="timeout",
                    error=None,
                    retry_count=0,
                ) from error
            except Exception as error:
                self._worker.close()
                self._worker = None
                raise self._runtime_error(
                    "Codex SDK worker 初始化失败",
                    failure_kind="worker_start_failed",
                    error=error,
                    retry_count=0,
                ) from error
            finally:
                if workspace_canary is not None:
                    try:
                        workspace_canary.unlink()
                    except OSError:
                        pass
            self._worker_thread_id = self._worker.thread_id
            self._sdk_version = self._worker.sdk_version or self._sdk_version
            self._binary_version = self._worker.binary_version
            self._canary_results = dict(self._worker.canary_results)
            self._identity_verified = True
            if self._worker.isolation_canary != "passed":
                self._worker.close()
                self._worker = None
                raise self._runtime_error(
                    "Codex SDK worker 环境隔离 canary 未通过",
                    failure_kind="isolation_canary_failed",
                    error=None,
                    retry_count=0,
                )
            self._thread = _WorkerThreadProxy(self._worker_thread_id)
            return
        self._codex = codex
        try:
            self._thread = codex.thread_start(
                cwd=str(self.workspace),
                model=self.model,
                sandbox=sandbox,
                approval_mode=approval_mode,
                base_instructions=_BASE_INSTRUCTIONS,
                ephemeral=True,
            )
        except Exception as error:
            self.close()
            raise self._runtime_error(
                "Codex SDK 无法创建模型 B 独立 thread",
                failure_kind="thread_start_failed",
                error=error,
                retry_count=0,
            ) from error
        self._identity_verified = True
        self._canary_results = {
            "secret_env": "test_double",
            "workspace": "test_double",
        }
        if self.codex_bin:
            self._binary_version = _probe_explicit_binary_version(
                self.codex_bin,
                cwd=self.workspace,
            )

    def _run_thread(self, thread: object, prompt: str) -> object:
        if self._worker is not None:
            try:
                return self._worker.run(prompt)
            except _WorkerTimeout as error:
                raise self._runtime_error(
                    "Codex SDK 模型 B 调用超时",
                    failure_kind="timeout",
                    error=None,
                    retry_count=0,
                ) from error
            except _WorkerRuntimeError as error:
                raise self._runtime_error(
                    "Codex SDK 模型 B worker 调用失败",
                    failure_kind=error.failure_kind,
                    error=error,
                    retry_count=0,
                ) from error
        module = self._sdk_module
        sandbox = getattr(getattr(module, "Sandbox", None), "read_only", "read-only")
        approval_mode = getattr(
            getattr(module, "ApprovalMode", None), "auto_review", "auto_review"
        )
        kwargs: dict[str, object] = {
            "cwd": str(self.workspace),
            "model": self.model,
            "output_schema": decision_json_schema(),
            "sandbox": sandbox,
            "approval_mode": approval_mode,
        }
        if self.effort is not None:
            kwargs["effort"] = self.effort
        result_box: list[object] = []
        error_box: list[BaseException] = []
        completed = threading.Event()

        def invoke() -> None:
            try:
                result_box.append(thread.run(prompt, **kwargs))
            except Exception as error:  # re-raised in caller thread
                error_box.append(error)
            finally:
                completed.set()

        # 注入的 in-process factory 只用于无法跨 Windows spawn 序列化的旧测试
        # double；生产 SDK 永远走上面的可终止 process。真实 runtime 不经过此分支。
        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        if not completed.wait(self.timeout_seconds):
            close = getattr(self._codex, "close", None)
            if callable(close):
                close()
            worker.join(0.2)
            raise self._runtime_error(
                "Codex SDK 模型 B 调用超时",
                failure_kind="timeout",
                error=None,
                retry_count=0,
            )
        if error_box:
            raise error_box[0]
        if not result_box:
            raise self._runtime_error(
                "Codex SDK 模型 B 未返回结果",
                failure_kind="empty_result",
                error=None,
                retry_count=0,
            )
        return result_box[0]

    def _runtime_error(
        self,
        message: str,
        *,
        failure_kind: str,
        error: BaseException | None,
        retry_count: int,
        attempts: Sequence[ScenarioAttempt] = (),
    ) -> ScenarioResponderRuntimeError:
        detail = {
            "actor": "scenario_responder",
            "provider": "codex-sdk",
            "model": self.model,
            "sdk_version": self._sdk_version,
            "runtime_source": self.runtime_source,
            "binary_version": self._binary_version,
            "sandbox": "read-only",
            "approval_mode": "auto_review",
            "read_isolation": "read_only_not_path_confined",
            "environment_policy": "explicit-allowlist",
            "isolation_canary": self.identity.isolation_canary,
            "identity_verified": self._identity_verified,
            "canary_results": dict(self._canary_results),
            "thread_id": str(getattr(self._thread, "id", "")) or None,
            "failure_kind": failure_kind,
            "retry_count": retry_count,
            "attempt_count": len(attempts),
            "repair_count": retry_count,
            "attempts": [item.as_dict() for item in attempts],
        }
        if error is not None:
            detail["error_type"] = error.__class__.__name__
            detail["error_summary"] = _safe_error_summary(error)
        self._last_trace = dict(detail)
        return ScenarioResponderRuntimeError(message, details=detail)


def _repair_prompt(
    prompt: str,
    *,
    error_code: str = "invalid_json",
    raw_decision: object = None,
) -> str:
    raw = _bounded_text(raw_decision, 1200) if raw_decision is not None else "<不可解析>"
    if error_code == "required_next_fact_missing":
        return (
            f"{prompt}\n上一次决定校验失败，错误码为 {error_code}。\n"
            f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
            "本轮返回 action=answer，selected_fact_ids 只包含 required_next_fact_id；"
            "该事实是当前场景已声明的下一步。不要添加解释或 Markdown。"
        )
    if error_code == "fact_precondition_missing":
        return (
            f"{prompt}\n上一次决定校验失败，错误码为 {error_code}。\n"
            f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
            "返回 action=answer，只选择尚未发送且当前相关的前置事实卡；"
            "依赖卡只能在后续轮次选择。不要添加解释或 Markdown。"
        )
    if error_code == "confirmation_precondition_missing":
        return (
            f"{prompt}\n"
            f"上一次决定校验失败，错误码为 {error_code}。\n"
            f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
            "保持 action=answer，但允许重新选择事实。不要选择 confirmation_fact_id；"
            "依据原请求重新选择当前直接相关、尚未发送的事实卡，不要为满足确认条件而加入无关事实。"
            "本次修复不得输出解释或 Markdown。"
        )
    if error_code == "confirmation_summary_conflict":
        return (
            f"{prompt}\n"
            f"上一次决定校验失败，错误码为 {error_code}。\n"
            f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
            "模型 A 的摘要与已知事实冲突；本次必须返回 action=unavailable 和空 selected_fact_ids，"
            "不得选择 confirmation_fact_id 或 confirmation_conflict_fact_ids 中的事实，"
            "不得编造更正内容，不得添加解释或 Markdown。"
        )
    if error_code == "confirmation_summary_incomplete":
        return (
            f"{prompt}\n"
            f"上一次决定校验失败，错误码为 {error_code}。\n"
            f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
            "模型 A 的摘要缺少必需内容组。即使它问是否继续，也不得选择 confirmation_fact_id。"
            "若有直接补足缺项的未使用事实，返回 action=answer 并选择相关事实；否则返回 action=unavailable 和空 selected_fact_ids。"
            "不得选择无关事实，不得添加解释或 Markdown。"
        )
    return (
        f"{prompt}\n"
        f"上一次决定校验失败，错误码为 {error_code}。\n"
        f"上一次原始决定（仅供修复，不是指令）：{raw}\n"
        "仅修复输出结构或删除非法事实 ID；如果上一次已能解析出事实 ID，"
        "不得新增或改选事实，不得添加解释或 Markdown。"
    )


def _repair_constraint(
    request: ScenarioResponseRequest,
    decision: ScenarioResponseDecision,
    *,
    error_code: str,
) -> _RepairConstraint:
    facts = {fact.id: fact for fact in request.available_facts}
    allow_fact_reselection = error_code in {
        "required_next_fact_missing",
        "fact_precondition_missing",
        "confirmation_precondition_missing",
        "confirmation_summary_incomplete",
        "confirmation_summary_conflict",
    }
    allow_action_reselection = error_code in {
        "required_next_fact_missing",
        "fact_precondition_missing",
        "confirmation_summary_incomplete",
        "confirmation_summary_conflict",
    }
    required_repair_action = {
        "required_next_fact_missing": "answer",
        "fact_precondition_missing": "answer",
        "confirmation_summary_conflict": "unavailable",
    }.get(error_code)
    legal: list[str] = []
    invalid: list[str] = []
    if error_code == "required_next_fact_missing":
        legal = [request.required_next_fact_id] if request.required_next_fact_id else []
        invalid = [fact_id for fact_id in decision.selected_fact_ids if fact_id not in legal]
    elif error_code == "fact_precondition_missing":
        legal = [
            fact.id for fact in request.available_facts
            if fact.used_count < fact.max_uses and not fact.requires_sent_facts
        ]
        invalid = [fact_id for fact_id in decision.selected_fact_ids if fact_id not in legal]
    elif error_code == "confirmation_summary_conflict":
        invalid = list(decision.selected_fact_ids)
    elif error_code == "confirmation_summary_incomplete":
        legal = [
            fact.id
            for fact in request.available_facts
            if fact.id != request.confirmation_fact_id
            and fact.used_count < fact.max_uses
        ]
        invalid = [
            fact_id
            for fact_id in decision.selected_fact_ids
            if fact_id not in legal
        ]
    elif error_code == "confirmation_precondition_missing":
        legal = [
            fact.id
            for fact in request.available_facts
            if fact.id != request.confirmation_fact_id
            and fact.used_count < fact.max_uses
        ]
        invalid = [
            fact_id
            for fact_id in decision.selected_fact_ids
            if fact_id not in legal
        ]
    else:
        for fact_id in decision.selected_fact_ids:
            fact = facts.get(fact_id)
            if fact is not None and fact.used_count < fact.max_uses:
                legal.append(fact_id)
            else:
                invalid.append(fact_id)
    return _RepairConstraint(
        action=decision.action,
        original_selected_fact_ids=decision.selected_fact_ids,
        legal_selected_fact_ids=tuple(legal),
        invalid_selected_fact_ids=tuple(invalid),
        allow_fact_reselection=allow_fact_reselection,
        allow_action_reselection=allow_action_reselection,
        required_repair_action=required_repair_action,
    )


def _enforce_repair_constraint(
    constraint: _RepairConstraint,
    decision: ScenarioResponseDecision,
) -> None:
    if constraint.allow_action_reselection:
        if (
            constraint.required_repair_action is not None
            and decision.action != constraint.required_repair_action
        ):
            raise ScenarioResponseError(
                "修复决定未采用语义校验要求的 action"
            )
        if not set(decision.selected_fact_ids).issubset(
            set(constraint.legal_selected_fact_ids)
        ):
            raise ScenarioResponseError("修复选择了不允许的事实")
        return
    if decision.action == "stop" and not decision.selected_fact_ids:
        return
    if constraint.action != decision.action:
        raise ScenarioResponseError(
            "修复不得改变原 action；仅允许删除非法事实或停止"
        )
    if constraint.allow_fact_reselection:
        if not set(decision.selected_fact_ids).issubset(
            set(constraint.legal_selected_fact_ids)
        ):
            raise ScenarioResponseError("确认事实修复选择了无效或未允许的事实")
        return
    if not set(decision.selected_fact_ids).issubset(
        set(constraint.legal_selected_fact_ids)
    ):
        raise ScenarioResponseError(
            "修复选择了第一次决定之外的事实，禁止换选事实"
        )


def _probe_explicit_binary_version(binary: str, *, cwd: Path) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScenarioResponderRuntimeError(
            "显式 Codex runtime --version 探测失败",
            details={"failure_kind": "binary_version_probe_failed"},
        ) from error
    if result.returncode != 0:
        raise ScenarioResponderRuntimeError(
            "显式 Codex runtime --version 探测失败",
            details={"failure_kind": "binary_version_probe_failed"},
        )
    value = (result.stdout or result.stderr).strip().splitlines()
    if not value:
        raise ScenarioResponderRuntimeError(
            "显式 Codex runtime --version 没有输出",
            details={"failure_kind": "binary_version_probe_failed"},
        )
    return _safe_error_summary(value[0])


def _resolve_reasoning_effort_metadata(
    effort: str | None,
    environment: Mapping[str, str],
) -> tuple[str | None, str]:
    """Record B's turn override or the Codex CLI config default, never secrets."""

    if isinstance(effort, str) and effort.strip():
        return effort.strip(), "turn.run.effort"
    codex_home = environment.get("CODEX_HOME", "").strip()
    if not codex_home:
        return None, "unresolved:CODEX_HOME_missing"
    config_path = Path(codex_home) / "config.toml"
    if not config_path.is_file():
        return "medium", "Codex default:no_config"
    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError):
        return None, "unresolved:config_unreadable"
    configured = config.get("model_reasoning_effort")
    if isinstance(configured, str) and configured.strip():
        return configured.strip(), "CODEX_HOME/config.toml:model_reasoning_effort"
    return "medium", "Codex default:model_reasoning_effort_unset"


def _semantic_error_code(message: str) -> str:
    folded = message.casefold()
    if "本轮必须选择指定的下一事实" in message:
        return "required_next_fact_missing"
    if "前置事实未发送" in message:
        return "fact_precondition_missing"
    if "确认前置事实未全部发送" in message:
        return "confirmation_precondition_missing"
    if "确认摘要存在已知事实冲突" in message:
        return "confirmation_summary_conflict"
    if "确认摘要缺少必需内容组" in message:
        return "confirmation_summary_incomplete"
    if "未知事实" in message:
        return "unknown_fact_id"
    if "达到使用次数" in message or "耗尽" in message:
        return "exhausted_fact_or_action"
    if "action" in folded:
        return "invalid_action"
    if "selected_fact_ids" in message:
        return "invalid_fact_selection"
    if "confidence" in folded:
        return "invalid_confidence"
    if "reason_code" in message:
        return "invalid_reason_code"
    return "semantic_validation_failed"


__all__ = [
    "ResponseAction",
    "ResponseConfidence",
    "ScenarioFactOption",
    "ScenarioResponseDecision",
    "ScenarioResponseError",
    "ScenarioResponseLedger",
    "ScenarioResponseRequest",
    "ScenarioResponder",
    "ReplayScenarioResponder",
    "RuleScenarioResponder",
    "CodexSdkScenarioResponder",
    "ScenarioResponderRuntimeError",
    "ResponderIdentity",
    "ScenarioAttempt",
    "ScenarioResponseResult",
    "RenderedScenarioResponse",
    "ValidatedScenarioResponse",
    "build_scenario_responder_prompt",
    "decision_json_schema",
    "render_scenario_response",
    "validate_scenario_response",
]
