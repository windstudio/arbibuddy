"""Real Windows open-file rejection and safe retry at public document.render."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.documents.public import render
from scripts.documents.view import managed_delivery_view
from scripts.platform_paths import absolute_path_for_io
from tests.documents.test_model_led_document_render import _create_archive, _application_request


@unittest.skipUnless(os.name == 'nt', 'real Windows file-sharing behavior')
class PublicOverwriteRecoveryTests(unittest.TestCase):
    def test_open_document_rejection_preserves_all_old_artifacts_and_retry_recovers(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        for shares in (1, 7):
            with self.subTest(share_flags=shares), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision = _create_archive(workspace)
                request = _application_request(case_id, revision, mode='candidate')
                original = render(request, workspace)
                self.assertTrue(original['ok'], original)
                payload = original['result']
                paths = [Path(payload[key]) for key in ('canonical_docx', 'verification_checklist', 'machine_manifest')]
                before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
                facts = next(section for section in request['sections'] if section['heading'] == '事实与理由')
                facts['full_text'] += '\n本候选稿供再次人工核对。'
                handle = kernel.CreateFileW(str(absolute_path_for_io(paths[0])), 0x80000000, shares, None, 3, 0x80, None)
                self.assertNotEqual(handle, wintypes.HANDLE(-1).value, 'probe must actually hold the DOCX open')
                try:
                    rejected = render(request, workspace)
                    self.assertFalse(rejected['ok'], rejected)
                    self.assertIn('permission_denied', {item['code'] for item in rejected['errors']})
                    self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}, 'all old canonical artifacts must remain byte-identical')
                    during = managed_delivery_view(workspace=workspace, case_id=case_id)
                    self.assertEqual(during['delivery_state'], 'candidate_ready', during)
                finally:
                    self.assertTrue(kernel.CloseHandle(handle))
                recovered = render(request, workspace)
                self.assertTrue(recovered['ok'], recovered)
                self.assertEqual(recovered['result']['canonical_docx'], str(paths[0]))
                self.assertNotEqual(before[str(paths[0])], hashlib.sha256(paths[0].read_bytes()).hexdigest())
                viewed = managed_delivery_view(workspace=workspace, case_id=case_id)
                self.assertEqual(viewed['delivery_state'], 'candidate_ready', viewed)
                repeated = render(request, workspace)
                self.assertTrue(repeated['ok'], repeated)
                self.assertEqual(repeated['result']['canonical_docx'], recovered['result']['canonical_docx'])
                self.assertEqual(repeated['result']['content_digest'], recovered['result']['content_digest'])


if __name__ == '__main__':
    unittest.main()
