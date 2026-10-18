#!/usr/bin/env python3
"""Build the offline TPC-H round28 exact-plan-regime formal package.

This preparation step is deliberately read-only with respect to PostgreSQL and
the remote campaign.  It consumes copied evidence, incorporates only trusted
round27 measurements into exact-regime componentwise calibration, excludes all
known replayed parameters/fingerprints, and emits a SHA-bound formal package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import tpch_round19_plan_regime_select as selector


INPUT = Path("/tmp/tpch_round28_input")
OUTPUT = Path("/tmp/tpch_round28_output")
CONTROLLER_SOURCE = Path("/tmp/run_tpch_formal_exact_plan_regime_batch_remote.py")
EXPECTED_STATE_SHA256 = (
    "d866de7efe376946cd2d5c441397d7efb11d27f57a713691df2d82f39d1ea006"
)
EXPECTED_ACCEPTED_COUNT = 1
EXPECTED_INCUMBENT_SCORE = 4.77702526499285
BASE = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
REMOTE_PACKAGE = BASE / (
    "formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_package"
)
REMOTE_OUTPUT = BASE / (
    "formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run"
)
BASELINE = (
    BASE / "candidate_measurements/formal_point_20260729T050311Z_3000/summary.json"
)
WORKLOAD = BASE / "code/preflight/run_tpch_weighted_candidate_attempt48.sh"
COUNTER = BASE / (
    "run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2."
    "accepted_shrink.counter.txt"
)
BASELINE_SHA256 = (
    "50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67"
)
WORKLOAD_SHA256 = (
    "19595534dccb0fa1fb6d6b2a5de66445516095dcd1511d772d1787e58b7c0c12"
)
CAPABILITY = {"cpu": 22.0, "io": 36.0, "memory": 1_730_000_000 / 1024.0 ** 3}
RESOURCES: Tuple[str, ...] = ("cpu", "io", "memory")
REQUIRED_AUDIT_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def params(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(value, Mapping):
        raise RuntimeError("candidate has no parameter mapping")
    return value


def exact_regime(candidate: Mapping[str, Any]) -> str:
    computed = selector.structural_plan_regime_id(candidate)
    declared = candidate.get("structural_plan_regime_id")
    if declared is not None and declared != computed:
        raise RuntimeError(f"structural regime mismatch: {declared} != {computed}")
    return computed


def replay_exclusions(
    prior_selection: Mapping[str, Any],
    merged_results: Mapping[str, Any],
    expanded: Sequence[Mapping[str, Any]],
) -> Tuple[set[str], set[str], Dict[str, Mapping[str, Any]]]:
    identifiers: set[str] = set()
    parameter_keys: set[str] = set()
    expanded_by_params = {
        selector.parameter_key(candidate): candidate for candidate in expanded
    }
    if len(expanded_by_params) != len(expanded):
        raise RuntimeError("expanded candidates contain duplicate parameter vectors")

    for regime in (prior_selection.get("validated_plan_regimes") or {}).values():
        for evidence in regime.get("evidence") or []:
            identifiers.add(str(evidence["source_fingerprint"]))
            identifiers.add(str(evidence["source_raw_fingerprint"]))
            parameter_keys.add(str(evidence["source_parameter_key"]))

    for candidate in prior_selection.get("top5") or []:
        identifiers.add(selector.fingerprint_id(candidate))
        parameter_keys.add(selector.parameter_key(candidate))
        raw = expanded_by_params.get(selector.parameter_key(candidate))
        if raw is not None:
            identifiers.add(selector.fingerprint_id(raw))

    for result in merged_results.get("results") or []:
        identifiers.add(selector.fingerprint_id(result))
        parameter_keys.add(selector.parameter_key(result))
        raw = expanded_by_params.get(selector.parameter_key(result))
        if raw is not None:
            identifiers.add(selector.fingerprint_id(raw))
    return identifiers, parameter_keys, expanded_by_params


def add_round27_feedback(
    prior_selection: Mapping[str, Any],
    merged_results: Mapping[str, Any],
    expanded_by_params: Mapping[str, Mapping[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    registry = copy.deepcopy(prior_selection["validated_plan_regimes"])
    source_by_rank = {
        int(candidate["rank"]): candidate for candidate in prior_selection["top5"]
    }
    feedback_audit: List[Dict[str, Any]] = []
    for result in merged_results["results"]:
        rank = int(result["rank"])
        source = source_by_rank[rank]
        if selector.parameter_key(result) != selector.parameter_key(source):
            raise RuntimeError(f"round27 result/source parameter mismatch at rank {rank}")
        role = str(source["selection_role"])
        if role != str(result["selection_role"]):
            raise RuntimeError(f"round27 result/source role mismatch at rank {rank}")
        if result.get("technical_failure") is not False or result.get("valid_replay") is not True:
            raise RuntimeError(f"round27 rank {rank} is not a valid replay")

        regime = exact_regime(source)
        calibrated_prediction = source["predicted_resource"]
        actual = result["actual_resource"]
        residual = {
            name: float(actual[name]) / float(calibrated_prediction[name])
            for name in RESOURCES
        }
        audit = {
            "rank": rank,
            "selection_role": role,
            "structural_plan_regime_id": regime,
            "source_fingerprint": selector.fingerprint_id(result),
            "calibrated_predicted_resource": {
                name: float(calibrated_prediction[name]) for name in RESOURCES
            },
            "actual_resource": {
                name: float(actual[name]) for name in RESOURCES
            },
            "residual_actual_over_calibrated_prediction": residual,
            "used_for_exact_regime_calibration": role == "trusted",
            "probe_updates_forbidden": role != "trusted",
        }
        feedback_audit.append(audit)
        if role != "trusted":
            continue
        if regime not in registry:
            raise RuntimeError(f"trusted round27 regime is absent from registry: {regime}")

        raw_prediction = source.get("precalibration_predicted_resource")
        if not isinstance(raw_prediction, Mapping):
            raise RuntimeError(f"round27 trusted rank {rank} lacks raw prediction")
        scalar = {
            name: float(actual[name]) / float(raw_prediction[name])
            for name in RESOURCES
        }
        raw_source = expanded_by_params.get(selector.parameter_key(source))
        raw_fingerprint = (
            selector.fingerprint_id(raw_source)
            if raw_source is not None
            else selector.fingerprint_id(source)
        )
        evidence = {
            "event": "tpch_round27_merged_formal_real_replay_complete",
            "source_rank": rank,
            "source_fingerprint": selector.fingerprint_id(result),
            "source_raw_fingerprint": raw_fingerprint,
            "source_parameter_key": selector.parameter_key(source),
            "ratio_plan_level": selector.plan_level(source),
            "predicted_resource": {
                name: float(raw_prediction[name]) for name in RESOURCES
            },
            "calibration_input_resource": {
                name: float(raw_prediction[name]) for name in RESOURCES
            },
            "calibrated_predicted_resource": {
                name: float(calibrated_prediction[name]) for name in RESOURCES
            },
            "actual_resource": {
                name: float(actual[name]) for name in RESOURCES
            },
            "scalar_by_resource": scalar,
            "residual_actual_over_calibrated_prediction": residual,
            "actual_score": float(result["actual_score"]),
            "source_results_sha256": sha256(INPUT / "merged_top5_results.json"),
        }
        registry[regime]["evidence"].append(evidence)

    for regime_id, calibration in registry.items():
        evidence = calibration["evidence"]
        calibration["evidence_count"] = len(evidence)
        calibration["scalar_by_resource"] = {
            name: statistics.median(
                float(item["scalar_by_resource"][name]) for item in evidence
            )
            for name in RESOURCES
        }
        calibration["actual_score_median"] = statistics.median(
            float(item["actual_score"]) for item in evidence
        )
        calibration["evidence_contract"] = (
            "exact_workload_structural_plan_regime_componentwise_median_v2_"
            "round27_feedback"
        )
        calibration["global_scalar_forbidden"] = True
        calibration["unknown_regime_calibration_forbidden"] = True
        calibration["structural_plan_regime_id"] = regime_id
    return registry, feedback_audit


def is_eligible_noncorner(
    candidate: Mapping[str, Any],
    excluded_ids: set[str],
    excluded_params: set[str],
) -> bool:
    return (
        candidate.get("boundary_corner") is not True
        and candidate.get("boundary_plateau") is not True
        and selector.fingerprint_id(candidate) not in excluded_ids
        and selector.parameter_key(candidate) not in excluded_params
    )


def calibrated_trusted(
    raw: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    item = copy.deepcopy(raw)
    raw_resource = item["predicted_resource"]
    corrected = {
        name: float(raw_resource[name])
        * float(calibration["scalar_by_resource"][name])
        for name in RESOURCES
    }
    score, utilization = selector.score_resource(corrected)
    item.update(
        {
            "precalibration_predicted_resource": copy.deepcopy(raw_resource),
            "predicted_resource": corrected,
            "final_resource": copy.deepcopy(corrected),
            "regime_adjusted_predicted_resource": copy.deepcopy(corrected),
            "candidate_score": score,
            "final_score": score,
            "regime_adjusted_score": score,
            "selection_score": float(calibration["actual_score_median"]),
            "utilization": utilization,
            "plan_regime_id": exact_regime(item),
            "structural_plan_regime_id": exact_regime(item),
            "regime_calibration": copy.deepcopy(calibration),
            "candidate_trust_confidence": 1.0,
            "candidate_trust_basis": (
                "validated_exact_plan_regime_componentwise_median_feedback"
            ),
            "three_level_fingerprint": selector.recalibrated_fingerprint(
                item, corrected, calibration
            ),
        }
    )
    return item


def select_top5(
    expanded: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
    excluded_ids: set[str],
    excluded_params: set[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    eligible = [
        candidate
        for candidate in expanded
        if is_eligible_noncorner(candidate, excluded_ids, excluded_params)
    ]
    trusted_pool: List[Dict[str, Any]] = []
    unknown_by_regime: Dict[str, List[Mapping[str, Any]]] = {}
    for raw in eligible:
        regime = exact_regime(raw)
        if regime in registry:
            candidate = calibrated_trusted(raw, registry[regime])
            if selector.fingerprint_id(candidate) in excluded_ids:
                continue
            trusted_pool.append(candidate)
        else:
            unknown_by_regime.setdefault(regime, []).append(raw)

    trusted_pool.sort(
        key=lambda item: (
            -float(item["selection_score"]),
            -float(item["candidate_score"]),
            selector.fingerprint_id(item),
        )
    )
    if len(trusted_pool) != 3:
        raise RuntimeError(
            f"expected exactly three eligible trusted candidates, got {len(trusted_pool)}"
        )
    trusted = trusted_pool
    unknown_representatives = [
        max(
            group,
            key=lambda item: (
                float(item["candidate_score"]),
                selector.fingerprint_id(item),
            ),
        )
        for group in unknown_by_regime.values()
    ]
    probes = selector.maximin_probes(unknown_representatives, trusted, 2)

    selected: List[Dict[str, Any]] = []
    for replay_rank, raw in enumerate([*trusted, *probes], 1):
        item = copy.deepcopy(raw)
        trusted_role = replay_rank <= 3
        if not trusted_role:
            _, utilization = selector.score_resource(item["predicted_resource"])
            item["utilization"] = utilization
        item.update(
            {
                "predicted_rank": int(item.get("rank", replay_rank)),
                "rank": replay_rank,
                "acquisition_role": (
                    "trusted_validation" if trusted_role else "uncertain_probe"
                ),
                "selection_role": "trusted" if trusted_role else "probe",
                "selection_basis": (
                    "exact_plan_regime_componentwise_median_feedback"
                    if trusted_role
                    else "unknown_exact_regime_noncorner_maximin"
                ),
                "structural_plan_regime_id": exact_regime(item),
                "candidate_trust_confidence": 1.0 if trusted_role else 0.0,
                "candidate_trust_basis": (
                    "validated_exact_plan_regime"
                    if trusted_role
                    else "unknown_plan_regime_probe_only"
                ),
                "excluded_from_spearman": not trusted_role,
                "eligible_for_best_candidate": trusted_role,
                "incumbent_update_eligible": trusted_role,
                "used_for_inner_incumbent_update": False,
                "diagnostic_only": False,
                "accepted_update_allowed": trusted_role,
                "gradient_update_allowed": False,
                "range_update_allowed": trusted_role,
                "qps_used_for_inner_scoring": False,
                "boundary_corner": False,
                "boundary_plateau": False,
            }
        )
        selected.append(item)

    identifiers = [selector.fingerprint_id(item) for item in selected]
    parameter_keys = [selector.parameter_key(item) for item in selected]
    regimes = [exact_regime(item) for item in selected]
    if len(set(identifiers)) != 5 or any(value in excluded_ids for value in identifiers):
        raise RuntimeError("selected fingerprints are duplicated or previously replayed")
    if len(set(parameter_keys)) != 5 or any(value in excluded_params for value in parameter_keys):
        raise RuntimeError("selected parameter vectors are duplicated or previously replayed")
    if len(set(regimes[3:])) != 2 or any(value in registry for value in regimes[3:]):
        raise RuntimeError("probe regimes are duplicated or already trusted")
    for item in selected:
        missing = [name for name in REQUIRED_AUDIT_FIELDS if name not in item]
        if missing:
            raise RuntimeError(f"rank {item['rank']} audit fields missing: {missing}")

    audit = {
        "expanded_candidate_count": len(expanded),
        "eligible_novel_noncorner_count": len(eligible),
        "eligible_trusted_count": len(trusted_pool),
        "eligible_unknown_regime_count": len(unknown_by_regime),
        "excluded_fingerprint_count": len(excluded_ids),
        "excluded_parameter_count": len(excluded_params),
        "selected_unique_fingerprint_count": len(set(identifiers)),
        "selected_unique_parameter_count": len(set(parameter_keys)),
        "selected_noncorner_count": sum(
            item["boundary_corner"] is False and item["boundary_plateau"] is False
            for item in selected
        ),
        "trusted_count": 3,
        "probe_count": 2,
    }
    return selected, audit


def descending_ranks(values: Sequence[float]) -> List[int]:
    order = sorted(range(len(values)), key=lambda index: (-values[index], index))
    ranks = [0] * len(values)
    for rank, index in enumerate(order, 1):
        ranks[index] = rank
    return ranks


def spearman(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise RuntimeError("invalid Spearman inputs")
    left_ranks = descending_ranks(left)
    right_ranks = descending_ranks(right)
    count = len(left)
    squared = sum((a - b) ** 2 for a, b in zip(left_ranks, right_ranks))
    return 1.0 - 6.0 * squared / (count * (count * count - 1))


def launcher(
    selection_sha: str,
    gate_sha: str,
    config_sha: str,
    controller_sha: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

BASE={BASE}
PACKAGE={REMOTE_PACKAGE}
OUTPUT={REMOTE_OUTPUT}
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR="$BASE/vendor/py311"
CONTROLLER="$PACKAGE/run_tpch_formal_exact_plan_regime_batch.py"
SELECTION="$PACKAGE/top5.json"
GATE="$PACKAGE/gate.json"
CONFIG="$PACKAGE/config.json"
RESUME="$PACKAGE/merged_formal_state.json"
BASELINE={BASELINE}
WORKLOAD={WORKLOAD}
COUNTER={COUNTER}
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5

test ! -e "$OUTPUT"
test "$(tr -d '[:space:]' < "$COUNTER")" = "1"
test "$(sha256sum "$CONTROLLER" | awk '{{print $1}}')" = "{controller_sha}"
test "$(sha256sum "$SELECTION" | awk '{{print $1}}')" = "{selection_sha}"
test "$(sha256sum "$GATE" | awk '{{print $1}}')" = "{gate_sha}"
test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "{config_sha}"
test "$(sha256sum "$RESUME" | awk '{{print $1}}')" = "{EXPECTED_STATE_SHA256}"
test "$(sha256sum "$BASELINE" | awk '{{print $1}}')" = "{BASELINE_SHA256}"
test "$(sha256sum "$WORKLOAD" | awk '{{print $1}}')" = "{WORKLOAD_SHA256}"
test -z "$(ps -eo comm,args | awk '$1 ~ /python/ && $0 ~ /run_tpch_formal_exact_plan_regime_batch/ {{print}}')"
test "$(docker inspect -f '{{{{.State.Running}}}} {{{{.HostConfig.NanoCpus}}}} {{{{.HostConfig.Memory}}}} {{{{.HostConfig.MemorySwap}}}} {{{{.HostConfig.CpusetCpus}}}}' "$CONTAINER")" = "true 22000000000 1730000000 1730000000 0-15,36-51"

EXPECTED_SETTINGS=$'cpu_index_tuple_cost=0.005\\ncpu_operator_cost=0.0025\\ncpu_tuple_cost=0.01\\nparallel_setup_cost=1000\\nparallel_tuple_cost=0.1\\nrandom_page_cost=4\\nseq_page_cost=1'
ACTUAL_SETTINGS=$(docker exec "$CONTAINER" psql -X -At -p 64304 -U postgres -d "$DATABASE" -c "SELECT name || '=' || setting FROM pg_settings WHERE name IN ('cpu_index_tuple_cost','cpu_operator_cost','cpu_tuple_cost','parallel_setup_cost','parallel_tuple_cost','random_page_cost','seq_page_cost') ORDER BY name;")
test "$ACTUAL_SETTINGS" = "$EXPECTED_SETTINGS"

mkdir -p "$OUTPUT"
nohup env -u LD_LIBRARY_PATH \\
  PYTHONPATH="$VENDOR:$BASE/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \\
  PYTHONNOUSERSITE=1 \\
  LD_PRELOAD=/usr/lib64/libstdc++.so.6 \\
  PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \\
  "$PYTHON" -u "$CONTROLLER" \\
    --selection "$SELECTION" --selection-sha256 "{selection_sha}" \\
    --gate-summary "$GATE" --gate-summary-sha256 "{gate_sha}" \\
    --baseline-summary "$BASELINE" --baseline-summary-sha256 "{BASELINE_SHA256}" \\
    --config "$CONFIG" --config-sha256 "{config_sha}" \\
    --workload "$WORKLOAD" --workload-sha256 "{WORKLOAD_SHA256}" \\
    --accepted-counter "$COUNTER" \\
    --resume-state "$RESUME" --resume-state-sha256 "{EXPECTED_STATE_SHA256}" \\
    --selection-start-rank 1 --maximum-real-replay-invocations 5 \\
    --output "$OUTPUT" --container "$CONTAINER" --database "$DATABASE" \\
    --timeout-seconds 900 >"$OUTPUT/driver.log" 2>&1 &

pid=$!
printf '%s\\n' "$pid" >"$OUTPUT/driver.pid"
sleep 12
kill -0 "$pid"
printf 'PID=%s\\n' "$pid"
"""


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refuse to overwrite {OUTPUT}")
    required = {
        "expanded_candidates.json",
        "top5.json",
        "merged_top5_results.json",
        "merged_formal_state.json",
        "adaptive_replay_policy.py",
        "formal_continuation_accepted1_config.json",
    }
    absent = sorted(name for name in required if not (INPUT / name).is_file())
    if absent:
        raise FileNotFoundError(f"round28 inputs missing: {absent}")
    if sha256(INPUT / "merged_formal_state.json") != EXPECTED_STATE_SHA256:
        raise RuntimeError("merged formal state SHA mismatch")
    state = load(INPUT / "merged_formal_state.json")
    if (
        state.get("schema_version") != "tpch_exact_plan_regime_formal_state_v1"
        or state.get("status") != "complete"
        or state.get("baseline_restored") is not True
        or int(state.get("technical_failure_count", -1)) != 0
        or int(state.get("accepted_count", -1)) != EXPECTED_ACCEPTED_COUNT
        or not math.isclose(
            float(state.get("incumbent_score")),
            EXPECTED_INCUMBENT_SCORE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("merged formal state is not the authorized clean state")

    expansion = load(INPUT / "expanded_candidates.json")
    expanded = expansion["ranked_candidates"]
    prior_selection = load(INPUT / "top5.json")
    merged_results = load(INPUT / "merged_top5_results.json")
    if (
        int(merged_results.get("physical_call_count", -1)) != 5
        or int(merged_results.get("valid_replay_count", -1)) != 5
        or int(merged_results.get("technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("round27 merged results are not clean 5/5 evidence")

    excluded_ids, excluded_params, expanded_by_params = replay_exclusions(
        prior_selection, merged_results, expanded
    )
    registry, feedback_audit = add_round27_feedback(
        prior_selection, merged_results, expanded_by_params
    )
    selected, selection_audit = select_top5(
        expanded, registry, excluded_ids, excluded_params
    )
    selection = {
        "schema_version": "tpch_round28_exact_regime_formal_selection_v1",
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
        ),
        "calibration_contract": (
            "exact_workload_structural_plan_regime_componentwise_median_v2_"
            "round27_feedback"
        ),
        "formal_continuation": True,
        "accepted_count_start": EXPECTED_ACCEPTED_COUNT,
        "accepted_shrink_target": 10,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "trusted_slots": 3,
        "probe_slots": 2,
        "diagnostic_only": False,
        "accepted_update_allowed": True,
        "incumbent_update_allowed": True,
        "range_update_allowed": True,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "rejected_updates_forbidden": True,
        "resume_state_sha256": EXPECTED_STATE_SHA256,
        "source_evidence": {
            name: sha256(INPUT / name)
            for name in (
                "expanded_candidates.json",
                "top5.json",
                "merged_top5_results.json",
                "merged_formal_state.json",
            )
        },
        "round27_feedback_audit": feedback_audit,
        "validated_plan_regimes": registry,
        "replay_exclusion": {
            "fingerprint_count": len(excluded_ids),
            "parameter_count": len(excluded_params),
            "fingerprints": sorted(excluded_ids),
            "parameter_keys": sorted(excluded_params),
        },
        "selection_audit": selection_audit,
        "novel_unique_fingerprint_count": 5,
        "top5": selected,
        "ranked_candidates": selected,
    }

    trusted_results = [
        result
        for result in merged_results["results"]
        if result["selection_role"] == "trusted"
    ]
    trusted_predicted = [float(result["candidate_score"]) for result in trusted_results]
    trusted_actual = [float(result["actual_score"]) for result in trusted_results]
    actual_best = max(trusted_results, key=lambda result: float(result["actual_score"]))
    gate = {
        "schema_version": "tpch_round28_formal_continuation_gate_v1",
        "status": "complete",
        "successful_real_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_params_restored": True,
        "formal_continuation_ready": True,
        "formal_entry_already_active": True,
        "accepted_count_start": EXPECTED_ACCEPTED_COUNT,
        "accepted_shrink_target": 10,
        "resume_state_sha256": EXPECTED_STATE_SHA256,
        "incumbent_score": EXPECTED_INCUMBENT_SCORE,
        "round27_feedback_validation": {
            "trusted_only_spearman_before_feedback": spearman(
                trusted_predicted, trusted_actual
            ),
            "actual_best_source_rank": int(actual_best["rank"]),
            "actual_best_within_source_top3": int(actual_best["rank"]) <= 3,
            "probe_excluded_from_spearman": True,
            "trusted_measurements_used_for_exact_regime_calibration": 3,
            "probe_measurements_used_for_exact_regime_calibration": 0,
            "global_scalar_used": False,
        },
        "selection_checks": {
            **selection_audit,
            "roles": [item["selection_role"] for item in selected],
            "all_required_audit_fields_present": True,
            "unknown_regime_trusted_count": 0,
            "qps_used_for_inner_scoring": False,
        },
        "source_evidence": selection["source_evidence"],
    }

    config = load(INPUT / "formal_continuation_accepted1_config.json")
    formal = config["formal_outer_protocol"]
    formal["accepted_shrink_target"] = 10
    formal["formal_search_accepted_count_start"] = EXPECTED_ACCEPTED_COUNT
    formal["formal_search_accepted_target"] = 10
    formal["formal_search_max_replay_top_k"] = 5
    config["_tpch_round28_exact_regime_feedback"] = {
        "calibration_contract": selection["calibration_contract"],
        "global_scalar_forbidden": True,
        "unknown_regime_trusted_allowed": False,
        "trusted_slots": 3,
        "probe_slots": 2,
        "qps_used_for_inner_scoring": False,
        "resume_state_sha256": EXPECTED_STATE_SHA256,
        "accepted_count_start": EXPECTED_ACCEPTED_COUNT,
        "incumbent_score": EXPECTED_INCUMBENT_SCORE,
    }

    OUTPUT.mkdir(parents=True)
    atomic_json(OUTPUT / "top5.json", selection)
    atomic_json(OUTPUT / "gate.json", gate)
    atomic_json(OUTPUT / "config.json", config)
    shutil.copyfile(
        INPUT / "merged_formal_state.json", OUTPUT / "merged_formal_state.json"
    )
    shutil.copyfile(
        INPUT / "adaptive_replay_policy.py", OUTPUT / "adaptive_replay_policy.py"
    )
    shutil.copyfile(
        CONTROLLER_SOURCE, OUTPUT / "run_tpch_formal_exact_plan_regime_batch.py"
    )

    output_shas = {
        path.name: sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.is_file()
    }
    launch_text = launcher(
        output_shas["top5.json"],
        output_shas["gate.json"],
        output_shas["config.json"],
        output_shas["run_tpch_formal_exact_plan_regime_batch.py"],
    )
    atomic_text(OUTPUT / "launch_tpch_round28_formal.sh", launch_text)
    os.chmod(OUTPUT / "launch_tpch_round28_formal.sh", 0o755)
    output_shas["launch_tpch_round28_formal.sh"] = sha256(
        OUTPUT / "launch_tpch_round28_formal.sh"
    )

    manifest = {
        "schema_version": "tpch_round28_exact_regime_formal_package_manifest_v1",
        "offline_preparation_only": True,
        "new_workload_calls": 0,
        "new_ssh_calls": 0,
        "remote_package": str(REMOTE_PACKAGE),
        "remote_output": str(REMOTE_OUTPUT),
        "accepted_count_start": EXPECTED_ACCEPTED_COUNT,
        "accepted_target": 10,
        "incumbent_score": EXPECTED_INCUMBENT_SCORE,
        "resume_state_sha256": EXPECTED_STATE_SHA256,
        "baseline_summary": str(BASELINE),
        "baseline_summary_sha256": BASELINE_SHA256,
        "workload": str(WORKLOAD),
        "workload_sha256": WORKLOAD_SHA256,
        "maximum_physical_calls": 5,
        "trusted_roles": 3,
        "probe_roles": 2,
        "serial_real_replay_required": True,
        "qps_used_for_inner_scoring": False,
        "package_file_sha256": output_shas,
        "input_file_sha256": {
            path.name: sha256(path)
            for path in sorted(INPUT.iterdir())
            if path.is_file()
        },
    }
    atomic_json(OUTPUT / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
