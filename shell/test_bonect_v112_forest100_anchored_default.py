#!/usr/bin/env python3
"""Verify the authoritative v112 forest-100 anchor: at the exact PostgreSQL
default parameters the anchored forest must predict QPS delta ~0 and CPU/Memory/
IO deltas ~0. Any large default improvement is a hard failure (the unsafe
no-baseline KNN-5 rescore failed exactly this: it omitted the real default
baseline label and predicted the default point as a large improvement).

Also confirm known strong points recall near the top and IO predictions are
nonconstant.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_replay_delta_tree import predict_replay_delta_tree  # noqa: E402


FOREST100 = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_v112_top18_plus_baseline_replay_delta_forest100.json"
)
LABELS = (
    ROOT
    / "history/bonect_parallel_vm_replay/memory_v112_top18_plus_baseline_labels_20260712.jsonl"
)

PG_DEFAULT = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def load_jsonl(path):
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_row(rows, candidate_id):
    for row in rows:
        if str(row.get("candidate_id") or "") == candidate_id:
            return row
    raise KeyError(candidate_id)


def main() -> int:
    assert FOREST100.is_file(), f"forest100 missing: {FOREST100}"
    assert LABELS.is_file(), f"labels missing: {LABELS}"
    forest = json.loads(FOREST100.read_text())
    assert forest["model_type"] == "multioutput_anchored_random_forest_regressor"
    assert forest["validation_protocol"]["baseline_candidates"] == [
        "__baseline_postgresql_defaults__"
    ], forest["validation_protocol"]
    rows = load_jsonl(LABELS)
    baseline_row = find_row(rows, "__baseline_postgresql_defaults__")
    baseline_params = baseline_row.get("params") or {}
    assert set(baseline_params) == set(PG_DEFAULT), baseline_params
    baseline_pf = baseline_row.get("plan_delta_features") or {}

    pred_default, detail_default = predict_replay_delta_tree(forest, PG_DEFAULT, baseline_pf)
    print(
        f"forest100 default prediction: QPS={pred_default['db_qps_delta_pct']:+.6f} "
        f"CPU={pred_default['cpu_delta']:+.8f} MEM={pred_default['memory_delta']:+.8f} "
        f"IO={pred_default['io_delta']:+.8f} out_of_support={detail_default.get('out_of_support_split_features') or []}",
        flush=True,
    )
    # Step 3 hard gate: default must NOT be predicted as a large improvement.
    assert abs(pred_default["db_qps_delta_pct"]) < 5.0, pred_default
    assert abs(pred_default["cpu_delta"]) < 0.1, pred_default
    assert abs(pred_default["memory_delta"]) < 0.02, pred_default
    assert abs(pred_default["io_delta"]) < 2.0, pred_default

    # Known strong points should predict strongly positive QPS delta.
    c4_row = find_row(rows, "candidate_0004")
    pred_c4, _ = predict_replay_delta_tree(
        forest, c4_row["params"], c4_row.get("plan_delta_features") or {}
    )
    print(f"forest100 candidate_0004 prediction: QPS={pred_c4['db_qps_delta_pct']:+.4f}", flush=True)
    assert pred_c4["db_qps_delta_pct"] > 50.0, pred_c4

    # IO predictions must be nonconstant across labels (plan features differ).
    io_values = set()
    for row in rows:
        params = row.get("params") or {}
        pf = row.get("plan_delta_features") or {}
        if set(params) != set(PG_DEFAULT):
            continue
        pred, _ = predict_replay_delta_tree(forest, params, pf)
        io_values.add(round(float(pred["io_delta"]), 6))
    print(f"forest100 IO predictions distinct={len(io_values)}", flush=True)
    assert len(io_values) > 1, io_values
    print("bonect v112 forest100 anchored baseline test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())