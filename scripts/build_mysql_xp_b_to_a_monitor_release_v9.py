#!/usr/bin/env python3
"""Freeze XP8 B-to-A monitor v9 for Algorithm-A v8."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_b_to_a_monitor_20260827_v9"
CONTRACT = OUTPUT / "handoff_contract.json"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "monitor": ROOT / "scripts/monitor_mysql_b_then_a_mixed_eight_worker_scene_v9.py",
    "monitor_predecessor": ROOT / "scripts/monitor_mysql_b_then_a_mixed_eight_worker_scene_v1.py",
    "a_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v8.py",
    "a_contract": ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v8/algorithm_a_top4_contract.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh handoff release required: {OUTPUT}")
    if any(not path.is_file() or path.is_symlink() for path in SOURCES.values()):
        raise RuntimeError("handoff v9 source is absent")
    OUTPUT.mkdir(parents=True, mode=0o700)
    value = {
        "schema_version": "mysql_xp8_b_to_a_handoff_contract_v9",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "cross_scene_docker_control_lock": False,
        "physical_workers": 8,
        "a_release": "mixed_eight_worker_v8",
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    }
    temporary = CONTRACT.with_name(f".{CONTRACT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, CONTRACT)
    CONTRACT.with_name(CONTRACT.name + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n", encoding="ascii"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
