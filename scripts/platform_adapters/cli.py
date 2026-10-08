from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from scripts.cli_encoding import configure_utf8_stdio
from .service import (
    SUPPORTED_PLATFORMS,
    PlatformAdapterError,
    build_artifact,
    evaluate_capabilities,
    identify_installation,
    install_skill,
    inspect_skill_source,
    load_capabilities,
    probe_capabilities,
    reap_workbuddy_cleanup,
    present_files,
    uninstall_skill,
    update_skill,
    verify_artifact,
    verify_core_contract,
    verify_install,
    verify_platform_discovery,
)


def _update_mode(value: str) -> str:
    if value != "copy":
        raise argparse.ArgumentTypeError(
            "update 只支持 mode=copy；需要链接安装请重新执行 install --mode link"
        )
    return value


def _default_platform_command(platform: str) -> list[str]:
    return ["claude"] if platform == "claude-code" else [platform]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arbibuddy-platform")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    evaluate.add_argument("--capabilities", type=Path, required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    install.add_argument("--scope", choices=("project", "user"), required=True)
    install.add_argument("--target-root", type=Path, required=True)
    install_source = install.add_mutually_exclusive_group(required=True)
    install_source.add_argument("--source", type=Path)
    install_source.add_argument("--artifact", type=Path)
    install.add_argument("--mode", choices=("copy", "link"), required=True)
    install.add_argument(
        "--runtime-python",
        "--workbuddy-runtime-python",
        "--target-python",
        dest="workbuddy_runtime_python",
        type=Path,
        help="WorkBuddy 源安装使用的目标 Python",
    )
    install.add_argument(
        "--wheelhouse",
        type=Path,
        help="WorkBuddy 源安装使用的锁定 wheelhouse；缺失时失败，不联网下载",
    )

    update = subparsers.add_parser("update")
    update.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    update.add_argument("--scope", choices=("project", "user"), required=True)
    update.add_argument("--target-root", type=Path, required=True)
    update_source = update.add_mutually_exclusive_group(required=True)
    update_source.add_argument("--source", type=Path)
    update_source.add_argument("--artifact", type=Path)
    update.add_argument(
        "--mode",
        type=_update_mode,
        required=True,
        help="更新只支持 copy；需要链接请重新执行 install --mode link",
    )
    update.add_argument(
        "--runtime-python",
        "--workbuddy-runtime-python",
        "--target-python",
        dest="workbuddy_runtime_python",
        type=Path,
        help="WorkBuddy 源更新使用的目标 Python",
    )
    update.add_argument(
        "--wheelhouse",
        type=Path,
        help="WorkBuddy 源更新使用的锁定 wheelhouse；缺失时失败，不联网下载",
    )

    inspect_source = subparsers.add_parser("inspect-source")
    inspect_source.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    inspect_source.add_argument("--source", type=Path, required=True)

    build = subparsers.add_parser("build-artifact")
    build.add_argument("--platform", choices=("workbuddy",), required=True)
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--runtime-python",
        "--workbuddy-runtime-python",
        "--target-python",
        dest="workbuddy_runtime_python",
        type=Path,
        help="WorkBuddy 目标 Python；文书包不得使用构建解释器依赖",
    )
    build.add_argument(
        "--wheelhouse",
        type=Path,
        help="WorkBuddy 锁定 wheelhouse；缺失时构建失败，不联网下载",
    )

    verify_artifact_parser = subparsers.add_parser("verify-artifact")
    verify_artifact_parser.add_argument(
        "--platform", choices=("workbuddy",), required=True
    )
    verify_artifact_parser.add_argument("--artifact", type=Path, required=True)
    verify_artifact_parser.add_argument(
        "--runtime-python",
        "--workbuddy-runtime-python",
        "--target-python",
        dest="workbuddy_runtime_python",
        type=Path,
        help="WorkBuddy 目标 Python；用于禁用用户 site 的真实 smoke",
    )

    verify_core = subparsers.add_parser("verify-core")
    verify_core.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    verify_core.add_argument("--skill-root", type=Path, required=True)

    verify = subparsers.add_parser("verify-install")
    verify.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    verify.add_argument("--skill-root", type=Path, required=True)

    identify = subparsers.add_parser("identify")
    identify.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    identify.add_argument("--skill-root", type=Path, required=True)

    uninstall = subparsers.add_parser("uninstall")
    uninstall.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    uninstall.add_argument("--scope", choices=("project", "user"), required=True)
    uninstall.add_argument("--target-root", type=Path, required=True)
    uninstall.add_argument("--skill-root", type=Path)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    cleanup.add_argument("--scope", choices=("project", "user"), required=True)
    cleanup.add_argument("--target-root", type=Path, required=True)

    verify_platform = subparsers.add_parser("verify-platform")
    verify_platform.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    verify_platform.add_argument("--skill-root", type=Path, required=True)
    verify_platform.add_argument(
        "--platform-command",
        nargs="+",
        help="平台CLI命令及固定前置参数；默认使用平台对应的CLI命令",
    )
    verify_platform.add_argument("--lifecycle-evidence", type=Path)
    verify_platform.add_argument("--evidence-output", type=Path)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--platform", choices=SUPPORTED_PLATFORMS, required=True)
    probe.add_argument("--skill-root", type=Path, required=True)
    probe.add_argument(
        "--platform-command",
        nargs="+",
        help="平台CLI命令及固定前置参数；默认使用平台对应的CLI命令",
    )
    probe.add_argument("--workspace", type=Path, required=True)
    probe.add_argument(
        "--network-check",
        choices=("skip", "official-source"),
        default="skip",
    )
    probe.add_argument(
        "--network-url",
        default="https://www.gov.cn/",
    )

    present = subparsers.add_parser(
        "present-files", help="检查并列出本地文件；不会在宿主界面显示或附加文件"
    )
    present.add_argument("--file", dest="files", action="append", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "evaluate":
            result = evaluate_capabilities(
                args.platform, load_capabilities(args.capabilities)
            )
        elif args.command == "install":
            result = install_skill(
                platform=args.platform,
                scope=args.scope,
                target_root=args.target_root,
                source=args.source,
                mode=args.mode,
                artifact=args.artifact,
                workbuddy_runtime_python=args.workbuddy_runtime_python,
                workbuddy_wheelhouse=args.wheelhouse,
            )
        elif args.command == "update":
            result = update_skill(
                platform=args.platform,
                scope=args.scope,
                target_root=args.target_root,
                source=args.source,
                mode=args.mode,
                artifact=args.artifact,
                workbuddy_runtime_python=args.workbuddy_runtime_python,
                workbuddy_wheelhouse=args.wheelhouse,
            )
        elif args.command == "inspect-source":
            result = inspect_skill_source(
                platform=args.platform,
                source=args.source,
            )
            if not result["source_valid"]:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "build-artifact":
            result = build_artifact(
                platform=args.platform,
                source=args.source,
                output=args.output,
                workbuddy_runtime_python=args.workbuddy_runtime_python,
                workbuddy_wheelhouse=args.wheelhouse,
            )
        elif args.command == "verify-artifact":
            result = verify_artifact(
                platform=args.platform,
                artifact=args.artifact,
                workbuddy_runtime_python=args.workbuddy_runtime_python,
            )
            if (
                not result["artifact_contract_valid"]
                or not result["integrity_verified"]
                or not result["resources_complete"]
                or not result.get("renderer_dependencies_verified", True)
                or (
                    args.platform == "workbuddy"
                    and not result.get("native_upload_runtime_identity_verified", False)
                )
                or not result["core_contract"]["verified"]
            ):
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "verify-core":
            result = verify_core_contract(
                platform=args.platform,
                skill_root=args.skill_root,
            )
            if not result["verified"]:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "verify-install":
            result = verify_install(
                platform=args.platform,
                skill_root=args.skill_root,
            )
            if (
                not result["discovery_contract_valid"]
                or not result["resources_complete"]
                or not result.get("renderer_dependencies_verified", True)
                or not result["runtime_identity_verified"]
            ):
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "identify":
            result = identify_installation(
                platform=args.platform,
                skill_root=args.skill_root,
            )
            if not result["verified"]:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "uninstall":
            result = uninstall_skill(
                platform=args.platform,
                scope=args.scope,
                target_root=args.target_root,
                skill_root=args.skill_root,
            )
        elif args.command == "cleanup":
            result = reap_workbuddy_cleanup(
                platform=args.platform,
                target_root=args.target_root,
                scope=args.scope,
            )
        elif args.command == "verify-platform":
            result = verify_platform_discovery(
                platform=args.platform,
                skill_root=args.skill_root,
                platform_command=args.platform_command
                or _default_platform_command(args.platform),
                lifecycle_evidence=args.lifecycle_evidence,
            )
            if args.evidence_output:
                if args.evidence_output.exists():
                    raise PlatformAdapterError("证据目标已存在，未覆盖")
                args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
                args.evidence_output.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if not result["verified"]:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "present-files":
            result = present_files(files=tuple(args.files))
        else:
            result = probe_capabilities(
                platform=args.platform,
                skill_root=args.skill_root,
                platform_command=args.platform_command
                or _default_platform_command(args.platform),
                workspace=args.workspace,
                network_check=args.network_check,
                network_url=args.network_url,
            )
    except PlatformAdapterError as error:
        print(f"平台适配失败：{error}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
