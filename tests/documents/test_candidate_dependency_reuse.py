from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import unittest

from scripts.case_archive import CaseArchive
from scripts.documents.public import create_delivery_set, render
from scripts.model_led_agent_eval.artifact_oracle import inspect_public_artifacts
from scripts.documents.view import managed_delivery_view
from scripts.workbuddy_acceptance.service import _audit_revision_timeline
from tests.documents.test_model_led_document_render import (
    _application_request, _create_archive, _evidence_request,
)


class CandidateDependencyReuseTests(unittest.TestCase):
    def _bundle(self, workspace, mode="candidate"):
        archive, case_id, revision = _create_archive(workspace)
        control = create_delivery_set({
            "case_id": case_id, "archive_revision": revision, "mode": mode,
            "requested_templates": ["labor-arbitration-application"],
            "confirmation_refs": [],
        }, workspace)
        self.assertTrue(control["ok"], control)
        self._render_receipts = []
        self._requests = []
        for factory in (_application_request, _evidence_request):
            request = factory(case_id, revision)
            request.update(mode=mode, confirmation_refs=[],
                           delivery_set_id=control["result"]["delivery_set_id"])
            result = render(request, workspace)
            self.assertTrue(result["ok"], result)
            self._render_receipts.append(result)
            self._requests.append(request)
        return archive, case_id, revision

    def _append(self, archive, case_id, revision, record_type, content):
        result = archive.commit({
            "case_id": case_id, "expected_revision": revision,
            "change_summary": "记录后续补充",
            "changes": [{"operation": "append", "record_type": record_type,
                         "content_markdown": content}],
        })
        self.assertTrue(result["ok"], result)
        return result["result"]["revision"]

    def test_unreferenced_followup_preserves_candidate_files_and_generation_revision(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = self._bundle(workspace)
            before = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(before["stopped"], before)
            paths = [Path(item["path"]) for item in before["presentation_files"]]
            originals = {path: path.read_bytes() for path in paths}
            current = self._append(archive, case_id, revision, "next_step",
                                   "保留当前候选稿；用户重申不会要求代签、代发或上传。")
            after = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(after["stopped"], after)
            self.assertEqual(after["delivery_set"]["archive_revision"], revision)
            self.assertEqual(after["current_archive_revision"], current)
            self.assertTrue(after["archive_dependencies_verified"])
            self.assertEqual(originals, {path: path.read_bytes() for path in paths})
            observed = inspect_public_artifacts(workspace)
            self.assertEqual(observed["invalid_delivery_count"], 0, observed)
            self.assertEqual(len(observed["deliveries"]), 2)
            self.assertTrue(all(item["archive_dependencies_verified"]
                                for item in observed["deliveries"]))

    def test_every_business_record_change_invalidates_candidate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            seed = root / "seed"
            _, case_id, revision = self._bundle(seed)
            for record_type in ("fact", "claim", "evidence", "analysis", "calculation",
                                "authority", "risk", "confirmation"):
                with self.subTest(record_type=record_type):
                    workspace = root / record_type
                    shutil.copytree(seed, workspace)
                    self._append(CaseArchive(workspace), case_id, revision, record_type,
                                 "新收到的业务信息，尚待核实。")
                    view = managed_delivery_view(workspace=workspace, case_id=case_id)
                    self.assertTrue(view["stopped"], view)
                    self.assertEqual(view["presentation_files"], [])
                    observed = inspect_public_artifacts(workspace)
                    self.assertEqual(observed["deliveries"], [])
                    self.assertEqual(observed["invalid_delivery_count"], 2)

    def test_external_final_remains_bound_to_exact_revision(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = self._bundle(workspace, "external_final")
            self._append(archive, case_id, revision, "next_step", "等用户预览文件。")
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertTrue(view["stopped"], view)
            self.assertEqual(inspect_public_artifacts(workspace)["deliveries"], [])

    def test_referenced_next_step_is_a_dependency(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = _create_archive(workspace)
            revision = self._append(archive, case_id, revision, "next_step",
                                    "文书标题：劳动人事争议仲裁申请书。")
            control = create_delivery_set({
                "case_id": case_id, "archive_revision": revision, "mode": "candidate",
                "requested_templates": ["labor-arbitration-application"],
                "confirmation_refs": [],
            }, workspace)
            self.assertTrue(control["ok"], control)
            for factory in (_application_request, _evidence_request):
                request = factory(case_id, revision)
                request.update(mode="candidate", confirmation_refs=[],
                               delivery_set_id=control["result"]["delivery_set_id"])
                if factory is _application_request:
                    request["locked_bindings"][0]["archive_record_ref"] = "N-001"
                self.assertTrue(render(request, workspace)["ok"])
            committed = archive.commit({
                "case_id": case_id, "expected_revision": revision,
                "change_summary": "修改被文书引用的下一步",
                "changes": [{"operation": "replace", "record_type": "next_step",
                             "record_id": "N-001",
                             "reason": "用户修正拟起草文书标题",
                             "content_markdown": "改为起草其他文书，原标题不再适用。"}],
            })
            self.assertTrue(committed["ok"], committed)
            self.assertTrue(managed_delivery_view(workspace=workspace, case_id=case_id)["stopped"])
            observed = inspect_public_artifacts(workspace)
            self.assertEqual(observed["invalid_delivery_count"], 1, observed)

    def test_legacy_manifest_without_dependency_proof_stays_strict(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = self._bundle(workspace)
            output = workspace / ".arbibuddy" / "cases" / case_id / "output"
            for path in output.rglob("*.json"):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                del manifest["archive_dependencies"]
                path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            self._append(archive, case_id, revision, "next_step", "保留候选稿。")
            self.assertTrue(managed_delivery_view(workspace=workspace, case_id=case_id)["stopped"])
            self.assertEqual(inspect_public_artifacts(workspace)["deliveries"], [])

    def test_modified_dependency_hash_is_rejected_even_at_original_revision(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            _, case_id, _ = self._bundle(workspace)
            path = Path(self._render_receipts[0]["result"]["machine_manifest"])
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["archive_dependencies"]["business_sha256"] = "0" * 64
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertTrue(view["stopped"], view)
            self.assertEqual(view["presentation_files"], [])
            self.assertEqual(inspect_public_artifacts(workspace)["invalid_delivery_count"], 1)
            replay = render(self._requests[0], workspace)
            self.assertFalse(replay["ok"], replay)

    def test_legacy_candidate_regeneration_acquires_dependency_proof(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = self._bundle(workspace)
            for receipt, request in zip(self._render_receipts, self._requests):
                path = Path(receipt["result"]["machine_manifest"])
                manifest = json.loads(path.read_text(encoding="utf-8"))
                del manifest["archive_dependencies"]
                path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                rerendered = render(request, workspace)
                self.assertTrue(rerendered["ok"], rerendered)
                self.assertIn("archive_dependencies", json.loads(path.read_text(encoding="utf-8")))
            self._append(archive, case_id, revision, "next_step", "保留候选稿。")
            self.assertFalse(managed_delivery_view(workspace=workspace, case_id=case_id)["stopped"])

    def test_presentation_timeline_accepts_verified_reuse_but_rejects_commit_after_view(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            archive, case_id, revision = self._bundle(workspace)
            current = self._append(archive, case_id, revision, "next_step", "保留候选稿。")
            view = managed_delivery_view(workspace=workspace, case_id=case_id)
            self.assertFalse(view["stopped"], view)
            records = []
            for i, receipt in enumerate(self._render_receipts):
                records.append({"type": "function_call_result", "call_id": f"render{i}",
                                "name": "document.render-v1", "status": "success",
                                "result": receipt})
            commit_record = {"type": "function_call_result", "name": "CaseArchive.commit",
                             "status": "success", "result": {
                                 "contract_version": "case-archive-v1", "operation": "commit",
                                 "ok": True, "result": {"case_id": case_id, "revision": current}}}
            records.extend([{"type": "function_call", "id": "commit",
                             "name": "CaseArchive.commit",
                             "arguments": {"case_id": case_id, "expected_revision": revision}},
                            commit_record,
                            {"type": "function_call", "id": "view", "name": "Bash",
                             "arguments": {"command": "python -m scripts.documents.runtime_cli view --json request"}},
                            {"type": "function_call_result", "call_id": "view",
                             "name": "Bash", "status": "success", "result": view},
                            {"type": "function_call", "name": "present_files",
                             "arguments": view["present_files_arguments"]}])
            timeline = _audit_revision_timeline(list(enumerate(records)),
                                               workspace=workspace, case_id=case_id)
            self.assertEqual(timeline["invalid_events"], [], timeline)
            self.assertEqual(len(timeline["historical_presentations"]), 1, timeline)
            # Even a valid reuse receipt cannot cover a later commit.
            records.insert(-1, commit_record)
            later = _audit_revision_timeline(list(enumerate(records)),
                                            workspace=workspace, case_id=case_id)
            self.assertTrue(later["invalid_events"], later)


if __name__ == "__main__":
    unittest.main()
