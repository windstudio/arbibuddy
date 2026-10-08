import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import sys
import unittest
from urllib.error import URLError
from unittest.mock import patch

from scripts.platform_adapters.cli import main
from scripts.version import display_version, read_version


ROOT = Path(__file__).resolve().parents[2]
DISPLAY_VERSION = display_version(ROOT)
RAW_VERSION = read_version(ROOT)


def run_cli(arguments: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def prepare_probe(root: Path, platform: str) -> tuple[Path, list[str]]:
    project = root / "project"
    project.mkdir()
    code, output, error = run_cli(
        [
            "install",
            "--platform",
            platform,
            "--scope",
            "project",
            "--target-root",
            str(project),
            "--source",
            str(ROOT),
            "--mode",
            "copy",
        ]
    )
    if code != 0:
        raise AssertionError(error)
    skill_root = Path(json.loads(output)["skill_root"])
    version_script = root / "platform_version.py"
    version_script.write_text(
        "import sys\n"
        "if sys.argv[1:] != ['--version']:\n"
        "    raise SystemExit(2)\n"
        f"print({platform + ' 0.test'!r})\n",
        encoding="utf-8",
    )
    return skill_root, [sys.executable, str(version_script)]


def prepare_discovery_cli(
    root: Path, platform: str, skill_root: Path
) -> list[str]:
    script = root / f"{platform}-discovery.py"
    if platform == "codex":
        body = (
            "import json, sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('codex 0.test')\n"
            "elif len(sys.argv) == 6 and sys.argv[1] == '-C' "
            "and sys.argv[3:] == ['debug', 'prompt-input', "
            "'我想梳理一件劳动争议并准备仲裁材料。']:\n"
            f"    print(json.dumps({{'skill': 'arbibuddy', 'path': {skill_root.as_posix()!r}}}))\n"
            "else:\n"
            "    raise SystemExit(2)\n"
        )
    else:
        body = (
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('2.1.117 (Claude Code)')\n"
            "elif sys.argv[1:] == ['--bare', '--help']:\n"
            "    print('Skills still resolve via /skill-name.')\n"
            "else:\n"
            "    raise SystemExit(2)\n"
        )
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


class PlatformAdapterCliTests(unittest.TestCase):
    def test_capability_report_observes_availability_without_business_decisions(self):
        capabilities = {name: False for name in ("filesystem", "network", "scripts", "word", "structure_inspection")}
        from scripts.platform_adapters.service import evaluate_capabilities
        report = evaluate_capabilities("codex", capabilities)
        self.assertEqual(report, {"platform": "codex", "capabilities": capabilities})

    def test_codex_and_claude_code_install_and_discover_same_core_skill(self):
        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            installed_skills: dict[str, Path] = {}

            for platform in ("codex", "claude-code"):
                with self.subTest(platform=platform):
                    project = temp_root / platform
                    project.mkdir()
                    code, output, error = run_cli(
                        [
                            "install",
                            "--platform",
                            platform,
                            "--scope",
                            "project",
                            "--target-root",
                            str(project),
                            "--source",
                            str(ROOT),
                            "--mode",
                            "copy",
                        ]
                    )
                    self.assertEqual(code, 0, error)
                    install_report = json.loads(output)
                    skill_root = Path(install_report["skill_root"])
                    installed_skills[platform] = skill_root

                    code, output, error = run_cli(
                        [
                            "verify-install",
                            "--platform",
                            platform,
                            "--skill-root",
                            str(skill_root),
                        ]
                    )
                    self.assertEqual(code, 0, error)
                    verification = json.loads(output)
                    self.assertTrue(verification["discovery_contract_valid"])
                    self.assertEqual(verification["skill_name"], "arbibuddy")
                    self.assertTrue(verification["resources_complete"])
                    self.assertEqual(verification["skill_version"], DISPLAY_VERSION)
                    self.assertEqual(
                        (skill_root / "VERSION").read_text(encoding="utf-8").strip(),
                        RAW_VERSION,
                    )

            codex_skill = installed_skills["codex"]
            claude_skill = installed_skills["claude-code"]
            self.assertEqual(
                (codex_skill / "SKILL.md").read_bytes(),
                (claude_skill / "SKILL.md").read_bytes(),
            )
            self.assertEqual(
                (codex_skill / "references" / "document-render.md").read_bytes(),
                (claude_skill / "references" / "document-render.md").read_bytes(),
            )

    def test_copy_install_can_run_verifier_from_installed_skill_itself(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, _ = prepare_probe(root, "codex")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.platform_adapters.cli",
                    "verify-install",
                    "--platform",
                    "codex",
                    "--skill-root",
                    str(skill_root),
                ],
                cwd=skill_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["discovery_contract_valid"])

    def test_claude_code_link_install_keeps_discovery_path_and_receipt_verifiable(self):
        with TemporaryDirectory() as temp:
            capability_root = Path(temp) / "symlink-capability"
            capability_target = capability_root / "target"
            capability_link = capability_root / "link"
            capability_target.mkdir(parents=True)
            try:
                capability_link.symlink_to(
                    capability_target,
                    target_is_directory=True,
                )
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest(
                        "当前 Windows 进程无创建目录符号链接特权"
                    )
                raise
            else:
                capability_link.unlink()

            project = Path(temp) / "project"
            code, output, error = run_cli(
                [
                    "install",
                    "--platform",
                    "claude-code",
                    "--scope",
                    "project",
                    "--target-root",
                    str(project),
                    "--source",
                    str(ROOT),
                    "--mode",
                    "link",
                ]
            )

            self.assertEqual(code, 0, error)
            skill_root = Path(json.loads(output)["skill_root"])
            self.assertTrue(skill_root.is_symlink())
            self.assertEqual(skill_root.parent, project.resolve() / ".claude" / "skills")

            code, output, error = run_cli(
                [
                    "verify-install",
                    "--platform",
                    "claude-code",
                    "--skill-root",
                    str(skill_root),
                ]
            )
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["discovery_contract_valid"])

    def test_platform_discovery_evidence_distinguishes_runtime_from_contract(self):
        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            for platform in ("codex", "claude-code"):
                with self.subTest(platform=platform):
                    platform_root = temp_root / platform
                    platform_root.mkdir()
                    skill_root, _ = prepare_probe(platform_root, platform)
                    command = prepare_discovery_cli(platform_root, platform, skill_root)

                    code, output, error = run_cli(
                        [
                            "verify-platform",
                            "--platform",
                            platform,
                            "--skill-root",
                            str(skill_root),
                            "--platform-command",
                            *command,
                        ]
                    )

                    self.assertEqual(code, 0, error)
                    evidence = json.loads(output)
                    self.assertTrue(evidence["verified"])
                    if platform == "codex":
                        self.assertEqual(evidence["level"], "runtime-discovery")
                        self.assertTrue(evidence["runtime_discovered"])
                    else:
                        self.assertEqual(evidence["level"], "local-platform-contract")
                        self.assertFalse(evidence["runtime_discovered"])

    def test_verify_platform_defaults_claude_code_to_claude_cli(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, _ = prepare_probe(root, "claude-code")
            with patch(
                "scripts.platform_adapters.cli.verify_platform_discovery",
                return_value={"verified": True},
            ) as verify:
                code, output, error = run_cli(
                    [
                        "verify-platform",
                        "--platform",
                        "claude-code",
                        "--skill-root",
                        str(skill_root),
                    ]
                )

            self.assertEqual(code, 0, error)
            self.assertEqual(
                verify.call_args.kwargs["platform_command"],
                ["claude"],
            )

    def test_probe_defaults_claude_code_to_claude_cli(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, _ = prepare_probe(root, "claude-code")
            with patch(
                "scripts.platform_adapters.cli.probe_capabilities",
                return_value={"platform": "claude-code"},
            ) as probe:
                code, output, error = run_cli(
                    [
                        "probe",
                        "--platform",
                        "claude-code",
                        "--skill-root",
                        str(skill_root),
                        "--workspace",
                        str(root),
                    ]
                )

            self.assertEqual(code, 0, error)
            self.assertEqual(
                probe.call_args.kwargs["platform_command"],
                ["claude"],
            )

    def test_verify_install_rejects_receipt_not_bound_to_discovery_root(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, _ = prepare_probe(root, "codex")
            receipt_path = skill_root.parent / "arbibuddy.install.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["target_root"] = str(root / "different-project")
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False), encoding="utf-8"
            )

            code, output, error = run_cli(
                [
                    "verify-install",
                    "--platform",
                    "codex",
                    "--skill-root",
                    str(skill_root),
                ]
            )

            self.assertEqual(code, 2, error)
            self.assertFalse(json.loads(output)["discovery_contract_valid"])

    def test_probe_records_version_method_capabilities_and_test_date(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, platform_command = prepare_probe(root, "codex")
            code, output, error = run_cli(
                [
                    "probe",
                    "--platform",
                    "codex",
                    "--skill-root",
                    str(skill_root),
                    "--platform-command",
                    *platform_command,
                    "--workspace",
                    str(root),
                    "--network-check",
                    "skip",
                ]
            )

            self.assertEqual(code, 0, error)
            report = json.loads(output)
            self.assertEqual(report["platform"], "codex")
            self.assertEqual(report["platform_version"], "codex 0.test")
            self.assertEqual(report["install_method"], "project-copy")
            self.assertRegex(report["tested_at"], r"^\d{4}-\d{2}-\d{2}T")
            self.assertTrue(report["installation"]["discovery_contract_valid"])
            self.assertTrue(report["capabilities"]["filesystem"]["available"])
            self.assertTrue(report["capabilities"]["scripts"]["available"])
            self.assertTrue(report["capabilities"]["word"]["available"])
            self.assertEqual(report["capabilities"]["network"]["state"], "未测试")
            self.assertNotIn("decisions", report)

    def test_probe_exposes_recoverable_network_permission_diagnostic(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, platform_command = prepare_probe(root, "codex")
            with patch(
                "scripts.platform_adapters.service.urlopen",
                side_effect=URLError(
                    PermissionError(13, "network permission denied")
                ),
            ):
                code, output, error = run_cli(
                    [
                        "probe",
                        "--platform",
                        "codex",
                        "--skill-root",
                        str(skill_root),
                        "--platform-command",
                        *platform_command,
                        "--workspace",
                        str(root),
                        "--network-check",
                        "official-source",
                        "--network-url",
                        "https://www.gov.cn/",
                    ]
                )

            self.assertEqual(code, 0, error)
            report = json.loads(output)
            network = report["capabilities"]["network"]
            self.assertFalse(network["available"])
            self.assertEqual(network["state"], "需要宿主联网权限")
            self.assertEqual(network["error_code"], "network_permission_required")
            self.assertEqual(
                network["next_action"],
                "request-network-permission-and-retry-same-command",
            )
            self.assertEqual(network["details"]["cause_type"], "URLError")

    def test_probe_continues_when_desktop_client_denies_self_launch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root, platform_command = prepare_probe(root, "codex")
            with patch(
                "scripts.platform_adapters.service._run_platform_command",
                side_effect=PermissionError("desktop sandbox denied self-launch"),
            ):
                code, output, error = run_cli([
                    "probe", "--platform", "codex",
                    "--skill-root", str(skill_root),
                    "--platform-command", *platform_command,
                    "--workspace", str(root), "--network-check", "skip",
                ])

            self.assertEqual(code, 0, error)
            report = json.loads(output)
            self.assertEqual(report["platform_version"], "unavailable")
            self.assertFalse(report["platform_version_probe"]["available"])
            self.assertIn("PermissionError", report["platform_version_probe"]["detail"])
            self.assertTrue(report["capabilities"]["filesystem"]["available"])
            self.assertTrue(report["capabilities"]["scripts"]["available"])

    def test_platform_metadata_does_not_duplicate_core_legal_workflows(self):
        forbidden = ("高风险动作门槛", "倾向支持", "倾向不支持", "劳动用工义务催告函")
        adapter_files = tuple((ROOT / "adapters").rglob("*.json"))
        self.assertGreaterEqual(len(adapter_files), 3)
        for path in adapter_files:
            text = path.read_text(encoding="utf-8")
            for phrase in forbidden:
                with self.subTest(path=path, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_core_skill_routes_to_shared_capability_contract_and_install_guides(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        contract_path = ROOT / "references" / "platform-capabilities.md"
        guide_path = ROOT / "docs" / "platforms" / "codex-claude-code.md"

        self.assertIn("references/platform-capabilities.md", skill)
        self.assertTrue(contract_path.is_file())
        contract = contract_path.read_text(encoding="utf-8")
        for phrase in (
            "filesystem",
            "network",
            "scripts",
            "structure_inspection",
        ):
            self.assertIn(phrase, contract)

        self.assertTrue(guide_path.is_file())
        guide = guide_path.read_text(encoding="utf-8")
        self.assertIn(".agents/skills/arbibuddy", guide)
        self.assertIn(".claude/skills/arbibuddy", guide)
        self.assertIn("verify-install", guide)
        self.assertIn("verify-platform", guide)
        self.assertIn("probe", guide)

    def test_real_platform_record_distinguishes_documented_and_observed_support(self):
        record_path = (
            ROOT
            / "docs"
            / "platforms"
            / "compatibility"
            / "codex-claude-code-2026-07-25.json"
        )
        self.assertTrue(record_path.is_file())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["test_date"], "2026-07-25")
        self.assertEqual({item["platform"] for item in record["platforms"]}, {"codex", "claude-code"})
        for item in record["platforms"]:
            with self.subTest(platform=item["platform"]):
                self.assertTrue(item["official_documentation"]["verified"])
                self.assertEqual(item["install_test"]["method"], "project-copy")
                self.assertTrue(item["install_test"]["discovery_contract_valid"])
                self.assertTrue(item["install_test"]["resources_complete"])
                self.assertTrue(item["platform_discovery"]["verified"])
                if item["platform"] == "codex":
                    self.assertTrue(item["platform_discovery"]["runtime_discovered"])
                else:
                    self.assertFalse(item["platform_discovery"]["runtime_discovered"])
                self.assertEqual(
                    set(item["capabilities"]),
                    {"filesystem", "network", "scripts", "word", "structure_inspection"},
                )
                self.assertEqual(item["capabilities"]["structure_inspection"]["state"], "可用")
                self.assertEqual(item["outcomes"]["documents"], "完整")
                self.assertEqual(
                    set(item["degradation_tests"]),
                    {"no_filesystem", "no_network", "no_scripts", "no_word", "no_structure_inspection"},
                )


if __name__ == "__main__":
    unittest.main()
