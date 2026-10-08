from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts.runtime_resources import RUNTIME_FILES


BASELINE_SCHEMA_VERSION = "legal-baseline-v1"
VERIFICATION_CONTRACT_VERSION = "legal-verification-v2"
SOURCE_CLEANUP_SCHEMA_VERSION = "source-cleanup-v1"
SOURCE_CLEANUP_COUNT = 13
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BASELINE_FIELDS = (
    "rule_id",
    "name",
    "issuing_authority",
    "version",
    "status",
    "jurisdiction",
    "official_url",
    "last_verified_on",
    "review_due_on",
    "verification_basis",
)
_DYNAMIC_TRIGGERS = frozenset(
    {
        "local_rule",
        "dynamic_value",
        "new_interpretation",
        "source_conflict",
        "stale_baseline",
        "user_requested_latest",
        "high_risk_finalization",
    }
)
_OUTCOMES = frozenset({"verified", "source_conflict", "stale", "unavailable"})
_OUTCOME_LABELS = {
    "verified": "已完成动态核验",
    "source_conflict": "官方来源或基线存在冲突，未形成单一现行结论",
    "stale": "已有基线超过复核日期，尚未确认现行规则",
    "unavailable": "官方来源暂未完成动态核验，当前无法取得可确认的官方来源",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_HTTPS_HOST_PATTERN = re.compile(
    r"^https://(?P<host>[^/?#:\s]+)(?::\d+)?(?:[/?#].*)?$",
    re.IGNORECASE,
)
_LOCAL_PUBLISHER_HOSTS = (
    ("shanghai.gov.cn", "上海市"),
    ("sh.gov.cn", "上海市"),
)
_NATIONAL_JURISDICTIONS = frozenset({"全国", "中央", "国家", "中国"})
_PENDING_JURISDICTIONS = frozenset(
    {"", "待用户确认", "待确认", "未填写", "未提供", "未确认", "不明", "未知"}
)
_TRUSTED_FACT_STATUS_MARKERS = ("**事实状态：用户陈述", "**事实状态：证据支持事实")
_TRUSTED_JURISDICTION_PATTERN = re.compile(
    r"(?:适用地区|地区|城市|劳动合同履行地|工作地点|工作地|"
    r"单位所在地|用人单位所在地|履行地)\s*[：:]\s*([^，。；;\n]+)"
)


def _https_hostname(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or any(
        marker in value for marker in ("\n", "\r")
    ):
        return None
    matched = _HTTPS_HOST_PATTERN.fullmatch(value)
    if matched is None:
        return None
    return matched.group("host").lower()


def _clean_jurisdiction(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s，。；;、:：()（）]+", "", value).casefold()


def _is_pending_jurisdiction(value: Any) -> bool:
    cleaned = _clean_jurisdiction(value)
    return not cleaned or cleaned in {
        _clean_jurisdiction(item) for item in _PENDING_JURISDICTIONS
    }


def _jurisdiction_matches(left: Any, right: Any) -> bool:
    left_clean = _clean_jurisdiction(left)
    right_clean = _clean_jurisdiction(right)
    if not left_clean or not right_clean:
        return False
    return (
        left_clean == right_clean
        or left_clean in right_clean
        or right_clean in left_clean
    )


def _publisher_jurisdiction(source: Mapping[str, Any]) -> str | None:
    host = _https_hostname(source.get("url"))
    if host is not None:
        for suffix, jurisdiction in _LOCAL_PUBLISHER_HOSTS:
            if host == suffix or host.endswith("." + suffix):
                return jurisdiction
    value = source.get("publisher_jurisdiction")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_local_publisher(value: Any) -> bool:
    cleaned = _clean_jurisdiction(value)
    return bool(cleaned) and cleaned not in {
        _clean_jurisdiction(item) for item in _NATIONAL_JURISDICTIONS
    } and not _is_pending_jurisdiction(value)


def _error(
    code: str,
    *,
    path: str,
    message: str,
    expected: Any = None,
    received: Any = None,
    recoverable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "expected": expected,
        "received": received,
        "recoverable": recoverable,
    }


def _failure(
    operation: str,
    errors: list[dict[str, Any]],
    *,
    contract_version: str = BASELINE_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "contract_version": contract_version,
        "operation": operation,
        "ok": False,
        "errors": errors,
        "warnings": [],
    }


def _success(
    operation: str,
    result: dict[str, Any],
    *,
    contract_version: str = BASELINE_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "contract_version": contract_version,
        "operation": operation,
        "ok": True,
        "result": result,
        "errors": [],
        "warnings": [],
    }


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


class LegalVerificationTracer:
    """Validate a published legal baseline without performing network access."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._baseline_path = (
            self._workspace_root / "references" / "legal" / "legal-baseline.v1.json"
        )
        self._source_cleanup_path = (
            self._workspace_root / "references" / "legal" / "source-cleanup.v1.json"
        )

    def load_baseline(self) -> dict[str, Any]:
        try:
            document = json.loads(self._baseline_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _failure(
                "load_baseline",
                [
                    _error(
                        "baseline_not_found",
                        path="baseline",
                        message="版本化法律基线不存在",
                        expected="legal-baseline.v1.json",
                        received=str(self._baseline_path),
                    )
                ],
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return _failure(
                "load_baseline",
                [
                    _error(
                        "baseline_invalid",
                        path="baseline",
                        message="版本化法律基线无法读取或解析",
                        expected="UTF-8 JSON legal baseline",
                        received=error.__class__.__name__,
                        recoverable=False,
                    )
                ],
            )

        result = self.validate_baseline(document)
        if not result["ok"]:
            return result
        result["operation"] = "load_baseline"
        return result

    def validate_baseline(self, document: Any) -> dict[str, Any]:
        operation = "validate_baseline"
        if not isinstance(document, Mapping):
            return _failure(
                operation,
                [
                    _error(
                        "baseline_invalid",
                        path="baseline",
                        message="法律基线必须是对象",
                        expected="object",
                        received=type(document).__name__,
                    )
                ],
            )

        errors: list[dict[str, Any]] = []
        if document.get("schema_version") != BASELINE_SCHEMA_VERSION:
            errors.append(
                _error(
                    "baseline_invalid",
                    path="schema_version",
                    message="法律基线版本不受支持",
                    expected=BASELINE_SCHEMA_VERSION,
                    received=document.get("schema_version"),
                )
            )
        if not isinstance(document.get("baseline_id"), str) or not document["baseline_id"].strip():
            errors.append(
                _error(
                    "baseline_invalid",
                    path="baseline_id",
                    message="法律基线必须有稳定版本标识",
                    expected="non-empty string",
                    received=document.get("baseline_id"),
                )
            )
        if not isinstance(document.get("policy"), str) or not document["policy"].strip():
            errors.append(
                _error(
                    "baseline_invalid",
                    path="policy",
                    message="法律基线必须说明案件中不重复联网的策略",
                    expected="non-empty string",
                    received=document.get("policy"),
                )
            )

        rules = document.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append(
                _error(
                    "baseline_invalid",
                    path="rules",
                    message="法律基线至少需要一条规则元数据",
                    expected="non-empty array",
                    received=rules,
                )
            )
            return _failure(operation, errors)

        seen_ids: set[str] = set()
        for index, rule in enumerate(rules):
            path = f"rules[{index}]"
            if not isinstance(rule, Mapping):
                errors.append(
                    _error(
                        "baseline_invalid",
                        path=path,
                        message="规则元数据必须是对象",
                        expected="object",
                        received=type(rule).__name__,
                    )
                )
                continue
            for field in _BASELINE_FIELDS:
                value = rule.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        _error(
                            "baseline_invalid",
                            path=f"{path}.{field}",
                            message="规则元数据字段不能为空",
                            expected="non-empty string",
                            received=value,
                        )
                    )
            rule_id = rule.get("rule_id")
            if isinstance(rule_id, str):
                if rule_id in seen_ids:
                    errors.append(
                        _error(
                            "baseline_invalid",
                            path=f"{path}.rule_id",
                            message="法律基线规则标识必须唯一",
                            expected="unique rule_id",
                            received=rule_id,
                        )
                    )
                seen_ids.add(rule_id)
            if rule.get("status") != "effective":
                errors.append(
                    _error(
                        "baseline_invalid",
                        path=f"{path}.status",
                        message="稳定基线只能登记现行有效规则",
                        expected="effective",
                        received=rule.get("status"),
                    )
                )
            if not isinstance(rule.get("official_url"), str) or not rule.get(
                "official_url", ""
            ).startswith("https://"):
                errors.append(
                    _error(
                        "baseline_invalid",
                        path=f"{path}.official_url",
                        message="官方来源必须是 HTTPS 链接",
                        expected="https URL",
                        received=rule.get("official_url"),
                    )
                )
            for field in ("last_verified_on", "review_due_on"):
                if not _valid_date(rule.get(field)):
                    errors.append(
                        _error(
                            "baseline_invalid",
                            path=f"{path}.{field}",
                            message="日期必须是有效的 ISO 日期",
                            expected="YYYY-MM-DD",
                            received=rule.get(field),
                        )
                    )
            basis = rule.get("verification_basis")
            if isinstance(basis, str) and basis.strip() == rule.get("official_url"):
                errors.append(
                    _error(
                        "baseline_invalid",
                        path=f"{path}.verification_basis",
                        message="链接存在不能单独代表基线内容已核验",
                        expected="independent verification basis",
                        received=basis,
                    )
                )

        if errors:
            return _failure(operation, errors)
        return _success(operation, dict(document))

    def load_source_cleanup(self) -> dict[str, Any]:
        try:
            document = json.loads(self._source_cleanup_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _failure(
                "load_source_cleanup",
                [
                    _error(
                        "source_cleanup_not_found",
                        path="source_cleanup",
                        message="来源登记清理清单不存在",
                        expected="source-cleanup.v1.json",
                        received=str(self._source_cleanup_path),
                    )
                ],
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return _failure(
                "load_source_cleanup",
                [
                    _error(
                        "source_cleanup_invalid",
                        path="source_cleanup",
                        message="来源登记清理清单无法读取或解析",
                        expected="UTF-8 JSON source cleanup manifest",
                        received=error.__class__.__name__,
                        recoverable=False,
                    )
                ],
            )
        result = self.validate_source_cleanup(document)
        if not result["ok"]:
            return result
        missing = [
            item["legacy_file"]
            for item in result["result"]["registrations"]
            if not (self._workspace_root / item["legacy_file"]).is_file()
        ]
        if missing:
            return _failure(
                "load_source_cleanup",
                [
                    _error(
                        "source_cleanup_invalid",
                        path="registrations.legacy_file",
                        message="来源登记清理清单引用了不存在的旧文件",
                        expected="existing repository file",
                        received=missing,
                    )
                ],
            )
        discovered = self._legacy_source_registration_files()
        registered = {item["legacy_file"] for item in result["result"]["registrations"]}
        if discovered != registered:
            return _failure(
                "load_source_cleanup",
                [
                    _error(
                        "source_cleanup_incomplete",
                        path="registrations",
                        message="清理清单没有逐一覆盖当前声明source_id的既有登记",
                        expected=sorted(discovered),
                        received=sorted(registered),
                    )
                ],
            )
        result["operation"] = "load_source_cleanup"
        return result

    def validate_source_cleanup(self, document: Any) -> dict[str, Any]:
        operation = "validate_source_cleanup"
        if not isinstance(document, Mapping):
            return _failure(
                operation,
                [
                    _error(
                        "source_cleanup_invalid",
                        path="source_cleanup",
                        message="来源登记清理清单必须是对象",
                        expected="object",
                        received=type(document).__name__,
                    )
                ],
            )
        errors: list[dict[str, Any]] = []
        if document.get("schema_version") != SOURCE_CLEANUP_SCHEMA_VERSION:
            errors.append(
                _error(
                    "source_cleanup_invalid",
                    path="schema_version",
                    message="来源登记清理清单版本不受支持",
                    expected=SOURCE_CLEANUP_SCHEMA_VERSION,
                    received=document.get("schema_version"),
                )
            )
        registrations = document.get("registrations")
        if not isinstance(registrations, list):
            errors.append(
                _error(
                    "source_cleanup_invalid",
                    path="registrations",
                    message="来源登记清理清单必须是数组",
                    expected="array",
                    received=registrations,
                )
            )
            return _failure(operation, errors)
        if len(registrations) != SOURCE_CLEANUP_COUNT:
            errors.append(
                _error(
                    "source_cleanup_invalid",
                    path="registrations",
                    message="来源登记清理清单必须覆盖全部现有登记",
                    expected=SOURCE_CLEANUP_COUNT,
                    received=len(registrations),
                )
            )
        seen_files: set[str] = set()
        dispositions = {"valid", "replace", "merge", "obsolete"}
        for index, item in enumerate(registrations):
            path = f"registrations[{index}]"
            if not isinstance(item, Mapping):
                errors.append(
                    _error(
                        "source_cleanup_invalid",
                        path=path,
                        message="来源登记必须是对象",
                        expected="object",
                        received=type(item).__name__,
                    )
                )
                continue
            legacy_file = item.get("legacy_file")
            if not isinstance(legacy_file, str) or not legacy_file.strip():
                errors.append(
                    _error(
                        "source_cleanup_invalid",
                        path=f"{path}.legacy_file",
                        message="旧登记文件路径不能为空",
                        expected="repository-relative path",
                        received=legacy_file,
                    )
                )
            elif legacy_file in seen_files:
                errors.append(
                    _error(
                        "source_cleanup_invalid",
                        path=f"{path}.legacy_file",
                        message="旧登记文件不得重复分类",
                        expected="unique legacy_file",
                        received=legacy_file,
                    )
                )
            else:
                seen_files.add(legacy_file)
            if item.get("disposition") not in dispositions:
                errors.append(
                    _error(
                        "source_cleanup_invalid",
                        path=f"{path}.disposition",
                        message="每份来源登记必须归入有效、替换、合并或废弃",
                        expected=sorted(dispositions),
                        received=item.get("disposition"),
                    )
                )
            for field in ("reason", "successor"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(
                        _error(
                            "source_cleanup_invalid",
                            path=f"{path}.{field}",
                            message="清理证据字段不能为空",
                            expected="non-empty string",
                            received=item.get(field),
                        )
                    )
            evidence = item.get("evidence")
            if (
                not isinstance(evidence, list)
                or not evidence
                or any(not isinstance(value, str) or not value.strip() for value in evidence)
            ):
                errors.append(
                    _error(
                        "source_cleanup_invalid",
                        path=f"{path}.evidence",
                        message="每份来源登记必须有可复核证据",
                        expected="non-empty array of strings",
                        received=evidence,
                    )
                )
            else:
                evidence_targets: list[str] = []
                for evidence_index, reference in enumerate(evidence):
                    target = self._manifest_reference_target(reference)
                    if target is None:
                        errors.append(
                            _error(
                                "source_cleanup_invalid",
                                path=f"{path}.evidence[{evidence_index}]",
                                message="清理证据必须指向存在的文件和可定位片段",
                                expected="existing repository file with optional fragment",
                                received=reference,
                            )
                        )
                    else:
                        evidence_targets.append(target)
                if (
                    isinstance(legacy_file, str)
                    and legacy_file.strip()
                    and legacy_file not in evidence_targets
                ):
                    errors.append(
                        _error(
                            "source_cleanup_invalid",
                            path=f"{path}.evidence",
                            message="清理证据必须直接包含对应的旧来源登记文件",
                            expected=legacy_file,
                            received=evidence_targets,
                        )
                    )
            successor = item.get("successor")
            if isinstance(successor, str) and successor.strip():
                for successor_index, reference in enumerate(successor.split(" + ")):
                    reference = reference.strip()
                    target = self._manifest_reference_target(reference)
                    if target is None:
                        errors.append(
                            _error(
                                "source_cleanup_invalid",
                                path=f"{path}.successor[{successor_index}]",
                                message="清理后继必须指向存在的文件和可定位片段",
                                expected="existing repository file with optional fragment",
                                received=reference,
                            )
                        )
                    elif not target.startswith(("references/", "docs/adr/")):
                        errors.append(
                            _error(
                                "source_cleanup_invalid",
                                path=f"{path}.successor[{successor_index}]",
                                message="清理后继必须指向 canonical 参考或 ADR，不能指向入口或测试文件",
                                expected="references/ or docs/adr/ path",
                                received=reference,
                            )
                        )
        if errors:
            return _failure(operation, errors)
        return _success(operation, dict(document))

    def _manifest_reference_target(self, reference: Any) -> str | None:
        if not isinstance(reference, str) or not reference.strip():
            return None
        path_text, separator, fragment = reference.partition("#")
        candidate = Path(path_text)
        if candidate.is_absolute() or ".." in candidate.parts:
            return None
        target = self._workspace_root / candidate
        if not target.is_file():
            return None
        if not separator or not fragment:
            return candidate.as_posix()
        try:
            if fragment in target.read_text(encoding="utf-8"):
                return candidate.as_posix()
        except (OSError, UnicodeError):
            return None
        return None

    def _legacy_source_registration_files(self) -> set[str]:
        excluded = {"legal-baseline.v1.json", "source-cleanup.v1.json"}
        discovered: set[str] = set()
        legal_root = self._workspace_root / "references" / "legal"
        for path in (self._workspace_root / relative for relative in RUNTIME_FILES if relative.startswith("references/legal/") and relative.endswith(".json")):
            if path.name in excluded:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if '"source_id"' in text:
                discovered.add(path.relative_to(self._workspace_root).as_posix())
        return discovered

    def assess(self, observation: Any) -> dict[str, Any]:
        """Classify a model-provided observation; never fetch or inspect a URL."""

        operation = "assess"
        if not isinstance(observation, Mapping):
            return _failure(
                operation,
                [
                    _error(
                        "invalid_observation",
                        path="observation",
                        message="动态核验观察必须是对象",
                        expected="object",
                        received=type(observation).__name__,
                    )
                ],
                contract_version=VERIFICATION_CONTRACT_VERSION,
            )

        errors: list[dict[str, Any]] = []
        required = (
            "scope",
            "trigger",
            "outcome",
            "official_sources",
            "accessed_on",
            "jurisdiction",
            "impact",
        )
        for key in sorted(set(observation) - set(required)):
            errors.append(
                _error(
                    "invalid_observation",
                    path=f"observation.{key}",
                    message="动态核验观察包含未公开字段；不得携带网页正文或内部载荷",
                    expected=sorted(required),
                    received=observation[key],
                )
            )
        for field in required:
            if field not in observation:
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"observation.{field}",
                        message="动态核验观察缺少必填字段",
                        expected="required",
                        received=None,
                    )
                )

        def require_text(field: str) -> None:
            value = observation.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"observation.{field}",
                        message="字段必须是非空文本",
                        expected="non-empty string",
                        received=value,
                    )
                )
            elif any(marker in value for marker in ("\x00", "<!--", "## ", "### [")):
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"observation.{field}",
                        message="字段不能注入档案章节或事务标记",
                        expected="plain text",
                        received=value,
                    )
                )

        for field in ("scope", "jurisdiction", "impact"):
            require_text(field)

        trigger = observation.get("trigger")
        if trigger not in _DYNAMIC_TRIGGERS:
            errors.append(
                _error(
                    "invalid_observation",
                    path="observation.trigger",
                    message="核验触发原因不在公开枚举内",
                    expected=sorted(_DYNAMIC_TRIGGERS),
                    received=trigger,
                )
            )
        outcome = observation.get("outcome")
        if outcome not in _OUTCOMES:
            errors.append(
                _error(
                    "invalid_observation",
                    path="observation.outcome",
                    message="核验结论不在公开枚举内；链接可访问不是结论",
                    expected=sorted(_OUTCOMES),
                    received=outcome,
                )
            )
        if not _valid_date(observation.get("accessed_on")):
            errors.append(
                _error(
                    "invalid_observation",
                    path="observation.accessed_on",
                    message="访问日期必须是有效的 ISO 日期",
                    expected="YYYY-MM-DD",
                    received=observation.get("accessed_on"),
                )
            )
        sources = observation.get("official_sources")
        if not isinstance(sources, list):
            errors.append(
                _error(
                    "invalid_observation",
                    path="observation.official_sources",
                    message="官方来源必须是数组",
                    expected="array",
                    received=sources,
                )
            )
            sources = []
        for index, source in enumerate(sources):
            path = f"observation.official_sources[{index}]"
            if not isinstance(source, Mapping):
                errors.append(
                    _error(
                        "invalid_observation",
                        path=path,
                        message="官方来源必须是对象",
                        expected="object",
                        received=type(source).__name__,
                    )
                )
                continue
            allowed_source_fields = {
                "title",
                "url",
                "final_url",
                "jurisdiction",
                "publisher_jurisdiction",
                "content_sha256",
            }
            for key in sorted(set(source) - allowed_source_fields):
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"{path}.{key}",
                        message="官方来源不得携带网页正文或内部载荷",
                        expected=sorted(allowed_source_fields),
                        received=source[key],
                    )
                )
            for field in ("title", "url", "jurisdiction"):
                value = source.get(field)
                if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                    errors.append(
                        _error(
                            "invalid_observation",
                            path=f"{path}.{field}",
                            message="官方来源字段必须是非空单行文本",
                            expected="non-empty single-line string",
                            received=value,
                        )
                    )
            publisher_jurisdiction = source.get("publisher_jurisdiction")
            if publisher_jurisdiction is not None and (
                not isinstance(publisher_jurisdiction, str)
                or not publisher_jurisdiction.strip()
                or "\n" in publisher_jurisdiction
                or "\r" in publisher_jurisdiction
            ):
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"{path}.publisher_jurisdiction",
                        message="来源发布地区必须是非空单行文本",
                        expected="non-empty single-line string",
                        received=publisher_jurisdiction,
                    )
                )
            registered_host = _https_hostname(source.get("url"))
            if registered_host is None:
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"{path}.url",
                        message="官方来源必须使用 HTTPS 链接",
                        expected="https URL",
                        received=source.get("url"),
                    )
                )
            final_url = source.get("final_url")
            if final_url is not None:
                final_host = _https_hostname(final_url)
                if final_host is None:
                    errors.append(
                        _error(
                            "invalid_observation",
                            path=f"{path}.final_url",
                            message="官方来源最终链接必须是有效的 HTTPS 链接",
                            expected="https URL",
                            received=final_url,
                        )
                    )
                elif registered_host is not None and final_host != registered_host:
                    errors.append(
                        _error(
                            "invalid_observation",
                            path=f"{path}.final_url",
                            message="最终链接必须与登记来源保持同一官方主机",
                            expected=registered_host,
                            received=final_host,
                        )
                    )
            digest = source.get("content_sha256")
            if digest is not None and (
                not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None
            ):
                errors.append(
                    _error(
                        "invalid_observation",
                        path=f"{path}.content_sha256",
                        message="来源内容摘要必须是 64 位十六进制 SHA-256",
                        expected="64 hex characters",
                        received=digest,
                    )
                )
            if outcome == "verified":
                if final_url is None:
                    errors.append(
                        _error(
                            "invalid_observation",
                            path=f"{path}.final_url",
                            message="已核验来源必须记录最终落地链接",
                            expected="https URL",
                            received=None,
                        )
                    )
                if digest is None:
                    errors.append(
                        _error(
                            "invalid_observation",
                            path=f"{path}.content_sha256",
                            message="已核验来源必须记录内容 SHA-256 摘要",
                            expected="64 hex characters",
                            received=None,
                        )
                    )
        if outcome == "verified" and not sources:
            errors.append(
                _error(
                    "invalid_observation",
                    path="observation.official_sources",
                    message="已核验结论必须列出至少一个官方来源；不能由 URL 存在自动推导",
                    expected="non-empty array",
                    received=sources,
                )
            )
        if errors:
            return _failure(
                operation,
                errors,
                contract_version=VERIFICATION_CONTRACT_VERSION,
            )

        content = self._render_archive_change(observation)
        return _success(
            operation,
            {
                "status": outcome,
                "dynamic_verification": True,
                "archive_change": {
                    "operation": "append",
                    "record_type": "authority",
                    "content_markdown": content,
                },
            },
            contract_version=VERIFICATION_CONTRACT_VERSION,
        )

    @staticmethod
    def _trusted_case_jurisdictions(markdown: str) -> set[str]:
        trusted_text_parts: list[str] = []
        bindings: set[str] = set()
        metadata_match = re.search(
            r"^- 适用地区：([^\r\n]+)$", markdown, re.MULTILINE
        )
        if metadata_match is not None and not _is_pending_jurisdiction(
            metadata_match.group(1)
        ):
            bindings.add(metadata_match.group(1).strip())

        trusted_scope = re.split(
            r"^## (?:分析与假设|计算结果|法律核验|风险与确认|下一步|更新记录)$",
            markdown,
            maxsplit=1,
            flags=re.MULTILINE,
        )[0]
        for block in re.split(r"^### [^\r\n]+$", trusted_scope, flags=re.MULTILINE):
            if any(marker in block for marker in _TRUSTED_FACT_STATUS_MARKERS):
                trusted_text_parts.append(block)

        for text in trusted_text_parts:
            bindings.update(
                match.group(1).strip()
                for match in _TRUSTED_JURISDICTION_PATTERN.finditer(text)
                if not _is_pending_jurisdiction(match.group(1))
            )
        return bindings

    def _publisher_binding_error(
        self,
        archive: Any,
        *,
        case_id: str,
        observation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        sources = observation.get("official_sources")
        if not isinstance(sources, list):
            return None
        local_sources: list[tuple[int, str]] = []
        for index, source in enumerate(sources):
            if not isinstance(source, Mapping):
                continue
            publisher = _publisher_jurisdiction(source)
            if publisher is not None and _is_local_publisher(publisher):
                local_sources.append((index, publisher))
        if not local_sources:
            return None

        try:
            current = archive.read(case_id)
        except (AttributeError, OSError, TypeError):
            return _error(
                "jurisdiction_binding_unavailable",
                path="case_archive",
                message="无法读取当前案情档案以验证地方来源的适用地区",
                expected="readable current case archive",
                received=case_id,
                recoverable=True,
            )
        if not isinstance(current, Mapping) or current.get("ok") is not True:
            return _error(
                "jurisdiction_binding_unavailable",
                path="case_archive",
                message="无法读取当前案情档案以验证地方来源的适用地区",
                expected="successful current case archive read",
                received=current,
                recoverable=True,
            )
        markdown = current.get("result", {}).get("markdown")
        if not isinstance(markdown, str):
            return _error(
                "jurisdiction_binding_unavailable",
                path="case_archive.markdown",
                message="当前案情档案缺少可验证的地区绑定内容",
                expected="archive markdown",
                received=type(markdown).__name__,
                recoverable=True,
            )
        bindings = self._trusted_case_jurisdictions(markdown)
        for index, publisher in local_sources:
            if not bindings:
                return _error(
                    "jurisdiction_confirmation_required",
                    path=f"observation.official_sources[{index}].publisher_jurisdiction",
                    message="地方发布来源必须先绑定当前用户或当前案情档案确认的适用地区；规则适用地区不能替代发布地区绑定",
                    expected="matching current user-confirmed jurisdiction",
                    received=publisher,
                    recoverable=True,
                )
            if not any(_jurisdiction_matches(binding, publisher) for binding in bindings):
                return _error(
                    "jurisdiction_conflict",
                    path=f"observation.official_sources[{index}].publisher_jurisdiction",
                    message="地方发布来源与当前用户或当前案情档案确认的适用地区不一致",
                    expected=sorted(bindings),
                    received=publisher,
                    recoverable=True,
                )
        return None

    def record(
        self,
        archive: Any,
        *,
        case_id: str,
        expected_revision: int,
        observation: Any,
        change_summary: str = "记录一次最小法律核验结果",
    ) -> dict[str, Any]:
        """Submit one generated authority change through CaseArchive.commit."""

        assessment = self.assess(observation)
        if not assessment["ok"]:
            assessment["operation"] = "record"
            return assessment
        binding_error = self._publisher_binding_error(
            archive,
            case_id=case_id,
            observation=observation,
        )
        if binding_error is not None:
            return _failure(
                "record",
                [binding_error],
                contract_version=VERIFICATION_CONTRACT_VERSION,
            )
        return archive.commit(
            {
                "case_id": case_id,
                "expected_revision": expected_revision,
                "change_summary": change_summary,
                "changes": [assessment["result"]["archive_change"]],
            }
        )

    @staticmethod
    def _render_archive_change(observation: Mapping[str, Any]) -> str:
        lines = [
            f"核验事项：{observation['scope']}",
            f"核验结论：{_OUTCOME_LABELS[observation['outcome']]}",
            "官方来源：",
        ]
        sources = observation["official_sources"]
        if sources:
            for source in sources:
                line = (
                    f"- {source['title']}；登记链接：{source['url']}；"
                    f"适用地区：{source['jurisdiction']}"
                )
                publisher_jurisdiction = _publisher_jurisdiction(source)
                if publisher_jurisdiction is not None:
                    line += f"；发布地区：{publisher_jurisdiction}"
                if source.get("final_url"):
                    line += f"；最终链接：{source['final_url']}"
                if source.get("content_sha256"):
                    line += f"；内容 SHA-256：{source['content_sha256']}"
                lines.append(line)
        else:
            lines.append("- 未取得可确认的官方来源。")
        lines.extend(
            (
                f"访问日期：{observation['accessed_on']}",
                f"适用地区：{observation['jurisdiction']}",
                f"影响范围：{observation['impact']}",
            )
        )
        return "\n".join(lines)
