#!/usr/bin/env python3
"""Bind sealed first-round evidence to the continued Algorithm C manifest."""

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
AUDIT = ORIGINAL / "four_scene_outer_001_audit.json"
MONITOR = ORIGINAL / "completion_monitor_summary.json"


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
    policy = manifest.get("monitoring_policy") or {}
    require(policy.get("continuous_monitoring_enabled") is False, "continuous monitoring is enabled")
    require(policy.get("future_status_checks") == "manual_one_off_only", "monitoring policy differs")

    audit = load(AUDIT)
    require(audit.get("all_four_scenes_complete_outer_001") is True, "four-scene outer 1 audit failed")
    require(int(audit.get("completed_scene_count") or 0) == 4, "four-scene outer 1 count differs")
    monitor = load(MONITOR)
    require(monitor.get("read_only") is True, "historical monitor was not read-only")
    require(monitor.get("process_control_performed") is False, "historical monitor controlled a process")
    require(monitor.get("at_least_one_complete_outer_round") is True, "historical monitor lacks round observation")

    manifest["stable_complete_round_observation"] = {
        "audit": {"path": str(AUDIT), "sha256": sha(AUDIT)},
        "read_only_monitor_snapshot": {"path": str(MONITOR), "sha256": sha(MONITOR)},
        "first_complete_outer_round_observed_at": monitor.get(
            "first_complete_outer_round_observed_at"
        ),
        "all_four_scenes_complete_outer_001": True,
        "physical_wave_shape": [2, 2, 1],
        "continuous_monitoring_now_enabled": False,
    }
    manifest["updated_at"] = datetime.now().astimezone().isoformat()

    temporary = MANIFEST.with_name(f".{MANIFEST.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, MANIFEST)
    checksum = MANIFEST.with_suffix(MANIFEST.suffix + ".sha256")
    checksum.write_text(f"{sha(MANIFEST)}  {MANIFEST.name}\n", encoding="utf-8")
    print(json.dumps({"path": str(MANIFEST), "sha256": sha(MANIFEST)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
