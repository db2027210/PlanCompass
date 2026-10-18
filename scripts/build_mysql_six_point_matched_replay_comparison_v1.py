#!/usr/bin/env python3
"""Build the unified six-point fresh baseline/champion comparison."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1108_six_point_matched_replay_comparison_20260830/"
    "matched_replay_comparison.json"
)
SOURCES = {
    "relational_baseline_job": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1096_matched_baseline_replay_v8_job_20260829/baseline_replay_result.json"
    ),
    "relational_baseline_tpch": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1081_matched_baseline_replay_tpch_20260829/baseline_replay_result.json"
    ),
    "relational_champion_job": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1097_matched_champion_replay_job_20260829/champion_replay_result.json"
    ),
    "relational_champion_tpch": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1098_matched_champion_replay_tpch_20260829/champion_replay_result.json"
    ),
    "xp_baseline_cpu": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1103_matched_xp_baseline_cpu_20260829/baseline_replay_result.json"
    ),
    "xp_baseline_io": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1104_matched_xp_baseline_io_20260829/baseline_replay_result.json"
    ),
    "xp_champion_cpu": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1106_matched_xp_champion_cpu_20260829/champion_replay_result.json"
    ),
    "xp_champion_io": ROOT / (
        "history/mysql_four_scene_20260809/"
        "v1107_matched_xp_champion_io_20260829/champion_replay_result.json"
    ),
}
PAIRS = (
    ("JOB / B", "job_memory", "memory", "relational_baseline_job", "b_job_baseline", "relational_champion_job", "b_job"),
    ("JOB / C", "job_memory", "memory", "relational_baseline_job", "c_job_baseline", "relational_champion_job", "c_job"),
    ("TPC-H / C", "tpch_sf4_memory", "memory", "relational_baseline_tpch", "c_tpch_baseline", "relational_champion_tpch", "c_tpch"),
    ("XP-CPU / B", "hybench_xp_cpu", "cpu", "xp_baseline_cpu", "b_cpu_baseline", "xp_champion_cpu", "b_cpu"),
    ("XP-I/O / B", "hybench_xp_io", "io", "xp_baseline_io", "b_io_baseline", "xp_champion_io", "b_io"),
    ("XP-I/O / C", "hybench_xp_io", "io", "xp_baseline_io", "c_io_baseline", "xp_champion_io", "c_io"),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("status") != "PASS":
        raise RuntimeError(f"PASS result object required: {path}")
    return dict(value)


def delta_pct(baseline: float, champion: float) -> float | None:
    if baseline == 0.0:
        return None
    return (champion / baseline - 1.0) * 100.0


def point_summary(point: Mapping[str, Any]) -> dict[str, Any]:
    stability = point.get("resource_stability") or {}
    decisions = stability.get("decisions") or []
    return {
        "worker_id": point["worker_id"],
        "device_major_minor": point["device_major_minor"],
        "client_qps": float(point["client_qps_audit"]),
        "db_qps": float(point["db_qps"]),
        "p95_ms": float(point["p95_ms"]),
        "p99_ms": float(point["p99_ms"]),
        "resource_means": {
            key: float(point["resource_means"][key])
            for key in ("cpu", "memory", "io")
        },
        "selected_measurement_seconds": stability.get("selected_measurement_seconds"),
        "final_window_mean_cv": (
            decisions[-1].get("window_mean_cv") if decisions else None
        ),
        "hard_gate_passed": point.get("hard_gate_passed") is True,
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT}")
    loaded = {key: load(path) for key, path in SOURCES.items()}
    comparisons: list[dict[str, Any]] = []
    for label, scene, target, b_source, b_label, c_source, c_label in PAIRS:
        baseline_raw = loaded[b_source]["point_results"][b_label]
        champion_raw = loaded[c_source]["point_results"][c_label]
        baseline = point_summary(baseline_raw)
        champion = point_summary(champion_raw)
        if (
            baseline_raw.get("scene") != scene
            or champion_raw.get("scene") != scene
            or baseline["worker_id"] != champion["worker_id"]
            or baseline["device_major_minor"] != champion["device_major_minor"]
            or not baseline["hard_gate_passed"]
            or not champion["hard_gate_passed"]
        ):
            raise RuntimeError(f"matched comparison boundary failed: {label}")
        target_baseline = baseline["resource_means"][target]
        target_champion = champion["resource_means"][target]
        comparisons.append({
            "label": label,
            "scene": scene,
            "target_resource": target,
            "baseline_point": b_label,
            "champion_point": c_label,
            "baseline": baseline,
            "champion": champion,
            "client_qps_delta_pct": delta_pct(baseline["client_qps"], champion["client_qps"]),
            "db_qps_delta_pct": delta_pct(baseline["db_qps"], champion["db_qps"]),
            "target_resource_delta_pct": delta_pct(target_baseline, target_champion),
            "same_worker": True,
            "same_device": True,
            "both_hard_gates_passed": True,
            "io_delta_interpretable": not (
                target == "io" and target_baseline < 1e-4
            ),
        })
    document = {
        "schema_version": "mysql_six_point_matched_replay_comparison_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "comparison_basis": "fresh_baseline_then_fresh_champion_same_worker_same_device",
        "client_qps_is_primary_external_throughput": True,
        "db_qps_is_separately_reported_internal_statement_rate": True,
        "sources": {
            key: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for key, path in SOURCES.items()
        },
        "comparisons": comparisons,
    }
    OUTPUT.parent.mkdir(parents=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    print(json.dumps({"status": "PASS", "output": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
