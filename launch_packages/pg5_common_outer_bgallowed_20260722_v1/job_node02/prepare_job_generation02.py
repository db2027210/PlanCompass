#!/usr/bin/env python3
"""Seal JOB generation_02 from the first accepted T-SGD outer round."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


PARAMETER_OPTIMIZER_SHA256 = (
    "cc84328de6fbff2923fb9ec76c66d001e699c5d966615560a7e8c2b380b2d033"
)
EXPECTED_COUNTERS = {
    "global_replay_count": 48,
    "formal_search_replay_count": 6,
    "accepted_shrink_count": 1,
    "startup_baseline_count": 1,
}
EXPECTED_ACCEPTED_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 3170.2992276262207,
    "parallel_tuple_cost": 0.1,
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def write_new_json(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def load_events(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def exactly_one(values: Iterable[Dict[str, Any]], description: str) -> Dict[str, Any]:
    selected = list(values)
    if len(selected) != 1:
        raise RuntimeError(f"expected exactly one {description}, got {len(selected)}")
    return selected[0]


def params_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return set(left) == set(right) and all(
        math.isclose(float(left[name]), float(right[name]), rel_tol=1e-12, abs_tol=1e-12)
        for name in left
    )


def params_readback_close(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return set(left) == set(right) and all(
        abs(float(left[name]) - float(right[name]))
        <= max(5e-7, abs(float(right[name])) * 1e-5)
        for name in left
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--source-history", type=Path, required=True)
    parser.add_argument("--generation-root", type=Path, required=True)
    args = parser.parse_args()

    attempt = args.attempt_root.resolve()
    workspace = args.workspace.resolve()
    source_config_path = args.source_config.resolve()
    source_history_path = args.source_history.resolve()
    generation = args.generation_root.resolve()
    if generation.exists() or generation.is_symlink():
        raise RuntimeError(f"generation root already exists: {generation}")
    if sha256_file(workspace / "parameter_optimizer.py") != PARAMETER_OPTIMIZER_SHA256:
        raise RuntimeError("generation_02 optimizer SHA mismatch")

    counter_paths = {
        "global_replay_count": attempt / "replay_counter.txt",
        "formal_search_replay_count": attempt / "formal_search_replay_counter.txt",
        "accepted_shrink_count": attempt / "formal_search_accepted_counter.txt",
        "startup_baseline_count": attempt / "formal_startup_baseline_counter.txt",
    }
    counters = {
        name: int(path.read_text(encoding="utf-8").strip())
        for name, path in counter_paths.items()
    }
    if counters != EXPECTED_COUNTERS:
        raise RuntimeError(f"generation_02 counters changed: {counters}")

    config = json.loads(source_config_path.read_text(encoding="utf-8"))
    events = load_events(source_history_path)
    complete = exactly_one(
        [
            event
            for event in events
            if event.get("event")
            == "experience_table_gradient_initial_real_replay_complete"
            and int(event.get("round_index", -1)) == 0
            and int(event.get("validated_candidate_count", -1)) == 5
            and int(event.get("initial_batch_size", -1)) == 5
        ],
        "round-0 five-valid barrier completion",
    )
    if complete.get("expanded_to_second_batch") is not False:
        raise RuntimeError("first five contained acceptance but expanded unexpectedly")
    evaluations = list(complete.get("candidate_evaluations") or [])
    if len(evaluations) != 5:
        raise RuntimeError("round-0 barrier must contain exactly five valid evaluations")
    selected_rank = int(complete.get("selected_estimated_rank") or 0)
    selected = exactly_one(
        [
            item
            for item in evaluations
            if int(item.get("estimated_rank") or 0) == selected_rank
            and item.get("accepted_by_actual_score") is True
        ],
        "selected accepted evaluation",
    )
    if selected_rank != 1 or selected.get("actual_accept_reason") != (
        "db_qps_higher_and_target_bottleneck_lower"
    ):
        raise RuntimeError("generation_02 incumbent is not the sealed rank-1 acceptance")
    accepted_params = selected.get("candidate_optimized_params") or {}
    if not params_equal(accepted_params, EXPECTED_ACCEPTED_PARAMS):
        raise RuntimeError("generation_02 accepted incumbent drifted")

    candidate_index = int(selected.get("candidate_index") or 0)
    # p44 completed immediately before the v5 marker-metadata process restart,
    # so its validation is sealed inside the five-valid completion event and
    # point_result rather than duplicated as a standalone JSONL event.
    validation_matches = [
        event
        for event in events
        if event.get("event") == "feedback_final_validation"
        and int(event.get("round_index", -1)) == 0
        and int(event.get("candidate_index", -1)) == candidate_index
    ]
    degradation_matches = [
        event
        for event in events
        if event.get("event") == "feedback_degradation_summary"
        and int(event.get("round_index", -1)) == 0
        and int(event.get("candidate_index", -1)) == candidate_index
    ]
    if len(validation_matches) > 1 or len(degradation_matches) > 1:
        raise RuntimeError("accepted p44 has duplicate standalone validation events")
    validation_event = validation_matches[0] if validation_matches else {}
    degradation_event = degradation_matches[0] if degradation_matches else {}
    online_event = exactly_one(
        [
            event
            for event in events
            if event.get("event") == "experience_table_online_update_and_suffix_rerank"
            and int(event.get("round_index", -1)) == 0
        ],
        "round-0 online T update",
    )
    range_event = exactly_one(
        [
            event
            for event in events
            if event.get("event") == "gradient_parameter_range_update"
            and int(event.get("round_index", -1)) == 0
            and event.get("reason") == "initial_common_outer_accepted"
        ],
        "accepted range shrink",
    )
    exhaustion_event = exactly_one(
        [
            event
            for event in events
            if event.get("event")
            == "experience_table_gradient_candidate_space_exhausted"
            and int(event.get("round_index", -1)) == 1
        ],
        "round-1 candidate-space exhaustion",
    )
    if (
        exhaustion_event.get("candidate_space_exhausted") is not True
        or int(exhaustion_event.get("selected_count", -1)) != 0
        or exhaustion_event.get("completion_status") == "complete"
    ):
        raise RuntimeError("old round-1 exhaustion audit is not explicitly incomplete")

    expected_range_state = copy.deepcopy(range_event.get("range_state") or {})
    if set(expected_range_state) != set(config.get("param_ranges") or {}):
        raise RuntimeError("accepted range state parameter set is incomplete")
    if int(range_event.get("accepted_shrink_count", -1)) != 1:
        raise RuntimeError("accepted range state is not exactly one binary shrink")
    if not math.isclose(float(range_event.get("normalized_width")), 0.5):
        raise RuntimeError("accepted range state is not the required 0.5 width")
    for name, value in accepted_params.items():
        bounds = expected_range_state[name]
        if not float(bounds["min_value"]) <= float(value) <= float(bounds["max_value"]):
            raise RuntimeError(f"accepted incumbent lies outside shrunken range: {name}")

    accepted_validation = copy.deepcopy(selected.get("candidate_final_validation") or {})
    protocol = (
        (accepted_validation.get("resource_stats") or {}).get(
            "candidate_measurement_protocol"
        )
        or {}
    )
    point_path = Path(str(protocol.get("point_result_path") or "")).resolve()
    point = json.loads(point_path.read_text(encoding="utf-8"))
    point_measurement = point.get("measurement") or {}
    point_stability = point_measurement.get("resource_stability") or {}
    if (
        point.get("status") != "complete"
        or point_measurement.get("correctness_passed") is not True
        or point_stability.get("passed") is not True
        or point_stability.get("stable") is not True
        or point.get("oom_killed")
        or int(point.get("database_restart_count") or 0) != 0
        or not params_readback_close(
            point.get("cost_parameters") or {}, accepted_params
        )
    ):
        raise RuntimeError("accepted p44 point evidence is invalid")

    parameter_updates = (online_event.get("audit") or {}).get("parameter_updates") or []
    online_table = {
        str(item["parameter"]): copy.deepcopy(item["updated_effects"])
        for item in parameter_updates
        if item.get("parameter") and isinstance(item.get("updated_effects"), dict)
    }
    if set(online_table) != set(accepted_params):
        raise RuntimeError("online T update does not contain all seven parameters")

    old_scope_params = copy.deepcopy(config.get("initial_params") or {})
    old_scope_signature = [
        [name, float(value).hex()]
        for name, value in sorted(old_scope_params.items())
    ]
    old_replayed_params = [
        copy.deepcopy(item.get("candidate_optimized_params") or {})
        for item in evaluations
    ]
    if (
        old_scope_signature
        != complete["replay_batch_audit"]["scope_incumbent_signature"]
        or len(old_replayed_params) != 5
        or any(set(item) != set(accepted_params) for item in old_replayed_params)
    ):
        raise RuntimeError("old first-five replay scope is incomplete")

    checkpoint = {
        "schema_version": 1,
        "kind": "job_gradient_accepted_resume_checkpoint_v1",
        "generation_id": "job_tsgd_generation_02_after_accept1",
        "attempt_root": str(attempt),
        "source_history_path": str(source_history_path),
        "source_history_sha256": sha256_file(source_history_path),
        "accepted_point_result_path": str(point_path),
        "accepted_point_result_sha256": sha256_file(point_path),
        "baseline_point_result_path": str(
            Path(config["startup_baseline_reuse"]["point_result_path"]).resolve()
        ),
        "baseline_point_result_sha256": config["startup_baseline_reuse"][
            "point_result_sha256"
        ],
        "counters": counters,
        "next_feedback_round": 1,
        "accepted_candidate_index": candidate_index,
        "accepted_estimated_rank": selected_rank,
        "accepted_reason": selected["actual_accept_reason"],
        "accepted_params": accepted_params,
        "accepted_validation": accepted_validation,
        "accepted_estimated_resource": copy.deepcopy(
            selected.get("candidate_estimated_resource") or {}
        ),
        "accepted_final_top_sql": copy.deepcopy(validation_event.get("top") or []),
        "accepted_final_plan_samples": copy.deepcopy(
            validation_event.get("final_plan_hashes") or []
        ),
        "accepted_final_plan_changes": [],
        "accepted_degradation_summary": copy.deepcopy(
            degradation_event.get("summary") or {}
        ),
        "expected_range_state_after_accept": expected_range_state,
        "online_sensitivity_table": online_table,
        "online_update_event": online_event,
        "replayed_scope_incumbent_params": old_scope_params,
        "replayed_candidate_params": old_replayed_params,
        "initial_completion_summary": {
            "validated_candidate_count": 5,
            "eligible_count": int(complete.get("eligible_count") or 0),
            "selected_estimated_rank": selected_rank,
            "expanded_to_second_batch": False,
            "valid_first5_barrier_satisfied": True,
            "accepted_standalone_validation_event_present": bool(
                validation_matches
            ),
            "accepted_validation_authoritative_source": (
                "five_valid_completion_event_and_point_result"
            ),
        },
        "generation_02_contract": {
            "rerun_all_127_subsets": True,
            "reuse_old_round1_ranking": False,
            "reuse_old_round1_manifest": False,
            "new_scope_seen_candidate_count": 0,
            "new_scope_incumbent_params": accepted_params,
            "new_scope_range_state": expected_range_state,
        },
    }

    checkpoint_path = generation / "generation_02_accepted_resume_checkpoint_v1.json"
    write_new_json(checkpoint_path, checkpoint)

    config = copy.deepcopy(config)
    generation_meta = config.setdefault("_tsgd_generation", {})
    generation_meta.update(
        {
            "generation_id": "job_tsgd_generation_02_after_accept1",
            "generation_root": str(generation),
            "generation_parent": "job_tsgd_fresh_v1",
            "accepted_count_start": 1,
            "accepted_incumbent_params": accepted_params,
            "accepted_range_state": expected_range_state,
            "all_127_subsets_must_rerun": True,
            "old_round1_candidate_space_exhausted_role": "audit_only",
            "parameter_optimizer_sha256": PARAMETER_OPTIMIZER_SHA256,
        }
    )
    generation_meta["deployed_core_sha256"][
        "parameter_optimizer.py"
    ] = PARAMETER_OPTIMIZER_SHA256
    generation_meta["formal_measurement_v5"][
        "parameter_optimizer_sha256"
    ] = PARAMETER_OPTIMIZER_SHA256
    generation_meta["campaign_stability_contract_v5"][
        "validated_by_parameter_optimizer_sha256"
    ] = PARAMETER_OPTIMIZER_SHA256
    config["logging"]["history_path"] = str(generation / "optimizer_history.jsonl")
    config["plan_capture"]["output_dir"] = str(generation / "plan_captures")
    config["resource_iteration_reports"]["output_dir"] = str(
        generation / "resource_reports"
    )
    config["auto_explain"]["output_dir"] = str(generation / "auto_explain")
    outer = config["formal_outer_protocol"]
    outer.update(
        {
            "algorithm_generation": "job_tsgd_generation_02_after_accept1",
            "global_replay_ordinal_at_binding": 48,
            "formal_search_replay_count_start": 6,
            "formal_search_accepted_count_start": 1,
            "formal_startup_baseline_count_start": 1,
            "initial_real_replay_count": 5,
            "valid_initial_batch_count_start": 5,
            "resume_next_feedback_round": 1,
            "formal_search_resume_checkpoint_path": str(checkpoint_path),
            "formal_search_resume_checkpoint_sha256": sha256_file(checkpoint_path),
            "generation_02_rerun_all_127_subsets": True,
            "generation_02_reuse_old_round1_ranking": False,
            "generation_02_new_scope_seen_candidate_count": 0,
            "generation_02_first_valid_barrier": 5,
            "accepted_shrink_target": 5,
            "formal_search_accepted_target": 5,
        }
    )
    config_path = generation / "config/memory/job.json"
    write_new_json(config_path, config)

    exhaustion_audit = {
        "schema_version": 1,
        "event": "job_generation_01_round1_candidate_space_exhausted_audit",
        "status": "preserved_incomplete_audit_only_not_completion",
        "source_history_path": str(source_history_path),
        "source_history_sha256": sha256_file(source_history_path),
        "source_event_canonical_sha256": canonical_sha256(exhaustion_event),
        "source_event": exhaustion_event,
        "root_cause": {
            "generic_runtime_memory_bytes": 124_931_569.37142856,
            "formal_memory_working_set_bytes": 2_685_864_244.916201,
            "bad_predicted_memory_gb": 0.11675250940491902,
            "repair": (
                "formal plan estimator now anchors resident memory to "
                "resource_stats.formal_pg_resource.aggregate.memory_working_set_bytes"
            ),
            "repaired_parameter_optimizer_sha256": PARAMETER_OPTIMIZER_SHA256,
        },
        "counts_as_completion": False,
        "counts_as_convergence": False,
        "ranking_reused_by_generation_02": False,
    }
    exhaustion_path = generation / "generation_01_round1_exhaustion_audit_v1.json"
    write_new_json(exhaustion_path, exhaustion_audit)

    manifest = {
        "schema_version": 1,
        "generation_id": "job_tsgd_generation_02_after_accept1",
        "status": "sealed_ready_to_rerun_127_subsets_from_accepted_incumbent",
        "machine": "anonymous@192.0.2.4",
        "disk": "/mnt/nvme2n1",
        "attempt_root": str(attempt),
        "generation_root": str(generation),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "parameter_optimizer_path": str(workspace / "parameter_optimizer.py"),
        "parameter_optimizer_sha256": PARAMETER_OPTIMIZER_SHA256,
        "source_history_path": str(source_history_path),
        "source_history_sha256": sha256_file(source_history_path),
        "inherited_counters": counters,
        "accepted_state": {
            "accepted_shrink_count": 1,
            "accepted_target": 5,
            "incumbent_params": accepted_params,
            "point_result_path": str(point_path),
            "point_result_sha256": sha256_file(point_path),
            "range_state": expected_range_state,
            "normalized_width": 0.5,
        },
        "candidate_generation": {
            "algorithm": "all_nonempty_experience_table_T_SGD_subsets",
            "subset_count": 127,
            "rerun_required": True,
            "old_ranking_reused": False,
            "old_manifest_reused": False,
            "exact_plan_cluster_required": True,
            "conservative_resource_error_boxes_required": True,
            "initial_valid_barrier": 5,
            "cumulative_valid_barriers": [5, 10, 16],
            "new_scope_seen_candidate_count": 0,
            "range_preflight_passed": True,
        },
        "old_exhaustion_audit_path": str(exhaustion_path),
        "old_exhaustion_audit_sha256": sha256_file(exhaustion_path),
        "exit_zero_is_completion": False,
    }
    manifest_path = generation / "generation_02_manifest_v1.json"
    write_new_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "generation_root": str(generation),
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256_file(manifest_path),
                "config_path": str(config_path),
                "config_sha256": sha256_file(config_path),
                "checkpoint_path": str(checkpoint_path),
                "checkpoint_sha256": sha256_file(checkpoint_path),
                "counters": counters,
                "next_point": 49,
                "accepted": "1/5",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
