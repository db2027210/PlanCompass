#!/usr/bin/env python3
"""Build the strict real-runtime A/B/C effect table for four-VM HyBenchXP runs."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional


TARGETS = ("cpu", "memory", "io", "none")
RESOURCES = ("cpu", "memory", "io")


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ratio(candidate: Any, baseline: Any) -> Optional[float]:
    candidate_value = number(candidate)
    baseline_value = number(baseline)
    if candidate_value is None or baseline_value in (None, 0.0):
        return None
    return (candidate_value - baseline_value) / baseline_value


def baseline_for_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    baseline = scenario.get("baseline_reference")
    if isinstance(baseline, dict) and str(baseline.get("status") or "").lower() == "ok":
        return baseline
    log_path = Path(str((scenario.get("artifacts") or {}).get("search_log") or ""))
    for row in load_jsonl(log_path):
        if row.get("event") == "baseline_validation" and str(row.get("status") or "").lower() == "ok":
            baseline = row
    if not isinstance(baseline, dict):
        raise RuntimeError(f"valid baseline missing for target={scenario.get('target')}")
    return baseline


def physical_goal(
    target: str,
    qps_gain: Optional[float],
    target_drop: Optional[float],
    resource_gains: Dict[str, Optional[float]],
) -> bool:
    if qps_gain is None or qps_gain < 0.15:
        return False
    if target == "none":
        return any((value or 0.0) > 0.0 for value in resource_gains.values())
    return target_drop is not None and 0.05 <= target_drop <= 0.10


def scenario_row(
    *,
    strategy: str,
    run_root: Path,
    scenario: Dict[str, Any],
) -> Dict[str, Any]:
    target = str(scenario.get("target") or "")
    baseline = baseline_for_scenario(scenario)
    point = scenario.get("strategy_final_point")
    if not isinstance(point, dict) or not point.get("label"):
        raise RuntimeError(f"strategy final point missing: strategy={strategy} target={target}")
    baseline_resources = baseline.get("resources") or {}
    point_resources = point.get("resources") or {}
    qps_gain = ratio(point.get("db_qps"), baseline.get("db_qps"))
    resource_gains = {
        resource: ratio(point_resources.get(resource), baseline_resources.get(resource))
        for resource in RESOURCES
    }
    target_drop = None if target == "none" else (
        -resource_gains[target] if resource_gains.get(target) is not None else None
    )
    score_b_details = scenario.get("global_score_b_best_details") if strategy == "B" else None
    score_c_details = scenario.get("global_score_c_best_details") if strategy == "C" else None
    confirmation = scenario.get("score_c_final_confirmation") if strategy == "C" else None
    confirmation_resources = (confirmation or {}).get("resources") or {}
    return {
        "strategy": strategy,
        "target": target,
        "run_root": str(run_root),
        "label": point.get("label"),
        "completed_rounds": scenario.get("completed_rounds"),
        "real_replay_count": scenario.get("candidate_validation_count"),
        "baseline_db_qps": number(baseline.get("db_qps")),
        "real_db_qps": number(point.get("db_qps")),
        "db_qps_gain_pct": qps_gain * 100.0 if qps_gain is not None else None,
        "baseline_cpu": number(baseline_resources.get("cpu")),
        "real_cpu": number(point_resources.get("cpu")),
        "cpu_change_pct": resource_gains["cpu"] * 100.0 if resource_gains["cpu"] is not None else None,
        "baseline_memory": number(baseline_resources.get("memory")),
        "real_memory": number(point_resources.get("memory")),
        "memory_change_pct": (
            resource_gains["memory"] * 100.0 if resource_gains["memory"] is not None else None
        ),
        "baseline_io": number(baseline_resources.get("io")),
        "real_io": number(point_resources.get("io")),
        "io_change_pct": resource_gains["io"] * 100.0 if resource_gains["io"] is not None else None,
        "target_drop_pct": target_drop * 100.0 if target_drop is not None else None,
        "physical_goal_reached": physical_goal(target, qps_gain, target_drop, resource_gains),
        "score_b": number((score_b_details or {}).get("score_b")),
        "score_b_successful": (score_b_details or {}).get("successful") if strategy == "B" else None,
        "score_c": number((score_c_details or {}).get("score_c")),
        "score_c_successful": (score_c_details or {}).get("successful") if strategy == "C" else None,
        "confirmation_label": (confirmation or {}).get("label"),
        "confirmation_db_qps": number((confirmation or {}).get("db_qps")),
        "confirmation_db_xact_tps": number((confirmation or {}).get("db_xact_tps")),
        "confirmation_cpu": number(confirmation_resources.get("cpu")),
        "confirmation_memory": number(confirmation_resources.get("memory")),
        "confirmation_io": number(confirmation_resources.get("io")),
        "confirmation_p90_ms": number((confirmation or {}).get("p90_ms")),
        "confirmation_p95_ms": number((confirmation or {}).get("p95_ms")),
        "confirmation_p99_ms": number((confirmation or {}).get("p99_ms")),
        "confirmation_resource_samples": (confirmation or {}).get("resource_samples"),
        "params": point.get("params") or {},
    }


def fmt(value: Any, digits: int = 2) -> str:
    numeric = number(value)
    return "—" if numeric is None else f"{numeric:.{digits}f}"


def markdown(rows: List[Dict[str, Any]], a_root: Path, b_root: Path, c_root: Path) -> str:
    lines = [
        "# HyBenchXP  VM A/B/C ",
        "",
        f"- `{time.strftime('%Y-%m-%d %H:%M:%S %Z')}`",
        f"- A`{a_root}`",
        f"- B`{b_root}`",
        f"- C`{c_root}`",
        "-  QPS ",
        "-  `(real-baseline)/baseline`",
        "",
        "|  |  |  |  | DB QPS → | QPS  | CPU | Memory | IO |  | Score B | Score C |  |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['strategy']} | {row['target']} | {row['completed_rounds']} | "
            f"{row['real_replay_count']} | {fmt(row['baseline_db_qps'])}→{fmt(row['real_db_qps'])} | "
            f"{fmt(row['db_qps_gain_pct'])}% | {fmt(row['cpu_change_pct'])}% | "
            f"{fmt(row['memory_change_pct'])}% | {fmt(row['io_change_pct'])}% | "
            f"{fmt(row['target_drop_pct'])}% | {fmt(row['score_b'], 4)} | "
            f"{fmt(row['score_c'], 4)} | "
            f"{'' if row['physical_goal_reached'] else ''} |"
        )
    c_rows = [row for row in rows if row["strategy"] == "C"]
    lines.extend(
        [
            "",
            "## C ",
            "",
            "|  |  |  | DB QPS | DB TPS | CPU | Memory | IO | p90 | p95 | p99 |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in c_rows:
        lines.append(
            f"| {row['target']} | {row['label']} | {row['confirmation_label']} | "
            f"{fmt(row['confirmation_db_qps'])} | {fmt(row['confirmation_db_xact_tps'])} | "
            f"{fmt(row['confirmation_cpu'])} | {fmt(row['confirmation_memory'])} | "
            f"{fmt(row['confirmation_io'])} | {fmt(row['confirmation_p90_ms'])} | "
            f"{fmt(row['confirmation_p95_ms'])} | {fmt(row['confirmation_p99_ms'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a_run_root", type=Path)
    parser.add_argument("b_run_root", type=Path)
    parser.add_argument("c_run_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    a_root = args.a_run_root.resolve()
    b_root = args.b_run_root.resolve()
    c_root = args.c_run_root.resolve()
    output_dir = args.output_dir.resolve()

    audits = {
        "A": load_json(a_root / "convergence_completion_audit.json"),
        "B": load_json(b_root / "convergence_completion_audit.json"),
        "C": load_json(c_root / "convergence_completion_audit.json"),
    }
    expected_roots = {"A": a_root, "B": b_root, "C": c_root}
    rows: List[Dict[str, Any]] = []
    for strategy in ("A", "B", "C"):
        audit = audits[strategy]
        if audit.get("complete") is not True:
            raise RuntimeError(f"strict audit is not complete: strategy={strategy}")
        if str(audit.get("experiment_strategy") or "").upper() != strategy:
            raise RuntimeError(f"audit strategy mismatch: expected={strategy}")
        scenarios = {str(row.get("target")): row for row in audit.get("scenarios") or []}
        if set(scenarios) != set(TARGETS):
            raise RuntimeError(f"scenario coverage mismatch: strategy={strategy} targets={sorted(scenarios)}")
        for target in TARGETS:
            rows.append(
                scenario_row(
                    strategy=strategy,
                    run_root=expected_roots[strategy],
                    scenario=scenarios[target],
                )
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "event": "hybenchxp_four_vm_abc_real_effect_table",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "a_run_root": str(a_root),
        "b_run_root": str(b_root),
        "c_run_root": str(c_root),
        "strict_audits_complete": True,
        "rows": rows,
    }
    atomic_write(output_dir / "four_vm_abc_effect.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    csv_buffer = io.StringIO()
    csv_rows = [{key: value for key, value in row.items() if key != "params"} for row in rows]
    writer = csv.DictWriter(csv_buffer, fieldnames=list(csv_rows[0]))
    writer.writeheader()
    writer.writerows(csv_rows)
    atomic_write(output_dir / "four_vm_abc_effect.csv", csv_buffer.getvalue())
    atomic_write(output_dir / "four_vm_abc_effect.md", markdown(rows, a_root, b_root, c_root))
    print(json.dumps({"complete": True, "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
