#!/usr/bin/env python3
"""Recreate the missing XP-CPU w1 container from its verified staged reset."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_client_primary_v6 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = HISTORY / "v1260_c_xp_cpu_w1_staged_recovery_v113_20260906"
FAILED_ROOT = HISTORY / "v1255_to_c_full_docker_path_resilience_v111_20260906_xp_cpu"
CHECKPOINT = HISTORY / "v1256_c_xp_cpu_resume_checkpoint_v112_20260906/prepared_binding.json"
AUTHORITY = HISTORY / "v1236_c_xp_cpu_scene_authority_v107_20260905/authority"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
WORKER_ID = "xp_cpu_w1"
CONTAINER = "ra_mysql_disk2_algc_xp_cpu_w1_20260809"
RAW_QUARANTINE = Path(
    "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/quarantine/"
    "current_interval_plan_coverage_001/"
    "outer_008_current_interval_plan_coverage_001_2"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh staged recovery output required")
    failed = load(FAILED_ROOT / "run_state.json")
    checkpoint = load(CHECKPOINT)
    freeze = load(FREEZE)
    prepared = load(AUTHORITY / "prepared_binding.json")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prior = {str(row["worker_id"]): dict(row) for row in prepared["workers"]}
    if (
        failed.get("status") != "FAILED_CLOSED"
        or failed.get("error") != (
            "CalledProcessError: Command '['docker', 'start', "
            "'ra_mysql_disk2_algc_xp_cpu_w1_20260809']' returned "
            "non-zero exit status 1."
        )
        or int(failed.get("outer_attempt") or 0) != 7
        or int(checkpoint.get("completed_outer_attempts") or 0) != 7
        or set(frozen) != set(prior)
    ):
        raise RuntimeError("staged XP-CPU recovery lineage differs")
    inventory = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{CONTAINER}$"],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()
    runtime_root = Path(str(freeze["storage"]["runtime_root"])) / WORKER_ID
    transformed = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / f"{RAW_QUARANTINE.parent.name}__{RAW_QUARANTINE.name}" / WORKER_ID
    )
    if (
        inventory
        or not (runtime_root / "datadir").is_dir()
        or not (runtime_root / "credentials").is_dir()
        or not (transformed / "datadir").is_dir()
        or not (transformed / "credentials").is_dir()
    ):
        raise RuntimeError("missing-container staged data-plane evidence differs")
    xp_rows = {
        worker_id: row for worker_id, row in prior.items()
        if worker_id.startswith(("xp_cpu_", "xp_io_"))
    }
    reset.configure(freeze=FREEZE, prepared=AUTHORITY / "prepared_binding.json",
                    xp_workers=xp_rows)
    OUTPUT.mkdir(parents=True, mode=0o700)
    try:
        row = reset._prepare_fresh_xp(
            freeze, frozen[WORKER_ID], prior[WORKER_ID], RAW_QUARANTINE,
            OUTPUT / "xp_resets/staged_w1", True, "formal_point",
            allow_unresponsive_old_container=True,
            allow_absent_predecessor_after_interrupted_discard=True,
        )
    finally:
        reset.uninstall()
    recovery_path = OUTPUT / "recovered_worker.json"
    document = {
        "schema_version": "mysql_c_xp_cpu_staged_worker_recovery_v1",
        "status": "PASS", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "worker": row,
        "source_failed_state": {"path": str(FAILED_ROOT / "run_state.json"),
                                "sha256": sha(FAILED_ROOT / "run_state.json")},
        "source_checkpoint": {"path": str(CHECKPOINT), "sha256": sha(CHECKPOINT)},
        "source_authority": {"path": str(AUTHORITY / "prepared_binding.json"),
                             "sha256": sha(AUTHORITY / "prepared_binding.json")},
        "staged_quarantine": str(transformed),
        "completed_outer_attempts_preserved": 7,
        "incomplete_outer_credit": 0,
        "bind_mounted_datadir_deleted": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(recovery_path, document)
    print(json.dumps({"status": "PASS", "worker_id": WORKER_ID,
                      "container_id": row["container_id"],
                      "recovery": str(recovery_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
