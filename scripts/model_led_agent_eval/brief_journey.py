"""Opt-in brief Journey checks over independently captured public artifacts.

Content groups are scenario smoke assertions, not a semantic or pagination judge.
No SUT source validators or generated prose are used to decide these assertions.
"""
from collections.abc import Mapping
import re

from .postdelivery import _text_leaves

_NUMERALS = "一二三四五六七八"
_CHAPTER_PREFIX = ("案件概要", "基本案情", "主要争议事项", "争议处理进展")
_FULL_CHAPTERS = (*_CHAPTER_PREFIX, "当事人诉求", "证据及材料情况", "尚待明确的事项", "拟与律师讨论的问题")
_COMPACT_CHAPTERS = (*_CHAPTER_PREFIX, "证据及待明确事项", "证据及材料情况", "尚待明确的事项",
                     "当事人诉求及咨询问题", "当事人诉求", "拟与律师讨论的问题")


def _valid_groups(groups):
    return isinstance(groups, list) and bool(groups) and all(
        isinstance(group, list) and group and all(isinstance(s, str) and s.strip() for s in group)
        for group in groups
    )


def validate_brief_spec(value, *, facts, initial, initial_fact_ids, max_turns,
                        deliveries, phone_correction):
    if value is None:
        return None
    from . import JourneyError
    allowed = {"first_delivery_turn", "content_groups", "opening_groups", "forbidden_content",
               "forbidden_archive_evidence", "amount_attributions", "structure", "section_expectations", "edits"}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise JourneyError("lawyer_case_brief 存在未知字段或不是对象")
    turn = value.get("first_delivery_turn")
    if type(turn) is not int or not 1 <= turn <= max_turns:
        raise JourneyError("lawyer_case_brief.first_delivery_turn 必须是有效轮次")
    if phone_correction is not None or len(deliveries) != 1 or deliveries[0].document_type != "lawyer_case_brief" or deliveries[0].mode != "external_final":
        raise JourneyError("简报场景须独立交付一份 external_final 简报，不混用电话更正场景")
    if value.get("structure") not in (None, "compact", "full"):
        raise JourneyError("structure 必须为 compact 或 full")
    for key in ("content_groups", "opening_groups"):
        if not _valid_groups(value.get(key)):
            raise JourneyError(f"lawyer_case_brief.{key} 必须是非空备选词组列表")
    assertions = value.get("section_expectations", {})
    if not isinstance(assertions, Mapping) or any(
        not isinstance(heading, str) or not heading.strip() or not isinstance(assertion, Mapping)
        or not assertion or set(assertion) - {"required", "forbidden", "ordered"}
        or ("required" in assertion and not _valid_groups(assertion["required"]))
        or ("ordered" in assertion and (not _valid_groups(assertion["ordered"]) or len(assertion["ordered"]) < 2))
        or ("forbidden" in assertion and (not isinstance(assertion["forbidden"], list)
            or any(not isinstance(s, str) or not s.strip() for s in assertion["forbidden"])))
        for heading, assertion in assertions.items()
    ):
        raise JourneyError("section_expectations 须为标题到 required、ordered 词组或 forbidden 词语的映射")
    for key in ("forbidden_content", "forbidden_archive_evidence"):
        markers = value.get(key, [])
        if not isinstance(markers, list) or any(not isinstance(s, str) or not s.strip() for s in markers):
            raise JourneyError(f"lawyer_case_brief.{key} 必须是字符串列表")
    attributions = value.get("amount_attributions", [])
    if not isinstance(attributions, list) or any(
        not isinstance(item, Mapping) or set(item) != {"amount", "context"}
        or not isinstance(item["amount"], str) or re.fullmatch(r"[0-9]+", item["amount"]) is None
        or not isinstance(item["context"], list) or not item["context"]
        or any(not isinstance(s, str) or not s.strip() for s in item["context"])
        for item in attributions):
        raise JourneyError("简报金额断言必须包含整数元 amount 与非空 context 备选词组")
    edits = value.get("edits", [])
    if not isinstance(edits, list) or len(edits) > max_turns - turn:
        raise JourneyError("lawyer_case_brief.edits 超出可用轮次")
    known = {fact.id: fact for fact in facts}
    seen = set()
    for edit in edits:
        if (not isinstance(edit, Mapping) or set(edit) != {"fact_id", "kind", "old", "new"}
            or any(not isinstance(edit[k], str) or not edit[k].strip() for k in edit)
            or edit["kind"] not in {"anonymize", "correct_fact"}
            or edit["fact_id"] not in known or edit["fact_id"] in initial_fact_ids
            or edit["fact_id"] in seen or edit["old"] == edit["new"]):
            raise JourneyError("简报更正须绑定唯一的非初始事实卡、修改种类及不同的新旧值")
        if edit["old"] not in initial or edit["new"] not in known[edit["fact_id"]].text:
            raise JourneyError("简报更正值必须来自原始场景和对应事实卡")
        seen.add(edit["fact_id"])
    return dict(value)


def next_brief_edit(spec, transcripts, used_facts):
    """Expose only the next edit after the preceding delivery has been verified."""
    edits = spec.get("edits", [])
    next_edit = next((e for e in edits if not used_facts.get(e["fact_id"], 0)), None)
    if not next_edit or not transcripts:
        return None
    snapshot = transcripts[-1].get("public_artifact_snapshot", {})
    if _snapshot_errors(snapshot):
        return None
    index = edits.index(next_edit)
    if index and edits[index - 1]["fact_id"] not in (transcripts[-1].get("selected_fact_ids") or ()):
        return None
    return next_edit["fact_id"]


def _snapshot_errors(snapshot):
    if not isinstance(snapshot, Mapping) or snapshot.get("verified") is not True:
        return ["snapshot_unverified"]
    if (snapshot.get("delivery_state") != "final_ready"
        or set(snapshot.get("ordered_blocks", {})) != {"lawyer_case_brief"}
        or len(snapshot.get("files", [])) != 2
        or {f.get("kind") for f in snapshot["files"]} != {"document", "checklist"}
        or not snapshot.get("delivery_set_id")):
        return ["brief_bundle_invalid"]
    if any(f.get("document_type") != "lawyer_case_brief"
           or f.get("archive_revision") != snapshot.get("archive_revision")
           or f.get("delivery_set_id") != snapshot.get("delivery_set_id")
           or re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256", ""))) is None
           for f in snapshot["files"]):
        return ["brief_file_identity_invalid"]
    return []


def _text(snapshot):
    return "\n".join(_text_leaves(snapshot.get("ordered_blocks", {})))


def _normalize_dates(text):
    text = re.sub(r"\s+", "", text)
    return re.sub(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?",
        lambda m: f"{m[1]}年{int(m[2])}月{int(m[3])}日", text)


def _brief_sections(blocks):
    """Read section titles and their content from actual Word paragraph styles."""
    names, titles, contents, opening = [], [], {}, []
    chapter, child = None, None
    subheadings = []
    for block in blocks:
        text = "\n".join(_text_leaves(block)).strip()
        if not text:
            continue
        style = block.get("style_id")
        if style == "Heading1":
            names.append(text)
            chapter = text.partition("、")[2]
            titles.append(chapter)
            contents[chapter] = []
            child = None
        elif style == "Heading2":
            match = re.fullmatch(r"（([一二三四五六七八九]|[0-9]+)）(.+)", text)
            child = match[2] if match else ""
            subheadings.append((chapter, match[1] if match else ""))
            contents[child] = []
        elif text.startswith("编制说明："):
            contents["编制说明"] = [text.removeprefix("编制说明：")]
        else:
            for name in (chapter, child):
                if name is not None:
                    contents[name].append(text)
        if len(names) < 2:
            opening.append(text)
    return names, titles, contents, opening, subheadings


def _content_errors(snapshot, spec):
    text = _text(snapshot)
    names, titles, sections, opening_lines, subheadings = _brief_sections(snapshot["ordered_blocks"]["lawyer_case_brief"])
    opening = "\n".join(opening_lines)
    failures = []
    if (opening_lines[:2] != ["案情简报", "供律师咨询参考"] or titles[:1] != ["案件概要"]
        or len(titles) != len(set(titles))
        or names != [f"{number}、{title}" for number, title in zip(_NUMERALS, titles)]
        or not any(line.startswith("编制说明：") for line in text.splitlines()[-1:])
        or any(not any(part.strip() for part in parts) for parts in sections.values())
        or snapshot.get("page_break_count") != 0):
        failures.append("brief_structure")
    for title in titles:
        numbers = [number for chapter, number in subheadings if chapter == title]
        expected = list("一二三四五六七八九"[:len(numbers)]) + [str(n) for n in range(10, len(numbers) + 1)]
        if numbers != expected:
            failures.append("brief_subheading_numbering")
    if spec.get("structure") == "full" and (
        titles != [title for title in _FULL_CHAPTERS if title in titles]
        or sum(chapter == "主要争议事项" for chapter, _ in subheadings) < 2):
        failures.append("brief_full_structure")
    if spec.get("structure") == "compact" and (
        len(titles) > 6 or titles != [title for title in _COMPACT_CHAPTERS if title in titles]):
        failures.append("brief_compact_structure")
    for heading, assertions in spec.get("section_expectations", {}).items():
        if heading not in sections:
            failures.append("missing_brief_section:" + heading)
            continue
        content = "\n".join(sections[heading])
        for index, group in enumerate(assertions.get("required", [])):
            if not any(marker in content for marker in group):
                failures.append(f"brief_section_content:{heading}:{index}")
        for marker in assertions.get("forbidden", []):
            if marker in content:
                failures.append(f"brief_section_forbidden:{heading}:{marker}")
        ordered = assertions.get("ordered", [])
        if ordered:
            remaining = _normalize_dates(content)
            for group in ordered:
                pattern = "|".join(re.escape(_normalize_dates(marker)) for marker in group)
                match = re.search(pattern, remaining)
                if match is None:
                    failures.append(f"brief_section_order:{heading}")
                    break
                remaining = remaining[match.end():]
    for key, target in (("content_groups", text), ("opening_groups", opening)):
        for index, group in enumerate(spec[key]):
            if not any(marker in target for marker in group):
                failures.append(f"{key}:{index}")
    for marker in ("面谈优先核对", *spec.get("forbidden_content", [])):
        if marker in text:
            failures.append("forbidden_content:" + marker)
    archive_markers = spec.get("forbidden_archive_evidence", [])
    archive_evidence = snapshot.get("archive_evidence_text")
    if archive_markers:
        if not isinstance(archive_evidence, str):
            failures.append("archive_evidence_not_observed")
        else:
            failures.extend("forbidden_archive_evidence:" + marker for marker in archive_markers
                            if marker in archive_evidence)
    clauses = re.split(r"[；;。\n]", text.replace(",", ""))
    for item in spec.get("amount_attributions", []):
        amount = re.compile(r"(?<![0-9.])" + re.escape(item["amount"]) + r"(?:\.0+)?\s*元")
        if not any(amount.search(clause) and any(marker in clause for marker in item["context"]) for clause in clauses):
            failures.append("amount_attribution:" + item["amount"])
    if re.search(r"(?<![A-Za-z0-9_])(?:F|E|CL|CAL|CONF|AUTH|N|SC|OR|DR|A|R)-?\d{2,}(?![A-Za-z0-9_])|\.arbibuddy|(?<![A-Za-z0-9_.])[A-Za-z]:[\\/]", text):
        failures.append("internal_content_leak")
    return failures


def check_brief_journey(spec, transcripts):
    """Require the initial delivery on time and each requested edit on its turn."""
    first = next((t for t in transcripts if t.get("turn") == spec["first_delivery_turn"]), None)
    if first is None:
        return ["first_delivery_missing"]
    snapshot = first.get("public_artifact_snapshot", {})
    failures = _snapshot_errors(snapshot)
    if failures:
        return ["first_delivery:" + error for error in failures]
    failures.extend(_content_errors(snapshot, spec))
    previous_index = transcripts.index(first)
    before = snapshot
    for edit in spec.get("edits", []):
        indexes = [i for i, t in enumerate(transcripts) if edit["fact_id"] in (t.get("selected_fact_ids") or ())]
        if len(indexes) != 1 or indexes[0] != previous_index + 1:
            failures.append("edit_sequence:" + edit["fact_id"])
            break
        index = indexes[0]
        after = transcripts[index].get("public_artifact_snapshot", {})
        errors = _snapshot_errors(after)
        if errors:
            failures.extend("edit:" + error for error in errors)
            break
        if before.get("case_id") != after.get("case_id"):
            failures.append("edit_case_changed")
        old, new = _normalize_dates(edit["old"]), _normalize_dates(edit["new"])
        before_text, after_text = _normalize_dates(_text(before)), _normalize_dates(_text(after))
        if old not in before_text or old in after_text or new not in after_text:
            failures.append("edit_document_value")
        if before_text.replace(old, new) != after_text:
            failures.append("edit_unrelated_content_changed")
        old_facts, new_facts = (_normalize_dates(s.get("archive_fact_text", "")) for s in (before, after))
        if edit["kind"] == "anonymize":
            if old_facts != new_facts or old not in new_facts:
                failures.append("anonymization_changed_facts")
        else:
            if new not in new_facts or old in new_facts:
                failures.append("correction_not_archived")
            if after["archive_revision"] <= before["archive_revision"] or after["delivery_set_id"] == before["delivery_set_id"]:
                failures.append("correction_stale_delivery")
        failures.extend(_content_errors(after, spec))
        before, previous_index = after, index
    return list(dict.fromkeys(failures))
