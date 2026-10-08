"""Fixture-only helpers for explicit public document binding locations."""

import re
from typing import Any

from scripts.documents.public import _CURRENCY_AMOUNT_PATTERN, _DATE_VALUE_PATTERN


def _positions(text: str, value: str) -> list[int]:
    positions: list[int] = []
    offset = 0
    while True:
        offset = text.find(value, offset)
        if offset < 0:
            return positions
        positions.append(offset)
        offset += 1


def _unique_excerpt(section_text: str, value: str, offset: int, pattern: Any) -> str:
    line_start = section_text.rfind("\n", 0, offset) + 1
    line_end = section_text.find("\n", offset)
    if line_end < 0:
        line_end = len(section_text)
    value_end = offset + len(value)
    max_extra = (offset - line_start) + (line_end - value_end)
    for total_extra in range(max_extra + 1):
        min_left = max(0, total_extra - (line_end - value_end))
        max_left = min(total_extra, offset - line_start)
        for left_extra in range(min_left, max_left + 1):
            right_extra = total_extra - left_extra
            start = offset - left_extra
            end = value_end + right_extra
            excerpt = section_text[start:end]
            matches = list(pattern.finditer(excerpt))
            if (
                len(matches) == 1
                and matches[0].span() == (left_extra, left_extra + len(value))
                and section_text.count(excerpt) == 1
            ):
                return excerpt
    raise AssertionError(f"no unique single-line excerpt for {value!r}")


def with_occurrence_bindings(request: dict[str, Any]) -> dict[str, Any]:
    """Bind each amount/date fixture to every exact body occurrence.

    This only makes test requests valid for the public contract. Tests that
    intentionally introduce drift mutate the returned request afterwards and
    keep its original bindings intact.
    """

    for binding in request.get("locked_bindings", []):
        kind = binding.get("kind")
        if kind not in {"amount", "date", "signature"}:
            continue
        value = binding["rendered_value"]
        if kind == "amount":
            pattern = _CURRENCY_AMOUNT_PATTERN
            search_values = [value]
        elif kind == "date":
            pattern = _DATE_VALUE_PATTERN
            search_values = [value]
            date_match = _DATE_VALUE_PATTERN.fullmatch(value)
            if date_match is not None:
                groups = (
                    date_match.groups()[:3]
                    if date_match.group(1)
                    else date_match.groups()[3:]
                )
                year, month, day = (int(part) for part in groups)
                search_values.append(f"{year:04d}年{month}月{day}日")
                search_values.append(f"{year:04d}-{month:02d}-{day:02d}")
        else:
            pattern = re.compile(re.escape(value))
            search_values = [value]
        occurrences = []
        if kind == "signature":
            closing_sections = [
                section
                for section in request["sections"]
                if section["heading"] == "落款"
            ]
            if (
                request["document_type"] == "employment_obligation_demand_letter"
                and request["sections"]
            ):
                closing_sections = [request["sections"][-1]]
            target_sections = closing_sections
        else:
            target_sections = request["sections"]
        for section in target_sections:
            for search_value in dict.fromkeys(search_values):
                for offset in _positions(section["full_text"], search_value):
                    occurrences.append(
                        {
                            "section_id": section["section_id"],
                            "text": _unique_excerpt(
                                section["full_text"], search_value, offset, pattern
                            ),
                        }
                    )
        if not occurrences:
            raise AssertionError(
                f"fixture {binding['kind']} value is absent from body: {value}"
            )
        binding["occurrences"] = occurrences
    return request
