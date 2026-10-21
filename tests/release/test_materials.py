"""Synthetic release packaging fixtures; no user cases or installed skills."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.release.materials import archive_files, build_materials, build_skillhub, json_bytes, verify_materials, write_archive
from scripts.release.skills_distribution import build_skills_repository
from scripts.runtime_resources import RUNTIME_FILES
from scripts.runtime_identity import RuntimeIdentityModule
from scripts.release.verification import REQUIRED_CHECKS


class ReleaseMaterialsTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        for name in RUNTIME_FILES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic release fixture\n")
        (source / "VERSION").write_text("1.2.11\n", encoding="utf-8")
        (source / "SKILL.md").write_text("---\nname: arbibuddy\ndescription: Synthetic release test.\n---\n", encoding="utf-8")
        (source / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=ArbiBuddy Tests", "-c", "user.email=tests@example.invalid", "commit", "-qm", "Synthetic fixture")):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        payload = {name: (source/name).read_bytes() for name in RUNTIME_FILES}
        identity = {"source_commit": commit, "skill_version": "v1.2.11"}
        identity_bytes = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        descriptor = {"skill_version": "v1.2.11", "runtime_identity": identity,
                      "runtime_identity_sha256": sha256(identity_bytes).hexdigest(),
                      "files": {name: sha256(data).hexdigest() for name, data in payload.items()}}
        package = {"arbibuddy/"+name: data for name, data in payload.items()}
        package["arbibuddy.package.json"] = json_bytes(descriptor)
        workbuddy = root/"workbuddy.zip"
        write_archive(workbuddy, package)
        dependencies = root/"reviewed-dependency-sources.zip"
        write_archive(dependencies, {"COPYING": b"synthetic source license", "source.c": b"/* synthetic */"})
        evidence = root/"evidence"
        evidence.mkdir()
        for name in ("release-gate.json", "release-manifest.json"):
            (evidence/name).write_bytes(json_bytes({"version": "1.2.11", "source_commit": commit, "status": "passed"}))
        (evidence/"BUILD-AND-REPLACE.md").write_text("Synthetic build instructions.", encoding="utf-8")
        (evidence/"report.md").write_bytes(b"Synthetic passing evidence, not a release acceptance.")
        (evidence/"release-evidence.json").write_bytes(json_bytes({
            "contract_version": "model-led-release-evidence-v1",
            "runtime_identity": RuntimeIdentityModule().generate(source),
            "checks": {name: {"status": "passed", "report": "report.md",
                "sha256": sha256((evidence/"report.md").read_bytes()).hexdigest()}
                for name in REQUIRED_CHECKS}}))
        notices = root/"NOTICES.txt"
        notices.write_text("Synthetic license notices.", encoding="utf-8")
        return source, workbuddy, dependencies, notices, evidence

    def test_fixed_source_two_zip_checksums_and_tampering_rejection(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            args = self.fixture(root)
            result = build_materials(*args, root/"release")
            target = root/"release/arbibuddy-1.2.11-source-materials.zip"
            self.assertTrue(verify_materials(target)["verified"])
            self.assertEqual(set(result["artifacts"]), {"arbibuddy-workbuddy.zip", target.name})
            files = archive_files(target)
            self.assertEqual(files["project-source/SKILL.md"], (args[0]/"SKILL.md").read_bytes())
            self.assertEqual(files["dependency-sources/"+args[2].name], args[2].read_bytes())
            files["project-source/SKILL.md"] += b"tampered"
            bad = root/"tampered.zip"
            write_archive(bad, files)
            with self.assertRaisesRegex(ValueError, "digest"):
                verify_materials(bad)

    def test_wrong_identity_dirty_source_and_existing_output_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            args = self.fixture(root)
            (args[0]/"README.md").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean"):
                build_materials(*args, root/"dirty-output")
            subprocess.run(["git", "restore", "README.md"], cwd=args[0], check=True)
            files = archive_files(args[1])
            descriptor = json.loads(files["arbibuddy.package.json"])
            descriptor["runtime_identity"]["source_commit"] = "0"*40
            identity_bytes = json.dumps(descriptor["runtime_identity"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            descriptor["runtime_identity_sha256"] = sha256(identity_bytes).hexdigest()
            files["arbibuddy.package.json"] = json_bytes(descriptor)
            wrong = root/"wrong.zip"
            write_archive(wrong, files)
            with self.assertRaisesRegex(ValueError, "identity"):
                build_materials(args[0], wrong, *args[2:], root/"wrong-output")
            existing = root/"existing"
            existing.mkdir()
            sentinel = existing/"keep.txt"
            sentinel.write_bytes(b"preserve")
            with self.assertRaisesRegex(ValueError, "new directory"):
                build_materials(*args, existing)
            self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_archive_traversal_and_extra_members_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            bad = root/"unsafe.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("../outside", "x")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                archive_files(bad)
            args = self.fixture(root)
            build_materials(*args, root/"release")
            files = archive_files(root/"release/arbibuddy-1.2.11-source-materials.zip")
            files["unexpected.py"] = b"unreviewed"
            extra = root/"extra.zip"
            write_archive(extra, files)
            with self.assertRaisesRegex(ValueError, "file set"):
                verify_materials(extra)

    def test_materials_require_five_category_evidence_and_report_digests(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            args = self.fixture(root)
            record = args[4]/"release-evidence.json"
            original = record.read_bytes()
            record.unlink()
            with self.assertRaises(FileNotFoundError):
                build_materials(*args, root/"missing-record")
            record.write_bytes(original)
            (args[4]/"report.md").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "证据文件"):
                build_materials(*args, root/"wrong-report")
            self.assertFalse((root/"missing-record").exists())
            self.assertFalse((root/"wrong-report").exists())

    def test_skillhub_root_layout_identity_and_unknown_file_rejection(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, *_ = self.fixture(root)
            generated = root/"generated"
            build_skills_repository(source, generated)
            skill = generated/"skills/arbibuddy"
            result = build_skillhub(skill, root/"skillhub.zip")
            self.assertTrue(result["verified"])
            self.assertIn("SKILL.md", archive_files(root/"skillhub.zip"))
            (skill/"unexpected.py").write_bytes(b"unreviewed")
            with self.assertRaisesRegex(ValueError, "identity"):
                build_skillhub(skill, root/"bad-skillhub.zip")

    def test_skillhub_prohibited_destinations_rejected_before_writing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, *_ = self.fixture(root)
            generated = root/"generated"
            build_skills_repository(source, generated)
            skill = generated/"skills/arbibuddy"
            for target in (root/".scratch/upload.zip", root/"tests/upload.zip",
                           root/".git/upload.zip", skill/"upload.zip", generated/"upload.zip",
                           Path(__file__).resolve().parents[2]/"upload.zip"):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, "outside source"):
                    build_skillhub(skill, target)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
