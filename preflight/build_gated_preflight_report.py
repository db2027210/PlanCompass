#!/usr/bin/env python3
"""Build the final causal-gated MySQL preflight report and machine summary."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
WIDE = ROOT / "preflight/wide_20260721"
FULL = ROOT / "preflight/full_20260721"

BENCHMARKS = (
    ("hybench_ap", "HyBench AP", "OLAP", "Memory"),
    ("job", "JOB / IMDB", "OLAP", "Memory"),
    ("tpch", "TPC-H", "OLAP", "Memory"),
    ("hybench_tp", "HyBench TP", "OLTP", "CPU"),
    ("tpcc", "TPC-C", "OLTP", "CPU"),
    ("hybench_xp", "HyBench XP", "HTAP", "IO"),
)

RESOURCE_FILES = {
    "hybench_ap": WIDE / "hybench_ap.json",
    "job": WIDE / "job.json",
    "tpch": FULL / "tpch.json",
    "hybench_tp": FULL / "hybench_tp_cpu1p5.json",
    "tpcc": FULL / "tpcc.json",
    "hybench_xp": FULL / "hybench_xp.json",
}

PLAN_FILES = {
    "hybench_ap": WIDE / "hybench_ap_plan_audit.json",
    "job": WIDE / "job_plan_audit.json",
    "tpch": WIDE / "tpch_plan_audit.json",
    "tpcc": WIDE / "tpcc_plan_audit.json",
    "hybench_xp": WIDE / "hybench_xp_plan_audit.json",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def qps(item: dict) -> float | None:
    raw = item.get("resource_raw", {})
    count = raw.get("query_count")
    duration = raw.get("query_time") or item.get("measurement_duration_sec")
    if finite(count) and finite(duration) and float(count) > 0 and float(duration) > 0:
        return float(count) / float(duration)
    return float(item["qps"]) if finite(item.get("qps")) else None


def plan_count(benchmark: str) -> int:
    if benchmark == "hybench_tp":
        scan = load(WIDE / "hybench_tp_plan_sensitivity_scan.json")
        return max(
            (int(item.get("distinct_structural_plans", 0)) for item in scan.get("queries", [])),
            default=0,
        )
    return int(load(PLAN_FILES[benchmark]).get("distinct_structural_plans", 0))


def summarize_resource(benchmark: str) -> dict:
    path = RESOURCE_FILES[benchmark]
    points = [item for item in load(path).get("points", []) if not item.get("error")]
    stable = [item for item in points if item.get("stable")]
    constructed = [item for item in stable if item.get("bottleneck_constructed")]
    rates = [value for item in points if (value := qps(item)) is not None]
    targets = [
        float(item["target_utilization"])
        for item in points if finite(item.get("target_utilization"))
    ]
    return {
        "source": str(path.resolve()),
        "measured_points": len(points),
        "stable_points": len(stable),
        "constructed_points": len(constructed),
        "early_stopped_points": sum(bool(item.get("early_stopped")) for item in points),
        "qps_min": min(rates, default=None),
        "qps_median": median(rates) if rates else None,
        "qps_max": max(rates, default=None),
        "qps_spread_pct": (
            100 * (max(rates) - min(rates)) / max(median(rates), 1e-12)
            if len(rates) >= 2 else None
        ),
        "target_utilization_min": min(targets, default=None),
        "target_utilization_max": max(targets, default=None),
        "target_utilization_spread": (
            max(targets) - min(targets) if len(targets) >= 2 else None
        ),
        "resource_construction_verified": bool(constructed),
    }


def main() -> int:
    rows = []
    for key, label, kind, target in BENCHMARKS:
        resource = summarize_resource(key)
        plans = plan_count(key)
        wide_runtime_complete = key in {"hybench_ap", "job"} and resource["measured_points"] == 10
        material_effect = bool(
            (
                resource["qps_spread_pct"] is not None
                and resource["qps_spread_pct"] >= 5.0
            )
            or (
                resource["target_utilization_spread"] is not None
                and resource["target_utilization_spread"] >= 0.05
            )
        ) if key in {"hybench_ap", "job"} else False
        # No wide-range candidate reached the effect gate, so no candidate was
        # promoted to the required two-repeat convergence verification stage.
        repeat_convergence_verified = False
        tuning_verified = bool(
            wide_runtime_complete
            and plans >= 2
            and material_effect
            and repeat_convergence_verified
        )
        if plans < 2:
            gate_reason = "10  1 cost "
        elif not wide_runtime_complete:
            gate_reason = " 10 "
        elif not material_effect:
            gate_reason = " QPS  5% 5 "
        else:
            gate_reason = ""
        rows.append({
            "benchmark": key,
            "label": label,
            "type": kind,
            "target_bottleneck": target,
            **resource,
            "runtime_design": (
                "absolute-wide 10-level design"
                if key in {"hybench_ap", "job"}
                else "earlier narrow pilot; absolute-wide design stopped at plan gate"
            ),
            "wide_plan_points": 10,
            "distinct_structural_plans": plans,
            "wide_runtime_complete": wide_runtime_complete,
            "material_effect_observed": material_effect,
            "repeat_convergence_verified": repeat_convergence_verified,
            "tuning_convergence_verified": tuning_verified,
            "gate_reason": gate_reason,
            "gate_result": "PASS" if tuning_verified else "FAIL",
        })

    scheme_valid = all(
        row["resource_construction_verified"] and row["tuning_convergence_verified"]
        for row in rows
    )
    result = {
        "scheme_valid_for_formal_experiment": scheme_valid,
        "formal_mysql_experiments_may_resume": scheme_valid,
        "point_design": str((ROOT / "preflight/experience_points_20260721.json").resolve()),
        "evidence_files": {
            "memory_pressure": str((WIDE / "memory_pressure_evidence.json").resolve()),
            "wide_runtime_ap": str((WIDE / "hybench_ap.json").resolve()),
            "wide_runtime_job": str((WIDE / "job.json").resolve()),
            "plan_audits": {
                key: str(path.resolve()) for key, path in PLAN_FILES.items()
            },
            "hybench_tp_plan_scan": str(
                (WIDE / "hybench_tp_plan_sensitivity_scan.json").resolve()
            ),
        },
        "protocol": {
            "warmup_sec": 60,
            "min_measure_sec": 180,
            "max_measure_sec": 600,
            "early_stop": "target and materially active resources stable",
            "causal_gate": (
                "at least two structural plans and either >=5% QPS effect "
                "or >=0.05 target-utilization effect, followed by two repeats"
            ),
        },
        "benchmarks": rows,
    }
    output_json = WIDE / "FINAL_GATE_REPORT.json"
    temporary = output_json.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output_json)

    lines = [
        "# MySQL 8 cost ",
        "",
        f"**{'' if result['scheme_valid_for_formal_experiment'] else ''}**",
        " MySQL ",
        "",
        "| Benchmark |  |  |  |  |  |  | QPS  |  |  |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for row in rows:
        if not row["wide_runtime_complete"]:
            # Narrow-pilot variation is useful for resource construction only;
            # do not visually present it as a wide cost-parameter effect.
            spread = "-"
            target_spread = "-"
        else:
            spread = "-" if row["qps_spread_pct"] is None else f"{row['qps_spread_pct']:.2f}%"
            target_spread = (
                "-"
                if row["target_utilization_spread"] is None
                else f"{100 * row['target_utilization_spread']:.2f} pp"
            )
        lines.append(
            f"| {row['label']} | {row['target_bottleneck']} | "
            f"{row['stable_points']}/{row['measured_points']} | "
            f"{row['constructed_points']}/{row['measured_points']} | "
            f"{row['distinct_structural_plans']} | {row['measured_points']} | "
            f"{'' if row['wide_runtime_complete'] else ''} | "
            f"{spread} | {target_spread} | {row['gate_result']} |"
        )
    lines.extend([
        "",
        " CPU/Memory cost  5% QPS  5 ",
        "",
        "## Reason",
        "",
    ])
    lines.extend(
        f"- **{row['label']}**{row['gate_reason']}"
        for row in rows
    )
    lines.extend([
        "",
        " benchmark  cost-only OLTP/CPU /TPC-H Q6  XP Q9 ",
        "",
    ])
    output_md = WIDE / "FINAL_GATE_REPORT.md"
    output_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "output_json": str(output_json),
        "output_md": str(output_md),
        "scheme_valid": result["scheme_valid_for_formal_experiment"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
