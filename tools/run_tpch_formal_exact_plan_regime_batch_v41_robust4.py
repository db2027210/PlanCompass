#!/usr/bin/env python3
"""Run TPC-H formal replay with four-observation robust acceptance.

The sealed formal runner remains responsible for workload execution, strict
serialization, technical-failure accounting, baseline restore, and atomic
accepted/incumbent/range commits.  This compatibility layer replaces only the
candidate measurement returned to that runner: three sealed diagnostic
resources plus the new raw formal resource are aggregated componentwise by
their four-observation median and rescored.  Thus accepted state remains
resource/score consistent and cannot be committed from one noisy replay.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, Mapping, Tuple


REMOTE_ORIGINAL = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726/code/"
    "run_tpch_formal_exact_plan_regime_batch.py"
)
LOCAL_ORIGINAL = Path(__file__).with_name(
    "run_tpch_formal_exact_plan_regime_batch.py"
)
EXPECTED_ORIGINAL_SHA256 = {
    REMOTE_ORIGINAL: (
        "5db636e8983b8e743520a870549e744af65e6c40fcb00eab61e66108a091ffb9"
    ),
    LOCAL_ORIGINAL: (
        "83664e16f31ab1fc9991d7336f696a8108657dd3315d0c433cecff12d6e412f5"
    ),
}
AGGREGATION_CONTRACT = (
    "tpch_componentwise_four_observation_resource_median_rescored_v1"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_original() -> Any:
    path = REMOTE_ORIGINAL if REMOTE_ORIGINAL.is_file() else LOCAL_ORIGINAL
    expected = EXPECTED_ORIGINAL_SHA256[path]
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError("TPC-H sealed formal runner SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v41_sealed_formal_runner",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H formal runner")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


original = load_original()


def parameter_key(params: Mapping[str, Any]) -> str:
    normalized = {
        name: format(float(params[name]), ".17g")
        for name in sorted(original.ALLOWED_PARAMS)
    }
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def sealed_history(candidate: Mapping[str, Any]) -> Dict[str, list[float]]:
    role = str(candidate.get("selection_role") or "")
    stability = candidate.get("three_observation_stability") or {}
    confidence = candidate.get("confidence") or {}
    if (
        role not in {"trusted", "probe"}
        or int(confidence.get("measurement_count", -1)) != 3
        or not isinstance(stability.get("score_values"), list)
        or len(stability["score_values"]) != 3
        or (role == "trusted" and stability.get("passed") is not True)
        or (role == "trusted" and candidate.get("exact_plan_regime_validated") is not True)
    ):
        raise RuntimeError("formal candidate lacks sealed three-observation evidence")
    resource_audit = (
        (candidate.get("three_level_fingerprint") or {}).get(
            "resource_formula_audit"
        )
        or {}
    )
    history: Dict[str, list[float]] = {}
    for name in original.RESOURCES:
        values = [
            float(value)
            for value in (resource_audit.get(name) or {}).get(
                "source_actual_resources", []
            )
        ]
        if len(values) != 3 or any(
            not math.isfinite(value) or value <= 0.0 for value in values
        ):
            raise RuntimeError(
                f"formal candidate lacks three positive {name} observations"
            )
        history[name] = values
    return history


def install_robust_measurement(
    selection: Mapping[str, Any],
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Mapping[str, Any]]]:
    candidates = original.validate_selection(selection)
    by_parameter: Dict[str, Mapping[str, Any]] = {}
    histories: Dict[str, Dict[str, list[float]]] = {}
    rank_by_parameter: Dict[str, int] = {}
    for rank, candidate in enumerate(candidates, 1):
        params = candidate.get("candidate_params") or candidate.get(
            "optimized_params"
        )
        key = parameter_key(params)
        if key in by_parameter:
            raise RuntimeError("formal Top-5 contains duplicate parameter points")
        by_parameter[key] = candidate
        rank_by_parameter[key] = rank
        histories[key] = sealed_history(candidate)

    sealed_apply_params = original.apply_params
    sealed_classify_payload = original.classify_payload
    sealed_atomic_json = original.atomic_json
    active_key: str | None = None
    audits: Dict[int, Dict[str, Any]] = {}

    def apply_params(
        container: str,
        database: str,
        params: Mapping[str, Any],
    ) -> Any:
        nonlocal active_key
        result = sealed_apply_params(container, database, params)
        active_key = parameter_key(params)
        return result

    def classify_payload(
        payload: Mapping[str, Any],
        returncode: int,
        capability: Mapping[str, float],
    ) -> tuple[Dict[str, float], Dict[str, float], float]:
        raw_resource, raw_utilization, raw_score = sealed_classify_payload(
            payload,
            returncode,
            capability,
        )
        if active_key not in by_parameter:
            raise RuntimeError("formal measurement is not bound to a Top-5 point")
        rank = rank_by_parameter[active_key]
        history = histories[active_key]
        aggregate_resource = {
            name: statistics.median([*history[name], float(raw_resource[name])])
            for name in original.RESOURCES
        }
        aggregate_score, aggregate_utilization = original.score_resource(
            aggregate_resource,
            capability,
        )
        audits[rank] = {
            "contract": AGGREGATION_CONTRACT,
            "rank": rank,
            "selection_role": by_parameter[active_key]["selection_role"],
            "diagnostic_resource_values": copy.deepcopy(history),
            "raw_formal_resource": copy.deepcopy(raw_resource),
            "raw_formal_utilization": copy.deepcopy(raw_utilization),
            "raw_formal_score": float(raw_score),
            "aggregate_resource": copy.deepcopy(aggregate_resource),
            "aggregate_utilization": copy.deepcopy(aggregate_utilization),
            "aggregate_score": float(aggregate_score),
            "observation_count": 4,
            "qps_used_for_inner_scoring": False,
        }
        return aggregate_resource, aggregate_utilization, aggregate_score

    def add_audit(result: Mapping[str, Any]) -> Dict[str, Any]:
        enriched = copy.deepcopy(dict(result))
        rank = int(enriched["rank"])
        audit = audits.get(rank)
        if audit is None:
            raise RuntimeError(f"valid formal rank lacks robust audit: {rank}")
        enriched.update(
            {
                "raw_formal_resource": copy.deepcopy(
                    audit["raw_formal_resource"]
                ),
                "raw_formal_utilization": copy.deepcopy(
                    audit["raw_formal_utilization"]
                ),
                "raw_formal_score": audit["raw_formal_score"],
                "formal_four_observation_aggregation": copy.deepcopy(
                    dict(audit)
                ),
            }
        )
        return enriched

    def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        enriched = copy.deepcopy(dict(value))
        if path.name == "result.json" and enriched.get("valid_replay") is True:
            enriched = add_audit(enriched)
        elif path.name == "summary.json":
            enriched["successful"] = [
                add_audit(result) for result in enriched.get("successful") or []
            ]
            enriched["formal_acceptance_statistic_contract"] = (
                AGGREGATION_CONTRACT
            )
            enriched["single_observation_acceptance_forbidden"] = True
        elif path.name == "formal_state.json":
            enriched["formal_acceptance_statistic_contract"] = (
                AGGREGATION_CONTRACT
            )
            enriched["single_observation_acceptance_forbidden"] = True
        sealed_atomic_json(path, enriched)

    original.apply_params = apply_params
    original.classify_payload = classify_payload
    original.atomic_json = atomic_json
    return audits, by_parameter


def annotate_outputs(output: Path, audits: Mapping[int, Mapping[str, Any]]) -> None:
    summary_path = output / "summary.json"
    state_path = output / "formal_state.json"
    if not summary_path.is_file() or not state_path.is_file():
        raise RuntimeError("sealed formal runner did not complete its state outputs")
    summary = original.load_json(summary_path)
    state = original.load_json(state_path)
    for result in summary.get("successful") or []:
        rank = int(result["rank"])
        audit = audits.get(rank)
        if audit is None:
            raise RuntimeError(f"valid formal rank lacks robust audit: {rank}")
        point_result = output / f"rank_{rank:02d}/attempt_01/result.json"
        point = original.load_json(point_result)
        if not (
            result.get("formal_four_observation_aggregation") == audit
            and point.get("formal_four_observation_aggregation") == audit
        ):
            raise RuntimeError(f"formal rank robust audit was not persisted: {rank}")
    if not (
        summary.get("formal_acceptance_statistic_contract")
        == AGGREGATION_CONTRACT
        and summary.get("single_observation_acceptance_forbidden") is True
        and state.get("formal_acceptance_statistic_contract")
        == AGGREGATION_CONTRACT
        and state.get("single_observation_acceptance_forbidden") is True
    ):
        raise RuntimeError("formal robust acceptance contract was not persisted")
    for rank in sorted(audits):
        original.append_event(
            output / "events.jsonl",
            {
                "event": "tpch_formal_four_observation_aggregation_audit",
                **copy.deepcopy(dict(audits[rank])),
            },
        )


def main() -> int:
    args = original.parse_args()
    if original.file_sha256(args.selection) != args.selection_sha256:
        raise SystemExit("formal selection SHA mismatch before robust binding")
    selection = original.load_json(args.selection)
    audits, _ = install_robust_measurement(selection)
    original.main()
    annotate_outputs(args.output, audits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
