from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.platform_adapters.service import _probe_attachment_presentation


class AttachmentPresentationProbeTests(unittest.TestCase):
    def test_readable_file_manifest_does_not_claim_host_presentation(self):
        with TemporaryDirectory() as temporary_root:
            report = _probe_attachment_presentation(Path(temporary_root))

        self.assertFalse(
            report["available"],
            "本地文件清单可读不能证明宿主已经展示附件",
        )
        self.assertTrue(report["file_manifest_readable"])
        self.assertFalse(report["host_presentation_verified"])
        self.assertIn("宿主", report["method"])


if __name__ == "__main__":
    unittest.main()
