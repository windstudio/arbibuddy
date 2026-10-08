"""Public, model-facing ``amount.calculate`` task contract.

This module deliberately has no case-archive dependency.  The model chooses the
legal formula, facts, period and scenario; this boundary validates explicit
units and performs deterministic Decimal arithmetic only.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.runtime_identity import (
    RuntimeIdentityModule,
    runtime_identity_failure_message,
)

CONTRACT_VERSION = "amount.calculate-v1"
OPERATION = "amount.calculate"
FORMULA_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "references" / "amount-formulas.v1.json"
)
MONEY_QUANTUM = Decimal("0.01")
AGGREGATION_POLICIES = frozenset(
    {"single_scenario", "sum_scenarios", "mutually_exclusive"}
)
REQUIRED_FIELDS = (
    "calculation_label",
    "formula_id",
    "formula_version",
    "legal_basis_summary",
    "source_refs",
    "aggregation_policy",
    "scenarios",
    "rounding_policy",
)
ROUNDING_FIELDS = ("id", "quantum", "mode", "aggregation")
SCENARIO_FIELDS = ("scenario_id", "assumptions", "period", "inputs")
PERIOD_FIELDS = ("start_date", "end_date", "semantics")
MEASURE_FIELDS = ("value", "unit")


def _error(
    code: str,
    path: str,
    message: str,
    expected: Any,
    received: Any,
    *,
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


def _success(result: dict[str, Any], warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": OPERATION,
        "ok": True,
        "result": result,
        "errors": [],
        "warnings": warnings or [],
    }


def _failure(errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation": OPERATION,
        "ok": False,
        "errors": errors,
        "warnings": [],
    }


def _installation_integrity_failure() -> dict[str, Any] | None:
    try:
        preflight = RuntimeIdentityModule().preflight()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        preflight = {
            "verified": False,
            "failures": [f"preflight_error:{type(error).__name__}"],
        }
    if preflight.get("verified") is True:
        return None
    return _failure(
        [
            _error(
                "installed_skill_modified",
                "runtime",
                runtime_identity_failure_message(preflight),
                "verified installed Skill identity",
                preflight.get("failures", ["runtime_identity_preflight_failed"]),
                recoverable=False,
            )
        ]
    )


def _load_registry(path: Path = FORMULA_REGISTRY_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        registry = json.load(handle)
    if not isinstance(registry, dict):
        raise ValueError("amount formula registry must be an object")
    return registry


def _text(value: Any, path: str, errors: list[dict[str, Any]]) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        errors.append(
            _error(
                "invalid_text",
                path,
                "字段必须是非空单行文本",
                "non-empty single-line string",
                value,
            )
        )
        return None
    return value.strip()


def _decimal(
    value: Any, path: str, errors: list[dict[str, Any]], *, nonnegative: bool = True
) -> Decimal | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(
            _error(
                "invalid_decimal",
                path,
                "数值必须使用十进制定点字符串",
                "decimal string",
                value,
            )
        )
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        errors.append(
            _error(
                "invalid_decimal",
                path,
                "数值不是有效的十进制定点数字",
                "finite decimal string",
                value,
            )
        )
        return None
    if not parsed.is_finite():
        errors.append(
            _error(
                "invalid_decimal",
                path,
                "数值必须是有限数字",
                "finite decimal string",
                value,
            )
        )
        return None
    if nonnegative and parsed < 0:
        errors.append(
            _error(
                "negative_value",
                path,
                "计算输入不得为负值",
                "value >= 0",
                value,
            )
        )
        return None
    return parsed


def _exact_keys(
    value: Any, allowed: tuple[str, ...], path: str, errors: list[dict[str, Any]]
) -> bool:
    if value is None:
        errors.append(
            _error(
                "missing_input",
                path,
                "必填对象缺失",
                "object",
                None,
            )
        )
        return False
    if not isinstance(value, Mapping):
        errors.append(_error("invalid_object", path, "字段必须是对象", "object", value))
        return False
    for key in sorted(set(value) - set(allowed)):
        errors.append(
            _error(
                "unknown_field",
                f"{path}.{key}",
                "字段不在公开契约中",
                list(allowed),
                value[key],
            )
        )
    return True


def _measure(
    value: Any,
    path: str,
    errors: list[dict[str, Any]],
    *,
    allowed_units: set[str],
) -> tuple[Decimal | None, str | None]:
    if not _exact_keys(value, MEASURE_FIELDS, path, errors):
        return None, None
    assert isinstance(value, Mapping)
    parsed = _decimal(value.get("value"), f"{path}.value", errors)
    unit = _text(value.get("unit"), f"{path}.unit", errors)
    if unit is not None and unit not in allowed_units:
        errors.append(
            _error(
                "unit_mismatch",
                f"{path}.unit",
                "输入单位不适用于当前公式字段",
                sorted(allowed_units),
                unit,
            )
        )
    return parsed, unit


def _money(value: Decimal) -> str:
    return f"{value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP):.2f}"


def _exact_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _period(
    value: Any, path: str, errors: list[dict[str, Any]], allowed_semantics: set[str]
) -> dict[str, str] | None:
    if not _exact_keys(value, PERIOD_FIELDS, path, errors):
        return None
    assert isinstance(value, Mapping)
    start = _text(value.get("start_date"), f"{path}.start_date", errors)
    end = _text(value.get("end_date"), f"{path}.end_date", errors)
    semantics = _text(value.get("semantics"), f"{path}.semantics", errors)
    parsed_start: date | None = None
    parsed_end: date | None = None
    if start is not None:
        try:
            parsed_start = date.fromisoformat(start)
        except ValueError:
            errors.append(
                _error(
                    "invalid_date_range",
                    f"{path}.start_date",
                    "日期必须使用 YYYY-MM-DD",
                    "ISO calendar date",
                    start,
                )
            )
    if end is not None:
        try:
            parsed_end = date.fromisoformat(end)
        except ValueError:
            errors.append(
                _error(
                    "invalid_date_range",
                    f"{path}.end_date",
                    "日期必须使用 YYYY-MM-DD",
                    "ISO calendar date",
                    end,
                )
            )
    if parsed_start is not None and parsed_end is not None and parsed_start > parsed_end:
        errors.append(
            _error(
                "invalid_date_range",
                path,
                "期间起始日期不得晚于结束日期",
                "start_date <= end_date",
                value,
            )
        )
    if semantics is not None and semantics not in allowed_semantics:
        errors.append(
            _error(
                "invalid_period_semantics",
                f"{path}.semantics",
                "期间数学约定不适用于当前公式",
                sorted(allowed_semantics),
                semantics,
            )
        )
    return {
        "start_date": start or "",
        "end_date": end or "",
        "semantics": semantics or "",
    }


def _validate_rounding_policy(value: Any, errors: list[dict[str, Any]]) -> dict[str, str] | None:
    path = "rounding_policy"
    if not _exact_keys(value, ROUNDING_FIELDS, path, errors):
        return None
    assert isinstance(value, Mapping)
    normalized = {
        key: _text(value.get(key), f"{path}.{key}", errors) or ""
        for key in ROUNDING_FIELDS
    }
    expected = {
        "id": "money-independent-unit-half-up-v1",
        "quantum": "0.01",
        "mode": "ROUND_HALF_UP",
        "aggregation": "round_each_line_item_then_sum",
    }
    for key, expected_value in expected.items():
        if normalized[key] != expected_value:
            errors.append(
                _error(
                    "rounding_policy_mismatch",
                    f"{path}.{key}",
                    "舍入策略必须明确使用公开的人民币分 ROUND_HALF_UP 规则",
                    expected_value,
                    normalized[key],
                )
            )
    return normalized


def _validate_source_refs(value: Any, errors: list[dict[str, Any]]) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(
            _error(
                "missing_input",
                "source_refs",
                "来源引用至少需要一项模型选择的事实、证据或法律来源引用",
                "non-empty array of strings",
                value,
            )
        )
        return []
    refs: list[str] = []
    for index, item in enumerate(value):
        item_path = f"source_refs[{index}]"
        parsed = _text(item, item_path, errors)
        if parsed is not None:
            refs.append(parsed)
    return refs


def _validate_assumptions(value: Any, path: str, errors: list[dict[str, Any]]) -> list[str]:
    if not isinstance(value, list):
        errors.append(_error("invalid_object", path, "假设必须是文本数组", "array", value))
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        parsed = _text(item, f"{path}[{index}]", errors)
        if parsed is not None:
            result.append(parsed)
    return result


def _validate_line_items(
    formula: Mapping[str, Any],
    scenario: Mapping[str, Any],
    scenario_path: str,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    inputs = scenario.get("inputs")
    inputs_path = f"{scenario_path}.inputs"
    if not _exact_keys(inputs, ("line_items",), inputs_path, errors):
        return []
    assert isinstance(inputs, Mapping)
    raw_items = inputs.get("line_items")
    if not isinstance(raw_items, list) or not raw_items:
        errors.append(
            _error(
                "missing_input",
                f"{inputs_path}.line_items",
                "每个情景至少需要一个独立分项",
                "non-empty array",
                raw_items,
            )
        )
        return []

    kind = formula.get("kind")
    normalized: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        path = f"{inputs_path}.line_items[{index}]"
        if formula.get("kind") == "independent_amount_total":
            line_fields = ("line_id", "amount", "cap")
        elif formula.get("kind") == "daily_rate_difference":
            line_fields = (
                "line_id",
                "basis",
                "divisor",
                "quantity",
                "multiplier",
                "paid",
                "cap",
            )
        else:
            line_fields = ("line_id", "basis", "quantity", "multiplier", "paid", "cap")
        if not _exact_keys(raw_item, line_fields, path, errors):
            continue
        assert isinstance(raw_item, Mapping)
        line_id = _text(raw_item.get("line_id"), f"{path}.line_id", errors)
        if kind == "independent_amount_total":
            amount, amount_unit = _measure(
                raw_item.get("amount"),
                f"{path}.amount",
                errors,
                allowed_units={"yuan"},
            )
            if amount is None or amount_unit != "yuan":
                errors.append(
                    _error(
                        "missing_input",
                        f"{path}.amount",
                        "独立金额分项必须提供 yuan 输入",
                        "{value: decimal string, unit: yuan}",
                        raw_item.get("amount"),
                    )
                )
                continue
            cap, cap_unit = (None, None)
            if raw_item.get("cap") is not None:
                cap, cap_unit = _measure(
                    raw_item.get("cap"),
                    f"{path}.cap",
                    errors,
                    allowed_units={"yuan"},
                )
                if cap_unit not in {None, "yuan"}:
                    continue
            normalized.append(
                {
                    "line_id": line_id or "",
                    "amount": amount,
                    "cap": cap,
                }
            )
            continue

        basis_units = set()
        for pair in formula.get("basis_quantity_pairs", []):
            if isinstance(pair, list) and len(pair) == 2:
                basis_units.add(pair[0])
        basis, basis_unit = _measure(
            raw_item.get("basis"), f"{path}.basis", errors, allowed_units=basis_units
        )
        divisor: Decimal | None = None
        divisor_unit: str | None = None
        if kind == "daily_rate_difference":
            expected_divisor_unit = formula.get("divisor_unit", "count")
            divisor, divisor_unit = _measure(
                raw_item.get("divisor"),
                f"{path}.divisor",
                errors,
                allowed_units={expected_divisor_unit},
            )
            if divisor is not None and divisor == 0:
                errors.append(
                    _error(
                        "division_by_zero",
                        f"{path}.divisor.value",
                        "日工资折算除数必须大于零",
                        "value > 0",
                        raw_item.get("divisor"),
                    )
                )
        quantity_units = {pair[1] for pair in formula.get("basis_quantity_pairs", [])}
        quantity, quantity_unit = _measure(
            raw_item.get("quantity"),
            f"{path}.quantity",
            errors,
            allowed_units=quantity_units,
        )
        multiplier, multiplier_unit = _measure(
            raw_item.get("multiplier"),
            f"{path}.multiplier",
            errors,
            allowed_units={"ratio"},
        )
        paid, paid_unit = _measure(
            raw_item.get("paid"), f"{path}.paid", errors, allowed_units={"yuan"}
        )
        cap, cap_unit = (None, None)
        if raw_item.get("cap") is not None:
            cap, cap_unit = _measure(
                raw_item.get("cap"), f"{path}.cap", errors, allowed_units={"yuan"}
            )
        pair_is_valid = True
        if basis_unit is not None and quantity_unit is not None:
            pairs = {
                tuple(pair)
                for pair in formula.get("basis_quantity_pairs", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            if (basis_unit, quantity_unit) not in pairs:
                errors.append(
                    _error(
                        "unit_mismatch",
                        f"{path}.quantity.unit",
                        "基数与数量的单位维度不匹配",
                        sorted(f"{left} × {right}" for left, right in pairs),
                        f"{basis_unit} × {quantity_unit}",
                    )
                )
                pair_is_valid = False
        if (
            basis is None
            or (kind == "daily_rate_difference" and (divisor is None or divisor <= 0))
            or quantity is None
            or multiplier is None
            or paid is None
            or basis_unit is None
            or quantity_unit is None
            or multiplier_unit != "ratio"
            or paid_unit != "yuan"
            or not pair_is_valid
            or (raw_item.get("cap") is not None and cap is None)
        ):
            continue
        normalized.append(
            {
                "line_id": line_id or "",
                "basis": basis,
                "basis_unit": basis_unit or "",
                **(
                    {
                        "divisor": divisor,
                        "divisor_unit": divisor_unit or "",
                    }
                    if kind == "daily_rate_difference"
                    else {}
                ),
                "quantity": quantity,
                "quantity_unit": quantity_unit or "",
                "multiplier": multiplier,
                "paid": paid,
                "cap": cap,
            }
        )
    return normalized


def _validate_scenarios(
    formula: Mapping[str, Any],
    value: Any,
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(value, list) or not value:
        errors.append(
            _error(
                "missing_input",
                "scenarios",
                "至少需要一个条件情景",
                "non-empty array",
                value,
            )
        )
        return [], []
    scenario_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    parsed_lines: list[dict[str, Any]] = []
    semantics = set(formula.get("period_semantics", []))
    for index, raw_scenario in enumerate(value):
        path = f"scenarios[{index}]"
        if not _exact_keys(raw_scenario, SCENARIO_FIELDS, path, errors):
            continue
        assert isinstance(raw_scenario, Mapping)
        scenario_id = _text(raw_scenario.get("scenario_id"), f"{path}.scenario_id", errors)
        if scenario_id is not None and scenario_id in scenario_ids:
            errors.append(
                _error(
                    "duplicate_scenario",
                    f"{path}.scenario_id",
                    "情景编号必须唯一",
                    "unique scenario_id",
                    scenario_id,
                )
            )
        if scenario_id is not None:
            scenario_ids.add(scenario_id)
        assumptions = _validate_assumptions(raw_scenario.get("assumptions"), f"{path}.assumptions", errors)
        period = _period(raw_scenario.get("period"), f"{path}.period", errors, semantics)
        lines = _validate_line_items(formula, raw_scenario, path, errors)
        normalized.append(
            {
                "scenario_id": scenario_id or "",
                "assumptions": assumptions,
                "period": period or {},
                "inputs": {"line_items": _normalized_line_inputs(lines, formula)},
            }
        )
        parsed_lines.append(
            {"scenario_id": scenario_id or "", "period": period or {}, "lines": lines}
        )
    return normalized, parsed_lines


def _normalized_line_inputs(lines: list[dict[str, Any]], formula: Mapping[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for line in lines:
        item: dict[str, Any] = {"line_id": line.get("line_id", "")}
        if formula.get("kind") == "independent_amount_total":
            item["amount"] = {
                "value": _exact_decimal(line["amount"]),
                "unit": "yuan",
            }
        else:
            item.update(
                {
                    "basis": {
                        "value": _exact_decimal(line["basis"]),
                        "unit": line["basis_unit"],
                    },
                    "quantity": {
                        "value": _exact_decimal(line["quantity"]),
                        "unit": line["quantity_unit"],
                    },
                    "multiplier": {
                        "value": _exact_decimal(line["multiplier"]),
                        "unit": "ratio",
                    },
                    "paid": {
                        "value": _exact_decimal(line["paid"]),
                        "unit": "yuan",
                    },
                }
            )
            if formula.get("kind") == "daily_rate_difference":
                item["divisor"] = {
                    "value": _exact_decimal(line["divisor"]),
                    "unit": line["divisor_unit"],
                }
        if line.get("cap") is not None:
            item["cap"] = {"value": _exact_decimal(line["cap"]), "unit": "yuan"}
        normalized.append(item)
    return normalized


def _formula_display(formula: Mapping[str, Any], line: Mapping[str, Any]) -> str:
    if formula.get("kind") == "independent_amount_total":
        return "amount"
    if formula.get("kind") == "daily_rate_difference":
        return (
            f"max(0, ({_exact_decimal(line['basis'])}{line['basis_unit']} ÷ "
            f"{_exact_decimal(line['divisor'])}{line['divisor_unit']}) × "
            f"{_exact_decimal(line['quantity'])}{line['quantity_unit']} × "
            f"{_exact_decimal(line['multiplier'])}ratio) - "
            f"{_exact_decimal(line['paid'])}yuan)"
        )
    return (
        f"max(0, ({_exact_decimal(line['basis'])}{line['basis_unit']} × "
        f"{_exact_decimal(line['quantity'])}{line['quantity_unit']} × "
        f"{_exact_decimal(line['multiplier'])}ratio) - "
        f"{_exact_decimal(line['paid'])}yuan)"
    )


def _calculate_lines(
    formula: Mapping[str, Any], parsed_scenarios: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    line_items: list[dict[str, Any]] = []
    intermediate_trace: list[dict[str, Any]] = []
    scenario_totals: list[dict[str, str]] = []
    for scenario in parsed_scenarios:
        displayed_results: list[Decimal] = []
        for line in scenario["lines"]:
            if formula.get("kind") == "independent_amount_total":
                raw = line["amount"]
                cap = line.get("cap")
            elif formula.get("kind") == "daily_rate_difference":
                raw = max(
                    Decimal("0"),
                    line["basis"] / line["divisor"] * line["quantity"] * line["multiplier"]
                    - line["paid"],
                )
                cap = line.get("cap")
            else:
                raw = max(
                    Decimal("0"),
                    line["basis"] * line["quantity"] * line["multiplier"] - line["paid"],
                )
                cap = line.get("cap")
            applied = min(raw, cap) if cap is not None else raw
            displayed = applied.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            displayed_results.append(displayed)
            line_items.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "line_id": line["line_id"],
                    "formula": _formula_display(formula, line),
                    "raw_result": _exact_decimal(raw),
                    "result": _money(displayed),
                    "cap": None if cap is None else _money(cap),
                    "cap_applied": cap is not None and applied < raw,
                }
            )
            intermediate_trace.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "line_id": line["line_id"],
                    "raw_result": _exact_decimal(raw),
                    "capped_result": _exact_decimal(applied),
                    "rounding_point": "independent_line_item_final_display",
                    "rounded_result": _money(displayed),
                }
            )
        total = sum(displayed_results, Decimal("0"))
        scenario_totals.append(
            {"scenario_id": scenario["scenario_id"], "total": _money(total)}
        )
    return line_items, intermediate_trace, scenario_totals


class AmountCalculator:
    """Expose the complete amount.calculate contract without legal decisions."""

    def __init__(self, registry_path: str | Path = FORMULA_REGISTRY_PATH) -> None:
        self._registry_path = Path(registry_path)

    def describe(self) -> dict[str, Any]:
        registry = _load_registry(self._registry_path)
        return {
            "contract_version": CONTRACT_VERSION,
            "operation": OPERATION,
            "input": {
                "fields": [
                    {"name": "calculation_label", "required": True, "type": "string"},
                    {"name": "formula_id", "required": True, "type": "enum"},
                    {"name": "formula_version", "required": True, "type": "string"},
                    {"name": "legal_basis_summary", "required": True, "type": "string"},
                    {"name": "source_refs", "required": True, "type": "array[string]"},
                    {"name": "aggregation_policy", "required": True, "type": "enum"},
                    {"name": "scenarios", "required": True, "type": "non-empty array"},
                    {"name": "rounding_policy", "required": True, "type": "object"},
                ],
                "aggregation_policy": sorted(AGGREGATION_POLICIES),
                "units": sorted(registry.get("units", {})),
                "measure": "{value: decimal string, unit: explicit unit}",
                "line_item_shapes": {
                    "linear_difference": [
                        "line_id",
                        "basis",
                        "quantity",
                        "multiplier",
                        "paid",
                        "cap (optional)",
                    ],
                    "daily_rate_difference": [
                        "line_id",
                        "basis",
                        "divisor",
                        "quantity",
                        "multiplier",
                        "paid",
                        "cap (optional)",
                    ],
                    "independent_amount_total": [
                        "line_id",
                        "amount",
                        "cap (optional)",
                    ],
                },
            },
            "formulas": deepcopy(registry.get("formulas", {})),
            "math_conventions": list(registry.get("math_conventions", [])),
            "output": {
                "fields": [
                    "calculation_id",
                    "formula_id",
                    "formula_version",
                    "normalized_inputs",
                    "formula_display",
                    "line_items",
                    "intermediate_trace",
                    "legal_basis_summary",
                    "source_refs",
                    "scenario_totals",
                    "grand_total",
                    "grand_total_status",
                    "rounding_policy",
                ],
                "archive_write": "模型通过 CaseArchive.commit 写回；amount.calculate 不写案件文件",
            },
            "errors": {
                "codes": [
                    "unknown_formula",
                    "formula_version_mismatch",
                    "missing_input",
                    "unknown_field",
                    "invalid_text",
                    "invalid_object",
                    "invalid_decimal",
                    "negative_value",
                    "unit_mismatch",
                    "invalid_date_range",
                    "invalid_period_semantics",
                    "division_by_zero",
                    "rounding_policy_mismatch",
                    "constraint_conflict",
                    "duplicate_scenario",
                "non_reproducible_result",
                "installed_skill_modified",
            ],
                "recoverable": "每项错误都带 path、expected、received；修正一次请求后重试",
            },
            "examples": {
                "success": "见 references/amount-calculate.md 的成功示例",
                "aggregate_failure": "见 references/amount-calculate.md 的聚合失败示例",
            },
        }

    def calculate(self, request: Any) -> dict[str, Any]:
        integrity_failure = _installation_integrity_failure()
        if integrity_failure is not None:
            return integrity_failure
        if not isinstance(request, Mapping):
            return _failure(
                [
                    _error(
                        "missing_input",
                        "request",
                        "amount.calculate 请求必须是对象",
                        "object",
                        type(request).__name__,
                    )
                ]
            )
        errors: list[dict[str, Any]] = []
        allowed = set(REQUIRED_FIELDS) | {"aggregation_policy"}
        for key in sorted(set(request) - allowed):
            errors.append(
                _error(
                    "unknown_field",
                    key,
                    "请求字段不在公开 amount.calculate 契约中",
                    sorted(allowed),
                    request[key],
                )
            )
        for field in REQUIRED_FIELDS:
            if field not in request:
                errors.append(
                    _error(
                        "missing_input",
                        field,
                        "请求缺少必填字段",
                        "required",
                        None,
                    )
                )
        label = _text(request.get("calculation_label"), "calculation_label", errors)
        formula_id = _text(request.get("formula_id"), "formula_id", errors)
        formula_version = _text(request.get("formula_version"), "formula_version", errors)
        legal_basis_summary = _text(
            request.get("legal_basis_summary"), "legal_basis_summary", errors
        )
        source_refs = _validate_source_refs(request.get("source_refs"), errors)
        aggregation_policy = request.get("aggregation_policy")
        if (
            not isinstance(aggregation_policy, str)
            or aggregation_policy not in AGGREGATION_POLICIES
        ):
            errors.append(
                _error(
                    "constraint_conflict",
                    "aggregation_policy",
                    "情景聚合方式不在公开枚举中",
                    sorted(AGGREGATION_POLICIES),
                    aggregation_policy,
                )
            )
        rounding_policy = _validate_rounding_policy(request.get("rounding_policy"), errors)

        try:
            registry = _load_registry(self._registry_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return _failure(
                [
                    _error(
                        "non_reproducible_result",
                        "formula_registry",
                        "公式登记表无法读取，不能生成可复算结果",
                        "readable formula registry",
                        error.__class__.__name__,
                        recoverable=False,
                    )
                ]
            )
        formulas = registry.get("formulas", {})
        formula = formulas.get(formula_id) if isinstance(formulas, Mapping) else None
        if formula is None:
            errors.append(
                _error(
                    "unknown_formula",
                    "formula_id",
                    "公式标识不在公开公式目录中",
                    sorted(formulas) if isinstance(formulas, Mapping) else [],
                    formula_id,
                )
            )
        elif formula_version != formula.get("version"):
            errors.append(
                _error(
                    "formula_version_mismatch",
                    "formula_version",
                    "公式版本与公开公式目录不一致",
                    formula.get("version"),
                    formula_version,
                )
            )

        normalized_scenarios: list[dict[str, Any]] = []
        parsed_scenarios: list[dict[str, Any]] = []
        if isinstance(formula, Mapping) and formula_version == formula.get("version"):
            normalized_scenarios, parsed_scenarios = _validate_scenarios(
                formula, request.get("scenarios"), errors
            )
        elif not isinstance(request.get("scenarios"), list):
            _validate_scenarios(
                {"kind": "independent_amount_total", "period_semantics": []},
                request.get("scenarios"),
                errors,
            )

        if aggregation_policy == "single_scenario" and len(parsed_scenarios) != 1:
            errors.append(
                _error(
                    "constraint_conflict",
                    "aggregation_policy",
                    "single_scenario 只能接收一个情景",
                    "exactly one scenario",
                    len(parsed_scenarios),
                )
            )
        if errors:
            return _failure(errors)

        assert isinstance(formula, Mapping)
        line_items, intermediate_trace, scenario_totals = _calculate_lines(
            formula, parsed_scenarios
        )
        if aggregation_policy == "mutually_exclusive":
            grand_total = None
            grand_total_status = "not_aggregated_mutually_exclusive"
        else:
            grand_total = _money(
                sum((Decimal(item["total"]) for item in scenario_totals), Decimal("0"))
            )
            grand_total_status = "calculated"
        normalized_inputs = {
            "calculation_label": label,
            "formula_id": formula_id,
            "formula_version": formula_version,
            "legal_basis_summary": legal_basis_summary,
            "source_refs": list(source_refs),
            "aggregation_policy": aggregation_policy,
            "scenarios": normalized_scenarios,
            "units": sorted(
                {
                    unit
                    for scenario in normalized_scenarios
                    for line in scenario["inputs"]["line_items"]
                    for measure in line.values()
                    if isinstance(measure, Mapping)
                    for unit in [measure.get("unit")]
                    if isinstance(unit, str)
                }
            ),
        }
        calculation_id = "calc-" + hashlib.sha256(
            json.dumps(normalized_inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        return _success(
            {
                "calculation_id": calculation_id,
                "formula_id": formula_id,
                "formula_version": formula_version,
                "legal_basis_summary": legal_basis_summary,
                "source_refs": list(source_refs),
                "normalized_inputs": normalized_inputs,
                "formula_display": formula.get("line_formula"),
                "line_items": line_items,
                "intermediate_trace": intermediate_trace,
                "scenario_totals": scenario_totals,
                "grand_total": grand_total,
                "grand_total_status": grand_total_status,
                "rounding_policy": rounding_policy,
            }
        )


def calculate(request: Any) -> dict[str, Any]:
    """Convenience entry point for the public amount.calculate operation."""

    return AmountCalculator().calculate(request)


def describe_contract() -> dict[str, Any]:
    return AmountCalculator().describe()


__all__ = ["AmountCalculator", "calculate", "describe_contract", "CONTRACT_VERSION"]
