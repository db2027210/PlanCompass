#!/usr/bin/env python3
"""Publish the frozen TPC-C typed collector atomically and idempotently."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path


SOURCE = Path(
    "/tmp/mysql_tpcc_typed_collector_payload_"
    "2ead5fc691e357db1aef115226348f6af1ae98164d2af8cbf6bca71cff7b695a"
)
BUNDLE_ID = "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
FILES = {
    "README.md": "03792e4ff6724d032f07897bd33d9b8f0caccc23197fbe523b462c333963ce5d",
    "test_tpcc_typed_collector.py": (
        "6c23587a3f05dc801e6a6dfcb7cc0dd3afadde5fd32f6646b8e64c2d8c285102"
    ),
    "tpcc_typed_collector.py": (
        "f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994"
    ),
}
DESTINATION_ROOT = (
    Path(__file__).resolve().parents[1] / "artifacts/mysql_tpcc_typed_collector"
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_exact(directory: Path) -> None:
    actual_names = {path.name for path in directory.iterdir() if path.is_file()}
    if actual_names != set(FILES):
        raise RuntimeError(
            f"bundle file set mismatch: expected={sorted(FILES)} "
            f"actual={sorted(actual_names)}"
        )
    for name, expected in FILES.items():
        actual = digest(directory / name)
        if actual != expected:
            raise RuntimeError(
                f"{name} sha256 mismatch: expected={expected} actual={actual}"
            )


def main() -> int:
    verify_exact(SOURCE)
    DESTINATION_ROOT.mkdir(parents=True, exist_ok=True)
    destination = DESTINATION_ROOT / BUNDLE_ID
    if destination.exists():
        verify_exact(destination)
        print(f"TPC_C_COLLECTOR_ALREADY_PUBLISHED {destination}")
        return 0

    staging = Path(
        tempfile.mkdtemp(prefix=f".{BUNDLE_ID}.", dir=str(DESTINATION_ROOT))
    )
    try:
        for name in sorted(FILES):
            shutil.copyfile(SOURCE / name, staging / name)
            os.chmod(staging / name, 0o444)
        verify_exact(staging)
        os.chmod(staging, 0o555)
        try:
            os.rename(staging, destination)
        except FileExistsError:
            verify_exact(destination)
        verify_exact(destination)
    finally:
        if staging.exists():
            os.chmod(staging, 0o755)
            shutil.rmtree(staging)

    print(f"TPC_C_COLLECTOR_PUBLISHED {destination}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"TPC_C_COLLECTOR_PUBLISH_FAILED {exc}", file=sys.stderr)
        raise
