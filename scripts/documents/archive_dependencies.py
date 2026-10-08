"""Conservative candidate freshness proof; hashes only, no second case store.

Every business section is a dependency, including unbound records and free
text. Only revision bookkeeping and unreferenced next steps are excluded.
The model still owns record classification and legal interpretation.
"""
from hashlib import sha256
import re
from typing import Any, Mapping

from scripts.case_archive.service import REQUIRED_SECTIONS, RECORD_HEADING_PATTERN


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def candidate_dependencies(markdown: str, reference_text: str = "") -> dict[str, Any]:
    sections = list(re.finditer(r"^## [^\r\n]+$", markdown, re.MULTILINE))
    if [match.group() for match in sections] != list(REQUIRED_SECTIONS):
        raise ValueError("archive_revision 依赖核对所需档案章节无效")
    business = [markdown[:sections[0].start()].strip()]
    next_steps = ""
    for index, match in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(markdown)
        chapter = markdown[match.start():end].strip()
        if match.group() == "## 更新记录":
            continue
        if match.group() == "## 下一步":
            next_steps = chapter
            continue
        if match.group() == "## 案件元数据":
            chapter = re.sub(r"^- (?:档案修订版本|最后更新)：[^\n]+\n?", "", chapter,
                             flags=re.MULTILINE).strip()
        business.append(chapter)
    business_text = "\n\n".join(business)
    matches = list(RECORD_HEADING_PATTERN.finditer(next_steps))
    records = {
        match.group("record_id"): next_steps[match.start():
            matches[index + 1].start() if index + 1 < len(matches) else len(next_steps)
        ].strip()
        for index, match in enumerate(matches)
    }
    ref_pattern = r"(?<![A-Za-z0-9_])N-\d{3,}(?![A-Za-z0-9_])"
    pending = set(re.findall(ref_pattern, business_text + "\n" + reference_text))
    dependencies = {}
    while pending:
        record_id = pending.pop()
        if record_id in dependencies:
            continue
        if record_id not in records:
            raise ValueError("archive_revision 依赖的下一步记录缺失")
        dependencies[record_id] = _digest(records[record_id])
        pending.update(re.findall(ref_pattern, records[record_id]))
    return {"schema_version": 1, "business_sha256": _digest(business_text),
            "next_step_sha256": dict(sorted(dependencies.items()))}


def validate_dependency_shape(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "business_sha256", "next_step_sha256"
    } or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("archive_revision 候选依赖证明格式无效")
    if not isinstance(value["business_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["business_sha256"]
    ):
        raise ValueError("archive_revision 业务依赖摘要无效")
    steps = value["next_step_sha256"]
    if not isinstance(steps, Mapping) or any(
        not isinstance(key, str) or not re.fullmatch(r"N-\d{3,}", key)
        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for key, digest in steps.items()
    ):
        raise ValueError("archive_revision 下一步依赖摘要无效")


def validate_archive_freshness(value: Mapping[str, Any], *, revision: int,
                               markdown: str | None) -> bool:
    """Validate original revision or proved candidate reuse; never rewrite it."""
    generated = value.get("archive_revision")
    if type(generated) is not int or generated < 0 or generated > revision:
        raise ValueError("archive_revision 原始生成修订版本无效")
    proof = value.get("archive_dependencies")
    if proof is not None:
        validate_dependency_shape(proof)
    if generated == revision:
        if proof is not None and isinstance(markdown, str):
            current = candidate_dependencies(markdown, " ".join(proof["next_step_sha256"]))
            if current != proof:
                raise ValueError("archive_revision 候选业务依赖摘要不一致")
        return False
    if value.get("mode") != "candidate" or proof is None or not isinstance(markdown, str):
        raise ValueError("archive_revision 档案修订版本不一致")
    current = candidate_dependencies(markdown, " ".join(proof["next_step_sha256"]))
    if current != proof:
        raise ValueError("archive_revision 文书业务依赖已变化，档案修订版本不一致")
    return True
