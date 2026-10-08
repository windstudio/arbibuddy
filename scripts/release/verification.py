"""发布证据的文件与身份校验；不执行测试、编排对话或生成发布制品。"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from scripts.runtime_identity import compare_runtime_identity
from scripts.version import read_version

REQUIRED_CHECKS = frozenset({"tools", "documents", "platforms", "migration", "review"})


def release_root(source: Path) -> Path:
    return source.expanduser().resolve() / "dist" / read_version(source)


def validate_release_destination(source: Path, destination: Path) -> Path:
    target = destination.expanduser().resolve()
    expected = release_root(source).resolve()
    if target != expected:
        raise ValueError(f"正式发布目录必须是当前版本目录：{expected}")
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError("正式发布目录不得经过符号链接")
    return target


def verify_evidence(source: Path, record_path: Path) -> dict[str, Any]:
    """只校验显式证据文件，不能把历史 Journey 或候选稿提升成当前正式验收。"""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping) or record.get("contract_version") != "model-led-release-evidence-v1":
        raise ValueError("发布证据契约不受支持")
    identity = record.get("runtime_identity")
    if not isinstance(identity, dict) or not compare_runtime_identity(identity, source)["verified"]:
        raise ValueError("发布证据与当前运行期身份不一致")
    checks = record.get("checks")
    if not isinstance(checks, dict) or set(checks) != REQUIRED_CHECKS:
        raise ValueError("发布证据必须包含工具、文书、平台、迁移和双轴审查")
    evidence_root = record_path.resolve().parent
    for name, check in checks.items():
        if not isinstance(check, dict) or check.get("status") not in {"passed", "accepted_with_notes"}:
            raise ValueError(f"发布检查未通过：{name}")
        if check["status"] == "accepted_with_notes" and not check.get("notes"):
            raise ValueError(f"有条件接受必须说明范围与遗留问题：{name}")
        relative = check.get("report")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError(f"证据文件路径无效：{name}")
        report = evidence_root / relative
        if (report.is_symlink() or not report.resolve().is_relative_to(evidence_root)
                or not report.is_file() or sha256(report.read_bytes()).hexdigest() != check.get("sha256")):
            raise ValueError(f"证据文件缺失、越界或被修改：{name}")
    return {"verified": True, "runtime_identity": identity, "checks": checks,
            "boundary": "evidence_files_and_identity_only"}
