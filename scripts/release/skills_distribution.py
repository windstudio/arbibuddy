"""从唯一运行资源白名单生成 skills CLI 可发现的仓库树，不复制开发资料。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Sequence

from scripts.cli_encoding import configure_utf8_stdio
from scripts.runtime_identity import (
    DISTRIBUTION_MARKER_NAME, RuntimeIdentityModule,
    build_distribution_marker, build_runtime_identity,
)
from scripts.runtime_resources import runtime_paths


def build_skills_repository(source: Path, destination: Path) -> dict[str, object]:
    """创建新目录；既有目录、源码内目标和链接资源在写入前拒绝。"""
    source = source.resolve()
    destination = destination.absolute()
    resolved_destination = destination.resolve()
    if resolved_destination == source or resolved_destination.is_relative_to(source):
        raise ValueError("分发目标不得位于源码工作树内")
    if source.is_relative_to(resolved_destination):
        raise ValueError("分发目标不得包含源码工作树")
    if destination.exists() or destination.is_symlink():
        raise ValueError("分发目标已存在，未覆盖")
    if any(part in {".scratch", "tests", ".git"} for part in destination.parts):
        raise ValueError("分发目标不得位于工单、测试或 Git 目录")
    paths = runtime_paths(source)
    identity = build_runtime_identity(source)
    marker = build_distribution_marker(identity)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="arbibuddy-skills-", dir=destination.parent) as temp:
        repository = Path(temp) / "repository"
        skill = repository / "skills" / "arbibuddy"
        skill.mkdir(parents=True)
        for path in paths:
            target = skill / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (skill / DISTRIBUTION_MARKER_NAME).write_bytes(marker)
        verification = RuntimeIdentityModule().preflight(skill)
        if not verification["verified"]:
            raise ValueError(f"分发资源校验失败：{verification['failures']}")
        for name in ("README.md", "LICENSE", "VERSION"):
            shutil.copyfile(source / name, repository / name)
        (repository / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        (repository / "SOURCE.md").write_text(
            "# 开发源码与分发来源\n\n"
            "本分支是自动生成的 Skill 安装视图。完整开发源码、测试和维护说明在 "
            "[codex/source 分支](https://github.com/windstudio/arbibuddy/tree/codex/source)。\n\n"
            f"本次来源提交：[{identity['source_commit']}]"
            f"(https://github.com/windstudio/arbibuddy/tree/{identity['source_commit']})。\n\n"
            "生成入口为源码分支的 `scripts.release.skills_distribution`；"
            "维护流程见该分支的 `docs/agents/skills-distribution.md`。\n",
            encoding="utf-8",
        )
        repository.rename(destination)
    return {
        "verified": True, "skill": "arbibuddy",
        "skill_version": identity["skill_version"],
        "source_commit": identity["source_commit"],
        "runtime_file_count": len(paths),
        "distribution_file_count": len(paths) + 1,
        "installation_manifest_sha256": identity["installation_manifest_sha256"],
        "repository_root": str(destination),
    }


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_skills_repository(args.source, args.output)
    except (OSError, ValueError) as error:
        print(json.dumps({"verified": False, "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
