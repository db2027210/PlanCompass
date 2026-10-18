#!/usr/bin/env python3
"""Freeze the single-invalid-replica XP/IO Algorithm-A retry release."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_baseline_w5_retry_20260827_v1"
    / "retry_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_io_baseline_w5_retry_v1.py"
RUNTIME = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12.py"
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v840_to_a_io_control2_fresh_20260827_xp_io"
)
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v849_to_a_io_baseline_w5_retry_current_rebind_20260827"
    / "authority/live_authority.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh retry contract required: {OUTPUT}")
    sources = {
        "controller": CONTROLLER,
        "runtime_v12": RUNTIME,
        "single_device_settle": ROOT / "scripts/mysql_async_xp_post_reset_settle_v2.py",
        "single_scene_measurement": ROOT / "scripts/mysql_single_scene_measurement_v1.py",
        "source_state": SOURCE / "algorithm_a_state.json",
        "source_batch": SOURCE / "formal_baseline/batch_result.json",
        "launch_authority": AUTHORITY,
    }
    if any(not path.is_file() or path.is_symlink() for path in sources.values()):
        raise RuntimeError("retry release source is absent or unsafe")
    document = {
        "schema_version": "mysql_algorithm_a_xp_io_baseline_w5_retry_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "hybench_xp_io",
        "failed_worker": "xp_io_w5",
        "retained_valid_workers": [
            "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
            "xp_io_w6", "xp_io_w7", "xp_io_w8",
        ],
        "failure_reason": "target_resource_stability_passed=false_at_900_seconds",
        "maximum_physical_retry_attempts": 3,
        "combined_baseline_requires_all_eight_valid": True,
        "physical_replay_parallelism_per_scene": 8,
        "docker_control_concurrency_per_scene": 2,
        "accepted_or_rejected_round_credit": 0,
        "candidate_measurements_reused": 0,
        "valid_baseline_measurements_retained": 7,
        "candidate_set_changed": False,
        "workload_or_resource_envelope_changed": False,
        "stability_threshold_changed": False,
        "native_xp_workload_changed": False,
        "top4_parameter_set_changed": False,
        "other_scene_completion_required": False,
        "four_scene_wave_barrier": False,
        "lease_mode": "none",
        "source_bindings": {label: binding(path) for label, path in sources.items()},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
