#!/usr/bin/env python3
"""TPC-C target-10 controller with a zero-call pre-third feasibility gate.

The v25 controller already prevents a mathematically impossible *formal*
batch after three diagnostic observations.  This successor applies the same
order-statistic argument one batch earlier: after two clean exact-point
observations, an arbitrarily good third observation cannot raise the median
above the larger of the two sealed scores.  If none of the five candidates
can beat the formal incumbent, the controller deterministically returns to
diagnostic discovery without launching the third clean5 replay.

The module wraps v25 instead of copying its controller loop.  Only
``choose_next_mode`` is replaced; every replay, state-neutrality, SHA pin,
serial execution, and formal acceptance check remains owned by v25.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

try:
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25


_ORIGINAL_CHOOSE_NEXT_MODE = v25.choose_next_mode
_FORMAL_STATE_PATH: Optional[Path] = None


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"non-finite {label}: {value!r}")
    return number


def _verified_payload(
    audit: Mapping[str, Any],
    path_field: str,
    sha_field: str,
) -> tuple[Path, Dict[str, Any]]:
    path = Path(str(audit[path_field])).resolve()
    expected_sha256 = str(audit[sha_field])
    if not path.is_file() or v25.sha256(path) != expected_sha256:
        raise RuntimeError(
            f"pre-third evidence SHA mismatch: {path_field}={path}"
        )
    return path, _load_json(path)


def build_prethird_feasibility_gate(
    latest_audit: Mapping[str, Any],
    formal_state_path: Path,
) -> Optional[Dict[str, Any]]:
    """Prove whether one third observation can still beat the incumbent.

    ``None`` means the latest audit is not the exact clean two-observation
    boundary to which this gate applies.  Once that boundary is identified,
    malformed or mismatched evidence raises and therefore fails closed.
    """
    if (
        latest_audit.get("mode") != "diagnostic_validation"
        or latest_audit.get("clean") is not True
        or int(latest_audit.get("physical_calls", -1)) != v25.CAP
        or int(latest_audit.get("valid_replays", -1)) != v25.CAP
        or int(latest_audit.get("technical_failures", -1)) != 0
        or int(
            latest_audit.get("minimum_total_exact_observation_count", -1)
        )
        != 2
        or int(
            latest_audit.get("maximum_total_exact_observation_count", -1)
        )
        != 2
    ):
        return None

    selection_path, selection = _verified_payload(
        latest_audit,
        "selection_path",
        "selection_sha256",
    )
    ledger_path, ledger = _verified_payload(
        latest_audit,
        "ledger_path",
        "ledger_sha256",
    )
    v25.validate_selection(selection, mode="diagnostic_validation")

    successful = ledger.get("successful") or []
    if (
        int(ledger.get("real_workload_invocation_count", -1)) != v25.CAP
        or len(successful) != v25.CAP
        or ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("pre-third gate requires a clean five-call ledger")
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != set(range(1, v25.CAP + 1)):
        raise RuntimeError("pre-third ledger does not cover ranks 1..5")

    formal_state_path = formal_state_path.resolve()
    if not formal_state_path.is_file():
        raise RuntimeError(f"formal state is missing: {formal_state_path}")
    formal = _load_json(formal_state_path)
    if formal.get("schema_version") != "tpcc_formal_target10_state_v1":
        raise RuntimeError("pre-third formal state schema mismatch")
    if formal.get("status") != "ready":
        raise RuntimeError("pre-third formal state is not ready")
    incumbent_score = _finite(formal["incumbent_score"], "incumbent_score")

    candidate_bounds = []
    for candidate in selection["top5"]:
        rank = int(candidate["rank"])
        observation = by_rank[rank]
        protocol = observation.get("measurement_protocol") or {}
        candidate_fingerprint = (
            candidate.get("three_level_fingerprint") or {}
        ).get("id")
        observation_fingerprint = (
            observation.get("three_level_fingerprint") or {}
        ).get("id")
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or observation_fingerprint != candidate_fingerprint
            or observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
        ):
            raise RuntimeError(
                f"pre-third candidate evidence mismatch: rank={rank}"
            )

        prior_scores = [
            _finite(value, f"rank_{rank}.prior_score")
            for value in candidate.get("independent_actual_scores") or []
        ]
        if len(prior_scores) != 1:
            raise RuntimeError(
                "pre-third boundary requires exactly one sealed prior score: "
                f"rank={rank} count={len(prior_scores)}"
            )
        scores = prior_scores + [
            _finite(observation["actual_score"], f"rank_{rank}.actual_score")
        ]
        upper_bound = v25.maximum_median_after_one_observation(scores)
        candidate_bounds.append(
            {
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": candidate_fingerprint,
                "sealed_scores": scores,
                "maximum_three_observation_median": upper_bound,
                "feasible": upper_bound > incumbent_score + 1e-12,
            }
        )

    feasible_ranks = [
        row["rank"] for row in candidate_bounds if row["feasible"]
    ]
    return {
        "contract": "two_observation_prethird_median_upper_bound_v1",
        "incumbent_score": incumbent_score,
        "candidate_bounds": candidate_bounds,
        "feasible_ranks": feasible_ranks,
        "passed": bool(feasible_ranks),
        "if_false": "zero_call_reroute_to_novel_diagnostic_discovery",
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": v25.sha256(ledger_path),
        "formal_state_path": str(formal_state_path),
        "formal_state_sha256": v25.sha256(formal_state_path),
    }


def choose_next_mode_v26(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    latest = audits[-1] if audits else None
    if latest is not None and _FORMAL_STATE_PATH is not None:
        prethird = build_prethird_feasibility_gate(
            latest,
            _FORMAL_STATE_PATH,
        )
        if prethird is not None:
            if not isinstance(gate, MutableMapping):
                raise RuntimeError("quality gate is not mutable")
            gate["prethird_acceptance_feasibility_gate"] = copy.deepcopy(
                prethird
            )
            if prethird["passed"] is False:
                gate["prethird_zero_call_reroute"] = True
                gate["prethird_zero_call_reason"] = (
                    "no_candidate_can_beat_incumbent_with_one_third_"
                    "observation"
                )
                return "diagnostic"
    return _ORIGINAL_CHOOSE_NEXT_MODE(audits, gate)


def _configure_from_argv(argv: Sequence[str]) -> None:
    global _FORMAL_STATE_PATH

    if len(argv) < 2:
        raise RuntimeError("manifest argument is required")
    manifest_path = Path(argv[1]).resolve()
    manifest = _load_json(manifest_path)
    _FORMAL_STATE_PATH = Path(manifest["formal_state_path"]).resolve()


def main() -> int:
    _configure_from_argv(sys.argv)
    v25.choose_next_mode = choose_next_mode_v26
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())
