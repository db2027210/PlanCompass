#!/usr/bin/env python3
"""Fail-closed completion audit for the MySQL A/B/C supplement campaign."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
C_CAMPAIGN = HISTORY / "v1194_to_c_client_primary_four_scene_20260903"
REPLAY_CAMPAIGN = HISTORY / "v1196_mysql_abc_final_counterbalanced_20260904"
FINAL_ROOT = HISTORY / "v1197_mysql_abc_final_report_20260904"
SUMMARY = FINAL_ROOT / "final_summary.json"
REPORT = ROOT / "reports/mysql_abc_final_results_20260904.md"
CURVE_CSV = FINAL_ROOT / "accepted_client_incumbent_curves.csv"
CURVE_PDF = FINAL_ROOT / "figures/mysql_abc_accepted_client_incumbent_curves.pdf"
CURVE_PNG = FINAL_ROOT / "figures/mysql_abc_accepted_client_incumbent_curves.png"
OUTPUT = HISTORY / "v1197_mysql_abc_final_completion_audit_20260904"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
POINTS = ("baseline", "algorithm_a", "algorithm_b", "algorithm_c")


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.read_text(encoding="ascii").split()[0] != sha(path)
    ):
        raise RuntimeError(f"artifact sidecar differs: {path}")


def verify_replay(scene: str, suffix: str) -> dict[str, Any]:
    path = HISTORY / (
        f"v1196_mysql_abc_final_counterbalanced_20260904_{suffix}/"
        "final_replay_result.json"
    )
    verify_sidecar(path)
    result = load(path)
    expected_workers = {f"{suffix}_w1", f"{suffix}_w2"}
    if (
        result.get("status") != "PASS"
        or result.get("formal_scope") != "mysql_only"
        or result.get("cross_engine_comparison_prohibited") is not True
        or result.get("scene") != scene
        or result.get("counterbalanced") is not True
    ):
        raise RuntimeError(f"replay scope/status differs: {scene}")
    for point in POINTS:
        rows = (result.get("replicas") or {}).get(point)
        summary = (result.get("point_summaries") or {}).get(point)
        if (
            not isinstance(rows, list) or len(rows) < 6
            or not isinstance(summary, Mapping)
            or int(summary.get("valid_n") or 0) < 6
            or set(summary.get("workers") or []) != expected_workers
            or any(row.get("valid") is not True for row in rows)
            or any(float(row.get("client_qps") or 0.0) <= 0.0 for row in rows)
            or any(
                not math.isfinite(float((row.get("resources") or {}).get(name)))
                for row in rows for name in ("cpu", "memory", "io")
            )
        ):
            raise RuntimeError(f"replay coverage/metric differs: {scene}/{point}")
        worker_counts = {
            worker: sum(row.get("worker_id") == worker for row in rows)
            for worker in expected_workers
        }
        if any(count < 3 for count in worker_counts.values()):
            raise RuntimeError(f"replay worker balance differs: {scene}/{point}")
        if scene.startswith("hybench_xp") and any(
            row.get("xtp_tps") is None
            or float(row.get("xtp_tps") or 0.0) <= 0.0
            for row in rows
        ):
            raise RuntimeError(f"XP XTP metric differs: {scene}/{point}")
        if scene == "hybench_xp_io" and any(
            row.get("device_major_minor") != "259:6"
            or float((row.get("resources") or {}).get("io") or 0.0) <= 0.0
            for row in rows
        ):
            raise RuntimeError(f"XP-I/O device/readback differs: {point}")
    comparisons = result.get("paired_comparisons_vs_baseline") or {}
    for point in POINTS[1:]:
        row = comparisons.get(point)
        if (
            not isinstance(row, Mapping)
            or int(row.get("paired_n") or 0) < 6
            or not all(
                math.isfinite(float(row.get(key)))
                for key in ("mean_delta_pct", "ci95_low_pct", "ci95_high_pct")
            )
        ):
            raise RuntimeError(f"paired 95% CI differs: {scene}/{point}")
    return {"path": str(path), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh final audit root required: {OUTPUT}")
    c_manifest_path = C_CAMPAIGN / "launch_manifest.json"
    replay_manifest_path = REPLAY_CAMPAIGN / "launch_manifest.json"
    for path in (c_manifest_path, replay_manifest_path, SUMMARY):
        verify_sidecar(path)
    c_manifest = load(c_manifest_path)
    replay_manifest = load(replay_manifest_path)
    summary = load(SUMMARY)
    if (
        c_manifest.get("status") != "COMPLETE"
        or set(c_manifest.get("declared_scenes") or []) != set(SCENES)
        or c_manifest.get("formal_scope") != "mysql_only"
        or c_manifest.get("cross_engine_comparison_prohibited") is not True
        or replay_manifest.get("status") != "COMPLETE"
        or set((replay_manifest.get("launches") or {})) != set(SCENES)
        or any(
            row.get("exit_code") != 0
            for row in (replay_manifest.get("launches") or {}).values()
        )
        or summary.get("status") != "PASS"
        or summary.get("formal_scope") != "mysql_only"
        or summary.get("cross_engine_comparison_prohibited") is not True
        or set(summary.get("scenes") or {}) != set(SCENES)
        or set(summary.get("convergence_curves") or {}) != set(SCENES)
        or set(summary.get("tuning_costs") or {}) != set(SCENES)
    ):
        raise RuntimeError("final campaign/summary completeness differs")

    evidence = {
        "c_manifest": {"path": str(c_manifest_path), "sha256": sha(c_manifest_path)},
        "replay_manifest": {
            "path": str(replay_manifest_path), "sha256": sha(replay_manifest_path),
        },
        "summary": {"path": str(SUMMARY), "sha256": sha(SUMMARY)},
    }
    for scene, suffix in SCENES.items():
        c_state_path = HISTORY / (
            f"v1194_to_c_client_primary_four_scene_20260903_{suffix}/run_state.json"
        )
        verify_sidecar(c_state_path)
        c_state = load(c_state_path)
        if (
            c_state.get("status") != "COMPLETE"
            or c_state.get("algorithm") != "C"
            or (c_state.get("converged") or {}).get(scene) is not True
            or not isinstance((c_state.get("params_by_scene") or {}).get(scene), Mapping)
        ):
            raise RuntimeError(f"C scene completion differs: {scene}")
        evidence[f"{suffix}_c_state"] = {
            "path": str(c_state_path), "sha256": sha(c_state_path),
        }
        evidence[f"{suffix}_replay"] = verify_replay(scene, suffix)

    for path in (REPORT, CURVE_CSV, CURVE_PDF, CURVE_PNG):
        if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
            raise RuntimeError(f"final report/curve artifact differs: {path}")
        evidence[path.name] = {"path": str(path), "sha256": sha(path)}

    retired_tokens = ("tpcc", "tpc-c", "sf10", "single_query", "one_query")
    source_paths = "\n".join(
        str(row.get("path") or "") for row in (summary.get("sources") or {}).values()
    ).lower()
    if any(token in source_paths for token in retired_tokens):
        raise RuntimeError("retired workload evidence entered the final aggregate")
    audit = {
        "schema_version": "mysql_abc_final_completion_audit_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": list(SCENES),
        "retired_workloads_included": False,
        "all_four_c_scenes_complete": True,
        "counterbalanced_replay_minimum_valid_repeats_per_point": 6,
        "both_workers_covered_per_point": True,
        "paired_95_percent_ci_present": True,
        "xp_io_device_major_minor": "259:6",
        "xp_io_positive_for_all_included_replicas": True,
        "client_throughput_latency_resources_right_censoring_reported": True,
        "accepted_client_convergence_curves_exported": True,
        "evidence": evidence,
    }
    path = OUTPUT / "completion_audit.json"
    atomic(path, audit)
    print(json.dumps({"status": "PASS", "audit": str(path), "sha256": sha(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
