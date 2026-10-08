"""Journey completion and attribution Oracle.

The runner owns I/O and turn orchestration; this module owns the one public,
deterministic completion decision. It imports runner dataclasses lazily so the
legacy package import remains acyclic while the Oracle has one authority.
"""

from __future__ import annotations

from typing import Any, Mapping


def _assessment(
    passed: bool,
    *,
    completion_path: str | None = None,
    failure_category: str | None = None,
    failure_code: str | None = None,
    message: str = "",
    missing: tuple[str, ...] = (),
) -> Any:
    from . import JourneyAssessment

    return JourneyAssessment(
        passed,
        completion_path=completion_path,
        failure_category=failure_category,
        failure_code=failure_code,
        message=message,
        missing=missing,
    )


def _transcript_text(transcripts: Any) -> str:
    return "\n".join(
        str(item.get("assistant_response", ""))
        for item in transcripts
        if isinstance(item, Mapping)
    ).casefold()


def _contains_any(text: str, markers: Any) -> bool:
    return bool(markers) and any(
        isinstance(marker, str) and marker.casefold() in text for marker in markers
    )


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _last_trigger_turn(
    transcripts: Any,
    fact_ids: tuple[str, ...],
) -> int | None:
    wanted = set(fact_ids)
    turns = [
        _int_value(item.get("turn"))
        for item in transcripts
        if isinstance(item, Mapping)
        and wanted.intersection(
            item.get("selected_fact_ids", [])
            if isinstance(item.get("selected_fact_ids", []), list)
            else []
        )
    ]
    valid = [turn for turn in turns if turn >= 0]
    return max(valid) if valid else None


def _capability_value(capabilities: Mapping[str, Any], key: str) -> object:
    value = capabilities.get(key)
    if value is not None:
        return value
    nested = capabilities.get("capabilities")
    if isinstance(nested, Mapping):
        return nested.get(key)
    return None


def _infer_delivery_trigger(journey: Any) -> str | None:
    ids = {fact.id for fact in journey.fact_pool}
    for candidate in (
        "final_confirmation",
        "candidate_request",
        "finalization_request",
    ):
        if candidate in ids:
            return candidate
    return None


def _has_invalid_managed_output(observation: Mapping[str, Any]) -> bool:
    for key in (
        "invalid_docx_count",
        "invalid_delivery_count",
        "inspection_error_count",
        "permission_denied_archive_count",
        "permission_denied_delivery_count",
        "permission_denied_docx_count",
    ):
        count = observation.get(key, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count != 0:
            return True
    return False


def _public_artifact_observer_permission_denied(
    observation: Mapping[str, Any],
) -> bool:
    """A denied read leaves the final public artifact state unverified."""

    counts = (
        observation.get("permission_denied_archive_count", 0),
        observation.get("permission_denied_delivery_count", 0),
        observation.get("permission_denied_docx_count", 0),
    )
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in counts
    ) and any(counts)


def _artifacts_satisfied(
    observation: Mapping[str, Any],
    required: tuple[str, ...],
    *,
    delivery_requirements: tuple[Any, ...] = (),
    allow_extra_deliveries: bool = False,
) -> bool:
    if _has_invalid_managed_output(observation):
        return False
    if not all(bool(observation.get(item)) for item in required):
        return False
    deliveries = observation.get("deliveries", [])
    archives = observation.get("case_archives", [])
    if not isinstance(deliveries, list) or not isinstance(archives, list):
        return False

    selected_indexes: set[int] = set()
    for requirement in delivery_requirements:
        matches = [
            index
            for index, delivery in enumerate(deliveries)
            if isinstance(delivery, Mapping)
            and delivery.get("document_type") == requirement.document_type
            and delivery.get("mode") == requirement.mode
        ]
        if len(matches) != 1:
            return False
        index = matches[0]
        delivery = deliveries[index]
        if (
            not delivery.get("document_path")
            or (requirement.require_checklist and not delivery.get("checklist_path"))
        ):
            return False
        case_id = delivery.get("case_id")
        same_case_archives = [
            archive
            for archive in archives
            if isinstance(archive, Mapping) and archive.get("case_id") == case_id
        ]
        if requirement.require_case_archive and not same_case_archives:
            return False
        if requirement.require_same_case_id:
            if (
                not same_case_archives
                or delivery.get("case_archive_path")
                != same_case_archives[0].get("archive_path")
            ):
                return False
        if requirement.require_current_archive_revision:
            if (
                (delivery.get("archive_revision")
                 != delivery.get("current_archive_revision")
                 and delivery.get("archive_dependencies_verified") is not True)
                or (
                    same_case_archives
                    and delivery.get("current_archive_revision")
                    != same_case_archives[0].get("archive_revision")
                )
            ):
                return False
        selected_indexes.add(index)

    if not allow_extra_deliveries and delivery_requirements:
        if len(selected_indexes) != len(deliveries):
            return False
    elif not allow_extra_deliveries and not delivery_requirements:
        if "docx" not in required and deliveries:
            return False
        if "docx" in required and len(deliveries) > 1:
            return False
    return True


class JourneyOracle:
    """所有中途 break 与终局判定共用的确定性完成/归因 Oracle。"""

    def __init__(self, journey: Any):
        self.journey = journey

    def evaluate(self, state: Any) -> Any:
        if state.journey.id != self.journey.id:
            return _assessment(
                False,
                failure_category="Harness",
                failure_code="journey_identity_mismatch",
                message="Oracle 收到的 Journey 身份不一致",
            )
        paths = self.journey.completion_paths or self._legacy_paths()
        for path in paths:
            missing = self._missing_for_path(path, state)
            if not missing:
                return _assessment(
                    True,
                    completion_path=path.id,
                    message=f"完成路径 {path.id} 已满足",
                )

        missing = self._missing_for_path(paths[0], state) if paths else ()
        category, code = self._attribute_incomplete(paths, state, missing)
        return _assessment(
            False,
            failure_category=category,
            failure_code=code,
            message=self._failure_message(code, missing),
            missing=tuple(missing),
        )

    def _legacy_paths(self) -> tuple[Any, ...]:
        from . import JourneyCompletionPath

        required_groups = tuple(label for label, _ in self.journey.response_markers)
        paths = [
            JourneyCompletionPath(
                id="delivered",
                required_artifacts=self.journey.required_artifacts,
                required_deliveries=self.journey.required_deliveries,
                required_response_groups=required_groups,
            )
        ]
        if self.journey.allow_degraded_delivery:
            trigger = _infer_delivery_trigger(self.journey)
            paths.append(
                JourneyCompletionPath(
                    id="delivery_degraded",
                    requires_sent_facts=(trigger,) if trigger else (),
                    required_artifacts=("case_archive",),
                    required_response_groups_after_trigger=(
                        "delivery",
                    )
                    if "delivery" in required_groups
                    else (),
                    requires_capabilities_unavailable=(
                        "complete_document_pipeline_available",
                    ),
                    forbids_artifacts=("docx", "checklist"),
                )
            )
        return tuple(paths)

    def _missing_for_path(self, path: Any, state: Any) -> tuple[str, ...]:
        missing: list[str] = []
        if not state.runtime_completed:
            missing.append("runtime_turn_incomplete")
        restart_context = state.restart_context
        if restart_context and restart_context.get("status") == "available":
            context_case_id = restart_context.get("case_id")
            context_revision = restart_context.get("archive_revision")
            context_path = restart_context.get("archive_path")
            context_archive_sha256 = restart_context.get("archive_sha256")
            context_facts_sha256 = restart_context.get(
                "confirmed_fact_summary_sha256"
            )
            restart_observation = (
                state.restart_observation
                if state.restart_observation is not None
                else state.observation
            )
            archives = restart_observation.get("case_archives", [])
            matching_archives = [
                item
                for item in archives
                if isinstance(item, Mapping)
                and item.get("case_id") == context_case_id
            ]
            if (
                not isinstance(context_case_id, str)
                or not isinstance(context_revision, int)
                or isinstance(context_revision, bool)
                or len(matching_archives) != 1
                or matching_archives[0].get("archive_revision") != context_revision
            ):
                missing.append("restart_context_revision_mismatch")
            elif (
                not isinstance(context_path, str)
                or matching_archives[0].get("archive_path") != context_path
            ):
                missing.append("restart_context_archive_path_mismatch")
            elif (
                not isinstance(context_archive_sha256, str)
                or matching_archives[0].get("archive_sha256")
                != context_archive_sha256
            ):
                missing.append("restart_context_archive_sha256_mismatch")
            elif (
                not isinstance(context_facts_sha256, str)
                or matching_archives[0].get("confirmed_fact_summary_sha256")
                != context_facts_sha256
            ):
                missing.append("restart_context_fact_summary_sha256_mismatch")
        sent = set(state.sent_fact_ids)
        for fact_id in path.requires_sent_facts:
            if fact_id not in sent:
                missing.append(f"sent_fact:{fact_id}")
        for milestone_id in path.required_milestones:
            if milestone_id not in state.observed_milestones:
                missing.append(f"milestone:{milestone_id}")
        observation = state.observation
        if _public_artifact_observer_permission_denied(observation):
            missing.append("artifact_observer_permission_denied")
        if observation.get("inspection_error_count", 0):
            missing.append("artifact_observer_failed")
        if (
            path.required_deliveries
            and observation.get("managed_view_status")
            in {
                "rejected",
                "unbound_command",
                "missing_or_unparseable_view_response",
            }
        ):
            missing.append("managed_view_failed")
        if not _artifacts_satisfied(
            observation,
            path.required_artifacts,
            delivery_requirements=path.required_deliveries,
            allow_extra_deliveries=self.journey.allow_extra_deliveries,
        ):
            for artifact in path.required_artifacts:
                if not observation.get(artifact, False):
                    missing.append(f"artifact:{artifact}")
            if path.required_deliveries and not _artifacts_satisfied(
                observation,
                (),
                delivery_requirements=path.required_deliveries,
                allow_extra_deliveries=self.journey.allow_extra_deliveries,
            ):
                missing.append("delivery:required")
        for artifact in path.forbids_artifacts:
            if observation.get(artifact, False):
                missing.append(f"forbidden_artifact:{artifact}")
        for capability in path.requires_capabilities_unavailable:
            if _capability_value(state.capabilities, capability) is not False:
                missing.append(f"capability_unavailable:{capability}")
        groups = dict(self.journey.response_markers)
        transcript_text = _transcript_text(state.transcripts)
        for group in path.required_response_groups:
            if not _contains_any(transcript_text, groups.get(group, ())):
                missing.append(f"response_group:{group}")
        if path.required_response_groups_after_trigger:
            trigger_turn = _last_trigger_turn(
                state.transcripts, path.requires_sent_facts
            )
            if trigger_turn is None:
                for group in path.required_response_groups_after_trigger:
                    missing.append(f"response_group_after_trigger:{group}")
            else:
                after_text = _transcript_text(
                    item
                    for item in state.transcripts
                    if _int_value(item.get("turn")) >= trigger_turn
                )
                for group in path.required_response_groups_after_trigger:
                    if not _contains_any(after_text, groups.get(group, ())):
                        missing.append(f"response_group_after_trigger:{group}")
        if path.id == "delivery_degraded" and self.journey.degradation_markers:
            marker_text = transcript_text
            trigger_turn = _last_trigger_turn(
                state.transcripts, path.requires_sent_facts
            )
            if trigger_turn is not None:
                marker_text = _transcript_text(
                    item
                    for item in state.transcripts
                    if _int_value(item.get("turn")) >= trigger_turn
                )
            if not _contains_any(marker_text, self.journey.degradation_markers):
                missing.append("degradation_marker")
        return tuple(dict.fromkeys(missing))

    def _attribute_incomplete(
        self,
        paths: tuple[Any, ...],
        state: Any,
        missing: tuple[str, ...],
    ) -> tuple[str, str]:
        if not state.runtime_completed:
            return "Runtime Adapter", "runtime_turn_incomplete"
        if "managed_view_failed" in missing:
            return "Runtime Adapter", "managed_view_failed"
        if "artifact_observer_failed" in missing:
            return "Runtime Adapter", "public_artifact_observer_failed"
        if "artifact_observer_permission_denied" in missing:
            return "Runtime Adapter", "public_artifact_observer_permission_denied"
        # 归因只考虑当前能力允许的路径；不可用的降级路径送过一张
        # 请求卡，不能替正常路径尚未送达的状态/确认事实补足前置条件。
        applicable_paths = tuple(
            path for path in paths
            if all(
                _capability_value(state.capabilities, capability) is False
                for capability in path.requires_capabilities_unavailable
            )
        )
        attribution_paths = applicable_paths or paths
        trigger_ids = {
            fact_id
            for path in attribution_paths
            for fact_id in path.requires_sent_facts
        }
        if (
            self.journey.restart is not None
            and any(item.startswith("milestone:") for item in missing)
            and (
                not state.restart_context
                or state.restart_context.get("status") != "available"
            )
        ):
            return "Harness", "restart_context_missing"
        if any(item.startswith("restart_context_") for item in missing):
            return "Harness", "restart_context_stale"
        if trigger_ids and not any(
            path.requires_sent_facts
            and set(path.requires_sent_facts).issubset(state.sent_fact_ids)
            for path in attribution_paths
        ):
            return "Scenario", "completion_trigger_not_sent"
        if any(item.startswith("capability_unavailable:") for item in missing):
            if any(
                item.startswith("sent_fact:")
                and item.split(":", 1)[1] in trigger_ids
                for item in missing
            ):
                return "Scenario", "completion_trigger_not_sent"
            return "SUT/Skill", "capability_path_not_available"
        if trigger_ids and trigger_ids.intersection(state.sent_fact_ids):
            return "SUT/Skill", "completion_contract_not_fulfilled"
        if any(item.startswith("milestone:") for item in missing):
            return "SUT/Skill", "milestone_not_observed"
        return "Scenario", "completion_path_not_reached"

    @staticmethod
    def _failure_message(code: str, missing: tuple[str, ...]) -> str:
        if code == "public_artifact_observer_permission_denied":
            return (
                "工作区权限阻止 Harness 验证公开交付产物；本次 Journey 未通过，"
                "不能据此判断 Skill 是否已生成文件。缺少："
                + ", ".join(missing)
            )
        artifact_missing = [
            item.split(":", 1)[1]
            for item in missing
            if item.startswith("artifact:")
        ]
        if artifact_missing:
            return "未观察到要求的公开文件产物：" + ", ".join(artifact_missing)
        messages = {
            "runtime_turn_incomplete": "平台 Agent 最后一轮未完成，不能仅凭已生成文件认定 Journey 交付",
            "managed_view_failed": "受管展示未成功，不能仅凭已生成文件认定 Journey 交付",
            "completion_trigger_not_sent": "Scenario 未发送任何完成路径所需的交付/确认事实",
            "capability_path_not_available": "已发出交付请求，但模型 A 未按能力不可用路径完成明确降级",
            "completion_contract_not_fulfilled": "已发送正确请求，但模型 A 未履行完成路径契约",
            "restart_context_missing": "重启恢复上下文未注入，无法验证恢复后的公开事实",
            "restart_context_stale": "重启恢复上下文未绑定同一案件的最新档案 revision、路径或事实摘要",
            "milestone_not_observed": "未观察到完成路径所需的恢复或业务里程碑",
            "completion_path_not_reached": "未达到任何显式 Journey 完成路径",
        }
        suffix = "；缺少：" + ", ".join(missing) if missing else ""
        return messages.get(code, code) + suffix


__all__ = ["JourneyOracle"]
