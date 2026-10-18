#!/usr/bin/env python3
"""Audit the metric mismatch behind TPC-H Algorithm A's reported gain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def gain(candidate: float, baseline: float) -> float:
    return candidate / baseline - 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--final-decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state_path = args.state.resolve()
    baseline_path = args.baseline.resolve()
    final_path = args.final_decision.resolve()
    state = load(state_path)
    baseline = load(baseline_path)
    final = load(final_path)
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "tpch_sf4_memory"
        or state.get("status") != "COMPLETE"
        or state.get("converged") is not True
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
    ):
        raise RuntimeError("TPC-H Algorithm A evidence differs")
    baseline_rows = dict(baseline.get("measurements") or {})
    if set(baseline_rows) != {"tpch_w1", "tpch_w2"}:
        raise RuntimeError("Algorithm A two-worker baseline differs")
    server_baseline = fmean(float(row["db_qps"]) for row in baseline_rows.values())
    client_baseline = fmean(
        float(row["client_qps_audit"]) for row in baseline_rows.values()
    )
    canonical = dict(state["canonical_baseline"])
    if abs(server_baseline - float(canonical["db_qps"])) > 1e-12:
        raise RuntimeError("Algorithm A canonical server baseline differs")
    accepted = dict(final.get("accepted") or {})
    final_row = dict(accepted.get("row") or {})
    final_outer = dict(accepted.get("decision") or {})
    if final_outer.get("accepted") is not True:
        raise RuntimeError("Algorithm A final point was not accepted")
    final_client = float(final_row["client_qps_audit"])
    final_server_normalized = float(final_row["db_qps"])
    final_server_raw = float(
        dict(final_row["same_worker_normalization"])["raw_db_qps"]
    )
    worker_id = str(final_row["worker_id"])
    same_worker_client_baseline = float(
        baseline_rows[worker_id]["client_qps_audit"]
    )

    accepted_points: list[dict[str, Any]] = []
    for binding in state.get("accepted_history") or []:
        path = Path(str(binding["decision_path"])).resolve()
        if sha(path) != binding.get("decision_sha256"):
            raise RuntimeError(f"Algorithm A accepted decision hash differs: {path}")
        document = load(path)
        row = dict(dict(document["accepted"])["row"])
        accepted_points.append({
            "accepted": int(binding["accepted"]),
            "round_index": int(binding["round_index"]),
            "worker_id": str(row["worker_id"]),
            "client_qps": float(row["client_qps_audit"]),
            "normalized_server_questions_qps": float(row["db_qps"]),
            "raw_server_questions_qps": float(
                dict(row["same_worker_normalization"])["raw_db_qps"]
            ),
            "decision_path": str(path),
            "decision_sha256": sha(path),
        })
    best_client = max(accepted_points, key=lambda row: row["client_qps"])
    best_worker_baseline = float(
        baseline_rows[best_client["worker_id"]]["client_qps_audit"]
    )
    result = {
        "schema_version": "mysql_tpch_algorithm_a_client_qps_audit_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "tpch_sf4_memory",
        "state_path": str(state_path),
        "state_sha256": sha(state_path),
        "baseline_path": str(baseline_path),
        "baseline_sha256": sha(baseline_path),
        "final_decision_path": str(final_path),
        "final_decision_sha256": sha(final_path),
        "baseline": {
            "server_questions_qps_mean": server_baseline,
            "client_qps_mean": client_baseline,
            "worker_client_qps": {
                worker: float(row["client_qps_audit"])
                for worker, row in baseline_rows.items()
            },
        },
        "reported_final": {
            "normalized_server_questions_qps": final_server_normalized,
            "reported_gain_vs_canonical_baseline": float(
                final_outer["qps_gain_vs_canonical_baseline"]
            ),
        },
        "actual_final": {
            "worker_id": worker_id,
            "raw_server_questions_qps": final_server_raw,
            "raw_server_questions_gain_vs_baseline_mean": gain(
                final_server_raw, server_baseline
            ),
            "client_qps": final_client,
            "client_qps_gain_vs_baseline_mean": gain(
                final_client, client_baseline
            ),
            "client_qps_gain_vs_same_worker_baseline": gain(
                final_client, same_worker_client_baseline
            ),
        },
        "best_accepted_client_point": {
            **best_client,
            "client_qps_gain_vs_baseline_mean": gain(
                float(best_client["client_qps"]), client_baseline
            ),
            "client_qps_gain_vs_same_worker_baseline": gain(
                float(best_client["client_qps"]), best_worker_baseline
            ),
        },
        "accepted_client_qps_monotonic": all(
            right["client_qps"] >= left["client_qps"]
            for left, right in zip(accepted_points, accepted_points[1:])
        ),
        "conclusion": {
            "reported_gain_is_server_questions_counter_not_client_qps": True,
            "final_client_qps_improved": final_client > client_baseline,
            "final_point_is_client_qps_champion": (
                final_client == float(best_client["client_qps"])
            ),
        },
        "accepted_points": accepted_points,
    }
    atomic(args.output.resolve(), result)
    print(json.dumps(result["conclusion"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
