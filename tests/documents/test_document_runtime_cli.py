from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.documents.public import render
from scripts.documents import runtime_cli
from scripts.documents.runtime_cli import main
from tests.documents.test_model_led_document_render import (
    _application_request,
    _create_archive,
    _evidence_request,
)


class DocumentRuntimeCliTests(unittest.TestCase):
    def test_runtime_check_reuses_current_environment_without_probing_other_python(self):
        with patch("scripts.runtime_identity.RuntimeIdentityModule.preflight", return_value={"verified": True}), \
             patch.object(runtime_cli, "_probe_document_python", return_value={"verified": True, "mode": "host"}) as probe:
            result = runtime_cli.check_document_runtime()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["selection"], "current")
        probe.assert_called_once()

    def test_runtime_check_unavailable_returns_preparation_without_cross_platform_fallback(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "codex/arbibuddy"
            root.mkdir(parents=True)
            with patch("scripts.runtime_identity.RuntimeIdentityModule.preflight", return_value={"verified": True}), \
                 patch.object(runtime_cli, "_workbuddy_root") as discovery, \
                 patch.object(runtime_cli, "_probe_document_python", return_value=None) as probe:
                result = runtime_cli.check_document_runtime(skill_root=root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "document_runtime_unavailable")
            self.assertEqual(result["result"]["next_action"], "prepare_document_environment")
            self.assertEqual(result["result"]["requirements_file"], str(root / "requirements-documents.txt"))
            discovery.assert_not_called()
            probe.assert_called_once()
            self.assertEqual(list(root.iterdir()), [])

    def test_managed_environment_paths_cannot_escape_workbuddy_root_through_parent_link(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / ".workbuddy/skills/arbibuddy"
            root.mkdir(parents=True)
            outside = Path(temp) / "outside"
            (outside / "Scripts").mkdir(parents=True)
            (outside / "Scripts/python.exe").write_bytes(b"placeholder")
            link = Path(temp) / ".workbuddy/binaries/python/envs/arbibuddy"
            link.parent.mkdir(parents=True)
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"environment cannot create symlink: {type(error).__name__}")
            self.assertEqual(runtime_cli._workbuddy_python_candidates(root, require_owned_skill=True), ())

    def test_dependency_probe_scrubs_python_paths_and_rejects_invalid_or_timed_out_receipt(self):
        with patch.object(runtime_cli.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"verified":true,"mode":"unknown"}')) as run:
            self.assertIsNone(runtime_cli._probe_document_python(Path("python.exe"), Path.cwd()))
        command = run.call_args.args[0]
        self.assertEqual(command[1:5], ["-B", "-X", "utf8", "-c"])
        self.assertNotIn("PYTHONPATH", run.call_args.kwargs["env"])
        self.assertNotIn("PYTHONHOME", run.call_args.kwargs["env"])
        with patch.object(runtime_cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 10)):
            self.assertIsNone(runtime_cli._probe_document_python(Path("python.exe"), Path.cwd()))

    def test_runtime_check_cli_and_describe_expose_preparation_without_creating_case(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            output = StringIO()
            checked = {"contract_version": "document.runtime-check-v1", "operation": "document.runtime-check", "ok": False,
                       "result": {"next_action": "prepare_document_environment"}, "errors": [], "warnings": []}
            with redirect_stdout(output), patch.object(runtime_cli, "check_document_runtime", return_value=checked), \
                 patch.object(runtime_cli, "bootstrap_document_runtime") as bootstrap:
                code = main(["--workspace", str(workspace), "runtime-check"])
            bootstrap.assert_not_called()
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue()), checked)
            described = StringIO()
            with redirect_stdout(described):
                self.assertEqual(main(["--workspace", str(workspace), "describe"]), 0)
            self.assertEqual(json.loads(described.getvalue())["result"]["runtime_preparation"]["operation"], "document.runtime-check")
            self.assertEqual(list(workspace.iterdir()), [])

    def test_runtime_check_selects_second_prepared_managed_environment_without_case_writes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / ".workbuddy/skills/arbibuddy"
            root.mkdir(parents=True)
            candidates = []
            for name in ("arbibuddy", "default"):
                python = Path(temp) / f".workbuddy/binaries/python/envs/{name}/Scripts/python.exe"
                python.parent.mkdir(parents=True)
                python.write_bytes(b"controlled placeholder")
                candidates.append(python)
            with patch("scripts.runtime_identity.RuntimeIdentityModule.preflight", return_value={"verified": True}), \
                 patch.object(runtime_cli, "_probe_document_python", side_effect=[None, None, {"verified": True, "mode": "host"}]) as probe:
                result = runtime_cli.check_document_runtime(skill_root=root)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["python_executable"], str(candidates[1].resolve()))
            self.assertEqual(probe.call_count, 3)
            self.assertFalse((root / ".arbibuddy").exists())

    def test_runtime_check_stops_before_probing_a_modified_installation(self):
        with patch("scripts.runtime_identity.RuntimeIdentityModule.preflight", return_value={"verified": False, "failures": ["distribution_marker_mismatch"]}), \
             patch.object(runtime_cli, "_probe_document_python") as probe:
            result = runtime_cli.check_document_runtime()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "installed_skill_modified")
        probe.assert_not_called()

    def test_probe_uses_stdlib_mode_without_host_python_paths(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"python_abi":"cp313","platform_tag":"win_amd64"}',
        )
        with patch.object(runtime_cli.subprocess, "run", return_value=completed) as run:
            result = runtime_cli._probe_workbuddy_python(Path("C:/managed/python.exe"))

        self.assertEqual(
            result,
            {"python_abi": "cp313", "platform_tag": "win_amd64"},
        )
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ["-S", "-c"])
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("PYTHONHOME", environment)

    def test_packaged_runtime_bootstrap_reexecutes_once_into_matching_workbuddy_python(self):
        with TemporaryDirectory() as temp:
            skill_root = Path(temp) / "arbibuddy"
            runtime_root = skill_root / "workbuddy-runtime"
            runtime_root.mkdir(parents=True)
            (runtime_root / "runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "python_abi": "cp999",
                        "platform_tag": "win_amd64",
                    }
                ),
                encoding="utf-8",
            )
            target = Path(temp) / "workbuddy" / "python.exe"
            target.parent.mkdir()
            target.write_bytes(b"controlled interpreter placeholder")
            argv = ["--workspace", str(Path(temp)), "describe"]

            with patch.object(
                runtime_cli,
                "_discover_workbuddy_python",
                return_value=target,
            ) as discover, patch.object(
                runtime_cli,
                "_probe_workbuddy_python",
                return_value={
                    "python_abi": "cp999",
                    "platform_tag": "win_amd64",
                },
            ) as probe, patch.object(runtime_cli.os, "execv") as execv, patch.dict(
                runtime_cli.os.environ,
                {},
                clear=False,
            ):
                runtime_cli.os.environ.pop(
                    runtime_cli.RUNTIME_BOOTSTRAP_ENV, None
                )
                self.assertTrue(
                    runtime_cli.bootstrap_document_runtime(
                        argv,
                        skill_root=skill_root,
                    )
                )

            discover.assert_called_once()
            probe.assert_called_once_with(target)
            execv.assert_called_once_with(
                str(target.resolve()),
                [
                    str(target.resolve()),
                    "-m",
                    "scripts.documents.runtime_cli",
                    *argv,
                ],
            )

    def test_packaged_runtime_bootstrap_loop_guard_fails_closed_without_restart(self):
        with TemporaryDirectory() as temp:
            skill_root = Path(temp) / "arbibuddy"
            runtime_root = skill_root / "workbuddy-runtime"
            runtime_root.mkdir(parents=True)
            (runtime_root / "runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "python_abi": "cp999",
                        "platform_tag": "win_amd64",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(runtime_cli.os, "execv") as execv, patch.dict(
                runtime_cli.os.environ,
                {runtime_cli.RUNTIME_BOOTSTRAP_ENV: "1"},
                clear=False,
            ):
                self.assertFalse(
                    runtime_cli.bootstrap_document_runtime(
                        ["describe"],
                        skill_root=skill_root,
                    )
                )

            execv.assert_not_called()

    def test_view_returns_only_the_complete_managed_delivery_set(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            delivery_output = StringIO()
            with redirect_stdout(delivery_output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "delivery-set",
                        "--json",
                        json.dumps(
                            {
                                "case_id": case_id,
                                "archive_revision": revision,
                                "mode": "external_final",
                                "requested_templates": [
                                    "labor-arbitration-application"
                                ],
                                "confirmation_refs": [],
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
            self.assertEqual(code, 0, delivery_output.getvalue())
            delivery = json.loads(delivery_output.getvalue())["result"]
            delivery_id = delivery["delivery_set_id"]

            application = _application_request(case_id, revision)
            application["delivery_set_id"] = delivery_id
            evidence = _evidence_request(case_id, revision)
            evidence["delivery_set_id"] = delivery_id
            self.assertTrue(render(application, workspace)["ok"])
            self.assertTrue(render(evidence, workspace)["ok"])

            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "view",
                        "--json",
                        json.dumps(
                            {
                                "case_id": case_id,
                                "delivery_set_id": delivery_id,
                            }
                        ),
                    ]
                )

            self.assertEqual(code, 0, output.getvalue())
            view = json.loads(output.getvalue())
            self.assertFalse(view["stopped"], view)
            self.assertEqual(view["delivery_set"]["delivery_set_id"], delivery_id)
            self.assertEqual(len(view["present_files_arguments"]["files"]), 4)

    def test_delivery_set_consumes_managed_runtime_input(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            input_path = workspace / ".arbibuddy" / "runtime-input" / "delivery.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "archive_revision": revision,
                        "mode": "candidate",
                        "requested_templates": ["employment-obligation-demand-letter"],
                        "confirmation_refs": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "delivery-set",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["contract_version"], "document.delivery-set-v1")

    def test_runtime_input_outside_managed_directory_is_not_read_or_deleted(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _archive, case_id, revision = _create_archive(workspace)
            input_path = workspace / "render-requests" / "delivery.json"
            input_path.parent.mkdir()
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "archive_revision": revision,
                        "mode": "candidate",
                        "requested_templates": ["employment-obligation-demand-letter"],
                        "confirmation_refs": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "delivery-set",
                        "--input",
                        str(input_path),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertTrue(input_path.exists())
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "invalid_runtime_input_path")


if __name__ == "__main__":
    unittest.main()
