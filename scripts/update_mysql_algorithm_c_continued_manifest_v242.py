#!/usr/bin/env python3
"""Bind the sealed original four-scene concurrent launch to the continuation."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN = HISTORY / "c_v212_continued_four_scene_20260929"
ORIGINAL = HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929"
MANIFEST = CAMPAIGN / "launch_manifest.json"
ORIGINAL_MANIFEST = ORIGINAL / "launch_manifest.json"
EXPECTED_SCENES = {
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
}


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_sidecar(path: Path) -> None:
    checksum = path.with_suffix(path.suffix + ".sha256")
    fields = checksum.read_text(encoding="utf-8").strip().split()
    require(len(fields) >= 2, "original launch checksum is malformed")
    require(fields[0] == sha(path), "original launch checksum differs")
    require(Path(fields[-1]).name == path.name, "original launch checksum filename differs")


def main() -> int:
    require(
        not (CAMPAIGN / "final_completion_audit.json").exists(),
        "final completion audit is already sealed",
    )
    manifest = load(MANIFEST)
    original = load(ORIGINAL_MANIFEST)
    verify_sidecar(ORIGINAL_MANIFEST)
    require(original.get("formal_scope") == "mysql_only", "original launch scope differs")
    require(
        original.get("cross_engine_comparison_prohibited") is True,
        "original launch comparison flag differs",
    )
    require(original.get("four_scene_round_barrier") is False, "original launch barrier differs")
    require(original.get("sibling_scene_wait") is False, "original sibling wait differs")
    require(set(original.get("launches") or {}) == EXPECTED_SCENES, "original scene set differs")
    maximum_skew = max(
        float(row["started_offset_seconds"])
        for row in original["launches"].values()
    )
    require(maximum_skew <= 1.0, "original launch start skew exceeds one second")

    manifest["original_full_parallel_launch"] = {
        "path": str(ORIGINAL_MANIFEST),
        "sha256": sha(ORIGINAL_MANIFEST),
        "checksum_path": str(
            ORIGINAL_MANIFEST.with_suffix(ORIGINAL_MANIFEST.suffix + ".sha256")
        ),
        "scene_count": 4,
        "maximum_start_skew_seconds": maximum_skew,
    }
    manifest["updated_at"] = datetime.now().astimezone().isoformat()

    checksum = MANIFEST.with_suffix(MANIFEST.suffix + ".sha256")
    manifest_temporary = MANIFEST.with_name(f".{MANIFEST.name}.{os.getpid()}.tmp")
    checksum_temporary = checksum.with_name(f".{checksum.name}.{os.getpid()}.tmp")
    try:
        manifest_temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        digest = sha(manifest_temporary)
        checksum_temporary.write_text(
            f"{digest}  {MANIFEST.name}\n",
            encoding="utf-8",
        )
        os.replace(checksum_temporary, checksum)
        os.replace(manifest_temporary, MANIFEST)
    finally:
        for temporary in (manifest_temporary, checksum_temporary):
            if temporary.exists():
                temporary.unlink()

    print(json.dumps({"path": str(MANIFEST), "sha256": sha(MANIFEST)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
