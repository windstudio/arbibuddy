"""只读发布证据检查入口。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scripts.cli_encoding import configure_utf8_stdio
from .verification import verify_evidence


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="arbibuddy-release")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_evidence(args.source, args.evidence)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"verified": False, "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
