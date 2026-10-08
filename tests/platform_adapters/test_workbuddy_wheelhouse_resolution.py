from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.platform_adapters.service import (
    WORKBUDDY_WHEELHOUSE_ENV,
    resolve_workbuddy_wheelhouse,
)


class WorkBuddyWheelhouseResolutionTests(unittest.TestCase):
    def test_explicit_argument_wins_over_environment_and_lock_default(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            explicit = Path(temp) / "explicit"
            environment_path = Path(temp) / "environment"
            lock = {"wheelhouse": "references/workbuddy-wheels"}
            environment = {WORKBUDDY_WHEELHOUSE_ENV: str(environment_path)}

            resolved = resolve_workbuddy_wheelhouse(
                source,
                wheelhouse=explicit,
                environment=environment,
                lock=lock,
            )

            self.assertEqual(resolved, explicit.resolve())

    def test_environment_wins_over_lock_default(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            environment_path = Path(temp) / "environment"
            lock = {"wheelhouse": "references/workbuddy-wheels"}

            resolved = resolve_workbuddy_wheelhouse(
                source,
                environment={WORKBUDDY_WHEELHOUSE_ENV: str(environment_path)},
                lock=lock,
            )

            self.assertEqual(resolved, environment_path.resolve())

    def test_lock_default_is_relative_to_the_skill_source(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            lock = {"wheelhouse": "references/workbuddy-wheels"}

            resolved = resolve_workbuddy_wheelhouse(
                source,
                environment={},
                lock=lock,
            )

            self.assertEqual(
                resolved,
                (source / "references" / "workbuddy-wheels").resolve(),
            )

    def test_lock_default_rejects_absolute_and_traversal_paths(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            for declared in ("/outside", "../outside"):
                with self.subTest(declared=declared):
                    with self.assertRaisesRegex(
                        ValueError, "wheelhouse 路径不安全"
                    ):
                        resolve_workbuddy_wheelhouse(
                            source,
                            environment={},
                            lock={"wheelhouse": declared},
                        )


if __name__ == "__main__":
    unittest.main()
