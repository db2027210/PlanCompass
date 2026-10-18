#!/usr/bin/env python3
"""Audit progress for the isolated fresh-only 18-run double-loop matrix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "config/double_loop_fresh_sgd_matrix_20260719.json"
DEFAULT_RESULT_ROOTS = (
    Path("/disk2/resource_adaptive_double_loop_sgd_formal_18_v3_20260718/runs"),
    Path("/disk3/resource_adaptive_double_loop_sgd_formal_18_v2_20260718/runs_retry1"),
)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    with handle:
        for raw in handle:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def expected_runs(spec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for experiment in spec.get("experiments") or []:
        if not isinstance(experiment, Mapping):
            continue
        for repeat_id in experiment.get("repeat_ids") or []:
            result.append(
                {
                    "exp_id": str(experiment["exp_id"]),
                    "benchmark": str(experiment["benchmark"]),
                    "scenario": str(experiment["bottleneck"]),
                    "repeat_id": int(repeat_id),
                }
            )
    return result


def active_output_dirs() -> Set[Path]:
    result: Set[Path] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            words = [
                item.decode("utf-8", errors="replace")
                for item in (entry / "cmdline").read_bytes().split(b"\0")
                if item
            ]
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if not any("run_double_loop_grid_scenarios.py" in word for word in words):
            continue
        if "--output-dir" not in words:
            continue
        index = words.index("--output-dir") + 1
        if index < len(words):
            result.add(Path(words[index]).resolve())
    return result


def discover_attempts(result_roots: Sequence[Path]) -> Dict[Tuple[str, str, int, str], List[Path]]:
    result: Dict[Tuple[str, str, int, str], List[Path]] = {}
    for result_root in result_roots:
        if not result_root.is_dir():
            continue
        for manifest_path in result_root.rglob("run_manifest.json"):
            manifest = read_json(manifest_path)
            if not manifest:
                continue
            args = manifest.get("args") if isinstance(manifest.get("args"), Mapping) else {}
            scenarios = manifest.get("scenarios") or []
            if len(scenarios) != 1:
                continue
            try:
                key = (
                    str(args["exp_id"]),
                    str(args["benchmark"]),
                    int(args["repeat_id"]),
                    str(scenarios[0]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            result.setdefault(key, []).append(manifest_path.parent.resolve())
    return result


def line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def optimizer_rows(scenario_dir: Path) -> List[Dict[str, Any]]:
    paths = sorted(scenario_dir.glob("optimizer*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not paths:
        return []
    return list(read_jsonl(paths[-1]))


def attempt_status(attempt: Path, scenario: str, running: bool) -> Dict[str, Any]:
    scenario_dir = attempt / scenario
    baseline = read_json(scenario_dir / "fresh_baseline.json") or {}
    measurement = baseline.get("measurement") if isinstance(baseline.get("measurement"), Mapping) else {}
    pruning = read_json(scenario_dir / "fresh_workload_pruning.json") or {}
    active_k = pruning.get("active_k")
    try:
        expected_subsets = (2 ** int(active_k)) - 1
    except (TypeError, ValueError):
        expected_subsets = None
    quality = read_json(scenario_dir / "fresh_model_quality_gate.json") or {}
    selected_quality = (
        quality.get("selected_quality")
        if isinstance(quality.get("selected_quality"), Mapping)
        else {}
    )
    selected_evaluation = (
        selected_quality.get("selected_evaluation")
        if isinstance(selected_quality.get("selected_evaluation"), Mapping)
        else {}
    )
    resource_metrics = (
        selected_evaluation.get("resource_metrics")
        if isinstance(selected_evaluation.get("resource_metrics"), Mapping)
        else {}
    )
    relative_error_p95_values = [
        float(item["relative_error_p95"])
        for item in resource_metrics.values()
        if isinstance(item, Mapping) and item.get("relative_error_p95") is not None
    ]
    quality_p95_max = (
        max(relative_error_p95_values) if relative_error_p95_values else None
    )
    quality_inversion = selected_evaluation.get("pairwise_inversion_rate")
    strict_quality_gate_10pct = None
    if selected_evaluation:
        strict_quality_gate_10pct = (
            quality.get("passed") is True
            and quality_p95_max is not None
            and quality_p95_max <= 0.10
            and quality_inversion is not None
            and float(quality_inversion) <= 0.10
        )
    calibration_rows = list(
        read_jsonl(scenario_dir / "fresh_model_calibration.jsonl")
    )
    calibration_replays = sum(
        row.get("event") == "fresh_model_calibration_replay"
        for row in calibration_rows
    )
    rows = optimizer_rows(scenario_dir)
    round_rows = [row for row in rows if row.get("event") == "double_loop_round"]
    topk_rows = list(read_jsonl(scenario_dir / "adaptive_topk_decision.jsonl"))
    latest_topk = topk_rows[-1] if topk_rows else {}
    summary = read_json(scenario_dir / "scenario_summary.json") or {}
    if summary:
        phase = str(summary.get("status") or "finished")
    elif round_rows or (quality.get("optimizer_started") is True):
        phase = "optimizer"
    elif quality:
        phase = "model_gate"
    elif baseline:
        phase = "calibration"
    else:
        phase = "baseline"
    if running:
        phase += "*"
    return {
        "attempt": str(attempt),
        "running": running,
        "phase": phase,
        "baseline_qps": measurement.get("db_qps"),
        "baseline_target": measurement.get(scenario) if scenario != "none" else None,
        "active_k": active_k,
        "subsets_per_round": expected_subsets,
        "calibration_replays": calibration_replays,
        "calibration_log_rows": len(calibration_rows),
        "quality_gate_passed": quality.get("passed"),
        "quality_p95_max": quality_p95_max,
        "quality_pairwise_inversion": quality_inversion,
        "strict_quality_gate_10pct": strict_quality_gate_10pct,
        "subset_rows": line_count(scenario_dir / "subset_converged_points.jsonl"),
        "outer_rounds": len(round_rows),
        "latest_real_k": latest_topk.get("replayed_k"),
        "latest_topk_action": latest_topk.get("action"),
        "successful": summary.get("successful"),
        "error": summary.get("error") or "",
    }


def build_rows(spec: Mapping[str, Any], result_roots: Sequence[Path]) -> List[Dict[str, Any]]:
    active = active_output_dirs()
    attempts = discover_attempts(result_roots)
    rows: List[Dict[str, Any]] = []
    for expected in expected_runs(spec):
        key = (
            expected["exp_id"],
            expected["benchmark"],
            expected["repeat_id"],
            expected["scenario"],
        )
        candidates = attempts.get(key) or []
        if not candidates:
            rows.append({**expected, "attempt": None, "running": False, "phase": "not_started"})
            continue
        selected = max(
            candidates,
            key=lambda path: (path in active, path.stat().st_mtime, str(path)),
        )
        rows.append(
            {
                **expected,
                **attempt_status(selected, expected["scenario"], selected in active),
                "attempt_count": len(candidates),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--result-root", type=Path, action="append")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    spec = read_json(args.spec)
    if not spec:
        raise RuntimeError(f"invalid matrix spec: {args.spec}")
    roots = tuple(args.result_root or DEFAULT_RESULT_ROOTS)
    rows = build_rows(spec, roots)
    payload = {
        "expected_runs": len(rows),
        "running_runs": sum(row.get("running") is True for row in rows),
        "finished_runs": sum(str(row.get("phase") or "").rstrip("*") in {"success", "failed"} for row in rows),
        "successful_runs": sum(row.get("successful") is True for row in rows),
        "rows": rows,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return 0
    print(
        "run\tbenchmark\tscenario\tphase\tcalib_replays\tk\t"
        "gate10\tsubset_rows\trounds\ttopk\tsuccess\terror",
        flush=True,
    )
    for row in rows:
        print(
            "\t".join(
                str(value if value is not None else "")
                for value in (
                    f"{row['exp_id']}_r{row['repeat_id']}",
                    row["benchmark"],
                    row["scenario"],
                    row.get("phase"),
                    row.get("calibration_replays"),
                    row.get("active_k"),
                    row.get("strict_quality_gate_10pct"),
                    row.get("subset_rows"),
                    row.get("outer_rounds"),
                    row.get("latest_real_k"),
                    row.get("successful"),
                    str(row.get("error") or "")[:100],
                )
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
