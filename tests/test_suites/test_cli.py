from __future__ import annotations

import os
import subprocess
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.test_suites.cli import (
    FAST_GROUPS,
    SLOW_GROUPS,
    WORKBUDDY_WHEELHOUSE_ENV,
    _parser,
    _group_environment,
    _temp_environment,
    run_profile,
)


class TestSuitesCliTests(unittest.TestCase):
    def test_profiles_are_disjoint_and_cover_every_executable_test_domain(self):
        fast = {group.name for group in FAST_GROUPS}
        slow = {group.name for group in SLOW_GROUPS}

        self.assertFalse(fast & slow)
        self.assertTrue({"case_archive", "amount_calculator", "documents_core", "documents_support", "platform_adapters", "runtime_identity", "contraction", "release"} <= fast | slow)
        self.assertTrue(all(group.timeout_seconds in {300, 900} for group in FAST_GROUPS + SLOW_GROUPS))

        tests_root = Path(__file__).resolve().parents[1]
        for group in FAST_GROUPS + SLOW_GROUPS:
            if group.test_modules:
                for module in group.test_modules:
                    module_path = (
                        Path(__file__).resolve().parents[2]
                        / Path(*module.split(".")).with_suffix(".py")
                    )
                    self.assertTrue(module_path.is_file(), module)
                continue
            group_root = (
                Path(__file__).resolve().parents[2] / group.start_dir
                if group.start_dir
                else tests_root / group.name
            )
            self.assertTrue(list(group_root.glob(group.pattern)), group.name)

    def test_fast_profile_buffers_success_noise_and_reports_durations_per_domain(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))

        with redirect_stdout(StringIO()):
            result = run_profile("fast", runner=runner)

        self.assertEqual(result, 0)
        command = runner.call_args_list[0].args[0]
        self.assertIn("-b", command)
        self.assertEqual(command[-2:], ["--durations", "20"])
        self.assertIsInstance(runner.call_args_list[0].kwargs["timeout"], int)

    def test_profile_continues_after_domain_timeout_and_returns_failure(self):
        runner = Mock(
            side_effect=[
                subprocess.TimeoutExpired(["python"], 1),
                *(
                    subprocess.CompletedProcess([], 0)
                    for _ in range(len(FAST_GROUPS) - 1)
                ),
            ]
        )

        with redirect_stdout(StringIO()):
            result = run_profile("fast", runner=runner)

        self.assertEqual(result, 1)
        self.assertEqual(runner.call_count, len(FAST_GROUPS))

    def test_explicit_temp_root_is_forwarded_to_all_temp_environment_names(self):
        parsed = _parser().parse_args(
            ["fast", "--temp-root", r"D:\controlled\rc22-temp"]
        )
        environment = _temp_environment(parsed.temp_root)

        self.assertEqual(
            environment["TEMP"], environment["TMP"],
        )
        self.assertEqual(environment["TMPDIR"], environment["TEMP"])
        self.assertEqual(
            environment["ARBIBUDDY_TEST_TEMP_ROOT"], environment["TEMP"]
        )

    def test_explicit_workbuddy_wheelhouse_is_forwarded_to_child_environment(self):
        parsed = _parser().parse_args(
            ["fast", "--wheelhouse", r"D:\controlled\workbuddy-wheels"]
        )
        with TemporaryDirectory() as temp:
            group_temp = Path(temp) / "group"
            environment = _group_environment(
                FAST_GROUPS[0],
                group_temp=group_temp,
                repo_root=Path(temp) / "repo",
                workbuddy_wheelhouse=parsed.workbuddy_wheelhouse,
            )

        self.assertEqual(
            Path(environment[WORKBUDDY_WHEELHOUSE_ENV]),
            Path(parsed.workbuddy_wheelhouse).resolve(),
        )

    def test_group_environment_injects_test_cleanup_sitecustomize(self):
        with TemporaryDirectory() as temp:
            repo_root = Path(temp)
            (repo_root / "tests").mkdir()
            (repo_root / "tests" / "sitecustomize.py").write_text(
                "# test harness marker\n", encoding="utf-8"
            )
            environment = _group_environment(
                FAST_GROUPS[0],
                group_temp=repo_root / "group",
                repo_root=repo_root,
            )

        self.assertIn(
            str(repo_root / "tests"),
            environment["PYTHONPATH"].split(os.pathsep),
        )

    def test_group_environment_keeps_process_temp_short_but_runtime_root_scoped(self):
        with TemporaryDirectory() as temp:
            repo_root = Path(temp)
            group_temp = repo_root / "arbibuddy-agent_eval-long-group-name"
            environment = _group_environment(
                FAST_GROUPS[0],
                group_temp=group_temp,
                repo_root=repo_root,
            )

        self.assertEqual(Path(environment["TEMP"]), group_temp.parent)
        self.assertEqual(
            Path(environment["ARBIBUDDY_TEST_TEMP_ROOT"]),
            group_temp,
        )

    def test_default_temp_root_is_external_to_the_repository(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            system_temp = Path(temp) / "system-temp"
            root.mkdir()
            runner = Mock(return_value=subprocess.CompletedProcess([], 0))

            with patch(
                "scripts.test_suites.cli.tempfile.gettempdir",
                return_value=str(system_temp),
            ), redirect_stdout(StringIO()):
                result = run_profile("fast", runner=runner, repo_root=root)

            self.assertEqual(result, 0)
            for call in runner.call_args_list:
                group_temp = Path(call.kwargs["env"]["TEMP"])
                self.assertFalse(group_temp.is_relative_to(root))


if __name__ == "__main__":
    unittest.main()
