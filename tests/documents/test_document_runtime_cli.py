from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
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
