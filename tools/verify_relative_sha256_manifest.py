#!/usr/bin/env python3
"""Verify a JSON ``files`` map of relative paths to SHA-256 digests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = load(args.manifest.resolve())
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest files map is empty")
    verified = 0
    for relative, expected in sorted(files.items()):
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("manifest files entries must be strings")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe manifest path: {relative}")
        path = (root / relative_path).resolve()
        if root not in path.parents:
            raise ValueError(f"manifest path escapes root: {relative}")
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"SHA-256 mismatch for {relative}: expected={expected} actual={actual}"
            )
        verified += 1
    print(
        json.dumps(
            {"status": "verified", "verified_file_count": verified},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
