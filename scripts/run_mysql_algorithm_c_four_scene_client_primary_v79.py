#!/usr/bin/env python3
"""Resume C scenes after bounded optimizer-cost control SQL timeouts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v75 as predecessor


runtime = predecessor.runtime
alg = predecessor.alg
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v169/"
    "algorithm_c_full_resume_contract.json"
)
BOUNDARIES = {
    "hybench_xp_cpu": {
        "completed": 5,
        "checkpoint": HISTORY / "c_v169_xp_cpu_outer5_checkpoint_20260923/prepared_binding.json",
        "failed_state": HISTORY / "c_v158_full_algorithm_resume_20260923_xp_cpu/run_state.json",
    },
    "tpch_sf4_memory": {
        "completed": 6,
        "checkpoint": HISTORY / "c_v169_tpch_outer6_checkpoint_20260923/prepared_binding.json",
        "failed_state": HISTORY / "c_v160_tpch_live_normalization_resume_20260923_tpch/run_state.json",
    },
}
CONTROL_SQL_TIMEOUT_SECONDS = 300


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v169"
        or value.get("release_version") != "v169-bounded-control-sql-timeout-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("control_sql_timeout_seconds") != CONTROL_SQL_TIMEOUT_SECONDS
        or value.get("formal_measurement_timeout_changed") is not False
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("failed_outer_credit") != 0
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("fixed_outer_round_stop") is not False
    ):
        raise RuntimeError("Algorithm C v169 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v169 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v169 is a completed-outer resume")
    owner = predecessor.predecessor.predecessor
    scene = owner.argument_value("--scene")
    checkpoint_path = owner.argument_path("--checkpoint")
    if scene not in BOUNDARIES:
        raise RuntimeError("v169 scene differs")
    expected = BOUNDARIES[scene]
    if checkpoint_path != expected["checkpoint"].resolve():
        raise RuntimeError("v169 checkpoint differs")
    checkpoint = runtime.scene_checkpoint.verify_or_standard(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    completed = int(expected["completed"])
    state = load(expected["failed_state"])
    source_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    decision = load(source_root / f"outer_{completed:03d}/{scene}.decision.json")
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != completed
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != completed
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or "timed out after 60 seconds" not in str(state.get("error") or "")
        or "FLUSH OPTIMIZER_COSTS" not in str(state.get("error") or "")
        or int(state.get("outer_attempt") or 0) != completed
        or decision.get("formal_evidence_eligible") is not True
        or int(decision.get("outer_attempt") or 0) != completed
        or list((source_root / f"outer_{completed + 1:03d}").glob("*.decision.json"))
    ):
        raise RuntimeError("Algorithm C v169 completed boundary differs")
    return checkpoint


def reuse_startup_paper_gate(
    *, incumbents: Mapping[str, Any], baseline_rows: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    result = predecessor.V158_REUSE_STARTUP_PAPER_GATE(
        incumbents=incumbents, baseline_rows=baseline_rows, output=output,
    )
    scene = next(iter(incumbents))
    expected = BOUNDARIES[scene]
    result.update({
        "schema_version": "mysql_algorithm_c_reused_startup_paper_baseline_gate_v5",
        "resume_after_completed_outer": int(expected["completed"]),
        "discarded_incomplete_outer": int(expected["completed"]) + 1,
        "boundary_checkpoint_path": str(expected["checkpoint"]),
        "boundary_checkpoint_sha256": sha(expected["checkpoint"]),
        "control_sql_timeout_seconds": CONTROL_SQL_TIMEOUT_SECONDS,
        "formal_measurement_timeout_changed": False,
    })
    runtime.alg.atomic(output, result)
    return result


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    validate_resume_boundary()
    old_boundary = predecessor.validate_resume_boundary
    old_gate = predecessor.reuse_startup_paper_gate
    old_mysql_root = alg.mysql_root

    def bounded_mysql_root(
        worker: Mapping[str, Any], sql: str, *,
        timeout_seconds: int = CONTROL_SQL_TIMEOUT_SECONDS,
    ) -> str:
        return old_mysql_root(worker, sql, timeout_seconds=timeout_seconds)

    predecessor.validate_resume_boundary = validate_resume_boundary
    predecessor.reuse_startup_paper_gate = reuse_startup_paper_gate
    alg.mysql_root = bounded_mysql_root
    try:
        return int(predecessor.main())
    finally:
        alg.mysql_root = old_mysql_root
        predecessor.reuse_startup_paper_gate = old_gate
        predecessor.validate_resume_boundary = old_boundary


if __name__ == "__main__":
    raise SystemExit(main())
