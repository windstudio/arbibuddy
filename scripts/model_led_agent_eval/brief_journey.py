"""Opt-in brief Journey checks over independently captured public artifacts.

Content groups are scenario smoke assertions, not a semantic or pagination judge.
No SUT source validators or generated prose are used to decide these assertions.
"""
from collections.abc import Mapping
import re

from .postdelivery import _text_leaves


def validate_brief_spec(value, *, facts, initial, initial_fact_ids, max_turns,
                        deliveries, phone_correction):
    if value is None:
        return None
    from . import JourneyError
    allowed = {"first_delivery_turn", "content_groups", "opening_groups", "forbidden_content",
               "forbidden_archive_evidence", "amount_attributions", "complex_timeline", "edits"}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise JourneyError("lawyer_case_brief 存在未知字段或不是对象")
    turn = value.get("first_delivery_turn")
    if type(turn) is not int or not 1 <= turn <= max_turns:
        raise JourneyError("lawyer_case_brief.first_delivery_turn 必须是有效轮次")
    if phone_correction is not None or len(deliveries) != 1 or deliveries[0].document_type != "lawyer_case_brief" or deliveries[0].mode != "external_final":
        raise JourneyError("简报场景须独立交付一份 external_final 简报，不混用电话更正场景")
    if type(value.get("complex_timeline", False)) is not bool:
        raise JourneyError("complex_timeline 必须是布尔值")
    for key in ("content_groups", "opening_groups"):
        groups = value.get(key, [])
        if not isinstance(groups, list) or not groups or any(
            not isinstance(group, list) or not group or any(not isinstance(s, str) or not s.strip() for s in group)
            for group in groups):
            raise JourneyError(f"lawyer_case_brief.{key} 必须是非空备选词组列表")
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
    if snapshot.get("verified") is not True:
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


def _content_errors(snapshot, spec):
    text = _text(snapshot)
    opening = snapshot.get("opening_text", "")
    failures = []
    timeline_count = text.splitlines().count("共用时间线")
    if spec.get("complex_timeline") is True and (
        timeline_count != 1
        or "共用时间线" in opening or snapshot.get("page_break_count") != 1):
        failures.append("complex_timeline_structure")
    if spec.get("complex_timeline") is False and (
        timeline_count or snapshot.get("page_break_count") != 0):
        failures.append("unexpected_brief_continuation")
    for key, target in (("content_groups", text), ("opening_groups", opening)):
        for index, group in enumerate(spec[key]):
            if not any(marker in target for marker in group):
                failures.append(f"{key}:{index}")
    for marker in spec.get("forbidden_content", []):
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
    if re.search(r"(?<![A-Za-z0-9_])(?:F|E|CL|CAL|CONF|AUTH|N|SC|OR|DR|A|R)-?\d{2,}(?![A-Za-z0-9_])|\.arbibuddy|[A-Za-z]:[\\/]", text):
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
