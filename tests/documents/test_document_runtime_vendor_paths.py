from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(os.name == "nt", "Windows extended paths only")
class DocumentRuntimeVendorPathTests(unittest.TestCase):
    def test_packaged_vendor_can_open_relative_template(self):
        with TemporaryDirectory() as temporary_root:
            vendor = Path(temporary_root) / "site-packages"
            docx = vendor / "docx"
            lxml = vendor / "lxml"
            templates = vendor / "templates"
            for directory in (docx, lxml, templates):
                directory.mkdir(parents=True)
            (docx / "__init__.py").write_text(
                "from pathlib import Path\n"
                "def read_footer():\n"
                "    return (Path(__file__).parent / '..' / 'templates' / 'default-footer.xml').read_text()\n",
                encoding="utf-8",
            )
            (lxml / "__init__.py").write_text("", encoding="utf-8")
            (templates / "default-footer.xml").write_text("footer", encoding="utf-8")
            script = (
                "import sys; "
                "from scripts.platform_paths import path_for_io; "
                "from scripts.documents.runtime_dependencies import _load_vendor; "
                "_load_vendor(path_for_io(sys.argv[1])); "
                "import docx; print(docx.read_footer())"
            )

            completed = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-c", script, str(vendor)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).resolve().parents[2],
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "footer")
