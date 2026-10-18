#!/usr/bin/env python3
"""Seal the TPC-H scene recovery after the v191 transient gate failure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_gate_recovery_20260929_v192/"
    "tpch_gate_recovery_contract.json"
)
SOURCE = HISTORY / "c_v191_pg_exact_hwloc_four_scene_20260929_tpch"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "recovery_launcher": ROOT / "scripts/launch_mysql_algorithm_c_tpch_v191_gate_recovery_v192.py",
    "scene_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v98.py",
    "source_four_scene_launch": HISTORY / "c_v191_pg_exact_hwloc_four_scene_20260929/launch_manifest.json",
    "source_failed_state": SOURCE / "run_state.json",
    "source_batch_1": SOURCE / "outer_001/top5_batch_1/batch_result.json",
    "source_batch_2": SOURCE / "outer_001/top5_batch_2/batch_result.json",
    "source_batch_3_gate": SOURCE / "outer_001/top5_batch_3_dynamic_noninterference_gate.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists() or CONTRACT.parent.exists():
        raise RuntimeError("v192 recovery release already exists")
    state = json.loads(SOURCES["source_failed_state"].read_text())
    batches = [json.loads(SOURCES[f"source_batch_{index}"].read_text()) for index in (1, 2)]
    gate = json.loads(SOURCES["source_batch_3_gate"].read_text())
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or any(batch.get("status") != "PASS" or batch.get("failures") not in ([], None) for batch in batches)
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate.get("errors") != ["dynamic_noninterference_window_timeout"]
    ):
        raise RuntimeError("v191 TPC-H recovery source differs")
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular recovery source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_tpch_gate_recovery_contract_v1",
        "release_version": "v192-tpch-v191-gate-recovery",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "tpch_sf4_memory",
        "source_bindings": bindings,
        "source_four_scene_started_concurrently": True,
        "source_failure": "transient_dynamic_noninterference_window_timeout",
        "source_formal_credit": 0,
        "source_completed_batches_not_counted_as_outer_round": True,
        "fresh_scene_search_required": True,
        "historical_interval_state_reused": False,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "other_scene_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    CONTRACT.parent.mkdir(parents=True, mode=0o700)
    CONTRACT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n"
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
