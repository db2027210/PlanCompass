#!/usr/bin/env python3
"""Patch the frozen TPC-H config to the validated node01 container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


OLD_CONTAINER = "pg5_tpch_s04_20260722"
NEW_CONTAINER = "pg5_tpch_ap_slot_node01_20260726"
EXPECTED_SOURCE_SHA = (
    "e021fd79e938fc2401115c3aeccd21fefd69067e2cf1fa2fbb05f4eb0a8742df"
)


def replace(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        count = 0
        for key, child in value.items():
            output[key], replaced = replace(child)
            count += replaced
        return output, count
    if isinstance(value, list):
        output_list = []
        count = 0
        for child in value:
            rewritten, replaced = replace(child)
            output_list.append(rewritten)
            count += replaced
        return output_list, count
    if value == OLD_CONTAINER:
        return NEW_CONTAINER, 1
    return value, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sha-output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.exists() or args.sha_output.exists():
        raise FileExistsError("refusing to overwrite container-binding proposal")
    source_bytes = args.source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError(f"source SHA mismatch: {source_sha}")
    payload = json.loads(source_bytes)
    rewritten, count = replace(payload)
    if count != 3:
        raise RuntimeError(f"expected exactly three stale container bindings, got {count}")
    rendered = json.dumps(rewritten, indent=2, sort_keys=True) + "\n"
    if OLD_CONTAINER in rendered or rendered.count(NEW_CONTAINER) < 3:
        raise RuntimeError("container binding postcondition failed")
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, args.output)
    args.sha_output.write_text(f"{digest}  {args.output.name}\n", encoding="utf-8")
    print(json.dumps({
        "source_sha256": source_sha,
        "output_sha256": digest,
        "replacements": count,
        "container": NEW_CONTAINER,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
