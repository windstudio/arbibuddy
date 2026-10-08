import json
import hashlib
from pathlib import Path
import shutil
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import re
import unittest
from unittest.mock import patch
import zipfile
from zipfile import ZipFile

from scripts.amount_calculator.public import AmountCalculator
from scripts.case_archive import CaseArchive
from scripts.case_archive import service as case_archive_service
from scripts.documents.public import render as public_render
from scripts.platform_adapters.service import (
    PlatformAdapterError,
    _build_workbuddy_runtime_layer_from_wheels,
    build_artifact,
    verify_artifact,
    verify_install,
)
from scripts.runtime_identity import RuntimeIdentityModule
from scripts.runtime_identity import (
    _workbuddy_external_anchor_failures,
    runtime_identity_digest,
    runtime_identity_failure_message,
    write_workbuddy_trust_anchor,
)
from scripts.runtime_manifest import (
    RuntimeManifestError,
    read_and_validate_runtime_manifest,
)
from scripts.documents.view import managed_delivery_view
from scripts.workbuddy_acceptance.service import audit_workspace
from tests.documents.model_led_demand_forced_fixtures import (
    create_case as create_model_case,
    demand_request as model_demand_request,
)
from tests.platform_adapters.test_workbuddy_client_acceptance import (
    _timeline_transcript,
)
from tests.platform_adapters.test_cli import run_cli
from tests.documents.test_model_led_document_render import (
    _application_request,
    _create_archive,
)


ROOT = Path(__file__).resolve().parents[2]


def _fake_workbuddy_runtime_payload() -> dict[str, bytes]:
    payload = {
        "workbuddy-runtime/site-packages/demo/__init__.py": b"VALUE = 1\n",
        "workbuddy-runtime/site-packages/demo-1.0.dist-info/LICENSE": b"demo license\n",
    }
    runtime_manifest = {
        "schema_version": 1,
        "python_abi": "cp313",
        "platform_tag": "win_amd64",
        "packages": [{"distribution": "demo", "version": "1.0"}],
        "licenses": ["site-packages/demo-1.0.dist-info/LICENSE"],
        "files": {
            relative.removeprefix("workbuddy-runtime/"): hashlib.sha256(data).hexdigest()
            for relative, data in payload.items()
        },
    }
    payload["workbuddy-runtime/runtime-manifest.json"] = (
        json.dumps(runtime_manifest, ensure_ascii=False).encode("utf-8")
    )
    return payload


def _build_fake_workbuddy_artifact(output: Path) -> dict[str, object]:
    with patch(
        "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
        return_value=(
            _fake_workbuddy_runtime_payload(),
            {
                "verified": True,
                "required_files": ["runtime-manifest.json"],
                "missing_files": [],
                "missing_packages": [],
            },
        ),
    ):
        return build_artifact(
            platform="workbuddy",
            source=ROOT,
            output=output,
        )


def _simulate_native_upload(artifact: Path, upload_root: Path) -> Path:
    with ZipFile(artifact) as archive:
        for name in archive.namelist():
            if not name.startswith("arbibuddy/"):
                continue
            target = upload_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    skill_root = upload_root / "arbibuddy"
    # WorkBuddy 上传器会在解包后的 Skill 根写入此元数据；ZIP 本身不含它。
    (skill_root / "_user_meta.json").write_text(
        json.dumps({"name": "arbibuddy", "installedAt": 1790922044674, "source": "userImport"}),
        encoding="utf-8",
    )
    return skill_root


class TwoBatchWorkBuddyRemediationTests(unittest.TestCase):
    def test_native_upload_host_metadata_allows_public_archive_create(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")
            self.assertTrue(RuntimeIdentityModule().preflight(skill_root)["verified"])
            self.assertTrue(verify_install(platform="workbuddy", skill_root=skill_root)["runtime_identity_verified"])
            workspace = root / "workspace"
            workspace.mkdir()
            completed = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-m", "scripts.case_archive.cli", "--root", str(workspace), "create", "--initial-goal", "准备虚构欠薪材料"],
                cwd=skill_root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            created = json.loads(completed.stdout)
            self.assertTrue(created["ok"], created)
            self.assertTrue(Path(created["result"]["canonical_location"]).is_file())

    def test_native_upload_rejects_invalid_or_misplaced_host_metadata(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")
            metadata = skill_root / "_user_meta.json"
            valid = metadata.read_bytes()
            for invalid in (
                b'not-json', b'[]',
                b'{"name":"other","installedAt":1,"source":"userImport"}',
                b'{"name":"arbibuddy","installedAt":true,"source":"userImport"}',
                b'{"name":"arbibuddy","installedAt":-1,"source":"userImport"}',
                b'{"name":"arbibuddy","installedAt":1,"source":"other"}',
                b'{"name":"arbibuddy","installedAt":1,"source":"userImport","command":"execute"}',
                b'{"name":"other","name":"arbibuddy","installedAt":1,"source":"userImport"}',
                b' ' * (16 * 1024 + 1),
            ):
                with self.subTest(invalid=invalid[:100]):
                    metadata.write_bytes(invalid)
                    self.assertFalse(RuntimeIdentityModule().preflight(skill_root)["verified"])
            metadata.write_bytes(valid)
            misplaced = skill_root / "scripts" / "_user_meta.json"
            misplaced.write_bytes(valid)
            self.assertFalse(RuntimeIdentityModule().preflight(skill_root)["verified"])
            misplaced.unlink()
            metadata.unlink()
            metadata.mkdir()
            self.assertFalse(RuntimeIdentityModule().preflight(skill_root)["verified"])
            metadata.rmdir()

    def test_native_upload_rejects_host_metadata_link(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")
            metadata = skill_root / "_user_meta.json"
            metadata.unlink()
            try:
                metadata.symlink_to(artifact)
            except OSError as error:
                self.skipTest(f"当前宿主不能创建元数据链接：{error}")
            self.assertFalse(RuntimeIdentityModule().preflight(skill_root)["verified"])

    def test_runtime_lock_records_wheel_filename_sha_and_expansion_contract(self):
        lock = json.loads(
            (ROOT / "references" / "workbuddy-document-runtime.lock.json").read_text(
                encoding="utf-8"
            )
        )

        for package in lock["packages"]:
            self.assertRegex(package["wheel_filename"], r".whl$")
            self.assertRegex(package["wheel_sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(package["wheel_path"], str)
            self.assertIsInstance(package["expand_path"], str)

    def test_runtime_manifest_rejects_unlisted_vendor_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            runtime_root = root / "workbuddy-runtime"
            vendor_root = runtime_root / "site-packages" / "demo"
            vendor_root.mkdir(parents=True)
            source = vendor_root / "__init__.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            license_path = vendor_root / "LICENSE"
            license_path.write_text("demo license\n", encoding="utf-8")
            files = {
                "site-packages/demo/__init__.py": hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                "site-packages/demo/LICENSE": hashlib.sha256(
                    license_path.read_bytes()
                ).hexdigest(),
            }
            (runtime_root / "runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "python_abi": "cp313",
                        "platform_tag": "win_amd64",
                        "packages": [{"distribution": "demo", "version": "1.0"}],
                        "licenses": ["site-packages/demo/LICENSE"],
                        "files": files,
                    }
                ),
                encoding="utf-8",
            )
            read_and_validate_runtime_manifest(
                root, expected_abi="cp313", expected_platform="win_amd64"
            )
            (vendor_root / "extra.py").write_text("BAD = 1\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeManifestError, "展开文件集"):
                read_and_validate_runtime_manifest(
                    root,
                    expected_abi="cp313",
                    expected_platform="win_amd64",
                )

    def test_empty_git_metadata_does_not_verify_as_a_source_checkout(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            with patch(
                "scripts.runtime_identity._identity_from_root",
                return_value={"schema_version": 2},
            ):
                result = RuntimeIdentityModule().preflight(root)

        self.assertFalse(result["verified"])

    def test_workbuddy_installation_without_external_trust_anchor_fails_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            runtime_root = root / "workbuddy-runtime"
            runtime_root.mkdir()
            (runtime_root / "runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "python_abi": "cp313",
                        "platform_tag": "win_amd64",
                        "files": {},
                        "licenses": [],
                    }
                ),
                encoding="utf-8",
            )
            identity = {
                "schema_version": 2,
                "installation_manifest_sha256": "i" * 64,
                "artifact_manifest_sha256": "a" * 64,
            }
            (root / "arbibuddy.install.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "platform": "workbuddy",
                        "target_root": str(root / "target"),
                        "runtime_identity": identity,
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "scripts.runtime_identity._identity_from_root",
                return_value=identity,
            ):
                result = RuntimeIdentityModule().preflight(root)

        self.assertFalse(result["verified"])
        self.assertIn("external_trust_anchor_missing", result["failures"])
        self.assertIn("受管 WorkBuddy 安装缺少外部信任锚", runtime_identity_failure_message(result))

    def test_workbuddy_portable_marker_does_not_require_managed_install_anchor(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")
            result = RuntimeIdentityModule().preflight(skill_root)
            self.assertTrue(result["verified"], result)
            self.assertEqual(result["mode"], "portable")
            self.assertEqual(result["failures"], [])

    def test_native_upload_rejects_added_core_execution_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")
            self.assertTrue(RuntimeIdentityModule().preflight(skill_root)["verified"])
            extra = skill_root / "scripts/core_workflow/cli.py"
            extra.parent.mkdir(parents=True)
            extra.write_text("# harmless extra file\n", encoding="utf-8")
            result = RuntimeIdentityModule().preflight(skill_root)
            self.assertFalse(result["verified"])
            self.assertIn("installed_resource_set_mismatch", result["failures"])
            self.assertFalse(verify_install(platform="workbuddy", skill_root=skill_root)["runtime_identity_verified"])

    def test_native_upload_subtree_uses_embedded_marker_and_blocks_missing_marker_write(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            built = _build_fake_workbuddy_artifact(artifact)
            self.assertTrue(built["artifact_contract_valid"])

            upload_root = root / "native-upload"
            skill_root = _simulate_native_upload(artifact, upload_root)
            self.assertFalse((upload_root / "arbibuddy.package.json").exists())
            portable_marker = skill_root / "arbibuddy.runtime.json"
            self.assertTrue(portable_marker.is_file())
            marker = json.loads(portable_marker.read_text(encoding="utf-8"))
            self.assertEqual(marker["schema_version"], 1)
            self.assertEqual(marker["platform"], "workbuddy")
            self.assertEqual(marker["marker_type"], "native-upload-portable")
            self.assertEqual(marker["skill"], "arbibuddy")
            self.assertEqual(
                marker["runtime_identity_sha256"],
                runtime_identity_digest(marker["runtime_identity"]),
            )
            self.assertNotIn(
                "arbibuddy.runtime.json",
                marker["runtime_identity"]["installation_manifest"]["files"],
            )
            with ZipFile(artifact) as archive:
                artifact_manifest = json.loads(
                    archive.read("arbibuddy.package.json")
                )
            self.assertIn("arbibuddy.runtime.json", artifact_manifest["files"])

            preflight = RuntimeIdentityModule().preflight(skill_root)
            self.assertTrue(preflight["verified"], preflight)
            self.assertEqual(preflight["mode"], "portable")

            workspace = root / "workspace"
            archive = CaseArchive(workspace)
            with patch.object(
                case_archive_service._RUNTIME_IDENTITY,
                "preflight",
                return_value=preflight,
            ):
                result = archive.create({"case_label": "原生上传"})
            self.assertTrue(result["ok"], result)
            self.assertTrue(workspace.exists())

            missing_marker_root = root / "native-upload-missing-marker"
            shutil.copytree(upload_root, missing_marker_root)
            missing_marker = missing_marker_root / "arbibuddy" / "arbibuddy.runtime.json"
            missing_marker.unlink()
            missing_marker_preflight = RuntimeIdentityModule().preflight(
                missing_marker_root / "arbibuddy"
            )
            self.assertFalse(missing_marker_preflight["verified"])
            self.assertIn(
                "native_upload_marker_missing",
                missing_marker_preflight["failures"],
            )

            failure_workspace = root / "failure-workspace"
            failed_archive = CaseArchive(failure_workspace)
            with patch.object(
                case_archive_service._RUNTIME_IDENTITY,
                "preflight",
                return_value=missing_marker_preflight,
            ):
                failed = failed_archive.create({"case_label": "原生上传"})

            self.assertFalse(failed["ok"])
            self.assertEqual(failed["errors"][0]["code"], "installed_skill_modified")
            self.assertFalse(failed["errors"][0]["recoverable"])
            self.assertFalse(failure_workspace.exists())

    def test_verify_install_accepts_native_upload_identity_without_managed_receipt(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            skill_root = _simulate_native_upload(artifact, root / "native-upload")

            result = verify_install(platform="workbuddy", skill_root=skill_root)

        self.assertEqual(result["installation_mode"], "native-upload")
        self.assertTrue(result["discovery_contract_valid"], result)
        self.assertTrue(result["runtime_identity_verified"], result)
        self.assertTrue(result["runtime_manifest_verification"]["verified"], result)
        self.assertNotIn(
            "install_receipt_identity_missing",
            result["runtime_identity_verification"]["failures"],
        )

    def test_native_upload_marker_precedes_stale_parent_manifest(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            built = _build_fake_workbuddy_artifact(artifact)
            upload_root = root / "native-upload"
            skill_root = _simulate_native_upload(artifact, upload_root)
            stale_identity = json.loads(
                json.dumps(built["runtime_identity"], ensure_ascii=False)
            )
            stale_identity["skill_version"] = "v1.2.6-rc7"
            stale_identity["version_sha256"] = "0" * 64
            (upload_root / "arbibuddy.package.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "workbuddy",
                        "skill": "arbibuddy",
                        "skill_version": "v1.2.6-rc7",
                        "files": {},
                        "runtime_identity": stale_identity,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = RuntimeIdentityModule().preflight(skill_root)

        self.assertTrue(result["verified"], result)
        self.assertEqual(result["mode"], "portable")
        self.assertEqual(result["expected"], built["runtime_identity"])

    def test_native_upload_key_marker_and_vendor_drift_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            built = _build_fake_workbuddy_artifact(artifact)

            key_root = root / "key-drift"
            key_skill = _simulate_native_upload(artifact, key_root)
            (key_skill / "SKILL.md").write_text(
                (key_skill / "SKILL.md").read_text(encoding="utf-8") + "\n漂移",
                encoding="utf-8",
            )
            key_result = RuntimeIdentityModule().preflight(key_skill)

            marker_root = root / "marker-drift"
            marker_skill = _simulate_native_upload(artifact, marker_root)
            (marker_skill / "arbibuddy.runtime.json").write_text(
                "{}",
                encoding="utf-8",
            )
            (marker_root / "arbibuddy.package.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "workbuddy",
                        "skill": "arbibuddy",
                        "skill_version": "v1.2.6-rc7",
                        "files": {},
                        "runtime_identity": built["runtime_identity"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            marker_result = RuntimeIdentityModule().preflight(marker_skill)

            vendor_root = root / "vendor-drift"
            vendor_skill = _simulate_native_upload(artifact, vendor_root)
            vendor_file = (
                vendor_skill
                / "workbuddy-runtime"
                / "site-packages"
                / "demo"
                / "__init__.py"
            )
            vendor_file.write_bytes(vendor_file.read_bytes() + b"DRIFT\n")
            vendor_result = RuntimeIdentityModule().preflight(vendor_skill)

        self.assertFalse(key_result["verified"])
        self.assertIn("native_upload_marker_stale", key_result["failures"])
        self.assertFalse(marker_result["verified"])
        self.assertIn("native_upload_marker_invalid", marker_result["failures"])
        self.assertFalse(vendor_result["verified"])
        self.assertIn("native_upload_vendor_runtime_invalid", vendor_result["failures"])

    def test_runtime_identity_explains_native_upload_recovery(self):
        from scripts.runtime_identity import runtime_identity_failure_next_action
        preflight = {"verified": False, "failures": ["native_upload_marker_missing"]}
        self.assertEqual(runtime_identity_failure_next_action(preflight), "reupload-workbuddy-skill-and-restart")
        self.assertIn("原生上传", runtime_identity_failure_message(preflight))
        self.assertIn("不会生成受管安装回执", runtime_identity_failure_message(preflight))

    def test_verify_artifact_requires_native_upload_subtree_identity(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "arbibuddy-workbuddy.zip"
            _build_fake_workbuddy_artifact(artifact)
            patches = (
                patch(
                    "scripts.platform_adapters.service._resolve_workbuddy_runtime_python",
                    return_value=Path("python.exe"),
                ),
                patch(
                    "scripts.platform_adapters.service._workbuddy_runtime_smoke",
                    return_value={"verified": True},
                ),
            )
            with patches[0], patches[1]:
                verified = verify_artifact(
                    platform="workbuddy",
                    artifact=artifact,
                )

            broken = root / "missing-portable-marker.zip"
            with ZipFile(artifact) as archive:
                entries = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if name != "arbibuddy/arbibuddy.runtime.json"
                }
            manifest = json.loads(entries["arbibuddy.package.json"])
            manifest["files"].pop("arbibuddy.runtime.json", None)
            entries["arbibuddy.package.json"] = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            with ZipFile(broken, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)

            with patches[0], patches[1]:
                rejected = verify_artifact(
                    platform="workbuddy",
                    artifact=broken,
                )

            with patches[0], patches[1]:
                code, output, error = run_cli(
                    [
                        "verify-artifact",
                        "--platform",
                        "workbuddy",
                        "--artifact",
                        str(broken),
                    ]
                )

        self.assertTrue(verified["artifact_contract_valid"], verified)
        self.assertTrue(verified["native_upload_runtime_identity_verified"], verified)
        self.assertEqual(
            verified["native_upload_runtime_identity_verification"]["mode"],
            "portable",
        )
        self.assertFalse(rejected["artifact_contract_valid"], rejected)
        self.assertFalse(rejected["native_upload_runtime_identity_verified"])
        self.assertIn(
            "native_upload_marker_missing",
            rejected["native_upload_runtime_identity_verification"]["failures"],
        )
        self.assertEqual(code, 2, error)
        self.assertFalse(json.loads(output)["native_upload_runtime_identity_verified"])

    def test_external_anchor_rejects_receipt_or_tree_rewrite(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root = root / "skill"
            target_root = root / "target"
            skill_root.mkdir()
            identity_a = {
                "schema_version": 2,
                "installation_manifest_sha256": "i" * 64,
                "artifact_manifest_sha256": "a" * 64,
            }
            identity_b = {
                **identity_a,
                "installation_manifest_sha256": "j" * 64,
                "artifact_manifest_sha256": "b" * 64,
            }
            write_workbuddy_trust_anchor(
                target_root=target_root,
                skill_root=skill_root,
                runtime_identity=identity_a,
            )
            receipt = {
                "target_root": str(target_root),
                "runtime_identity": identity_b,
                "runtime_identity_sha256": runtime_identity_digest(identity_b),
            }

            failures = _workbuddy_external_anchor_failures(
                skill_root, receipt, identity_b
            )

        self.assertIn("external_runtime_identity_sha256_mismatch", failures)
        self.assertIn("external_installation_manifest_sha256_mismatch", failures)
        self.assertIn("external_artifact_manifest_sha256_mismatch", failures)

    def test_external_anchor_rejects_a_tampered_receipt_identity_digest(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root = root / "skill"
            target_root = root / "target"
            skill_root.mkdir()
            identity = {
                "schema_version": 2,
                "installation_manifest_sha256": "i" * 64,
                "artifact_manifest_sha256": "a" * 64,
            }
            write_workbuddy_trust_anchor(
                target_root=target_root,
                skill_root=skill_root,
                runtime_identity=identity,
            )
            receipt = {
                "target_root": str(target_root),
                "runtime_identity": identity,
                "runtime_identity_sha256": "0" * 64,
            }

            failures = _workbuddy_external_anchor_failures(
                skill_root, receipt, identity
            )

        self.assertIn("install_receipt_identity_digest_mismatch", failures)

    def test_amount_entrypoint_fails_before_processing_when_installation_is_modified(self):
        with patch.object(
            RuntimeIdentityModule,
            "preflight",
            return_value={"verified": False, "failures": ["tree_tampered"]},
        ):
            result = AmountCalculator().calculate({})

        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "installed_skill_modified")
        self.assertFalse(result["errors"][0]["recoverable"])

    def test_document_entrypoint_fails_before_staging_when_installation_is_modified(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            request = _application_request(case_id, revision)
            with patch(
                "scripts.documents.public.RuntimeIdentityModule.preflight",
                return_value={"verified": False, "failures": ["tree_tampered"]},
            ):
                result = public_render(request, workspace)

            output_root = workspace / ".arbibuddy" / "cases" / case_id / "output"

        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "installed_skill_modified")
        self.assertFalse(output_root.exists())

    def test_archive_entrypoints_fail_before_any_archive_side_effect(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive = CaseArchive(workspace)
            with patch.object(
                RuntimeIdentityModule,
                "preflight",
                return_value={"verified": False, "failures": ["tree_tampered"]},
            ):
                results = [
                    archive.create({"case_label": "测试"}),
                    archive.read("case-" + "0" * 24),
                    archive.commit({}),
                ]

            created_paths = list(workspace.rglob("*"))

        self.assertEqual(
            [item["operation"] for item in results], ["create", "read", "commit"]
        )
        for result in results:
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "installed_skill_modified")
            self.assertFalse(result["errors"][0]["recoverable"])
        self.assertEqual(created_paths, [])

    def test_build_rejects_oversized_payload_before_creating_zip(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "oversized.zip"
            with (
                patch(
                    "scripts.platform_adapters.service._read_runtime_lock",
                    return_value={"schema_version": 1, "python_abi": "cp313", "platform_tag": "win_amd64", "packages": []},
                ),
                patch(
                    "scripts.platform_adapters.service._package_payload",
                    return_value={"huge.bin": b"x" * (50 * 1024 * 1024 + 1)},
                ),
                patch(
                    "scripts.platform_adapters.service._build_workbuddy_runtime_layer",
                    return_value=(
                        _fake_workbuddy_runtime_payload(),
                        {"verified": True},
                    ),
                ),
                patch(
                    "scripts.platform_adapters.service._validate_skill_source",
                    return_value=None,
                ),
                patch(
                    "scripts.platform_adapters.service._skill_version",
                    return_value="v1.2.6-rc9",
                ),
                patch(
                    "scripts.platform_adapters.service.build_runtime_identity",
                    return_value={
                        "schema_version": 2,
                        "skill_version": "v1.2.6-rc9",
                    },
                ),
            ):
                with self.assertRaisesRegex(PlatformAdapterError, "50 MiB"):
                    build_artifact(
                        platform="workbuddy",
                        source=ROOT,
                        output=output,
                    )

            self.assertFalse(output.exists())

    def test_locked_wheel_is_verified_and_expanded_without_host_site_packages(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            filename = "demo-1.0-py3-none-any.whl"
            wheel = wheelhouse / filename
            entries = {
                "demo/__init__.py": b"VALUE = 1\n",
                "demo-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nVersion: 1.0\n",
                "demo-1.0.dist-info/LICENSE": b"demo license\n",
            }
            with ZipFile(wheel, "w") as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            lock = {
                "schema_version": 1,
                "python_abi": "cp313",
                "platform_tag": "win_amd64",
                "wheelhouse": "wheelhouse",
                "packages": [
                    {
                        "distribution": "demo",
                        "version": "1.0",
                        "wheel_filename": filename,
                        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        "wheel_path": f"wheelhouse/{filename}",
                        "expand_path": "site-packages",
                        "paths": ["demo", "demo-1.0.dist-info"],
                        "licenses": ["demo-1.0.dist-info/LICENSE"],
                    }
                ],
            }
            payload, report = _build_workbuddy_runtime_layer_from_wheels(
                source=root,
                lock=lock,
                target_python=root / "python.exe",
                probe={"python_abi": "cp313", "platform_tag": "win_amd64"},
                wheelhouse=wheelhouse,
            )

        self.assertTrue(report["verified"])
        self.assertIn(
            "workbuddy-runtime/site-packages/demo/__init__.py",
            payload,
        )
        self.assertIn("workbuddy-runtime/runtime-manifest.json", payload)

    def test_lxml_subset_omits_whole_isoschematron_but_preserves_binary_and_license(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            filename = "lxml-6.1.1-cp313-cp313-win_amd64.whl"
            wheel = wheelhouse / filename
            entries = {
                "lxml/etree.cp313-win_amd64.pyd": b"synthetic native bytes",
                "lxml/isoschematron/__init__.py": b"unused module",
                "lxml/isoschematron/resources/xsl/RNG2Schtrn.xsl": b"excluded resource",
                "lxml/isoschematron_extra.py": b"keep adjacent module",
                "lxml-6.1.1.dist-info/licenses/LICENSE.txt": b"original license",
            }
            with ZipFile(wheel, "w") as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            lock = {
                "python_abi": "cp313", "platform_tag": "win_amd64",
                "packages": [{
                    "distribution": "lxml", "version": "6.1.1",
                    "wheel_filename": filename,
                    "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    "wheel_path": filename, "expand_path": "site-packages",
                    "paths": ["lxml", "lxml-6.1.1.dist-info"],
                    "licenses": ["lxml-6.1.1.dist-info/licenses/LICENSE.txt"],
                }],
            }
            payload, report = _build_workbuddy_runtime_layer_from_wheels(
                source=root, lock=lock, target_python=root / "python.exe",
                probe={}, wheelhouse=wheelhouse,
            )
            prefix = "workbuddy-runtime/site-packages/"
            for name, data in entries.items():
                if name.startswith("lxml/isoschematron/"):
                    self.assertNotIn(prefix + name, payload)
                else:
                    self.assertEqual(payload[prefix + name], data)
            manifest = json.loads(payload["workbuddy-runtime/runtime-manifest.json"])
            self.assertEqual(manifest["packages"][0]["excluded_files"], [
                "lxml/isoschematron/__init__.py",
                "lxml/isoschematron/resources/xsl/RNG2Schtrn.xsl",
            ])
            self.assertEqual(report["packages"], manifest["packages"])

    def test_locked_wheel_sha_mismatch_and_path_traversal_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            filename = "demo-1.0-py3-none-any.whl"
            wheel = wheelhouse / filename
            with ZipFile(wheel, "w") as archive:
                archive.writestr("../escape.py", b"bad")
            lock = {
                "schema_version": 1,
                "python_abi": "cp313",
                "platform_tag": "win_amd64",
                "wheelhouse": "wheelhouse",
                "packages": [
                    {
                        "distribution": "demo",
                        "version": "1.0",
                        "wheel_filename": filename,
                        "wheel_sha256": "0" * 64,
                        "wheel_path": f"wheelhouse/{filename}",
                        "expand_path": "site-packages",
                        "paths": ["demo"],
                        "licenses": ["demo/LICENSE"],
                    }
                ],
            }
            with self.assertRaisesRegex(PlatformAdapterError, "SHA-256"):
                _build_workbuddy_runtime_layer_from_wheels(
                    source=root,
                    lock=lock,
                    target_python=root / "python.exe",
                    probe={"python_abi": "cp313", "platform_tag": "win_amd64"},
                    wheelhouse=wheelhouse,
                )

            lock["packages"][0]["wheel_sha256"] = hashlib.sha256(
                wheel.read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(PlatformAdapterError, "路径穿越"):
                _build_workbuddy_runtime_layer_from_wheels(
                    source=root,
                    lock=lock,
                    target_python=root / "python.exe",
                    probe={"python_abi": "cp313", "platform_tag": "win_amd64"},
                    wheelhouse=wheelhouse,
                )

    def test_historical_presentation_rejects_render_file_digest_drift(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision, _ = create_model_case(workspace)
            rendered = public_render(
                model_demand_request(case_id, revision), workspace
            )
            self.assertTrue(rendered["ok"], rendered)
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            files = [item["path"] for item in view["presentation_files"]]
            manifest = Path(str(rendered["result"]["machine_manifest"]))
            records = _timeline_transcript(
                session_id="timeline-digest",
                case_id=case_id,
                revision=revision,
                manifest=manifest,
                files=files,
            )
            Path(files[0]).write_bytes(Path(files[0]).read_bytes() + b"tampered")
            transcript = workspace / "timeline-digest.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
                encoding="utf-8",
            )
            report = audit_workspace(
                workspace=workspace,
                case_id=case_id,
                transcript_jsonl=transcript,
                session_id="timeline-digest",
            )

        self.assertIn(
            "invalid_revision_timeline",
            {item["code"] for item in report["violations"]},
        )


if __name__ == "__main__":
    unittest.main()
