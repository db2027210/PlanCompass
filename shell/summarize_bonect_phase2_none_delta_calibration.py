#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_DIR = ROOT / "report/bonect/none_delta_cal_v78_20260711"
MANIFEST_PATH = CAMPAIGN_DIR / "manifest.json"
STATE_PATH = CAMPAIGN_DIR / "state.jsonl"
JSON_PATH = CAMPAIGN_DIR / "summary.json"
CSV_PATH = CAMPAIGN_DIR / "summary.csv"
MD_PATH = CAMPAIGN_DIR / "summary.md"


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def complete_records(path: Path) -> List[Dict[str, Any]]:
    latest: Dict[int, Dict[str, Any]] = {}
    latest_current_baseline: Dict[str, Any] = {}
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("event") != "calibration_point_complete":
                continue
            record = dict(record)
            kind = str(record.get("kind") or "")
            if kind == "current_feature_baseline":
                latest_current_baseline = record
            elif kind == "current_anchor_repeat" and latest_current_baseline:
                try:
                    baseline_ts = datetime.fromisoformat(
                        str(latest_current_baseline.get("timestamp") or "")
                    )
                    candidate_ts = datetime.fromisoformat(
                        str(record.get("timestamp") or "")
                    )
                    elapsed_seconds = (candidate_ts - baseline_ts).total_seconds()
                except ValueError:
                    elapsed_seconds = -1.0
                same_campaign = (
                    record.get("campaign_id")
                    == latest_current_baseline.get("campaign_id")
                )
                if same_campaign and 0.0 <= elapsed_seconds <= 1800.0:
                    record["paired_baseline_valid"] = True
                    record["paired_baseline_position"] = "before"
                    record["paired_baseline"] = {
                        key: latest_current_baseline.get(key)
                        for key in (
                            "run_id",
                            "history_path",
                            "db_qps",
                            "db_xact_tps",
                            "resource_normalized",
                            "utilization_pct",
                            "resource_sources",
                            "stable_sample_count",
                            "plan_features",
                            "plan_feature_count",
                            "plan_feature_signature",
                        )
                    }
            index = int(record.get("point_index", -1))
            if index >= 0:
                latest[index] = record
    return [latest[index] for index in sorted(latest)]


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def delta_pct(value: float, baseline: float) -> float:
    return 100.0 * (value - baseline) / baseline if baseline else 0.0


def comparison_baseline(
    record: Dict[str, Any],
    *,
    fallback_resource: Dict[str, Any],
    fallback_db_qps: float,
) -> Tuple[Dict[str, float], bool, str]:
    paired_baseline = (
        record.get("paired_baseline")
        if record.get("paired_baseline_valid")
        else {}
    ) or {}
    if paired_baseline:
        resource = paired_baseline.get("resource_normalized") or {}
        return (
            {
                "cpu": number(resource.get("cpu")),
                "memory": number(resource.get("memory")),
                "io": number(resource.get("io")),
                "db_qps": number(paired_baseline.get("db_qps")),
            },
            True,
            "adjacent_paired_baseline",
        )
    if str(record.get("kind") or "") == "current_feature_baseline":
        resource = record.get("resource_normalized") or {}
        return (
            {
                "cpu": number(resource.get("cpu")),
                "memory": number(resource.get("memory")),
                "io": number(resource.get("io")),
                "db_qps": number(record.get("db_qps")),
            },
            True,
            "self_baseline",
        )
    return (
        {
            "cpu": number(fallback_resource.get("cpu")),
            "memory": number(fallback_resource.get("memory")),
            "io": number(fallback_resource.get("io")),
            "db_qps": fallback_db_qps,
        },
        False,
        "unpaired_cross_time_diagnostic",
    )


def optional_delta(value: float, baseline: float, comparison_valid: bool) -> Optional[float]:
    if not comparison_valid:
        return None
    return delta_pct(value, baseline)


def format_delta(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def main() -> int:
    manifest = read_json(MANIFEST_PATH)
    records = complete_records(STATE_PATH)
    baseline_resource = manifest.get("baseline_resource_normalized") or {}
    baseline_db_qps = number(manifest.get("baseline_db_qps"))
    baseline_source = {
        "kind": "manifest_historical_evidence",
        "run_id": manifest.get("baseline_evidence_run_id"),
        "point_index": None,
    }
    current_baselines = [
        record
        for record in records
        if record.get("kind") == "current_feature_baseline"
    ]
    if current_baselines:
        current_baseline = current_baselines[-1]
        baseline_resource = current_baseline.get("resource_normalized") or {}
        baseline_db_qps = number(current_baseline.get("db_qps"))
        baseline_source = {
            "kind": "current_feature_baseline",
            "run_id": current_baseline.get("run_id"),
            "point_index": current_baseline.get("point_index"),
        }
    baseline = {
        "cpu": number(baseline_resource.get("cpu")),
        "memory": number(baseline_resource.get("memory")),
        "io": number(baseline_resource.get("io")),
        "db_qps": baseline_db_qps,
    }
    rows: List[Dict[str, Any]] = []
    for record in records:
        paired_baseline = (
            record.get("paired_baseline")
            if record.get("paired_baseline_valid")
            else {}
        ) or {}
        row_baseline, comparison_valid, comparison_source = comparison_baseline(
            record,
            fallback_resource=baseline_resource,
            fallback_db_qps=baseline["db_qps"],
        )
        resource = record.get("resource_normalized") or {}
        utilization = record.get("utilization_pct") or {}
        sources = record.get("resource_sources") or {}
        samples = record.get("stable_sample_count") or {}
        benchmark = record.get("benchmark") or {}
        cpu = number(resource.get("cpu"))
        memory = number(resource.get("memory"))
        io = number(resource.get("io"))
        io_read = number(resource.get("io_read"))
        io_write = number(resource.get("io_write"))
        db_qps = number(record.get("db_qps"))
        io_source = str(sources.get("io_source") or "")
        io_devices = str(sources.get("io_disk_devices") or "")
        io_valid = bool(
            io > 0.0
            and io_read > 0.0
            and io_write > 0.0
            and io_source == "vm_disk_device"
            and "vda" in io_devices
            and number(samples.get("io")) >= 5
        )
        rows.append(
            {
                "point_index": record.get("point_index"),
                "point_name": record.get("point_name"),
                "run_id": record.get("run_id"),
                "paired_baseline_valid": bool(record.get("paired_baseline_valid")),
                "paired_baseline_position": record.get("paired_baseline_position"),
                "paired_baseline_run_id": paired_baseline.get("run_id"),
                "comparison_baseline_valid": comparison_valid,
                "comparison_baseline_source": comparison_source,
                "db_qps": db_qps,
                "db_qps_delta_pct": optional_delta(
                    db_qps, row_baseline["db_qps"], comparison_valid
                ),
                "xp_qps": number(benchmark.get("xp_qps")),
                "xp_tps": number(benchmark.get("xp_tps")),
                "cpu": cpu,
                "cpu_delta_pct": optional_delta(
                    cpu, row_baseline["cpu"], comparison_valid
                ),
                "cpu_util_pct": number(utilization.get("cpu")),
                "memory_gib": memory,
                "memory_delta_pct": optional_delta(
                    memory, row_baseline["memory"], comparison_valid
                ),
                "memory_util_pct": number(utilization.get("memory")),
                "io_mib_s": io,
                "io_delta_pct": optional_delta(
                    io, row_baseline["io"], comparison_valid
                ),
                "io_util_pct": number(utilization.get("io")),
                "io_read_mib_s": io_read,
                "io_write_mib_s": io_write,
                "io_source": io_source,
                "io_devices": io_devices,
                "io_valid": io_valid,
                "strong_none_gate_passed": record.get("strong_none_gate_passed"),
                "plan_capture_count": record.get("plan_capture_count"),
                "all_sql_count": record.get("all_sql_count"),
                "plan_feature_count": record.get("plan_feature_count"),
                "plan_feature_signature": record.get("plan_feature_signature"),
                "plan_capture_file": record.get("plan_capture_file"),
                "plan_features": record.get("plan_features") or {},
                "plan_estimated_resource_normalized": (
                    record.get("plan_estimated_resource_normalized") or {}
                ),
                "params": record.get("params") or {},
                "history_path": record.get("history_path"),
            }
        )

    payload = {
        "campaign_id": manifest.get("campaign_id"),
        "baseline": baseline,
        "baseline_source": baseline_source,
        "completed_count": len(rows),
        "comparison_valid_count": sum(
            1 for row in rows if row["comparison_baseline_valid"]
        ),
        "planned_count": len(manifest.get("points") or []),
        "rows": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fieldnames = [
        key
        for key in (rows[0].keys() if rows else [])
        if key not in {
            "params",
            "history_path",
            "plan_capture_file",
            "plan_features",
            "plan_estimated_resource_normalized",
        }
    ]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})

    lines = [
        "# Phase2 None ",
        "",
        f"- campaign: `{manifest.get('campaign_id')}`",
        f"- `{len(rows)}/{len(manifest.get('points') or [])}`",
        f"- baseline run: `{baseline_source.get('run_id')}`",
        f"- baseline source: `{baseline_source.get('kind')}`",
        (
            "- baseline: DB QPS "
            f"`{baseline['db_qps']:.3f}`CPU `{baseline['cpu']:.3f}` cores"
            f"Memory `{baseline['memory']:.3f}` GiBIO `{baseline['io']:.3f}` MiB/s"
        ),
        "",
        "| idx |  |  | DB QPS Δ | CPU Δ | Memory Δ | IO Δ | XP QPS/TPS |  | IO | None gate |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        if row["comparison_baseline_source"] == "self_baseline":
            comparison_label = "baseline"
        elif row["paired_baseline_valid"]:
            comparison_label = (
                "/" + str(row.get("paired_baseline_position") or "")
            )
        else:
            comparison_label = "/"
        lines.append(
            f"| {row['point_index']} | `{row['point_name']}` | "
            f"{comparison_label} | "
            f"{format_delta(row['db_qps_delta_pct'])} | "
            f"{format_delta(row['cpu_delta_pct'])} | "
            f"{format_delta(row['memory_delta_pct'])} | "
            f"{format_delta(row['io_delta_pct'])} | "
            f"{row['xp_qps']:.2f}/{row['xp_tps']:.2f} | "
            f"{row.get('plan_feature_count') or 0} | "
            f"{'' if row['io_valid'] else ''} | "
            f"{row['strong_none_gate_passed']} |"
        )
    lines.extend(
        [
            "",
            "## ",
            "",
            "- / baseline-relative  delta ",
            "- IO  VM `vda`read/write ",
            "-  fallback  delta ",
            "-  `paired_baseline_valid=true`  delta  restored default baseline",
            "-  delta  `N/A` fallback/ baseline ",
            "-  top15 pairwise inversion  `<=0.10`  full-grid",
            "",
        ]
    )
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(MD_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
