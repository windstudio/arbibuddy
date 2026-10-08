from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.platform_adapters.service import (
    install_skill,
    update_skill,
    verify_install,
    verify_platform_discovery,
    probe_capabilities,
)
from scripts.version import display_version


ROOT = Path(__file__).resolve().parents[2]


class CodexClaudeFullPathTests(unittest.TestCase):
    def test_codex_discovery_uses_windows_home_fallback(self):
        with TemporaryDirectory() as temp:
            target_root = Path(temp) / "codex-project"
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=target_root,
                source=ROOT,
                mode="copy",
            )
            skill_root = Path(installed["skill_root"])
            calls: list[dict[str, object]] = []

            def fake_run(command, **kwargs):
                calls.append(kwargs)
                if "--version" in command:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="codex-cli 0.test\n",
                        stderr="",
                    )
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"arbibuddy {skill_root.as_posix()}\n",
                    stderr="",
                )

            with (
                patch("scripts.platform_adapters.service.os.name", "nt"),
                patch.dict(
                    os.environ,
                    {"HOME": "", "USERPROFILE": r"C:\Users\runtime-test"},
                    clear=False,
                ),
                patch(
                    "scripts.platform_adapters.service.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                report = verify_platform_discovery(
                    platform="codex",
                    skill_root=skill_root,
                    platform_command=[sys.executable],
                )

            self.assertTrue(report["verified"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(
                all(
                    call["env"]["HOME"] == r"C:\Users\runtime-test"
                    for call in calls
                )
            )
            self.assertTrue(
                all(str(target_root) not in call["env"]["HOME"] for call in calls)
            )

    def test_both_platforms_have_install_update_verify_uninstall_lifecycle(self):
        from scripts.platform_adapters.service import uninstall_skill

        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            for platform in ("codex", "claude-code"):
                with self.subTest(platform=platform):
                    target_root = temp_root / platform
                    target_root.mkdir()
                    sentinel = target_root / "keep-me.txt"
                    sentinel.write_text("unrelated", encoding="utf-8")

                    installed = install_skill(
                        platform=platform,
                        scope="project",
                        target_root=target_root,
                        source=ROOT,
                        mode="copy",
                    )
                    skill_root = Path(installed["skill_root"])
                    self.assertEqual(installed["skill_version"], display_version(ROOT))
                    self.assertFalse(
                        (skill_root / "scripts" / "model_led_agent_eval").exists()
                    )
                    self.assertTrue(verify_install(
                        platform=platform,
                        skill_root=skill_root,
                    )["runtime_identity_verified"])

                    updated = update_skill(
                        platform=platform,
                        scope="project",
                        target_root=target_root,
                        source=ROOT,
                        mode="copy",
                    )
                    self.assertTrue(updated["updated"])
                    self.assertTrue(updated["verified"])
                    self.assertEqual(updated["skill_version"], display_version(ROOT))

                    removed = uninstall_skill(
                        platform=platform,
                        scope="project",
                        target_root=target_root,
                    )
                    self.assertTrue(removed["uninstalled"])
                    self.assertEqual(removed["skill_version"], display_version(ROOT))
                    self.assertFalse(skill_root.exists())
                    self.assertFalse(Path(installed["receipt"]).exists())
                    self.assertTrue(sentinel.is_file())

                    repeated = uninstall_skill(
                        platform=platform,
                        scope="project",
                        target_root=target_root,
                    )
                    self.assertTrue(repeated["already_absent"])

    def test_post_commit_transaction_cleanup_is_a_warning(self):
        with TemporaryDirectory() as temp:
            target_root = Path(temp) / "project"
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=target_root,
                source=ROOT,
                mode="copy",
            )

            with patch(
                "scripts.platform_adapters.service.shutil.rmtree",
                side_effect=OSError("simulated cleanup failure"),
            ):
                report = update_skill(
                    platform="codex",
                    scope="project",
                    target_root=target_root,
                    source=ROOT,
                    mode="copy",
                )

            self.assertTrue(report["updated"])
            self.assertTrue(report["verified"])
            self.assertTrue(report["cleanup_pending"])
            self.assertTrue(report["warnings"])
            self.assertTrue(
                verify_install(
                    platform="codex",
                    skill_root=Path(installed["skill_root"]),
                )["runtime_identity_verified"]
            )

    def test_uninstall_receipt_cleanup_is_a_warning_after_skill_removal(self):
        from scripts.platform_adapters.service import uninstall_skill

        with TemporaryDirectory() as temp:
            target_root = Path(temp) / "project"
            installed = install_skill(
                platform="codex",
                scope="project",
                target_root=target_root,
                source=ROOT,
                mode="copy",
            )
            receipt = Path(installed["receipt"])

            with patch.object(Path, "unlink", side_effect=OSError("locked")):
                report = uninstall_skill(
                    platform="codex",
                    scope="project",
                    target_root=target_root,
                )

            self.assertTrue(report["uninstalled"])
            self.assertTrue(report["cleanup_pending"])
            self.assertFalse(Path(installed["skill_root"]).exists())
            self.assertTrue(receipt.exists())

    def test_shared_core_identity_and_file_presentation_are_public_contracts(self):
        from scripts.platform_adapters.service import present_files

        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            identities = []
            presented = []
            for platform in ("codex", "claude-code"):
                target_root = temp_root / platform
                installed = install_skill(
                    platform=platform,
                    scope="project",
                    target_root=target_root,
                    source=ROOT,
                    mode="copy",
                )
                skill_root = Path(installed["skill_root"])
                verification = verify_install(
                    platform=platform,
                    skill_root=skill_root,
                )
                identities.append(verification["core_identity"])
                presented.append(
                    present_files(
                        files=(
                            skill_root / "SKILL.md",
                            skill_root / "VERSION",
                        )
                    )
                )

            self.assertEqual(identities[0], identities[1])
            for report in presented:
                self.assertTrue(report["displayable"])
                self.assertEqual(
                    [item["name"] for item in report["files"]],
                    ["SKILL.md", "VERSION"],
                )
                self.assertTrue(all(item["exists"] for item in report["files"]))

    def test_capability_probe_records_unicode_path_encoding_and_permissions(self):
        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            version_script = temp_root / "平台版本.py"
            version_script.write_text(
                "import sys\n"
                "assert sys.argv[1:] == ['--version']\n"
                "print('codex 0.test')\n",
                encoding="utf-8",
            )

            for platform in ("codex", "claude-code"):
                with self.subTest(platform=platform):
                    target_root = temp_root / f"项目 工作区-{platform}"
                    installed = install_skill(
                        platform=platform,
                        scope="project",
                        target_root=target_root,
                        source=ROOT,
                        mode="copy",
                    )
                    report = __import__(
                        "scripts.platform_adapters.service",
                        fromlist=["probe_capabilities"],
                    ).probe_capabilities(
                        platform=platform,
                        skill_root=Path(installed["skill_root"]),
                        platform_command=[sys.executable, str(version_script)],
                        workspace=target_root,
                        network_check="skip",
                        network_url="https://www.gov.cn/",
                    )

                    filesystem = report["capabilities"]["filesystem"]
                    self.assertTrue(filesystem["available"])
                    self.assertEqual(filesystem["encoding"], "utf-8")
                    self.assertTrue(filesystem["unicode_path_verified"])
                    self.assertTrue(filesystem["atomic_replace_verified"])
                    self.assertTrue(filesystem["cleanup_verified"])
                    self.assertTrue(filesystem["permissions"]["write"])
                    self.assertTrue(filesystem["permissions"]["read"])

    def test_skill_and_platform_metadata_use_natural_language_trigger(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        codex_metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        claude_metadata = json.loads(
            (ROOT / "adapters" / "claude-code" / "adapter.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("劳动争议", skill)
        self.assertIn("references/platform-capabilities.md", skill)
        for forbidden in ("$arbibuddy", "agent_task", "PublicTurn", "JSON"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, codex_metadata)
        self.assertIn("自然语言", codex_metadata)
        self.assertNotIn("default_prompt", claude_metadata)


if __name__ == "__main__":
    unittest.main()
