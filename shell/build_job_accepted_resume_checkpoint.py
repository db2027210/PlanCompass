#!/usr/bin/env python3
"""Build a fail-closed JOB accepted-state resume checkpoint from sealed JSONL evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError(f"history line {line_number} is not an object")
            events.append(value)
    return events


def one(items: List[Dict[str, Any]], description: str) -> Dict[str, Any]:
    if len(items) != 1:
        raise RuntimeError(f"expected exactly one {description}, got {len(items)}")
    return items[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = args.attempt_root.resolve()
    history_path = args.history.resolve()
    events = load_events(history_path)

    complete = one(
        [
            event
            for event in events
            if event.get("event") == "experience_table_gradient_initial_real_replay_complete"
            and event.get("round_index") == 0
            and event.get("validated_candidate_count") == 10
        ],
        "round-0 ten-candidate completion event",
    )
    accepted = [
        item
        for item in complete.get("candidate_evaluations") or []
        if item.get("accepted_by_actual_score") is True
    ]
    selected_rank = complete.get("selected_estimated_rank")
    selected = one(
        [item for item in accepted if item.get("estimated_rank") == selected_rank],
        "selected accepted candidate",
    )
    if selected.get("actual_accept_reason") != "db_qps_higher_and_target_bottleneck_lower":
        raise RuntimeError("selected candidate does not satisfy the common outer contract")

    candidate_index = int(selected["candidate_index"])
    validation_event = one(
        [
            event
            for event in events
            if event.get("event") == "feedback_final_validation"
            and event.get("round_index") == 0
            and event.get("candidate_index") == candidate_index
        ],
        "selected feedback validation event",
    )
    degradation_event = one(
        [
            event
            for event in events
            if event.get("event") == "feedback_degradation_summary"
            and event.get("round_index") == 0
            and event.get("candidate_index") == candidate_index
        ],
        "selected degradation event",
    )
    online_update = one(
        [
            event
            for event in events
            if event.get("event") == "experience_table_online_delta_update"
            and event.get("round_index") == 0
        ],
        "round-0 online experience-table update",
    )
    range_update = one(
        [
            event
            for event in events
            if event.get("event") == "gradient_parameter_range_update"
            and event.get("round_index") == 0
            and event.get("reason") == "initial_common_outer_accepted"
        ],
        "round-0 accepted range update",
    )

    counters = {
        "global_replay_count": int((root / "replay_counter.txt").read_text().strip()),
        "formal_search_replay_count": int(
            (root / "formal_search_replay_counter.txt").read_text().strip()
        ),
        "accepted_shrink_count": int(
            (root / "formal_search_accepted_counter.txt").read_text().strip()
        ),
        "startup_baseline_count": int(
            (root / "formal_startup_baseline_counter.txt").read_text().strip()
        ),
    }
    expected_counters = {
        "global_replay_count": 41,
        "formal_search_replay_count": 10,
        "accepted_shrink_count": 1,
        "startup_baseline_count": 1,
    }
    if counters != expected_counters:
        raise RuntimeError(f"resume counters changed: {counters} != {expected_counters}")

    accepted_validation = selected.get("candidate_final_validation") or {}
    protocol = ((accepted_validation.get("resource_stats") or {}).get(
        "candidate_measurement_protocol"
    ) or {})
    point_result_path = Path(str(protocol.get("point_result_path") or "")).resolve()
    point_result = json.loads(point_result_path.read_text(encoding="utf-8"))
    measurement = point_result.get("measurement") or {}
    if point_result.get("status") != "complete":
        raise RuntimeError("accepted point_result is not complete")
    if measurement.get("correctness_passed") is not True:
        raise RuntimeError("accepted point failed correctness")
    stability = measurement.get("resource_stability") or {}
    if stability.get("passed") is not True or stability.get("stable") is not True:
        raise RuntimeError("accepted point failed stability")
    if point_result.get("oom_killed") or int(point_result.get("database_restart_count") or 0):
        raise RuntimeError("accepted point has OOM/restart evidence")

    accepted_params = selected.get("candidate_optimized_params") or {}
    point_costs = point_result.get("cost_parameters") or {}
    if set(point_costs) != set(accepted_params) or any(
        abs(float(point_costs[name]) - float(accepted_params[name]))
        > max(5e-7, abs(float(accepted_params[name])) * 1e-5)
        for name in accepted_params
    ):
        raise RuntimeError("accepted params do not match point_result readback")
    if float(accepted_validation["db_qps"]) <= 2.027730735851316:
        raise RuntimeError("accepted DB QPS is not strictly above immutable R0")
    if float((accepted_validation.get("utilization_pct") or {})["memory"]) >= 85.98963264172697:
        raise RuntimeError("accepted memory is not strictly below immutable R0")

    sensitivity_table: Dict[str, Dict[str, Any]] = {}
    for update in online_update.get("parameter_updates") or []:
        parameter = str(update.get("parameter") or "")
        effects = update.get("updated_effects")
        if not parameter or not isinstance(effects, dict):
            raise RuntimeError("online update is missing a parameter/effects record")
        sensitivity_table[parameter] = effects
    if len(sensitivity_table) != 7:
        raise RuntimeError(f"expected seven restored T rows, got {len(sensitivity_table)}")

    baseline_point_path = root / "formal_points/point_0031/point_result.json"
    baseline_point = json.loads(baseline_point_path.read_text(encoding="utf-8"))
    replayed_candidate_params = [
        item.get("candidate_optimized_params") or {}
        for item in complete.get("candidate_evaluations") or []
    ]
    if len(replayed_candidate_params) != 10:
        raise RuntimeError("resume checkpoint requires exactly ten replayed candidates")

    checkpoint = {
        "schema_version": 1,
        "kind": "job_gradient_accepted_resume_checkpoint_v1",
        "attempt_root": str(root),
        "source_history_path": str(history_path),
        "source_history_sha256": sha256_file(history_path),
        "accepted_point_result_path": str(point_result_path),
        "accepted_point_result_sha256": sha256_file(point_result_path),
        "baseline_point_result_path": str(baseline_point_path),
        "baseline_point_result_sha256": sha256_file(baseline_point_path),
        "counters": counters,
        "next_feedback_round": 1,
        "accepted_candidate_index": candidate_index,
        "accepted_estimated_rank": int(selected_rank),
        "accepted_reason": selected["actual_accept_reason"],
        "accepted_params": accepted_params,
        "accepted_validation": accepted_validation,
        "accepted_estimated_resource": selected.get("candidate_estimated_resource") or {},
        "accepted_final_top_sql": validation_event.get("top") or [],
        "accepted_final_plan_samples": validation_event.get("final_plan_hashes") or [],
        "accepted_final_plan_changes": [],
        "accepted_degradation_summary": degradation_event.get("summary") or {},
        "expected_range_state_after_accept": range_update.get("range_state") or {},
        "online_sensitivity_table": sensitivity_table,
        "online_update_event": online_update,
        "replayed_scope_incumbent_params": baseline_point.get("cost_parameters") or {},
        "replayed_candidate_params": replayed_candidate_params,
        "initial_completion_summary": {
            "validated_candidate_count": complete.get("validated_candidate_count"),
            "eligible_count": complete.get("eligible_count"),
            "selected_estimated_rank": selected_rank,
            "expanded_to_second_batch": complete.get("expanded_to_second_batch"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(args.output)
    print(
        json.dumps(
            {
                "checkpoint_path": str(args.output.resolve()),
                "checkpoint_sha256": sha256_file(args.output),
                "accepted_point": str(point_result_path),
                "accepted_shrink_count": counters["accepted_shrink_count"],
                "next_feedback_round": 1,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
