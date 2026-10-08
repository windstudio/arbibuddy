"""ScenarioResponder 的稳定审计契约。

这个模块故意不依赖 Journey runner。它只描述一次模型 B 交互的公开输入、
实际身份和结果，便于 runner、replay 和 evidence 共用同一份哈希口径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from .scenario_responder import ScenarioResponseDecision, ScenarioResponseRequest


class ScenarioDecision(Protocol):
    action: str
    selected_fact_ids: tuple[str, ...]
    confidence: str
    reason_code: str


@dataclass(frozen=True)
class ResponderIdentity:
    """实际执行交互的 adapter 身份，而不是 CLI 传入的期望参数。"""

    mode: str
    provider: str
    model: str | None = None
    sdk_version: str | None = None
    runtime_source: str = "unknown"
    binary_version: str | None = None
    sandbox: str | None = None
    approval_mode: str | None = None
    requested_reasoning_effort: str | None = None
    effective_reasoning_effort: str | None = None
    reasoning_effort_source: str = "not_recorded"
    read_isolation: str = "unknown"
    environment_policy: str = "unknown"
    isolation_canary: str = "not_run"
    identity_verified: bool = True
    canary_results: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "sdk_version": self.sdk_version,
            "runtime_source": self.runtime_source,
            "binary_version": self.binary_version,
            "sandbox": self.sandbox,
            "approval_mode": self.approval_mode,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "effective_reasoning_effort": self.effective_reasoning_effort,
            "reasoning_effort_source": self.reasoning_effort_source,
            "read_isolation": self.read_isolation,
            "environment_policy": self.environment_policy,
            "isolation_canary": self.isolation_canary,
            "identity_verified": self.identity_verified,
            "canary_results": dict(self.canary_results),
        }


@dataclass(frozen=True)
class ScenarioAttempt:
    """一次原始调用及其确定性校验结果。"""

    attempt: int
    request_sha256: str
    raw_response_sha256: str | None = None
    validation: str = "not_run"
    validation_error_code: str | None = None
    action: str | None = None
    selected_fact_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "request_sha256": self.request_sha256,
            "raw_response_sha256": self.raw_response_sha256,
            "validation": self.validation,
            "validation_error_code": self.validation_error_code,
            "action": self.action,
            "selected_fact_ids": list(self.selected_fact_ids),
        }


@dataclass(frozen=True)
class ScenarioResponseResult:
    """已完成解析与语义校验的 Scenario 决定及其审计信息。"""

    decision: "ScenarioResponseDecision"
    identity: ResponderIdentity
    request_sha256: str
    attempt_count: int
    repair_count: int
    duration_seconds: float
    result_code: str
    attempts: tuple[ScenarioAttempt, ...] = ()
    validation: str = "passed"
    diagnostic: Mapping[str, object] = field(default_factory=dict)
    trace: Mapping[str, object] = field(default_factory=dict)

    @property
    def second_model_used(self) -> bool:
        return (
            self.identity.mode == "model"
            and self.identity.provider == "codex-sdk"
            and self.validation == "passed"
        )

    @property
    def action(self) -> str:
        return self.decision.action

    @property
    def selected_fact_ids(self) -> tuple[str, ...]:
        return self.decision.selected_fact_ids

    @property
    def confidence(self) -> str:
        return self.decision.confidence

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code

    def as_dict(self) -> dict[str, object]:
        decision = self.decision
        return {
            "identity": self.identity.as_dict(),
            "request_sha256": self.request_sha256,
            "attempt_count": self.attempt_count,
            "repair_count": self.repair_count,
            "duration_seconds": self.duration_seconds,
            "result_code": self.result_code,
            "validation": self.validation,
            "attempts": [item.as_dict() for item in self.attempts],
            "decision": {
                "action": decision.action,
                "selected_fact_ids": list(decision.selected_fact_ids),
                "confidence": decision.confidence,
                "reason_code": decision.reason_code,
            },
            "diagnostic": dict(self.diagnostic),
            "trace": dict(self.trace),
        }


class ScenarioInteraction(Protocol):
    """模型 B 单轮交互的窄接口。"""

    def respond(self, request: "ScenarioResponseRequest") -> ScenarioResponseResult:
        """返回已完成解析、语义校验和实际身份记录的单一结果。"""


def scenario_request_sha256(request: "ScenarioResponseRequest") -> str:
    """用公开 request payload 计算稳定哈希，不包含路径、环境或隐藏断言。"""

    payload_method = getattr(request, "prompt_payload", None)
    if not callable(payload_method):
        raise TypeError("Scenario request 缺少 prompt_payload()")
    payload = payload_method()
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def raw_response_sha256(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


__all__ = [
    "ScenarioDecision",
    "ResponderIdentity",
    "ScenarioAttempt",
    "ScenarioInteraction",
    "ScenarioResponseResult",
    "raw_response_sha256",
    "scenario_request_sha256",
]
