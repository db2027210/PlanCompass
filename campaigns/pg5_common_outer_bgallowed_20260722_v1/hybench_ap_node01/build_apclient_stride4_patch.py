#!/usr/bin/env python3
"""Build a lane-private APClient classpath patch that advances sequences by four."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


CLASS_PATH = "com/hybench/workload/APClient.class"
ORIGINAL = bytes.fromhex("1908150b840b011908be702e")
PATCHED = bytes.fromhex("1908150b840b041908be702e")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-jar", required=True, type=Path)
    parser.add_argument("--output-jar", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    if args.output_jar.exists() or args.summary.exists():
        raise FileExistsError("stride patch outputs must be new")
    with zipfile.ZipFile(args.source_jar, "r") as source:
        original_class = source.read(CLASS_PATH)
    count = original_class.count(ORIGINAL)
    if count != 1:
        raise RuntimeError(f"expected one AP sequence step byte pattern, found {count}")
    patched_class = original_class.replace(ORIGINAL, PATCHED)
    if patched_class.count(PATCHED) != 1 or patched_class.count(ORIGINAL) != 0:
        raise RuntimeError("AP sequence stride patch did not apply exactly once")

    args.output_jar.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output_jar, "x", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(CLASS_PATH, patched_class)
    jar_payload = args.output_jar.read_bytes()
    summary = {
        "schema_version": 1,
        "patch_id": "hybench_apclient_sequence_stride4_v1",
        "source_jar": str(args.source_jar.resolve()),
        "source_jar_sha256": sha256_bytes(args.source_jar.read_bytes()),
        "class_path": CLASS_PATH,
        "source_class_sha256": sha256_bytes(original_class),
        "patched_class_sha256": sha256_bytes(patched_class),
        "original_pattern_hex": ORIGINAL.hex(),
        "patched_pattern_hex": PATCHED.hex(),
        "patch_offset": original_class.index(ORIGINAL),
        "patch_count": count,
        "semantic_change": "configured AP query sequence index increment changes from 1 to 4",
        "output_jar": str(args.output_jar.resolve()),
        "output_jar_sha256": sha256_bytes(jar_payload),
    }
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
