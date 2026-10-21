"""Assemble and verify source materials and SkillHub uploads for a fixed release."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile

from scripts.release.skills_distribution import validate_distribution_limits
from scripts.release.verification import REQUIRED_CHECKS, verify_evidence
from scripts.runtime_identity import RuntimeIdentityModule
from scripts.runtime_resources import RUNTIME_FILES
from scripts.version import read_version, display_version


INVENTORY = "source-materials-inventory.json"


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or ":" in name or any(part in {".git", ".scratch", ".venv", "__pycache__"} for part in path.parts)):
        raise ValueError(f"Unsafe archive member: {name}")


def archive_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        result = {}
        for entry in archive.infolist():
            safe_name(entry.filename)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Directory or link archive member")
            result[entry.filename] = archive.read(entry)
        return result


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    if path.exists():
        raise ValueError("Archive destination already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            safe_name(name)
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, data)


def git(source: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=source, capture_output=True, check=True, timeout=60).stdout


def fixed_source(source: Path) -> tuple[str, dict[str, bytes]]:
    if git(source, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Release source must be clean")
    commit = git(source, "rev-parse", "HEAD").decode().strip()
    files = {}
    for raw_name in git(source, "ls-files", "-z").split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8")
        safe_name(name)
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Release source contains a link or missing tracked file")
        data = git(source, "show", f"{commit}:{name}")
        if path.read_bytes() != data:
            raise ValueError("Source checkout bytes differ from fixed Git blobs")
        files["project-source/" + name] = data
    return commit, files


def verify_materials(path: Path) -> dict[str, object]:
    files = archive_files(path)
    inventory = json.loads(files[INVENTORY])
    expected = inventory["files"]
    if set(files) != set(expected) | {INVENTORY}:
        raise ValueError("Source materials file set differs from inventory")
    if any(digest(files[name]) != checksum for name, checksum in expected.items()):
        raise ValueError("Source materials content digest mismatch")
    required = {"LICENSE", "THIRD-PARTY-NOTICES.txt", "BUILD-AND-REPLACE.md",
                "SOURCE.json", "release-manifest.json", "release-gate.json", "project-source/VERSION"}
    if not required <= set(files) or not any(name.startswith("dependency-sources/") for name in files):
        raise ValueError("Source materials are incomplete")
    provenance = json.loads(files["SOURCE.json"])
    if files["project-source/VERSION"].decode().strip() != provenance["version"]:
        raise ValueError("Source materials version mismatch")
    for name in ("release-manifest.json", "release-gate.json"):
        record = json.loads(files[name])
        if record["version"] != provenance["version"] or record["source_commit"] != provenance["source_commit"]:
            raise ValueError("Source materials provenance mismatch")
    record = json.loads(files["release-evidence.json"])
    identity = record["runtime_identity"]
    if (record["contract_version"] != "model-led-release-evidence-v1"
            or identity["source_commit"] != provenance["source_commit"]
            or identity["skill_version"] != "v" + provenance["version"]
            or set(record["checks"]) != REQUIRED_CHECKS):
        raise ValueError("Source materials evidence identity mismatch")
    for check in record["checks"].values():
        name = check["report"]
        safe_name(name)
        if (check["status"] not in {"passed", "accepted_with_notes"}
                or (check["status"] == "accepted_with_notes" and not check.get("notes"))
                or digest(files[name]) != check["sha256"]):
            raise ValueError("Source materials evidence report mismatch")
    if json.loads(files["release-gate.json"]).get("status") not in {"passed", "accepted_with_notes"}:
        raise ValueError("Source materials gate has not accepted this source")
    return {"verified": True, "version": provenance["version"],
            "source_commit": provenance["source_commit"], "members": len(files),
            "sha256": digest(path.read_bytes())}


def build_materials(source: Path, workbuddy: Path, dependency_sources: Path,
                    notices: Path, evidence: Path, output: Path) -> dict[str, object]:
    source, output = source.resolve(), output.absolute()
    if output.exists() or output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
        raise ValueError("Release output must be a new directory outside the source")
    if any(part in {".scratch", ".git", "tests"} for part in output.parts):
        raise ValueError("Invalid release output directory")
    commit, files = fixed_source(source)
    verify_evidence(source, evidence / "release-evidence.json")
    version = read_version(source)
    package = archive_files(workbuddy)
    descriptor = json.loads(package["arbibuddy.package.json"])
    if descriptor["skill_version"] != display_version(source):
        raise ValueError("WorkBuddy version mismatch")
    identity = descriptor["runtime_identity"]
    identity_bytes = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if identity["source_commit"] != commit or digest(identity_bytes) != descriptor["runtime_identity_sha256"]:
        raise ValueError("WorkBuddy source identity mismatch")
    if set(package) != {"arbibuddy.package.json"} | {"arbibuddy/" + name for name in descriptor["files"]}:
        raise ValueError("WorkBuddy file set mismatch")
    if any(digest(package["arbibuddy/" + name]) != checksum for name, checksum in descriptor["files"].items()):
        raise ValueError("WorkBuddy member digest mismatch")
    for name in RUNTIME_FILES:
        if package["arbibuddy/" + name] != files["project-source/" + name]:
            raise ValueError("WorkBuddy payload differs from fixed release source")
    # The reviewed dependency source archive is retained intact with its original name.
    dependency_data = dependency_sources.read_bytes()
    with zipfile.ZipFile(dependency_sources) as archive:
        if archive.testzip() is not None:
            raise ValueError("Dependency source archive CRC mismatch")
    files["dependency-sources/" + dependency_sources.name] = dependency_data
    files["LICENSE"] = files["project-source/LICENSE"]
    files["THIRD-PARTY-NOTICES.txt"] = notices.read_bytes()
    for path in sorted(evidence.rglob("*")):
        if path.is_symlink():
            raise ValueError("Evidence link rejected")
        if path.is_file():
            name = path.relative_to(evidence).as_posix()
            safe_name(name)
            if name in files or name == INVENTORY or name == "SOURCE.json":
                raise ValueError("Evidence overwrites release payload")
            files[name] = path.read_bytes()
    for name in ("release-manifest.json", "release-gate.json"):
        record = json.loads(files[name])
        if record["version"] != version or record["source_commit"] != commit:
            raise ValueError("Release evidence is for a different source")
    gate = json.loads(files["release-gate.json"])
    if gate.get("status") not in {"passed", "accepted_with_notes"}:
        raise ValueError("Release gate has not accepted this source")
    files["SOURCE.json"] = json_bytes({"version": version, "source_commit": commit,
        "repository": "https://github.com/windstudio/arbibuddy",
        "workbuddy_sha256": digest(workbuddy.read_bytes()),
        "dependency_sources": {"filename": dependency_sources.name, "sha256": digest(dependency_data)}})
    files[INVENTORY] = json_bytes({"schema": "arbibuddy-source-materials-v1",
                                  "files": {name: digest(data) for name, data in sorted(files.items())}})
    output.mkdir(parents=True)
    target = output / f"arbibuddy-{version}-source-materials.zip"
    write_archive(target, files)
    verification = verify_materials(target)
    shutil.copyfile(workbuddy, output / "arbibuddy-workbuddy.zip")
    sums = {"arbibuddy-workbuddy.zip": digest(workbuddy.read_bytes()), target.name: verification["sha256"]}
    (output / "SHA256SUMS.txt").write_text("".join(f"{value}  {name}\n" for name, value in sums.items()), encoding="utf-8")
    return {**verification, "artifacts": sums}


def build_skillhub(skill: Path, output: Path) -> dict[str, object]:
    skill = skill.resolve()
    output = output.absolute()
    source_roots = [Path(__file__).resolve().parents[2], skill]
    if skill.parent.name == "skills":
        source_roots.append(skill.parent.parent)
    source_roots.extend(parent for parent in skill.parents if (parent / ".git").exists())
    if (any(part.lower() in {".scratch", ".git", "tests"} for part in output.parts)
            or any(output.resolve().is_relative_to(root) for root in source_roots)
            or any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
                   for parent in (output, *output.parents))):
        raise ValueError("SkillHub output must be outside source and prohibited directories")
    limits = validate_distribution_limits(skill)
    if not RuntimeIdentityModule().preflight(skill)["verified"]:
        raise ValueError("SkillHub distribution identity failed verification")
    files = {}
    for path in sorted(skill.rglob("*")):
        if path.is_symlink():
            raise ValueError("SkillHub upload contains a link")
        if path.is_file():
            files[path.relative_to(skill).as_posix()] = path.read_bytes()
    if not {"SKILL.md", "VERSION.txt", "LICENSE.txt", "arbibuddy.distribution.json"} <= set(files):
        raise ValueError("SkillHub upload lacks the distribution entry or identity")
    write_archive(output, files)
    actual = archive_files(output)
    if actual != files:
        raise ValueError("SkillHub archive changed during assembly")
    return {"verified": True, **limits, "zip_sha256": digest(output.read_bytes()),
            "zip_bytes": output.stat().st_size,
            "files": {name: digest(data) for name, data in actual.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    materials = sub.add_parser("build")
    for flag in ("source", "workbuddy", "dependency-sources", "notices", "evidence", "output"):
        materials.add_argument("--" + flag, required=True, type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("archive", type=Path)
    hub = sub.add_parser("skillhub")
    hub.add_argument("--skill", required=True, type=Path)
    hub.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        result = build_materials(args.source, args.workbuddy, args.dependency_sources, args.notices, args.evidence, args.output)
    elif args.command == "verify":
        result = verify_materials(args.archive)
    else:
        result = build_skillhub(args.skill, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
