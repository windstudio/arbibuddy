from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ModelLedPlatformDocumentRouteTests(unittest.TestCase):
    def test_codex_claude_guide_routes_model_documents_to_issue_08_public_seam(self):
        guide = (ROOT / "docs" / "platforms" / "codex-claude-code.md").read_text(
            encoding="utf-8"
        )

        route_match = re.search(
            r"文书生成只能通过 (?P<route>`document\.render-v1` 的 "
            r"`scripts\.documents\.public\.render\(request, workspace_root\)`，"
            r"使用 10 个顶层章节的 `CaseArchive`)",
            guide,
        )
        self.assertIsNotNone(route_match, guide)
        assert route_match is not None
        self.assertNotIn("scripts.documents.cli", route_match.group("route"))

        self.assertNotIn("scripts.documents.cli", guide)



if __name__ == "__main__":
    unittest.main()
