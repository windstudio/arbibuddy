"""Read-only, opt-in observer for the post-delivery phone scenario.

Snapshots belong to Harness evidence in a fictional workspace, never to the
installed Skill. OOXML blocks are read independently of the SUT's edit-context.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .artifact_oracle import inspect_public_artifacts

_TYPES = {"arbitration_application", "evidence_catalog"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _blocks(parent):
    result = []
    for child in parent:
        if child.tag == _W + "p":
            props = child.find(_W + "pPr")
            style = props.find(_W + "pStyle") if props is not None else None
            numbering = props.find(_W + "numPr") if props is not None else None
            text = "".join(node.text or "" if node.tag == _W + "t" else "\t" if node.tag == _W + "tab" else "\n"
                           for node in child.iter() if node.tag in {_W + "t", _W + "tab", _W + "br", _W + "cr"})
            result.append({"kind": "paragraph", "text": text,
                           "style_id": style.get(_W + "val") if style is not None else None,
                           "numbering": {node.tag.removeprefix(_W): dict(sorted(node.attrib.items()))
                                         for node in numbering} if numbering is not None else None})
        elif child.tag == _W + "tbl":
            result.append({"kind": "table", "rows": [[_blocks(cell) for cell in row.findall(_W + "tc")]
                                                        for row in child.findall(_W + "tr")]})
    return result


def capture_delivery_snapshot(workspace: Path) -> dict:
    """Capture only a complete, current two-member delivery, or no trusted text."""
    failure = {"verified": False, "ordered_blocks": {}, "files": []}
    try:
        from scripts.documents.view import managed_delivery_view
        root = Path(workspace).resolve()
        observed = inspect_public_artifacts(root)
        if any(value for key, value in observed.items() if key.endswith("_count")):
            raise ValueError("independent_artifact_validation_failed")
        archives = observed["case_archives"]
        deliveries = observed["deliveries"]
        if len(archives) != 1 or len(deliveries) != 2 or {d["document_type"] for d in deliveries} != _TYPES:
            raise ValueError("complete_single_case_bundle_required")
        case_id, revision = archives[0]["case_id"], archives[0]["archive_revision"]
        archive_path = root / archives[0]["archive_path"]
        archive_bytes = archive_path.read_bytes()
        archive_digest = sha256(archive_bytes).hexdigest()
        if archive_digest != archives[0]["archive_sha256"]:
            raise ValueError("archive_changed_during_capture")
        fact_section = re.search(r"(?ms)^## 事实\s*\n(.*?)(?=^## |\Z)", archive_bytes.decode("utf-8"))
        if fact_section is None:
            raise ValueError("archive_fact_section_missing")
        view = managed_delivery_view(workspace=root, case_id=case_id)
        if view.get("stopped") or len(view.get("presentation_files", [])) != 4:
            raise ValueError("managed_view_rejected")
        set_id = (view.get("delivery_set") or {}).get("delivery_set_id")
        if not isinstance(set_id, str) or not set_id:
            raise ValueError("delivery_set_required")
        presented = {Path(item["path"]).resolve(): item for item in view["presentation_files"]}
        files, blocks = [], {}
        for delivery in deliveries:
            if delivery["case_id"] != case_id or delivery["archive_revision"] != revision:
                raise ValueError("current_revision_required")
            manifest = json.loads((root / delivery["manifest_path"]).read_text(encoding="utf-8"))
            if manifest.get("delivery_set_id") != set_id:
                raise ValueError("manifest_set_mismatch")
            for kind, key in (("document", "document_path"), ("checklist", "checklist_path")):
                path = (root / delivery[key]).resolve()
                data = path.read_bytes()
                digest = sha256(data).hexdigest()
                if not path.is_relative_to(root) or path not in presented or presented[path]["sha256"] != digest:
                    raise ValueError("snapshot_changed_during_capture")
                files.append({"document_type": delivery["document_type"], "kind": kind,
                              "path": path.relative_to(root).as_posix(), "size": len(data), "sha256": digest,
                              "archive_revision": revision, "delivery_set_id": set_id})
                if kind == "document":
                    from io import BytesIO
                    with ZipFile(BytesIO(data)) as package:
                        body = ET.fromstring(package.read("word/document.xml")).find(_W + "body")
                        if body is None:
                            raise ValueError("document_body_missing")
                        blocks[delivery["document_type"]] = _blocks(body)
        final_view = managed_delivery_view(workspace=root, case_id=case_id)
        if final_view.get("stopped") or final_view.get("current_archive_revision") != revision or final_view.get("presentation_files") != view["presentation_files"] or final_view.get("delivery_set") != view.get("delivery_set") or sha256(archive_path.read_bytes()).hexdigest() != archive_digest:
            raise ValueError("snapshot_changed_during_capture")
        return {"verified": True, "case_id": case_id, "archive_revision": revision,
                "delivery_set_id": set_id, "delivery_state": view["delivery_state"],
                "archive_fact_text": fact_section.group(1),
                "ordered_blocks": blocks, "files": files}
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError, BadZipFile) as error:
        return {**failure, "failure_reason": str(error)[:160]}


def _text_leaves(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "text" and isinstance(child, str):
                yield child
            elif isinstance(child, (dict, list)):
                yield from _text_leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_leaves(child)


def _replace_text(value, old, new):
    if isinstance(value, dict):
        return {key: child.replace(old, new) if key == "text" and isinstance(child, str)
                else _replace_text(child, old, new) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_text(child, old, new) for child in value]
    return value


def check_phone_correction(before: dict, after: dict, *, old_phone: str, new_phone: str) -> list[str]:
    """Require a new valid bundle with exactly the authorized phone token change."""
    failures = []
    if not all(isinstance(p, str) and re.fullmatch(r"1[0-9]{10}", p) for p in (old_phone, new_phone)) or old_phone == new_phone:
        return ["invalid_phone_scenario"]
    for label, snapshot in (("before", before), ("after", after)):
        if snapshot.get("verified") is not True:
            failures.append(f"{label}_not_verified")
            continue
        revision = snapshot.get("archive_revision")
        files = snapshot.get("files", [])
        if set(snapshot.get("ordered_blocks", {})) != _TYPES or len(files) != 4 or {(f.get("document_type"), f.get("kind")) for f in files} != {(d, k) for d in _TYPES for k in ("document", "checklist")}:
            failures.append(f"{label}_incomplete_bundle")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            failures.append(f"{label}_invalid_revision")
        if any(f.get("archive_revision") != revision or f.get("delivery_set_id") != snapshot.get("delivery_set_id") or not re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256", ""))) or not isinstance(f.get("size"), int) or f["size"] <= 0 for f in files):
            failures.append(f"{label}_invalid_file_identity")
    if failures:
        return failures
    if before.get("case_id") != after.get("case_id"):
        failures.append("case_identity_changed")
    if old_phone not in before.get("archive_fact_text", "") or new_phone not in after.get("archive_fact_text", ""):
        failures.append("archive_phone_source_missing")
    if after["archive_revision"] <= before["archive_revision"]:
        failures.append("archive_revision_not_advanced")
    if not before.get("delivery_set_id") or before.get("delivery_set_id") == after.get("delivery_set_id"):
        failures.append("delivery_set_not_rebuilt")
    old_text = "\n".join(_text_leaves(before["ordered_blocks"]))
    new_text = "\n".join(_text_leaves(after["ordered_blocks"]))
    if old_phone not in old_text or new_phone in old_text or old_phone in new_text or new_text.count(new_phone) != old_text.count(old_phone):
        failures.append("phone_replacement_not_observed")
    if _replace_text(deepcopy(before["ordered_blocks"]), old_phone, new_phone) != after["ordered_blocks"]:
        failures.append("changes_beyond_phone")
    return failures
