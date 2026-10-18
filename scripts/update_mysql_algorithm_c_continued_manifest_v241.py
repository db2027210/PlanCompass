#!/usr/bin/env python3
"""Bind the sealed PG/MySQL Algorithm C correspondence audit."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "history/mysql_four_scene_20260809/c_v212_continued_four_scene_20260929"
MANIFEST = CAMPAIGN / "launch_manifest.json"
AUDIT = CAMPAIGN / "pg_full_logic_recheck_v6_20260929.json"


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


def main() -> int:
    manifest = load(MANIFEST)
    audit = load(AUDIT)
    require(audit.get("schema_version") == "mysql_algorithm_c_pg_full_logic_recheck_v6", "logic audit schema differs")
    require(audit.get("status") == "PASS_RUNNING_NOT_COMPLETE", "logic audit status differs")
    semantics = audit.get("semantic_correspondence") or {}
    require(len(semantics) == 15, "logic semantic check count differs")
    require(all(row.get("status") == "PASS" for row in semantics.values()), "logic semantic check failed")

    manifest["pg_algorithm_c_logic_correspondence"] = {
        "path": str(AUDIT),
        "sha256": sha(AUDIT),
        "semantic_check_count": len(semantics),
        "historical_progress_snapshot_used_for_completion": False,
    }
    manifest["updated_at"] = datetime.now().astimezone().isoformat()
    temporary = MANIFEST.with_name(f".{MANIFEST.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, MANIFEST)
    checksum = MANIFEST.with_suffix(MANIFEST.suffix + ".sha256")
    checksum.write_text(f"{sha(MANIFEST)}  {MANIFEST.name}\n", encoding="utf-8")
    print(json.dumps({"path": str(MANIFEST), "sha256": sha(MANIFEST)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
