#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    bundle_root = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle_root))
    from xp_unattended_adapters import _diagnostic_entry_gate  # type: ignore

    result: Dict[str, Any] = _diagnostic_entry_gate(
        args.selection.resolve(),
        args.replay.resolve(),
    )
    result["bundle_adapter_sha256"] = sha256(bundle_root / "xp_unattended_adapters.py")
    result["selection_sha256"] = sha256(args.selection)
    result["replay_sha256"] = sha256(args.replay)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="", flush=True)
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
