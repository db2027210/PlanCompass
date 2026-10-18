#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

def load_result(run_dir: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "status": None,
        "alive_files": [],
    }
    summary_path = run_dir / "parallel_replay_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        out["status"] = summary.get("status")
        out["stop_reason"] = summary.get("stop_reason")
        out["baseline_gate"] = summary.get("baseline_gate")
        out["anchor_gate"] = summary.get("anchor_gate")
    jsonl = run_dir / "parallel_validation_results.jsonl"
    rows: List[Dict[str, Any]] = []
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    out["result_count"] = len(rows)
    if not rows:
        # try worker logs status
        log = None
        for p in sorted(run_dir.glob("**/*.log")):
            log = p
        out["note"] = "no validation results yet"
        return out
    # take first/only candidate row
    row = rows[0]
    util = row.get("actual_utilization_pct") or {}
    base_util = row.get("worker_baseline_utilization_pct") or {}
    delta = row.get("resource_delta_pp") or {}
    qps_delta = row.get("db_qps_delta_pct")
    base_qps = row.get("worker_baseline_qps")
    cand_qps = (row.get("db_delta") or {}).get("db_qps")
    io_src = ((row.get("raw_resource_stats") or {}).get("io_source") or {}).get("value")
    io_dev = ((row.get("raw_resource_stats") or {}).get("io_disk_devices") or {}).get("value")
    io_warn = ((row.get("raw_resource_stats") or {}).get("io_metric_warning") or {}).get("value")
    family_pass = bool(
        qps_delta is not None and qps_delta > 0
        and float(delta.get("memory") or 0) < 0
        and float(delta.get("cpu") or 0) > 0
        and float(delta.get("io") or 0) > 0
        and not io_warn
    )
    out.update({
        "worker": row.get("parallel_worker_id") or (row.get("worker") or {}).get("worker_id"),
        "candidate_id": row.get("candidate_id"),
        "params": row.get("params"),
        "status_row": row.get("status"),
        "base_qps": base_qps,
        "cand_qps": cand_qps,
        "db_qps_delta_pct": qps_delta,
        "base_util": base_util,
        "cand_util": util,
        "resource_delta_pp": delta,
        "ap_qps_base": (row.get("worker_baseline_benchmark") or {}).get("ap_qps"),
        "ap_qps_cand": (row.get("benchmark") or {}).get("ap_qps"),
        "io_source": io_src,
        "io_disk_devices": io_dev,
        "io_metric_warning": io_warn,
        "family_positive": family_pass,
        "accept_reason": (
            "QPS up + Mem down + CPU up + physical IO up"
            if family_pass else
            "missing full positive direction"
        ),
    })
    return out

def main(argv: List[str]) -> int:
    runs = [Path(a) for a in argv[1:]]
    payload = [load_result(r) for r in runs]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
