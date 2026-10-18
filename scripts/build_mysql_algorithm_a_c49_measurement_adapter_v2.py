#!/usr/bin/env python3
"""Freeze the Algorithm-A C49 adapter against the current boundary runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260827_v1/algorithm_c_measurement_adapter_contract.json"
OUTPUT = ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260828_v2/algorithm_c_measurement_adapter_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.parent.exists() or OUTPUT.parent.is_symlink():
        raise RuntimeError(f"fresh adapter contract required: {OUTPUT.parent}")
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document["created_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    successor = document["algorithm_a_measurement_adapter_successor"]
    successor.update({
        "release_version": "v2",
        "predecessor_adapter_contract_path": str(SOURCE.relative_to(ROOT)),
        "predecessor_adapter_contract_sha256": sha(SOURCE),
        "algorithm_c_result_or_search_state_changed": False,
        "measurement_adapter_source_bindings_refreshed": True,
        "refreshed_binding_count": 1,
    })
    boundary = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
    document["source_bindings"]["boundary_runtime"] = {
        "path": str(boundary.relative_to(ROOT)), "sha256": sha(boundary),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
