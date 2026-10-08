"""Test-process-only Windows cleanup hardening.

The product runtime uses ``scripts.runtime_temp``.  This module is injected
only into test subprocesses by ``scripts.test_suites.cli`` so third-party
handles or Windows Defender races do not turn an otherwise completed test
into ``WinError 145`` during ``TemporaryDirectory`` cleanup.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from time import sleep


if os.name == "nt":
    _shutil_rmtree = shutil.rmtree

    def _is_windows_directory_not_empty(error) -> bool:
        return (
            getattr(error, "winerror", None) == 145
            or getattr(error, "errno", None) == 145
        )

    def _clear_rmtree_residue(path) -> None:
        """Best-effort bottom-up retry for a transient Windows directory lock."""

        for current, directories, files in os.walk(path, topdown=False):
            current_path = os.fspath(current)
            for filename in files:
                try:
                    os.unlink(os.path.join(current_path, filename))
                except FileNotFoundError:
                    pass
            for directory in directories:
                try:
                    os.rmdir(os.path.join(current_path, directory))
                except (FileNotFoundError, OSError) as error:
                    if not _is_windows_directory_not_empty(error):
                        raise

    def _shutil_rmtree_with_windows_retry(path, *args, **kwargs):
        for attempt in range(6):
            try:
                return _shutil_rmtree(path, *args, **kwargs)
            except OSError as error:
                if not _is_windows_directory_not_empty(error) or attempt == 5:
                    raise
                _clear_rmtree_residue(path)
                sleep(0.05)

    shutil.rmtree = _shutil_rmtree_with_windows_retry

    _rmtree = tempfile.TemporaryDirectory._rmtree

    def _rmtree_with_windows_retry(
        cls,
        name,
        ignore_errors=False,
        repeated=False,
    ) -> None:
        for attempt in range(3):
            try:
                _rmtree(name, ignore_errors, repeated)
                return
            except OSError as error:
                if getattr(error, "winerror", None) != 145 or attempt == 2:
                    raise
                sleep(0.05)

    tempfile.TemporaryDirectory._rmtree = classmethod(_rmtree_with_windows_retry)
