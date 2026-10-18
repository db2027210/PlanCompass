#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Tuple

from resource_utility import ResourceUtilityCalculator


ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
CAPABILITY = {"cpu": 5.5, "memory": 223_155_000.0, "io": 103_809_024.0}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON : {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def parameter_signature(params: Dict[str, float]) -> str:
    return canonical_sha(
        {name: float(params[name]).hex() for name in sorted(params)}
    )


def expand_prediction_dedup_to_subset_audit_rows(
    ranking: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Restore all 63 subset identities without undoing prediction dedup.

    The subset optimizer intentionally collapses identical predicted points.
    PostgreSQL quantization and exact-plan grouping still need a complete
    subset ledger, so duplicate subset rows reference their prediction
    representative and remain suppressible before real replay.
    """
    unique_candidates = copy.deepcopy(ranking.get("candidate_rankings"))
    audit_records = copy.deepcopy(ranking.get("audit_records"))
    if not isinstance(unique_candidates, list) or not unique_candidates:
        raise RuntimeError("source has no prediction candidates")
    if not isinstance(audit_records, list) or len(audit_records) != 63:
        raise RuntimeError("source must contain 63 successful subset audit rows")
    by_subset = {
        str(candidate["subset_id"]): candidate for candidate in unique_candidates
    }
    expanded: List[Dict[str, Any]] = []
    for audit in audit_records:
        if audit.get("status") != "success":
            raise RuntimeError(f"subset audit is not successful: {audit.get('subset_id')}")
        subset_id = str(audit["subset_id"])
        representative_id = str(
            audit.get("deduplicated_into_subset_id") or subset_id
        )
        representative = by_subset.get(representative_id)
        if representative is None:
            raise RuntimeError(
                f"prediction representative is absent: {subset_id} -> {representative_id}"
            )
        candidate = copy.deepcopy(representative)
        candidate.update(
            {
                "subset_id": subset_id,
                "active_subset": copy.deepcopy(audit["active_subset"]),
                "canonical_subset_order": int(audit["canonical_subset_order"]),
                "optimized_params": copy.deepcopy(audit["optimized_params"]),
                "final_score": float(audit["final_score"]),
                "iterations": int(audit["iterations"]),
                "log_path": str(audit["log_path"]),
                "prediction_deduplicated": representative_id != subset_id,
                "prediction_representative_subset_id": representative_id,
                "source_prediction_representative_rank": int(representative["rank"]),
            }
        )
        metadata = copy.deepcopy(candidate.get("final_estimation_metadata") or {})
        metadata["subset_id"] = subset_id
        metadata["active_subset"] = copy.deepcopy(audit["active_subset"])
        candidate["final_estimation_metadata"] = metadata
        candidate.pop("duplicate_subset_ids", None)
        expanded.append(candidate)
    expanded.sort(
        key=lambda candidate: (
            -float(candidate["final_score"]),
            float(candidate["ranking_tiebreak"]["predicted_target_utilization"]),
            int(candidate["canonical_subset_order"]),
        )
    )
    for rank, candidate in enumerate(expanded, start=1):
        candidate["rank"] = rank
    return expanded, {
        "policy_id": "all_63_subset_identities_with_prediction_representative_mapping_v1",
        "formal_nonempty_subset_count": 63,
        "prediction_unique_candidate_count": len(unique_candidates),
        "prediction_duplicate_subset_count": 63 - len(unique_candidates),
        "duplicates_are_not_additional_recall_candidates": True,
        "next_dedup_stage": "postgresql_applied_signature_then_exact_plan_cluster",
    }


def bulk_round_trip(
    candidates: List[Dict[str, Any]],
    *,
    image: str,
    pgdata: Path,
) -> Dict[Tuple[int, str], Dict[str, Any]]:
    script = r'''
set -eu
while [ "$#" -gt 0 ]; do
  rank_token=$1
  setting_name=$2
  raw_value=$3
  shift 3
  first_show=$(postgres -D /var/lib/postgresql/data -C "$setting_name" -c "$setting_name=$raw_value")
  second_show=$(postgres -D /var/lib/postgresql/data -C "$setting_name" -c "$setting_name=$first_show")
  printf '%s\t%s\t%s\t%s\n' "$rank_token" "$setting_name" "$first_show" "$second_show"
done
'''
    probe_name = f"p24_ap6_tsgd_guc_probe_{os.getpid()}"
    command = [
        "docker",
        "run",
        "--name",
        probe_name,
        "--rm",
        "--user",
        "postgres",
        "--volume",
        f"{pgdata}:/var/lib/postgresql/data:ro",
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
        "roundtrip",
    ]
    for candidate in candidates:
        rank = int(candidate["rank"])
        continuous = candidate.get("optimized_params") or {}
        if set(continuous) != set(ACTIVE_PARAMS):
            raise RuntimeError(f"rank {rank}: continuous params are not exact AP6")
        for name in ACTIVE_PARAMS:
            value = float(continuous[name])
            if not math.isfinite(value) or value <= 0.0:
                raise RuntimeError(f"rank {rank}: invalid {name}={value!r}")
            command.extend([str(rank), name, format(value, ".17g")])
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        subprocess.run(
            ["docker", "rm", "-f", probe_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raise RuntimeError(f"PostgreSQL GUC probe failed: {result.stderr.strip()}")
    rows: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        rank_text, name, first_show, second_show = line.split("\t")
        rank = int(rank_text)
        key = (rank, name)
        if key in rows or name not in ACTIVE_PARAMS or first_show != second_show:
            raise RuntimeError(f"invalid/non-idempotent GUC row: {line}")
        applied = float(first_show)
        if not math.isfinite(applied) or applied <= 0.0:
            raise RuntimeError(f"non-positive/nonfinite GUC row: {line}")
        rows[key] = {
            "first_show": first_show,
            "second_show": second_show,
            "applied_value": applied,
            "idempotent": True,
        }
    expected = len(candidates) * len(ACTIVE_PARAMS)
    if len(rows) != expected:
        raise RuntimeError(f"incomplete GUC rows: {len(rows)} != {expected}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--ranking-json", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--pgdata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state-json", type=Path)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    ranking_path = args.ranking_json.resolve()
    pgdata = args.pgdata.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite output: {output}")
    ranking = load_json(ranking_path)
    candidates, prediction_dedup = expand_prediction_dedup_to_subset_audit_rows(
        ranking
    )
    if (
        len(candidates) != 63
        or ranking.get("subset_count") != 63
        or ranking.get("successful_subset_count") != 63
        or ranking.get("failed_subset_count") != 0
    ):
        raise RuntimeError("source must be a complete 63-subset AP6 T-SGD ranking")

    table_path = lane / "experience_table_T_ap6_v3.json"
    table_artifact = load_json(table_path)
    table = table_artifact["table"]
    state_path = args.state_json.resolve() if args.state_json else None
    if state_path is None:
        baseline_path = (
            lane
            / "results/shared_baseline/P0_repeat_1_attempt_34_retry3/measurement_summary.json"
        )
        baseline = load_json(baseline_path)
        initial_params = copy.deepcopy(INITIAL_PARAMS)
        base_resource = {
            name: float(baseline["resource_mean_utilization"][name]) * CAPABILITY[name]
            for name in ("cpu", "memory", "io")
        }
        state_sha256 = None
    else:
        state = load_json(state_path)
        if state.get("status") != "accepted_incumbent_state":
            raise RuntimeError("state-json is not an accepted incumbent state")
        incumbent = state.get("incumbent") or {}
        raw_params = incumbent.get("params") or {}
        utilization_pct = incumbent.get("utilization_pct") or {}
        if not set(raw_params).issuperset(ACTIVE_PARAMS):
            raise RuntimeError("accepted incumbent params are incomplete")
        initial_params = {name: float(raw_params[name]) for name in ACTIVE_PARAMS}
        base_resource = {
            name: (float(utilization_pct[name]) / 100.0) * CAPABILITY[name]
            for name in ("cpu", "memory", "io")
        }
        baseline_path = state_path
        state_sha256 = sha256(state_path)
    scoring_path = lane / "common_scoring_contract.json"
    scoring = load_json(scoring_path)
    utility = ResourceUtilityCalculator(
        reward_type="log",
        penalty_type="power",
        use_continuous=False,
        overload_threshold=float(scoring["overload_threshold"]),
        score_mode=str(scoring["score_mode"]),
        resource_weights=scoring.get("resource_weights"),
        resource_target_ranges=scoring.get("resource_target_ranges"),
    )
    roundtrips = bulk_round_trip(candidates, image=args.image, pgdata=pgdata)

    applied_candidates: List[Dict[str, Any]] = []
    for source in candidates:
        old_rank = int(source["rank"])
        continuous_params = copy.deepcopy(source["optimized_params"])
        applied_params = {
            name: roundtrips[(old_rank, name)]["applied_value"]
            for name in ACTIVE_PARAMS
        }
        effects = {name: 0.0 for name in ("cpu", "memory", "io")}
        for param_name in ACTIVE_PARAMS:
            delta = math.log(applied_params[param_name] / initial_params[param_name])
            for resource_name in effects:
                effects[resource_name] += float(table[param_name][resource_name]) * delta
        final_resource = {
            name: base_resource[name] * math.exp(effects[name])
            for name in effects
        }
        final_score = float(
            utility.calculate_utility_score(final_resource, CAPABILITY)
        )
        candidate = copy.deepcopy(source)
        candidate["source_continuous_rank"] = old_rank
        candidate["continuous_model_only"] = {
            "optimized_params": continuous_params,
            "final_resource": copy.deepcopy(source["final_resource"]),
            "final_score": float(source["final_score"]),
            "score_improvement": float(source["score_improvement"]),
            "role": "audit_only_never_replayed_or_used_as_shrink_anchor",
        }
        candidate["optimized_params"] = applied_params
        candidate["final_resource"] = final_resource
        candidate["final_score"] = final_score
        candidate["score_improvement"] = final_score - float(source["initial_score"])
        candidate["applied_parameter_signature_sha256"] = parameter_signature(
            applied_params
        )
        candidate["postgresql_guc_roundtrip"] = {
            name: {
                "continuous_value": float(continuous_params[name]),
                **roundtrips[(old_rank, name)],
            }
            for name in ACTIVE_PARAMS
        }
        candidate["ranking_tiebreak"] = {
            "contract": (
                "applied_final_score_desc_then_applied_target_utilization_asc_"
                "then_canonical_subset_order_v1"
            ),
            "target_resource": "memory",
            "predicted_target_resource": final_resource["memory"],
            "predicted_target_utilization": (
                final_resource["memory"] / CAPABILITY["memory"]
            ),
            "applies_only_when_final_score_equal": True,
        }
        applied_candidates.append(candidate)

    signature_groups: Dict[str, List[str]] = {}
    for candidate in applied_candidates:
        signature_groups.setdefault(
            candidate["applied_parameter_signature_sha256"], []
        ).append(str(candidate["subset_id"]))
    signatures = set(signature_groups)
    applied_candidates.sort(
        key=lambda candidate: (
            -float(candidate["final_score"]),
            float(candidate["ranking_tiebreak"]["predicted_target_utilization"]),
            int(candidate["canonical_subset_order"]),
        )
    )
    subset_to_rank: Dict[str, int] = {}
    for rank, candidate in enumerate(applied_candidates, start=1):
        candidate["rank"] = rank
        subset_to_rank[str(candidate["subset_id"])] = rank

    audit_records = copy.deepcopy(ranking["audit_records"])
    for record in audit_records:
        record["continuous_model_candidate_rank"] = record.get("candidate_rank")
        record["candidate_rank"] = subset_to_rank[str(record["subset_id"])]

    result = copy.deepcopy(ranking)
    result.update(
        {
            "schema_version": 2,
            "status": "complete_postgresql_applied",
            "algorithm": "experience_table_subset_sgd_v1",
            "source_ranking_path": str(ranking_path),
            "source_ranking_sha256": sha256(ranking_path),
            "candidate_rankings": applied_candidates,
            "audit_records": audit_records,
            "unique_candidate_count": len(signatures),
            "formal_nonempty_subset_candidate_count": 63,
            "prediction_deduplication": prediction_dedup,
            "postgresql_applied_signature_deduplication": {
                "policy_id": "preserve_subset_audit_rows_and_suppress_by_exact_plan_before_replay_v1",
                "formal_subset_row_count": 63,
                "unique_applied_signature_count": len(signatures),
                "duplicate_applied_signature_count": 63 - len(signatures),
                "signature_groups": signature_groups,
                "duplicates_are_not_additional_recall_candidates": True,
            },
            "postgresql_guc_quantization": {
                "image": args.image,
                "probe_pgdata": str(pgdata),
                "semantics": (
                    "postgres_-C_parse_show_then_second_-C_idempotence_before_EXPLAIN"
                ),
                "continuous_values_role": "audit_only",
                "applied_values_role": (
                    "ranking_signature_EXPLAIN_real_replay_acceptance_and_shrink_anchor"
                ),
                "roundtrip_row_count": len(roundtrips),
                "all_idempotent": True,
            },
            "experience_table_path": str(table_path),
            "experience_table_sha256": sha256(table_path),
            "baseline_path": str(baseline_path),
            "baseline_sha256": sha256(baseline_path),
            "state_path": str(state_path) if state_path else None,
            "state_sha256": state_sha256,
            "incumbent_params": initial_params,
            "scoring_contract_path": str(scoring_path),
            "scoring_contract_sha256": sha256(scoring_path),
        }
    )
    write_json(output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(output),
                "sha256": sha256(output),
                "candidate_count": len(applied_candidates),
                "unique_applied_signatures": len(signatures),
                "top_ranks": [
                    {
                        "rank": candidate["rank"],
                        "source_continuous_rank": candidate["source_continuous_rank"],
                        "subset_id": candidate["subset_id"],
                        "final_score": candidate["final_score"],
                    }
                    for candidate in applied_candidates[:8]
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
