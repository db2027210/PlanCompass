#!/usr/bin/env python3
"""Aggregate final counterbalanced replay and tuning-cost evidence."""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN = "v1196_mysql_abc_final_counterbalanced_20260904"
OUTPUT = HISTORY / "v1197_mysql_abc_final_report_20260904"
REPORT = ROOT / "reports/mysql_abc_final_results_20260904.md"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
A_STATES = {
    "job_memory": HISTORY / (
        "v153_to_a_job4_round11_20260825_attempt001_job/algorithm_a_state.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/"
        "algorithm_a_state.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/algorithm_a_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1060_to_a_io_round6_v2_resume_20260828_xp_io/algorithm_a_state.json"
    ),
}
B_ROOTS = {
    "job_memory": HISTORY / "v1178_to_b_client_primary_four_scene_20260903_job",
    "tpch_sf4_memory": HISTORY / (
        "v1191_to_b_tpch_client_primary_predecision_continue_20260903_tpch"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io"
    ),
}
C_ROOTS = {
    scene: HISTORY / f"v1194_to_c_client_primary_four_scene_20260903_{suffix}"
    for scene, suffix in SCENES.items()
}
PLOTTER = ROOT / "scripts/plot_mysql_abc_final_results_v1.py"
PLOT_PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
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


def client_qps(row: Mapping[str, Any], scene: str) -> float:
    if scene.startswith("hybench_xp"):
        return float(
            row.get("client_xap_qps")
            or ((row.get("workload_result") or {}).get("formal_metrics") or {}).get(
                "xap_qps"
            )
            or row.get("client_qps_audit") or 0.0
        )
    if scene == "tpch_sf4_memory":
        return float(
            row.get("same_worker_normalized_client_qps")
            or row.get("db_qps")
            or row.get("client_qps_audit") or 0.0
        )
    return float(row.get("client_qps_audit") or 0.0)


def selected_row(document: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = list(document.get("evaluations") or [])
    selected = [row for row in rows if row.get("selected_for_outer_decision") is True]
    if len(selected) == 1:
        return dict(selected[0])
    accepted = [
        row for row in rows
        if (row.get("outer_decision") or {}).get("accepted") is True
    ]
    if accepted:
        return dict(max(accepted, key=lambda row: float(row.get("db_qps") or 0.0)))
    return None


def bc_curve(root: Path, scene: str, algorithm: str) -> list[dict[str, Any]]:
    curve: list[dict[str, Any]] = []
    for path in sorted(root.glob(f"outer_*/{scene}.decision.json")):
        decision = load(path)
        if decision.get("accepted") is not True:
            continue
        row = selected_row(decision)
        if row is None:
            raise RuntimeError(f"accepted {algorithm} decision row absent: {path}")
        curve.append({
            "accepted_index": len(curve) + 1,
            "outer_attempt": int(
                decision.get("outer_attempt") or decision.get("outer_iteration") or 0
            ),
            "client_incumbent_qps": client_qps(row, scene),
            "outer_metric_qps": float(row.get("db_qps") or 0.0),
            "params": row.get("params"),
            "decision_path": str(path),
            "decision_sha256": sha(path),
        })
    return curve


def c_evidence_root(projected_root: Path, state: Mapping[str, Any]) -> Path:
    """Resolve real decision artifacts behind a memory-pair state projection."""
    source = state.get("memory_pair_source_state")
    if source is None:
        return projected_root
    source_path = Path(str(source)).resolve()
    if (
        not source_path.is_file() or source_path.is_symlink()
        or state.get("memory_pair_source_state_sha256") != sha(source_path)
    ):
        raise RuntimeError(f"C memory-pair projection source differs: {source_path}")
    return source_path.parent


def assert_strict_outer_progress(
    curve: list[dict[str, Any]], *, scene: str, algorithm: str,
) -> None:
    values = [float(row["outer_metric_qps"]) for row in curve]
    if any(
        not math.isfinite(value) or value <= 0.0
        for value in values
    ) or any(right <= left for left, right in zip(values, values[1:])):
        raise RuntimeError(
            f"{algorithm} accepted outer incumbent is not strictly increasing: {scene}"
        )


def validate_replay_result(result: Mapping[str, Any], *, scene: str) -> None:
    expected_workers = {f"{SCENES[scene]}_w1", f"{SCENES[scene]}_w2"}
    summaries = result.get("point_summaries")
    replicas = result.get("replicas")
    comparisons = result.get("paired_comparisons_vs_baseline")
    if not isinstance(summaries, Mapping) or not isinstance(replicas, Mapping):
        raise RuntimeError(f"final replay summary differs: {scene}")
    for point in ("baseline", "algorithm_a", "algorithm_b", "algorithm_c"):
        summary_row = summaries.get(point)
        point_rows = replicas.get(point)
        if (
            not isinstance(summary_row, Mapping)
            or int(summary_row.get("valid_n") or 0) < 6
            or set(summary_row.get("workers") or []) != expected_workers
            or not isinstance(point_rows, list)
            or len(point_rows) < 6
            or any(row.get("valid") is not True for row in point_rows)
            or any(float(row.get("client_qps") or 0.0) <= 0.0 for row in point_rows)
        ):
            raise RuntimeError(f"final replay point coverage differs: {scene}/{point}")
        if scene == "hybench_xp_io" and any(
            row.get("device_major_minor") != "259:6"
            or float((row.get("resources") or {}).get("io") or 0.0) <= 0.0
            for row in point_rows
        ):
            raise RuntimeError(f"XP-I/O replay device/readback differs: {point}")
    if not isinstance(comparisons, Mapping) or any(
        int((comparisons.get(point) or {}).get("paired_n") or 0) < 6
        for point in ("algorithm_a", "algorithm_b", "algorithm_c")
    ):
        raise RuntimeError(f"final replay paired confidence evidence differs: {scene}")


def a_curve(state_path: Path, scene: str) -> list[dict[str, Any]]:
    state = load(state_path)
    curve: list[dict[str, Any]] = []
    for accepted in state.get("accepted_history") or []:
        path = Path(str(accepted["decision_path"]))
        document = load(path)
        row = dict((document.get("accepted") or {}).get("row") or {})
        curve.append({
            "accepted_index": len(curve) + 1,
            "outer_attempt": int(accepted.get("round_index") or 0),
            "client_incumbent_qps": client_qps(row, scene),
            "outer_metric_qps": float(row.get("db_qps") or 0.0),
            "params": row.get("params"),
            "decision_path": str(path),
            "decision_sha256": sha(path),
        })
    return curve


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def cost_from_curve(
    root: Path, state: Mapping[str, Any], scene: str, curve: list[dict[str, Any]],
) -> dict[str, Any]:
    decisions = [load(path) for path in sorted(root.glob(f"outer_*/{scene}.decision.json"))]
    candidates = sum(
        int(doc.get("valid_real_evaluation_count") or len(doc.get("evaluations") or []))
        + len(doc.get("invalid_candidate_attempts") or [])
        for doc in decisions
    )
    started = parse_time(state.get("started_at"))
    completed = parse_time(state.get("completed_at"))
    return {
        "outer_rounds": len(decisions),
        "accepted_rounds": len(curve),
        "rejected_rounds": sum(doc.get("accepted") is not True for doc in decisions),
        "physical_candidates": candidates,
        "wall_time_seconds": (
            (completed - started).total_seconds()
            if started is not None and completed is not None else None
        ),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh final report root required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    replay: dict[str, Any] = {}
    curves: dict[str, Any] = {}
    costs: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for scene, suffix in SCENES.items():
        result_path = HISTORY / f"{CAMPAIGN}_{suffix}" / "final_replay_result.json"
        result = load(result_path)
        if result.get("status") != "PASS" or result.get("scene") != scene:
            raise RuntimeError(f"final replay result differs: {result_path}")
        validate_replay_result(result, scene=scene)
        replay[scene] = result
        sources[f"{suffix}_replay"] = {
            "path": str(result_path), "sha256": sha(result_path),
        }
        b_state = load(B_ROOTS[scene] / "run_state.json")
        c_state = load(C_ROOTS[scene] / "run_state.json")
        c_root = c_evidence_root(C_ROOTS[scene], c_state)
        scene_curves = {
            "A": a_curve(A_STATES[scene], scene),
            "B": bc_curve(B_ROOTS[scene], scene, "B"),
            "C": bc_curve(c_root, scene, "C"),
        }
        assert_strict_outer_progress(scene_curves["B"], scene=scene, algorithm="B")
        assert_strict_outer_progress(scene_curves["C"], scene=scene, algorithm="C")
        curves[scene] = scene_curves
        a_state = load(A_STATES[scene])
        a_started = parse_time(a_state.get("started_at"))
        a_completed = parse_time(a_state.get("completed_at"))
        costs[scene] = {
            "A": {
                "outer_rounds": int(a_state.get("round_index") or 0),
                "accepted_rounds": len(scene_curves["A"]),
                "rejected_rounds": int(a_state.get("rejected_rounds") or 0),
                "physical_candidates": (
                    int(a_state.get("round_index") or 0)
                    * int(a_state.get("candidate_count_per_round") or 0)
                ),
                "wall_time_seconds": (
                    (a_completed - a_started).total_seconds()
                    if a_started is not None and a_completed is not None else None
                ),
                "wall_time_scope": "terminal_formal_resume",
            },
            "B": cost_from_curve(B_ROOTS[scene], b_state, scene, scene_curves["B"]),
            "C": cost_from_curve(c_root, c_state, scene, scene_curves["C"]),
        }
        sources.update({
            f"{suffix}_a_state": {
                "path": str(A_STATES[scene]), "sha256": sha(A_STATES[scene]),
            },
            f"{suffix}_b_state": {
                "path": str(B_ROOTS[scene] / "run_state.json"),
                "sha256": sha(B_ROOTS[scene] / "run_state.json"),
            },
            f"{suffix}_c_state": {
                "path": str(C_ROOTS[scene] / "run_state.json"),
                "sha256": sha(C_ROOTS[scene] / "run_state.json"),
            },
        })
    summary = {
        "schema_version": "mysql_abc_final_report_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "primary_evidence": "same_batch_two_worker_counterbalanced_client_throughput",
        "scenes": replay,
        "convergence_curves": curves,
        "tuning_costs": costs,
        "sources": sources,
    }
    atomic(OUTPUT / "final_summary.json", summary)
    curve_csv = OUTPUT / "accepted_client_incumbent_curves.csv"
    with curve_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "scene", "algorithm", "accepted_index", "outer_attempt",
            "client_incumbent_qps", "outer_metric_qps", "decision_path",
        ])
        for scene, algorithms in curves.items():
            for algorithm, rows in algorithms.items():
                for row in rows:
                    writer.writerow([
                        scene, algorithm, row["accepted_index"], row["outer_attempt"],
                        row["client_incumbent_qps"], row["outer_metric_qps"],
                        row["decision_path"],
                    ])
    lines = [
        "# MySQL A/B/C ",
        "",
        "`mysql_only` worker",
        "",
        "|  |  | n |  |  baseline95% CI | XTP TPS | P95 ms | P99 ms | CPU | Memory | I/O | XAP | XTP |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "baseline": "Baseline", "algorithm_a": "A",
        "algorithm_b": "B", "algorithm_c": "C",
    }
    for scene, result in replay.items():
        for point in ("baseline", "algorithm_a", "algorithm_b", "algorithm_c"):
            row = result["point_summaries"][point]
            comparison = result["paired_comparisons_vs_baseline"].get(point)
            delta = "--" if comparison is None else (
                f"{comparison['mean_delta_pct']:+.3f}% "
                f"[{comparison['ci95_low_pct']:+.3f}, {comparison['ci95_high_pct']:+.3f}]"
            )
            lines.append(
                f"| {scene} | {labels[point]} | {row['valid_n']} | "
                f"{row['mean_client_qps']:.6f} | {delta} | "
                f"{('--' if row['mean_xtp_tps'] is None else f'{row['mean_xtp_tps']:.6f}')} | "
                f"{row['mean_p95_ms']:.3f} | {row['mean_p99_ms']:.3f} | "
                f"{row['mean_resources']['cpu']:.6f} | "
                f"{row['mean_resources']['memory']:.6f} | "
                f"{row['mean_resources']['io']:.6f} | "
                f"{row['right_censored_totals']['xap']} | "
                f"{row['right_censored_totals']['xtp']} |"
            )
    lines.extend(["", "## ", "", "|  |  |  |  |  |  |  |", "|---|---:|---:|---:|---:|---:|---:|"])
    for scene, algorithms in costs.items():
        for algorithm, row in algorithms.items():
            lines.append(
                f"| {scene} | {algorithm} | {row.get('outer_rounds')} | "
                f"{row.get('accepted_rounds')} | {row.get('rejected_rounds', '--')} | "
                f"{row.get('physical_candidates')} | {row.get('wall_time_seconds')} |"
            )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not PLOT_PYTHON.is_file() or not PLOTTER.is_file():
        raise RuntimeError("final convergence plot runtime differs")
    subprocess.run([
        str(PLOT_PYTHON), str(PLOTTER),
        "--summary", str(OUTPUT / "final_summary.json"),
        "--output-dir", str(OUTPUT / "figures"),
    ], cwd=ROOT, check=True)
    print(json.dumps({
        "status": "PASS",
        "summary": str(OUTPUT / "final_summary.json"),
        "report": str(REPORT),
        "curves": str(curve_csv),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
