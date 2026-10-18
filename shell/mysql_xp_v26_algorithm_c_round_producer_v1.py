#!/usr/bin/env python3
"""Produce one strict XP Algorithm C debug round from sealed real observations.

The producer owns the original-gradient arithmetic, exact 127-subspace
enumeration, global ranking and fixed Top-5 selection.  Workload measurement
is intentionally a separate data-plane concern: this entrypoint accepts only
sealed observations and real-replay journals bound to the same live v26
adoption, dynamic gate and fresh R0.  It never imports or invokes the v8
postcapture producer and it cannot credit an accepted round.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import sys


TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
PARAMETERS = (
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
    "key_compare_cost", "disk_temptable_create_cost",
    "disk_temptable_row_cost", "memory_temptable_row_cost",
)
RESOURCES = ("cpu", "memory", "io")
L3_FIELDS = {
    "shared_read_blocks", "shared_write_blocks", "local_read_blocks",
    "local_write_blocks", "temp_read_blocks", "temp_write_blocks",
    "io_wait_ms",
}
FORMULA = "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"


class ProducerError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value, label: str) -> float:
    if isinstance(value, bool):
        raise ProducerError(label + " is not finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProducerError(label + " is not finite") from exc
    if not math.isfinite(number):
        raise ProducerError(label + " is not finite")
    return number


def load_target(bundle: Path):
    bundle = bundle.resolve(strict=True)
    if bundle.name != TARGET_ID or sha256(bundle / "bundle_manifest.json") != TARGET_SHA:
        raise ProducerError("target v26 identity differs")
    path = bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v26_round_target", path)
    if spec is None or spec.loader is None:
        raise ProducerError("target v26 materializer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    module.verify_bundle(bundle)
    return bundle, module


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise ProducerError("output and sidecar must be fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar.write_text(sha256(path) + "  " + path.name + "\n", encoding="ascii")


def fingerprint(row, label: str) -> dict:
    if (not isinstance(row, dict)
            or not isinstance(row.get("parameter_signature"), str)
            or not row["parameter_signature"]
            or not isinstance(row.get("l1"), dict) or not row["l1"]
            or not isinstance(row.get("l2"), dict) or not row["l2"]
            or not isinstance(row.get("l3"), dict)
            or set(row["l3"]) != L3_FIELDS
            or any(row["l3"][key] is None for key in L3_FIELDS)):
        raise ProducerError(label + " lacks complete L1/L2/L3")
    return dict(row)


def complete_subsets() -> set[tuple[str, ...]]:
    return {
        tuple(row)
        for width in range(1, len(PARAMETERS) + 1)
        for row in itertools.combinations(PARAMETERS, width)
    }


def gradient_event(row: dict, baseline: dict, ranges: dict) -> dict:
    subset_id = row.get("subset_id")
    active = row.get("active_subset")
    if not isinstance(subset_id, str) or not subset_id or not isinstance(active, list):
        raise ProducerError("subset identity differs")
    active_set = set(active)
    normalized = tuple(name for name in PARAMETERS if name in active_set)
    if not normalized or len(normalized) != len(active):
        raise ProducerError("active subset differs")
    slopes = row.get("dScore/dU")
    transform = row.get("T")
    confidence = row.get("confidence")
    if not all(isinstance(value, dict) for value in (slopes, transform, confidence)):
        raise ProducerError("gradient maps differ")
    if set(slopes) != active_set or set(transform) != active_set or set(confidence) != active_set:
        raise ProducerError("gradient maps must contain exactly the active subset")
    eta = finite(row.get("eta"), subset_id + ".eta")
    if eta <= 0:
        raise ProducerError("eta must be positive")
    gradients = {}
    deltas = {}
    for parameter in normalized:
        if not all(isinstance(value.get(parameter), dict)
                   for value in (slopes, transform, confidence)):
            raise ProducerError("per-parameter resource maps differ")
        if (set(slopes[parameter]) != set(RESOURCES)
                or set(transform[parameter]) != set(RESOURCES)
                or set(confidence[parameter]) != set(RESOURCES)):
            raise ProducerError("cpu/memory/io gradient closure differs")
        gradient = sum(
            finite(slopes[parameter][resource], "dScore/dU")
            * finite(transform[parameter][resource], "T")
            * finite(confidence[parameter][resource], "confidence")
            for resource in RESOURCES
        )
        before = finite(baseline[parameter], parameter + ".baseline")
        minimum = finite(ranges[parameter]["min_value"], parameter + ".min")
        maximum = finite(ranges[parameter]["max_value"], parameter + ".max")
        if before <= 0 or minimum <= 0 or minimum > maximum:
            raise ProducerError("parameter range differs")
        proposed = before * math.exp(eta * gradient)
        after = min(maximum, max(minimum, proposed))
        gradients[parameter] = gradient
        deltas[parameter] = {
            "before": before,
            "proposed_before_clip": proposed,
            "after_clip": after,
            "absolute": after - before,
            "relative": (after - before) / before,
            "clipped": not math.isclose(after, proposed, rel_tol=1e-12, abs_tol=1e-15),
        }
    for field in ("feature_ratio", "predicted_resource", "utilization"):
        value = row.get(field)
        if not isinstance(value, dict) or not value:
            raise ProducerError(field + " differs")
        for key, item in value.items():
            finite(item, field + "." + str(key))
    event = {
        "subset_id": subset_id,
        "active_subset": list(normalized),
        "feature_ratio": row["feature_ratio"],
        "predicted_resource": row["predicted_resource"],
        "utilization": row["utilization"],
        "dScore/dU": slopes,
        "T": transform,
        "confidence": confidence,
        "g_j": gradients,
        "eta": eta,
        "delta_param": deltas,
        "candidate_score": finite(row.get("candidate_score"), subset_id + ".candidate_score"),
        "gradient_source": "original_dScore_dU_times_T_times_confidence",
    }
    return event


def plan(bundle_path, adoption_path, gate_path, r0_path, observations_path, output):
    unused_bundle, target = load_target(bundle_path)
    unused_adoption, identity = target.verify_adoption(adoption_path, TARGET_ID)
    gate = target.load_sealed(gate_path, "dynamic gate")
    r0 = target.load_sealed(r0_path, "fresh R0")
    observations = target.load_sealed(observations_path, "round observations")
    target._verify_gate(gate, TARGET_ID, identity)
    baseline = observations.get("baseline_parameters"); ranges = observations.get("parameter_ranges")
    rows = observations.get("subsets"); candidate_fps = observations.get("candidate_fingerprints")
    if (r0.get("status") != "R0_PASS" or r0.get("same_live_identity") != identity
            or observations.get("schema_version") != "mysql_xp_algorithm_c_round_observations_v1"
            or observations.get("status") != "PASS_SEALED_REAL_PLAN_OBSERVATIONS"
            or observations.get("bundle_id") != TARGET_ID
            or observations.get("same_live_identity") != identity
            or observations.get("fresh_r0_sha256") != sha256(r0_path)
            or observations.get("dynamic_gate_sha256") != sha256(gate_path)
            or observations.get("qps_resource_input_present") is not False
            or observations.get("replacement_search_algorithm_used") is not False
            or not isinstance(baseline, dict) or set(baseline) != set(PARAMETERS)
            or not isinstance(ranges, dict) or set(ranges) != set(PARAMETERS)
            or not isinstance(rows, list) or len(rows) != 127
            or not isinstance(candidate_fps, list) or len(candidate_fps) != 127):
        raise ProducerError("candidate plan predecessor closure differs")
    events = [gradient_event(row, baseline, ranges) for row in rows]
    mapping = {event["subset_id"]: tuple(event["active_subset"]) for event in events}
    if len(mapping) != 127 or set(mapping.values()) != complete_subsets():
        raise ProducerError("exact 2^7-1 non-empty subsets differ")
    ordered = sorted(events, key=lambda event: (-event["candidate_score"], event["subset_id"]))
    ranking = [{"rank": i, "subset_id": e["subset_id"], "candidate_score": e["candidate_score"],
                "candidate_parameters": {name: e["delta_param"].get(name, {"after_clip": baseline[name]})["after_clip"]
                                         if name in e["delta_param"] else baseline[name]
                                         for name in PARAMETERS}}
               for i, e in enumerate(ordered, 1)]
    result = {"schema_version":"mysql_xp_algorithm_c_candidate_plan_v1",
      "status":"SEALED_127_GLOBAL_RANKING_FIXED_TOP5_NOT_REPLAYED","formal_scope":"mysql_only",
      "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
      "bundle_id":TARGET_ID,"same_live_identity":identity,"fresh_r0_sha256":sha256(r0_path),
      "dynamic_gate_sha256":sha256(gate_path),"round_observations_sha256":sha256(observations_path),
      "gradient_events":events,"global_candidate_ranking":ranking,"fixed_top5_ranks":[1,2,3,4,5],
      "candidate_fingerprints":[fingerprint(row,"candidate fingerprint") for row in candidate_fps],
      "qps_resource_input_present":False,"real_replays_completed":0,"accepted_rounds_credit":0}
    atomic_json(output,result); return result


def produce(bundle_path: Path, adoption_path: Path, gate_path: Path,
            r0_path: Path, observations_path: Path, replays_path: Path,
            output: Path, candidate_plan_path: Path | None = None) -> dict:
    bundle, target = load_target(bundle_path)
    adoption, identity = target.verify_adoption(adoption_path, TARGET_ID)
    gate = target.load_sealed(gate_path, "dynamic gate")
    r0 = target.load_sealed(r0_path, "fresh R0")
    observations = target.load_sealed(observations_path, "round observations")
    replays = target.load_sealed(replays_path, "fixed Top-5 replays")
    target._verify_gate(gate, TARGET_ID, identity)
    if (r0.get("schema_version") != "mysql_xp_fresh_r0_result_v3"
            or r0.get("status") != "R0_PASS"
            or r0.get("same_live_identity") != identity
            or observations.get("schema_version") != "mysql_xp_algorithm_c_round_observations_v1"
            or observations.get("status") != "PASS_SEALED_REAL_PLAN_OBSERVATIONS"
            or observations.get("bundle_id") != TARGET_ID
            or observations.get("same_live_identity") != identity
            or observations.get("fresh_r0_sha256") != sha256(r0_path)
            or observations.get("dynamic_gate_sha256") != sha256(gate_path)
            or observations.get("qps_resource_input_present") is not False
            or observations.get("replacement_search_algorithm_used") is not False
            or replays.get("schema_version") != "mysql_xp_algorithm_c_fixed_top5_replays_v1"
            or replays.get("status") != "PASS_FIXED_TOP5_REAL_REPLAYS"
            or replays.get("bundle_id") != TARGET_ID
            or replays.get("same_live_identity") != identity
            or replays.get("fresh_r0_sha256") != sha256(r0_path)
            or replays.get("dynamic_gate_sha256") != sha256(gate_path)):
        raise ProducerError("predecessor/live observation chain differs")
    baseline = observations.get("baseline_parameters")
    ranges = observations.get("parameter_ranges")
    rows = observations.get("subsets")
    if (not isinstance(baseline, dict) or set(baseline) != set(PARAMETERS)
            or not isinstance(ranges, dict) or set(ranges) != set(PARAMETERS)
            or not isinstance(rows, list) or len(rows) != 127):
        raise ProducerError("seven-parameter/127 observation closure differs")
    events = [gradient_event(row, baseline, ranges) for row in rows]
    mapping = {event["subset_id"]: tuple(event["active_subset"]) for event in events}
    if len(mapping) != 127 or set(mapping.values()) != complete_subsets():
        raise ProducerError("exact 2^7-1 non-empty subsets differ")
    ordered = sorted(events, key=lambda event: (-event["candidate_score"], event["subset_id"]))
    ranking = [
        {"rank": index, "subset_id": event["subset_id"],
         "candidate_score": event["candidate_score"]}
        for index, event in enumerate(ordered, 1)
    ]
    expected_top5 = [row["subset_id"] for row in ranking[:5]]
    if candidate_plan_path is None:
        raise ProducerError("sealed pre-replay candidate plan is required")
    candidate_plan = target.load_sealed(candidate_plan_path, "candidate plan")
    if (candidate_plan.get("schema_version") != "mysql_xp_algorithm_c_candidate_plan_v1"
            or candidate_plan.get("status") != "SEALED_127_GLOBAL_RANKING_FIXED_TOP5_NOT_REPLAYED"
            or candidate_plan.get("same_live_identity") != identity
            or candidate_plan.get("round_observations_sha256") != sha256(observations_path)
            or [{"rank":x["rank"],"subset_id":x["subset_id"],"candidate_score":x["candidate_score"]}
                for x in candidate_plan.get("global_candidate_ranking",[])] != ranking
            or replays.get("candidate_plan_sha256") != sha256(candidate_plan_path)):
        raise ProducerError("sealed pre-replay candidate plan differs")
    journals = replays.get("point_journals")
    if not isinstance(journals, list) or len(journals) != 5:
        raise ProducerError("fixed Top-5 replay cardinality differs")
    normalized_journals = []
    replay_fingerprints = []
    tokens = set()
    for rank, journal in enumerate(journals, 1):
        token = journal.get("attempt_token") if isinstance(journal, dict) else None
        qps = (journal.get("outer_acceptance") or {}).get("qps") if isinstance(journal, dict) else None
        if (not isinstance(journal, dict)
                or journal.get("rank") != rank
                or journal.get("subset_id") != expected_top5[rank - 1]
                or not journal.get("sealed_candidate_identity")
                or not isinstance(token, str) or not token or token in tokens
                or journal.get("started") is not True
                or journal.get("measurement_complete") is not True
                or journal.get("completion_recorded") is not True
                or journal.get("real_replay") is not True
                or journal.get("failed_replay") is not False
                or journal.get("database_counter_timeout_count") != 0
                or journal.get("qps_used_for_gradient") is not False
                or journal.get("qps_sampled_after_real_replay") is not True
                or journal.get("accepted_rounds_credit") != 0
                or qps is None):
            raise ProducerError("fixed Top-5 real replay journal differs")
        finite(qps, "outer acceptance qps")
        tokens.add(token)
        normalized_journals.append(dict(journal))
        replay_fingerprints.append(fingerprint(journal.get("fingerprint"), "replay fingerprint"))
    candidate_fingerprints = observations.get("candidate_fingerprints")
    if not isinstance(candidate_fingerprints, list) or len(candidate_fingerprints) != 127:
        raise ProducerError("candidate fingerprint cardinality differs")
    candidate_fingerprints = [fingerprint(row, "candidate fingerprint")
                              for row in candidate_fingerprints]
    result = {
        "schema_version": "mysql_xp_algorithm_c_debug_round_v3",
        "status": "PASS_DEBUG_ROUND",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": TARGET_ID,
        "same_live_identity": identity,
        "adopted_service_sha256": sha256(adoption_path),
        "fresh_r0_sha256": sha256(r0_path),
        "dynamic_gate_sha256": sha256(gate_path),
        "round_observations_sha256": sha256(observations_path),
        "fixed_top5_replays_sha256": sha256(replays_path),
        "completed_nonempty_subsets": 127,
        "gradient_events": events,
        "global_candidate_ranking": ranking,
        "candidate_order": "global_descending_final_candidate_score",
        "fixed_top5_only": True,
        "sealed_top5_ranks": [1, 2, 3, 4, 5],
        "replayed_top5_ranks": [1, 2, 3, 4, 5],
        "top5_point_journals": normalized_journals,
        "candidate_fingerprints": candidate_fingerprints + replay_fingerprints,
        "algorithm_formula": FORMULA,
        "parameter_update_source": "g_j_and_eta_only",
        "replacement_search_algorithm_used": False,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "fingerprint_duplicate_rule": "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3",
        "missing_fingerprint_level_policy": "independent_candidate",
        "prediction_vs_real_ranking_compared": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    target.verify_debug(result, TARGET_ID, identity)
    atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "finalize"), default="finalize")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path, required=True)
    parser.add_argument("--fresh-r0", type=Path, required=True)
    parser.add_argument("--round-observations", type=Path, required=True)
    parser.add_argument("--fixed-top5-replays", type=Path)
    parser.add_argument("--candidate-plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "plan":
        result = plan(args.bundle,args.adopted_service,args.dynamic_gate,args.fresh_r0,
                      args.round_observations,args.output)
        print(json.dumps({"status":result["status"],"subsets":127,"top5":[1,2,3,4,5]},sort_keys=True)); return 0
    if args.candidate_plan is None or args.fixed_top5_replays is None:
        raise ProducerError("finalize requires candidate plan and Top-5 replays")
    result = produce(args.bundle, args.adopted_service, args.dynamic_gate,
                     args.fresh_r0, args.round_observations,
                     args.fixed_top5_replays, args.output,args.candidate_plan)
    print(json.dumps({"status": result["status"], "subsets": 127,
                      "top5": 5, "accepted_rounds_credit": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProducerError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
