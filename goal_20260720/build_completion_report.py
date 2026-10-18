#!/usr/bin/env python3
"""Build the final auditable MySQL formal-matrix and recovery report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def compact_matrix(audit: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    history = audit.get("history_audit", {})
    issues: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in history.get("reused", []):
        path = Path(str(item["history_path"]))
        result = read_json(path)
        meta = result.get("meta", {})
        if meta.get("db") != "mysql8":
            issues.append(f"{item['run_id']}: meta.db is not mysql8")
        if meta.get("method") != "outer_binary_inner_estimated_grid":
            issues.append(f"{item['run_id']}: unexpected method {meta.get('method')!r}")
        acceptance = item.get("acceptance", {})
        rows.append({
            "run_id": item.get("run_id"),
            "benchmark": meta.get("benchmark"),
            "bottleneck": meta.get("bottleneck"),
            "repeat_id": meta.get("repeat_id"),
            "result_path": str(path),
            "passed": acceptance.get("passed") is True,
            "qps_gain_pct": acceptance.get("qps_gain_pct"),
            "bottleneck_drop_pct_points": acceptance.get("bottleneck_drop_pct_points"),
            "resource_changes": acceptance.get("resource_changes"),
            "gates": acceptance.get("gates", {}),
        })
    if history.get("missing_count") != 0:
        issues.append(f"matrix has {history.get('missing_count')} missing runs")
    if len(rows) != 18:
        issues.append(f"matrix has {len(rows)} accepted rows, expected 18")
    if any(not row["passed"] for row in rows):
        issues.append("at least one matrix row is not accepted")
    return rows, issues


def compact_recovery(audit: dict[str, Any]) -> dict[str, Any]:
    attempts = []
    for item in audit.get("attempts", []):
        attempts.append({
            key: item.get(key)
            for key in (
                "attempt_index", "result_path", "passed", "qps_before", "qps_after",
                "delta_bottleneck_util_pp", "cpu_util_change_pp", "p95_before_ms",
                "p95_after_ms", "p99_before_ms", "p99_after_ms", "final_latency_source",
                "checks",
            )
        })
    return {
        "protocol_id": audit.get("protocol_id"),
        "attempt_limit": audit.get("attempt_limit"),
        "passed_attempts": audit.get("passed_attempts"),
        "required_passes": audit.get("required_passes"),
        "acceptance_met": audit.get("acceptance_met") is True,
        "attempts": attempts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MySQL ",
        "",
        f"- {report['generated_at_utc']}",
        f"- {report['matrix']['accepted_runs']}/18",
        f"- B5 {report['recoveries']['B5']['passed_attempts']}/{report['recoveries']['B5']['attempt_limit']}",
        f"- B6 {report['recoveries']['B6']['passed_attempts']}/{report['recoveries']['B6']['attempt_limit']}",
        f"- {'' if report['complete'] else ''}",
        "",
        "| Run | Benchmark |  | QPS  | (pp) |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in report["matrix"]["runs"]:
        gain = row.get("qps_gain_pct")
        drop = row.get("bottleneck_drop_pct_points")
        lines.append(
            f"| {row['run_id']} | {row['benchmark']} | {row['bottleneck']} | "
            f"{gain:.2f}% | {drop:.2f}" if isinstance(drop, (int, float)) else
            f"| {row['run_id']} | {row['benchmark']} | {row['bottleneck']} | {gain:.2f}% | — |"
        )
    if report["issues"]:
        lines.extend(["", "## ", ""] + [f"- {value}" for value in report["issues"]])
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    matrix = read_json(args.matrix_audit)
    b5 = compact_recovery(read_json(args.b5_audit))
    b6 = compact_recovery(read_json(args.b6_audit))
    rows, issues = compact_matrix(matrix)
    if not b5["acceptance_met"]:
        issues.append("B5 fixed recovery did not meet its acceptance rule")
    if not b6["acceptance_met"]:
        issues.append("B6 fixed recovery did not meet its acceptance rule")
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete": not issues,
        "matrix": {"expected_runs": 18, "accepted_runs": len(rows), "runs": rows},
        "recoveries": {"B5": b5, "B6": b6},
        "invalidated_batches": [str(path.resolve()) for path in args.invalidated_batch],
        "issues": issues,
    }
    write_text(args.output_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(args.output_markdown, render_markdown(report))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-audit", type=Path, required=True)
    parser.add_argument("--b5-audit", type=Path, required=True)
    parser.add_argument("--b6-audit", type=Path, required=True)
    parser.add_argument("--invalidated-batch", type=Path, action="append", default=[])
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    report = run(parse_args())
    print(json.dumps({"complete": report["complete"], "issues": report["issues"]}, ensure_ascii=False))
