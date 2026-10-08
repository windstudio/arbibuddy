from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.documents import delivery as delivery_module
from scripts.documents.delivery import (
    commit_generation_staging,
)


class Rc14DeliveryTransactionTests(unittest.TestCase):
    def _paths(self, temp: str) -> tuple[Path, Path, Path]:
        root = Path(temp) / ".arbibuddy" / "cases"
        case_id = "case-aaaaaaaaaaaaaaaaaaaaaaaa"
        output = root / case_id / "output" / "仲裁申请书"
        output.parent.mkdir(parents=True)
        return root, output, output.parent

    def test_generation_staging_promotion_retains_old_backup_and_is_durable(self):
        with TemporaryDirectory() as temp:
            root, output, parent = self._paths(temp)
            output.mkdir()
            (output / "document.docx").write_bytes(b"old")
            staging = parent / ".仲裁申请书.staging-new"
            staging.mkdir()
            (staging / "document.docx").write_bytes(b"new")
            (staging / "manifest.json").write_text("{}", encoding="utf-8")

            commit_generation_staging(
                case_root=root,
                case_id="case-aaaaaaaaaaaaaaaaaaaaaaaa",
                output_dir=output,
                staging_dir=staging,
                transaction_id="a" * 64,
                replace_existing=True,
            )

            self.assertEqual((output / "document.docx").read_bytes(), b"new")
            archives = list(parent.glob(".仲裁申请书.committed-backup-a*-*"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(
                (archives[0] / "document.docx").read_bytes(),
                b"old",
            )
            self.assertFalse(staging.exists())

    def test_generation_rename_failure_keeps_old_or_complete_new_directory(self):
        with TemporaryDirectory() as temp:
            root, output, parent = self._paths(temp)
            output.mkdir()
            (output / "document.docx").write_bytes(b"old")
            staging = parent / ".仲裁申请书.staging-failure"
            staging.mkdir()
            (staging / "document.docx").write_bytes(b"new")
            (staging / "manifest.json").write_text("{}", encoding="utf-8")
            original = delivery_module._replace_generation_child

            def fail_staging(source: Path, target: Path) -> None:
                if source == staging:
                    raise OSError("simulated generation rename failure")
                original(source, target)

            with patch(
                "scripts.documents.delivery._replace_generation_child",
                side_effect=fail_staging,
            ):
                with self.assertRaisesRegex(OSError, "generation rename failure"):
                    commit_generation_staging(
                        case_root=root,
                        case_id="case-aaaaaaaaaaaaaaaaaaaaaaaa",
                        output_dir=output,
                        staging_dir=staging,
                        transaction_id="b" * 64,
                        replace_existing=True,
                    )

            self.assertEqual((output / "document.docx").read_bytes(), b"old")
            self.assertTrue(staging.is_dir())
            self.assertEqual(list(parent.glob(".仲裁申请书.regeneration-*")), [])




if __name__ == "__main__":
    unittest.main()
