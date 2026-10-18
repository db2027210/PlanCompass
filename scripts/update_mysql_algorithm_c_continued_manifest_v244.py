#!/usr/bin/env python3
"""Record the fifth-timeout TPC-H recovery in the composite manifest."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "history/mysql_four_scene_20260809/c_v212_continued_four_scene_20260929"
MANIFEST = CAMPAIGN / "launch_manifest.json"
LAUNCH = ROOT / "history/mysql_four_scene_20260809/c_v244_tpch_fifth_timeout_recovery_20260929/launch_manifest.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    document = load(MANIFEST)
    launch = load(LAUNCH)
    if (
        launch.get("release_version")
        != "v244-tpch-fifth-timeout-recovery"
        or launch.get("status") != "RUNNING"
        or int(launch.get("permanent_invalid_candidate_count") or 0) != 18
        or int(launch.get("soft_replayed_candidate_count") or 0) != 9
        or int(launch.get("quarantined_candidate_count") or 0) != 27
    ):
        raise RuntimeError("v244 launch differs")
    document["launches"]["tpch_sf4_memory"] = {
        "run_id": launch["run_id"],
        "pid": launch["pid"],
        "log": launch["log"],
        "controller": launch["controller"],
        "controller_sha256": launch["controller_sha256"],
        "controller_cpuset": launch["controller_cpuset"],
        "contract": launch["contract"],
        "contract_sha256": launch["contract_sha256"],
        "checkpoint": launch["checkpoint"],
        "checkpoint_sha256": launch["checkpoint_sha256"],
        "resumed_after_outer": 10,
        "current_pg_runtime_release": "v59",
        "bounded_tpch_cleanup_retry": True,
        "tpch_cleanup_readback_after_error": True,
        "permanent_invalid_exclusion_all_candidate_paths": True,
        "quarantined_timeout_candidates": 18,
        "soft_replayed_candidates": 9,
        "quarantined_candidate_count": 27,
    }
    document["continuation"].update({
        "continuation_run_id": launch["run_id"],
        "source_run_id": "c_v243_tpch_confirmed_cleanup_recovery_20260929_tpch",
        "checkpoint": launch["checkpoint"],
        "checkpoint_sha256": launch["checkpoint_sha256"],
        "quarantine": launch["quarantine"],
        "quarantine_sha256": launch["quarantine_sha256"],
        "quarantine_checkpoint": launch["quarantine_checkpoint"],
        "quarantine_checkpoint_sha256": launch["quarantine_checkpoint_sha256"],
        "latest_failed_outer_timeout_candidates": 1,
        "latest_failed_outer_valid_zero_credit_replays": 1,
        "technical_timeout_candidate_count": 18,
        "permanent_invalid_candidate_count": 18,
        "soft_replayed_candidate_count": 9,
        "quarantined_candidate_count": 27,
    })
    document["decision_lineage"]["tpch_sf4_memory"] = [
        {
            "from_outer": 1,
            "through_outer": 10,
            "run_id": "c_v211f_pg_dynamic_gate_four_scene_20260929_tpch",
        },
        {"from_outer": 11, "through_outer": "terminal", "run_id": launch["run_id"]},
    ]
    failed = document.setdefault("failed_recovery_attempts", [])
    failed_id = "c_v243_tpch_confirmed_cleanup_recovery_20260929_tpch"
    if not any(row.get("run_id") == failed_id for row in failed):
        failed.append({
            "run_id": failed_id,
            "reason": "one_full_suite_900s_timeout_and_one_valid_zero_credit_replay_before_outer11_decision",
            "completed_outer_credit": 0,
            "rejected_outer_attempt_increment": 0,
            "incomplete_outer_credit": 0,
            "workload_started": True,
        })
    lineage = document["scene_lineage"]["tpch_sf4_memory"]
    if launch["run_id"] not in lineage:
        lineage.append(launch["run_id"])
    document["updated_at"] = datetime.now().astimezone().isoformat()
    atomic(MANIFEST, document)
    print(json.dumps({
        "status": "UPDATED", "manifest": str(MANIFEST),
        "sha256": sha(MANIFEST), "tpch_pid": launch["pid"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
