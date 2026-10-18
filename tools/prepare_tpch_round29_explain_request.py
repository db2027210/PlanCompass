#!/usr/bin/env python3
"""Build the offline request for TPC-H round29 EXPLAIN-only expansion.

The output is a parameter-probe request, not an assertion that a candidate
belongs to an exact regime.  Exact-regime membership must be established by
PostgreSQL EXPLAIN and checked against ``expected_structural_plan_regime_id``.
No database connection or workload invocation is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping

import prepare_tpch_round29_corrected_diagnostic as diagnostic
import tpch_exact_regime_neighbor_expand as neighbor
import tpch_round19_plan_regime_select as selector


FACTORS = (0.95, 1.05, 0.90, 1.10, 0.80, 1.20)


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


def build(args: argparse.Namespace) -> Dict[str, Any]:
    selection_path = Path(args.selection)
    state_path = Path(args.state)
    expansion_path = Path(args.old_expansion)
    config_path = Path(args.config)
    output_path = Path(args.output)
    if output_path.exists():
        raise FileExistsError(f"refuse to overwrite output: {output_path}")
    for path in (selection_path, state_path, expansion_path, config_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    selection = load(selection_path)
    source_by_rank = diagnostic.validate_round28_selection(selection)
    state = load(state_path)
    diagnostic.validate_round28_state(state)
    selection_sha = sha256(selection_path)
    if state.get("selection_sha256") != selection_sha:
        raise RuntimeError("post-round28 state is not bound to round28 selection")
    if (
        int(state.get("physical_call_count", -1)) != 5
        or int(state.get("valid_replay_count", -1)) != 5
        or int(state.get("batch_added_accepted_count", -1)) != 0
    ):
        raise RuntimeError("post-round28 state counters are not clean")

    expansion = load(expansion_path)
    old_candidates = expansion.get("ranked_candidates")
    if not isinstance(old_candidates, list) or not old_candidates:
        raise RuntimeError("old expansion has no ranked_candidates")

    replay_exclusion = selection.get("replay_exclusion") or {}
    excluded_ids = set(replay_exclusion.get("fingerprints") or [])
    excluded_parameter_keys = set(replay_exclusion.get("parameter_keys") or [])
    for candidate in selection["top5"]:
        excluded_ids.add(selector.fingerprint_id(candidate))
        excluded_parameter_keys.add(selector.parameter_key(candidate))

    incumbent = state.get("incumbent_params")
    ranges = state.get("range_state")
    if not isinstance(incumbent, Mapping) or not isinstance(ranges, Mapping):
        raise RuntimeError("state lacks incumbent_params or range_state")
    config = load(config_path)
    if config.get("initial_params") != incumbent:
        raise RuntimeError("EXPLAIN config initial_params do not match incumbent")
    config_ranges = config.get("param_ranges")
    if not isinstance(config_ranges, Mapping):
        raise RuntimeError("EXPLAIN config lacks param_ranges")
    for name, state_range in ranges.items():
        config_range = config_ranges.get(name)
        if not isinstance(config_range, Mapping):
            raise RuntimeError(f"EXPLAIN config lacks range for {name}")
        for bound in ("min_value", "max_value"):
            if not math.isclose(
                float(config_range[bound]),
                float(state_range[bound]),
                rel_tol=0.0,
                abs_tol=0.0,
            ):
                raise RuntimeError(
                    f"EXPLAIN config/state range mismatch: {name}.{bound}"
                )

    requests = []
    request_parameter_keys: set[str] = set()
    for rank in (1, 2, 3):
        anchor = source_by_rank[rank]
        if anchor.get("selection_role") != "trusted":
            raise RuntimeError(f"round28 rank {rank} is not trusted")
        anchor_params = anchor.get("candidate_params")
        if not isinstance(anchor_params, Mapping):
            raise RuntimeError(f"round28 rank {rank} lacks candidate_params")
        candidates = neighbor.neighboring_params(anchor_params, incumbent, ranges)
        observed_factors = tuple(factor for factor, _params in candidates)
        if observed_factors != FACTORS:
            raise RuntimeError(
                f"rank {rank} did not produce the complete geometric factor set"
            )
        rank_requests = []
        for attempt, (factor, params) in enumerate(candidates, 1):
            for name, value in params.items():
                low = float(ranges[name]["min_value"])
                high = float(ranges[name]["max_value"])
                if not low < float(value) < high:
                    raise RuntimeError(
                        f"rank {rank} request {attempt} is outside range: {name}"
                    )
            parameter_key = selector.parameter_key({"candidate_params": params})
            if (
                parameter_key in excluded_parameter_keys
                or parameter_key in request_parameter_keys
            ):
                raise RuntimeError(
                    f"rank {rank} request {attempt} is not a novel parameter vector"
                )
            request_parameter_keys.add(parameter_key)
            rank_requests.append(
                {
                    "attempt": attempt,
                    "factor": factor,
                    "candidate_params": params,
                    "parameter_key": parameter_key,
                    "expected_structural_plan_regime_id": (
                        diagnostic.exact_regime(anchor)
                    ),
                    "accept_only_if_exact_regime_matches": True,
                    "stop_after_first_matching_novel_fingerprint": True,
                }
            )
        requests.append(
            {
                "anchor_rank": rank,
                "anchor_fingerprint": selector.fingerprint_id(anchor),
                "anchor_parameter_key": selector.parameter_key(anchor),
                "expected_structural_plan_regime_id": (
                    diagnostic.exact_regime(anchor)
                ),
                "minimum_matching_candidates_required": 1,
                "attempts": rank_requests,
            }
        )

    validated_regimes = set(selection.get("validated_plan_regimes") or {})
    unknown_pool = []
    for candidate in old_candidates:
        if candidate.get("boundary_corner") is True:
            continue
        if candidate.get("boundary_plateau") is True:
            continue
        fingerprint = selector.fingerprint_id(candidate)
        parameter_key = selector.parameter_key(candidate)
        if fingerprint in excluded_ids or parameter_key in excluded_parameter_keys:
            continue
        regime = diagnostic.exact_regime(candidate)
        if regime in validated_regimes:
            continue
        unknown_pool.append(
            {
                "fingerprint": fingerprint,
                "parameter_key": parameter_key,
                "structural_plan_regime_id": regime,
                "candidate_score": float(candidate["candidate_score"]),
            }
        )
    if len(unknown_pool) < 2:
        raise RuntimeError("old expansion lacks two novel unknown-regime probes")
    if len({item["structural_plan_regime_id"] for item in unknown_pool}) < 2:
        raise RuntimeError("unknown probe pool lacks two distinct exact regimes")

    request = {
        "schema_version": "tpch_round29_explain_only_request_v1",
        "offline_preparation_only": True,
        "new_ssh_calls": 0,
        "new_workload_calls": 0,
        "explain_required": True,
        "explain_has_not_been_executed": True,
        "diagnostic_only": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "source_evidence": {
            "round28_selection": {
                "path": str(selection_path),
                "sha256": selection_sha,
            },
            "post_round28_state": {
                "path": str(state_path),
                "sha256": sha256(state_path),
            },
            "old_expansion": {
                "path": str(expansion_path),
                "sha256": sha256(expansion_path),
            },
            "range_bound_explain_config": {
                "path": str(config_path),
                "sha256": sha256(config_path),
            },
        },
        "trusted_anchor_count": 3,
        "maximum_explain_attempts_per_anchor": len(FACTORS),
        "total_prepared_explain_attempts": sum(
            len(item["attempts"]) for item in requests
        ),
        "trusted_exact_regime_requests": requests,
        "existing_novel_unknown_probe_pool": unknown_pool,
        "existing_novel_unknown_probe_count": len(unknown_pool),
        "existing_novel_unknown_regime_count": len(
            {item["structural_plan_regime_id"] for item in unknown_pool}
        ),
        "completion_gate": {
            "minimum_matching_validated_exact_regime_candidates": 3,
            "one_matching_candidate_required_per_trusted_anchor": True,
            "minimum_distinct_unknown_exact_regimes": 2,
            "minimum_novel_noncorner_three_level_fingerprints": 5,
            "unknown_regime_trusted_allowed": False,
        },
        "execution_strategy": {
            "script": "tools/tpch_exact_regime_neighbor_expand.py",
            "evidence_top5": str(selection_path),
            "history_or_analysis": str(expansion_path),
            "config": str(config_path),
            "missing_local_runtime_inputs": [
                "baseline plan capture containing SQL text for all 22 query IDs",
                "live TPC-H PostgreSQL endpoint for EXPLAIN",
            ],
            "workload_invocation_forbidden": True,
            "formal_state_mutation_forbidden": True,
            "fail_closed_if_any_trusted_anchor_has_no_same_regime_neighbor": True,
        },
    }
    atomic_json(output_path, request)
    return request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selection",
        default="/tmp/tpch_round28_output/top5.json",
    )
    parser.add_argument(
        "--state",
        default="/tmp/tpch_round29_input/round28_formal_state.json",
    )
    parser.add_argument(
        "--old-expansion",
        default="/tmp/tpch_round28_input/expanded_candidates.json",
    )
    parser.add_argument(
        "--config",
        default="/tmp/tpch_round29_input/round29_explain_config.json",
    )
    parser.add_argument(
        "--output",
        default="/tmp/tpch_round29_input/round29_explain_request.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request = build(args)
    print(
        json.dumps(
            {
                "output": args.output,
                "trusted_anchor_count": request["trusted_anchor_count"],
                "total_prepared_explain_attempts": request[
                    "total_prepared_explain_attempts"
                ],
                "existing_novel_unknown_probe_count": request[
                    "existing_novel_unknown_probe_count"
                ],
                "existing_novel_unknown_regime_count": request[
                    "existing_novel_unknown_regime_count"
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
