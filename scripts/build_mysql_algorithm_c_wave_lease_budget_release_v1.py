#!/usr/bin/env python3
"""Freeze a wave lease that covers reset plus the unchanged 900s workload."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v61"
    / "coordination_contract.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v62"
    / "coordination_contract.json"
)
FAILED_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v67_formal_outer16_xp_rollback_safety_pruning_20260817_attempt035"
)
LEASE_SECONDS = 1800


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence file required: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    predecessor = json.loads(PREDECESSOR.read_text(encoding="utf-8"))
    if (
        predecessor.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or predecessor.get("release_version") != "v61"
        or predecessor.get("maximum_wave_lease_seconds") != 1140
        or (predecessor.get("fairness") or {}).get(
            "mysql_maximum_wave_lease_seconds"
        ) != 1140
    ):
        raise RuntimeError("v61 predecessor lease authority differs")
    state = FAILED_ROOT / "run_state.json"
    watchdog = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
    samples = FAILED_ROOT / "formal_incumbent_baseline/resource_samples.jsonl"
    failed = json.loads(state.read_text(encoding="utf-8"))
    terminal = json.loads(watchdog.read_text(encoding="utf-8").splitlines()[-2])
    if (
        failed.get("status") != "FAILED_CLOSED"
        or failed.get("error") != "KeyboardInterrupt: "
        or terminal.get("event") != "cross_project_workload_lease_deadline_exceeded"
        or terminal.get("maximum_wave_lease_seconds") != 1140.0
        or not 1140 <= float(terminal.get("lease_elapsed_seconds") or 0) <= 1145
    ):
        raise RuntimeError("Attempt035 exact lease failure evidence differs")

    value = copy.deepcopy(predecessor)
    value.update({
        "release_version": "v62",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "predecessor": binding(PREDECESSOR),
        "maximum_wave_lease_seconds": LEASE_SECONDS,
        "successor_reason": (
            "cover the already-frozen quiet gate, sealed XP reset, unchanged "
            "900-second full workload bound, plan capture, and batch aggregation; "
            "Attempt035 reached the predecessor 1140-second outer lease only after "
            "all workload clients completed, so the predecessor budget was shorter "
            "than its own valid reset-plus-measurement path"
        ),
        "wave_lease_budget_v62": {
            "quiet_gate_maximum_seconds": 60,
            "observed_four_xp_reset_upper_seconds": 400,
            "formal_workload_maximum_seconds_unchanged": 900,
            "plan_capture_aggregation_margin_seconds": 180,
            "minimum_derived_lease_seconds": 1540,
            "frozen_lease_seconds": LEASE_SECONDS,
            "per_workload_900_second_fail_close_unchanged": True,
            "release_between_candidate_batches_unchanged": True,
            "whole_outer_round_exclusive_lease_prohibited": True,
            "workload_resource_candidate_score_top5_binary_shrink_and_convergence_changed": False,
            "accepted_or_rejected_state_credit_from_attempt035": 0,
            "external_process_mutation": False,
            "failed_state": binding(state),
            "runtime_watchdog": binding(watchdog),
            "resource_samples": binding(samples),
            "builder_source": binding(Path(__file__)),
        },
    })
    value["fairness"] = copy.deepcopy(value["fairness"])
    value["fairness"]["mysql_maximum_wave_lease_seconds"] = LEASE_SECONDS
    value["fairness"]["lease_covers_reset_plus_measurement"] = True
    atomic(OUTPUT, value)
    written = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if (
        written.get("release_version") != "v62"
        or written.get("maximum_wave_lease_seconds") != LEASE_SECONDS
        or (written.get("wave_lease_budget_v62") or {}).get(
            "per_workload_900_second_fail_close_unchanged"
        ) is not True
    ):
        raise RuntimeError("v62 written contract differs")
    print(json.dumps({"status": "PREPARED", "path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
