#!/usr/bin/env python3
"""TPC-C target-10 controller with role-aware feasibility gates.

The exact-repeat builder deliberately reassigns trusted roles after every
observation using the accumulated best/median/worst score ladder.  A
pre-third gate based on the *previous* roles can therefore launch a third
clean5 batch solely because a point that will become an uncertain probe has a
high upper bound.  Likewise, a formal feasibility gate must never be passed
by a probe because probes are state-neutral by contract.

This successor computes the prospective exact-repeat roles before deciding
whether a third observation is useful and restricts formal feasibility to
the three trusted candidates.  It wraps v27 and changes no replay, runner,
state mutation, or acceptance implementation.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

try:
    import build_tpcc_exact_plan_repeat_validation_v3 as repeat_v3
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v26_prethird_bound as v26
    import tpcc_autonomous_target10_controller_v27_score_consistent as v27
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v3 as repeat_v3
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v26_prethird_bound as v26
    from tools import tpcc_autonomous_target10_controller_v27_score_consistent as v27


_FORMAL_STATE_PATH: Optional[Path] = None


def role_aware_feasibility_rows(
    candidates: Sequence[Mapping[str, Any]],
    incumbent_score: float,
) -> list[Dict[str, Any]]:
    """Return median upper bounds without allowing probes to pass the gate."""
    rows: list[Dict[str, Any]] = []
    for candidate in candidates:
        scores = [
            float(value)
            for value in candidate.get("independent_actual_scores") or []
        ]
        upper_bound = v25.maximum_median_after_one_observation(scores)
        trusted = candidate.get("selection_role") == "trusted"
        rows.append(
            {
                "rank": int(candidate["rank"]),
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": (
                    candidate.get("three_level_fingerprint") or {}
                ).get("id"),
                "sealed_scores": scores,
                "maximum_median_after_one_observation": upper_bound,
                "trusted_eligible": trusted,
                "feasible": bool(
                    trusted and upper_bound > float(incumbent_score) + 1e-12
                ),
            }
        )
    return rows


def build_role_aware_prethird_feasibility_gate(
    latest_audit: Mapping[str, Any],
    formal_state_path: Path,
) -> Optional[Dict[str, Any]]:
    """Evaluate the roles that the next exact-repeat builder will seal."""
    original = v26.build_prethird_feasibility_gate(
        latest_audit,
        formal_state_path,
    )
    if original is None:
        return None

    selection_path, selection = v26._verified_payload(
        latest_audit,
        "selection_path",
        "selection_sha256",
    )
    ledger_path, ledger = v26._verified_payload(
        latest_audit,
        "ledger_path",
        "ledger_sha256",
    )
    prospective = repeat_v3.build(
        selection,
        ledger,
        minimum_score_span=0.0,
    )
    rows = role_aware_feasibility_rows(
        prospective["top5"],
        float(original["incumbent_score"]),
    )
    feasible_ranks = [row["rank"] for row in rows if row["feasible"]]
    return {
        **copy.deepcopy(original),
        "contract": "two_observation_role_aware_prethird_median_upper_bound_v2",
        "candidate_bounds": rows,
        "feasible_ranks": feasible_ranks,
        "passed": bool(feasible_ranks),
        "prospective_selection_schema_version": prospective.get(
            "schema_version"
        ),
        "prospective_trusted_selection_strategy": prospective.get(
            "trusted_selection_strategy"
        ),
        "prospective_roles": [
            item["selection_role"] for item in prospective["top5"]
        ],
        "source_selection_path": str(selection_path),
        "source_ledger_path": str(ledger_path),
        "probe_upper_bounds_affect_gate": False,
        "manual_candidate_injection": False,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
    }


def choose_next_mode_v28(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    latest = audits[-1] if audits else None
    if latest is not None and _FORMAL_STATE_PATH is not None:
        role_gate = build_role_aware_prethird_feasibility_gate(
            latest,
            _FORMAL_STATE_PATH,
        )
        if role_gate is not None:
            if not isinstance(gate, MutableMapping):
                raise RuntimeError("quality gate is not mutable")
            gate["prethird_acceptance_feasibility_gate"] = copy.deepcopy(
                role_gate
            )
            if role_gate["passed"] is False:
                gate["prethird_zero_call_reroute"] = True
                gate["prethird_zero_call_reason"] = (
                    "no_prospective_trusted_candidate_can_beat_incumbent_"
                    "with_one_third_observation"
                )
                return "diagnostic"
    return v26.choose_next_mode_v26(audits, gate)


def accumulated_observation_formal_selection_v28(
    diagnostic_selection: Mapping[str, Any],
    diagnostic_ledger: Mapping[str, Any],
    quality_gate_payload: Mapping[str, Any],
    source_path: Any,
    incumbent_score: float,
) -> Dict[str, Any]:
    promoted = v27.accumulated_observation_formal_selection_v27(
        diagnostic_selection,
        diagnostic_ledger,
        quality_gate_payload,
        source_path,
        incumbent_score,
    )
    rows = role_aware_feasibility_rows(
        promoted["top5"],
        incumbent_score,
    )
    feasible_ranks = [row["rank"] for row in rows if row["feasible"]]
    promoted["formal_acceptance_feasibility_gate"] = {
        "contract": "trusted_only_one_additional_observation_median_upper_bound_v2",
        "incumbent_score": float(incumbent_score),
        "candidate_bounds": rows,
        "feasible_ranks": feasible_ranks,
        "passed": bool(feasible_ranks),
        "probe_upper_bounds_affect_gate": False,
        "manual_candidate_injection": False,
    }
    promoted["formal_prediction_contract"] = (
        "score_consistent_trusted_only_resource_representative_plus_"
        "independent_actual_score_median_v6"
    )
    return promoted


def _configure_from_argv(argv: Sequence[str]) -> None:
    global _FORMAL_STATE_PATH

    v26._configure_from_argv(argv)
    _FORMAL_STATE_PATH = v26._FORMAL_STATE_PATH


def main() -> int:
    _configure_from_argv(sys.argv)
    v25.accumulated_observation_formal_selection = (
        accumulated_observation_formal_selection_v28
    )
    v25.choose_next_mode = choose_next_mode_v28
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())
