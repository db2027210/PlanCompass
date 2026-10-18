"""PG-aligned interval helpers with permanent-invalid identity exclusion."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from scripts.mysql_algorithm_c_interval_control_v2 import *  # noqa: F403
from scripts.mysql_algorithm_c_interval_control_v2 import POLICY_ID as V2_POLICY_ID


POLICY_ID = "mysql_algorithm_c_pg_interval_regions_permanent_exclusion_v3"


def select_expanded_top5(
    *,
    coverage_rankings: Sequence[Mapping[str, Any]],
    local_rankings: Sequence[Mapping[str, Any]],
    incumbent_params: Mapping[str, Any],
    replayed_signatures: set[str],
    permanently_invalid_signatures: set[str],
    accepted_signatures: set[str],
    policy: Any,
    optimizer_params_key: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select fresh first-miss points while never reviving invalid identities."""
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded_replayed = 0
    excluded_permanent = 0
    for source, rows in (
        ("current_interval_coverage", coverage_rankings),
        ("trusted_gradient", local_rankings),
    ):
        for raw in rows:
            row = copy.deepcopy(dict(raw))
            signature = optimizer_params_key(row["optimized_params"])
            if signature in permanently_invalid_signatures:
                excluded_permanent += 1
                continue
            if signature in replayed_signatures:
                excluded_replayed += 1
                continue
            if signature in seen:
                continue
            seen.add(signature)
            row["algorithm_c_first_miss_source"] = source
            pool.append(row)
    admitted, audit = policy.admit_partial_topk(
        pool,
        incumbent_params=incumbent_params,
        previously_accepted_signatures=accepted_signatures,
    )
    for rank, row in enumerate(admitted, 1):
        row["rank"] = rank
    selected_keys = {
        optimizer_params_key(row["optimized_params"]) for row in admitted
    }
    if selected_keys.intersection(permanently_invalid_signatures):
        raise RuntimeError("permanent-invalid Algorithm C identity was selected")
    return admitted, {
        "contract": POLICY_ID,
        "source_contract": V2_POLICY_ID,
        "trigger": "one_complete_valid_rejected_batch",
        "coverage_generated": len(coverage_rankings),
        "local_generated": len(local_rankings),
        "excluded_previously_replayed": excluded_replayed,
        "excluded_permanently_invalid": excluded_permanent,
        "permanent_invalid_selected": False,
        "admission": audit,
        "qps_used_for_generation_or_ranking": False,
        "selected_count": len(admitted),
    }


def filter_recovery_rankings(
    rows: Sequence[Mapping[str, Any]], *,
    replayed_signatures: set[str],
    permanently_invalid_signatures: set[str],
    optimizer_params_key: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the same identity exclusions to boundary/numeric recovery pools."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    replayed = permanent = duplicate = 0
    for raw in rows:
        row = copy.deepcopy(dict(raw))
        signature = optimizer_params_key(row["optimized_params"])
        if signature in permanently_invalid_signatures:
            permanent += 1
            continue
        if signature in replayed_signatures:
            replayed += 1
            continue
        if signature in seen:
            duplicate += 1
            continue
        seen.add(signature)
        result.append(row)
    return result, {
        "contract": POLICY_ID,
        "input_count": len(rows),
        "output_count": len(result),
        "excluded_permanently_invalid": permanent,
        "excluded_previously_replayed": replayed,
        "excluded_duplicate": duplicate,
        "permanent_invalid_selected": False,
    }
