#!/usr/bin/env python3
"""Algorithm B v31: PG B inner grid plus PG-aligned outer state machine.

The engine is intentionally separated from physical MySQL orchestration.  A
runtime adapter supplies full formal workload replays, cached valid evidence,
fallback plan expansion, and restoration.  This keeps
the algorithmic state machine testable without changing the source-hash-frozen
MySQL C controller used by existing campaigns.

No formal experiment may launch this source until a versioned MySQL adapter,
checkpoint schema, isolation contract, and source-binding manifest are frozen.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from scripts import mysql_four_scene_algorithm_b_pg_grid_v30 as inner
from scripts import run_mysql_four_scene_algorithm_c_v2 as mysql_runtime


METHOD = "mysql_algorithm_b_pg_inner_pg_outer_v31"


class SceneRuntimeAdapter(Protocol):
    """Physical services needed by one scene-local PG-style outer lane."""

    def generate_rankings(
        self, scene: str, state: Mapping[str, Any], round_index: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    def cached_valid_rows(
        self, scene: str, state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]], round_index: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    def replay_batch(
        self, scene: str, state: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]], round_index: int,
        batch_index: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    def expansion_candidates(
        self, scene: str, state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]], scope: str,
        round_index: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    def restore_clean_baseline(
        self, scene: str, state: Mapping[str, Any], reason: str,
    ) -> None: ...


def controller_contract() -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_b_pg_outer_controller_v31",
        "status": "IMPLEMENTED_NOT_FORMALLY_FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "method": METHOD,
        "inner_loop": inner.search_contract(),
        "outer_loop": policy.policy_contract(),
        "existing_mysql_c_source_modified": False,
        "physical_mysql_runtime_adapter_frozen": False,
        "formal_launch_authorized": False,
    }


def initialize_scene_state(
    *,
    scene: str,
    target: str,
    incumbent_params: Mapping[str, Any],
    ranges: Mapping[str, Any],
    active_params: Sequence[str],
    canonical_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    if scene not in mysql_runtime.SCENES or target != mysql_runtime.TARGET[scene]:
        raise RuntimeError("frozen MySQL scene/target binding differs")
    params = {str(name): float(value) for name, value in incumbent_params.items()}
    active = [str(name) for name in active_params]
    if (
        not active
        or len(active) != len(set(active))
        or any(name not in inner.MYSQL_OPTIMIZER_COST_PARAMETERS for name in active)
        or any(name not in params for name in active)
        or set(ranges) != set(active)
    ):
        raise RuntimeError("scene active parameters are not the MySQL-only set")
    return {
        "schema_version": "mysql_algorithm_b_pg_outer_scene_state_v31",
        "status": "RUNNING",
        "phase": "formal_search",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": scene,
        "target": target,
        "accepted": 0,
        "accepted_target": policy.ACCEPTED_TARGET,
        "round_index": 0,
        "max_rounds": policy.MAX_SEARCH_ROUNDS,
        "consecutive_missed_outer_rounds": 0,
        "normalized_width": 1.0,
        "incumbent_params": params,
        "ranges": copy.deepcopy(dict(ranges)),
        "initial_ranges": copy.deepcopy(dict(ranges)),
        "active_params": active,
        "canonical_baseline": copy.deepcopy(dict(canonical_baseline)),
        "incumbent_validation": copy.deepcopy(dict(canonical_baseline)),
        "accepted_candidate_archive": [],
        "previously_accepted_parameter_signatures": [],
        "hard_pruned_plan_fingerprints": [],
        "best_feasible_champion": None,
        "final_champion": None,
        "post_search_stability_confirmation": False,
        "outer_policy": policy.policy_contract(),
    }


def _candidate_ranking(row: Mapping[str, Any]) -> dict[str, Any]:
    ranking = row.get("candidate_ranking")
    if isinstance(ranking, Mapping):
        return dict(ranking)
    # Cached/fake adapters may return a ranking-shaped real row directly.
    if isinstance(row.get("optimized_params"), Mapping):
        return {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key in {
                "optimized_params", "active_subset", "rank",
                "pg_outer_admission_rank", "resource_model",
                "candidate_physical_plan_sha256", "three_level_fingerprint",
            }
        }
    raise RuntimeError("real replay lacks candidate ranking identity")


def _measurement_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "db_qps": float(row["db_qps"]),
        "p95_ms": float(row["p95_ms"]),
        "p99_ms": float(row["p99_ms"]),
        "resource_means": {
            name: float(row["resource_means"][name])
            for name in policy.RESOURCE_NAMES
        },
    }


def _binary_shrink_subset(
    ranges: Mapping[str, Any],
    params: Mapping[str, Any],
    shrink_subset: Sequence[str],
) -> dict[str, Any]:
    # Reuse the shared MySQL log-space binary shrink implementation.  Supplying
    # only the moved subset preserves every other interval byte-for-byte.
    return mysql_runtime.shrink_ranges(
        ranges,
        {str(name): float(value) for name, value in params.items()},
        list(shrink_subset),
    )


def _final_champion_key(champion: Mapping[str, Any]) -> tuple[Any, ...]:
    decision = champion["final_decision"]
    return (
        policy.TIER_RANK.get(str(decision.get("acceptance_tier") or ""), 0),
        float(decision.get("qps_gate_gain") or -math.inf),
        float(decision.get("target_drop_vs_reference") or 0.0),
        -int(champion.get("accepted_index") or 0),
    )


class AlgorithmBPGOuterController:
    def __init__(self, adapter: SceneRuntimeAdapter) -> None:
        self.adapter = adapter

    def _accept(
        self, state: dict[str, Any], evaluation: Mapping[str, Any],
    ) -> None:
        row = evaluation["row"]
        ranking = _candidate_ranking(row)
        params = {
            str(name): float(value)
            for name, value in ranking["optimized_params"].items()
        }
        shrink_subset = policy.moved_shrink_subset(
            incumbent_params=state["incumbent_params"],
            candidate_ranking=ranking,
            active_params=state["active_params"],
        )
        state["ranges"] = _binary_shrink_subset(
            state["ranges"], params, shrink_subset,
        )
        state["accepted"] += 1
        state["normalized_width"] = 0.5 ** int(state["accepted"])
        state["incumbent_params"] = params
        state["incumbent_validation"] = _measurement_summary(row)
        state["consecutive_missed_outer_rounds"] = 0
        signature = policy.parameter_signature(params)
        if signature not in state["previously_accepted_parameter_signatures"]:
            state["previously_accepted_parameter_signatures"].append(signature)
        final_decision = policy.outer_semantic_decision(
            row,
            state["canonical_baseline"],
            canonical_baseline=state["canonical_baseline"],
            target=state["target"],
        )
        champion = {
            "accepted_index": int(state["accepted"]),
            "round_index": int(state["round_index"]),
            "params": copy.deepcopy(params),
            "measurement": _measurement_summary(row),
            "decision": copy.deepcopy(evaluation["decision"]),
            "final_decision": final_decision,
            "candidate_ranking": copy.deepcopy(ranking),
            "range_shrink_parameter_subset": list(shrink_subset),
        }
        state["accepted_candidate_archive"].append(champion)
        best = state.get("best_feasible_champion")
        if best is None or _final_champion_key(champion) > _final_champion_key(best):
            state["best_feasible_champion"] = copy.deepcopy(champion)

    def _replay_candidates(
        self,
        state: dict[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        *,
        batch_kind: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(policy.replay_batches(candidates), 1):
            replayed = self.adapter.replay_batch(
                state["scene"], state, batch, int(state["round_index"]), batch_index,
            )
            for raw in replayed:
                row = copy.deepcopy(dict(raw))
                row.setdefault("batch_kind", batch_kind)
                rows.append(row)
        self.adapter.restore_clean_baseline(
            state["scene"], state,
            f"round_{int(state['round_index']):03d}_{batch_kind}",
        )
        return rows

    def _evaluate_and_accept(
        self, state: dict[str, Any], rows: Sequence[Mapping[str, Any]],
    ) -> tuple[bool, int]:
        accepted, evaluations = policy.evaluate_batch(
            rows,
            state["incumbent_validation"],
            canonical_baseline=state["canonical_baseline"],
            target=state["target"],
        )
        if accepted is not None:
            self._accept(state, accepted)
            return True, len(evaluations)
        return False, len(evaluations)

    def _expansion_attempt(
        self,
        state: dict[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        *,
        scope: str,
    ) -> tuple[bool, int, int]:
        raw = self.adapter.expansion_candidates(
            state["scene"], state, rankings, scope, int(state["round_index"]),
        )
        admitted, _audit = policy.admit_partial_topk(
            raw,
            incumbent_params=state["incumbent_params"],
            previously_accepted_signatures=set(
                state["previously_accepted_parameter_signatures"]
            ),
            hard_pruned_plan_fingerprints=set(
                state["hard_pruned_plan_fingerprints"]
            ),
        )
        if not admitted:
            return False, 0, 0
        rows = self._replay_candidates(state, admitted, batch_kind=scope)
        accepted, valid = self._evaluate_and_accept(state, rows)
        return accepted, valid, len(rows)

    def run_scene(self, initial_state: Mapping[str, Any]) -> dict[str, Any]:
        state = copy.deepcopy(dict(initial_state))
        if state.get("schema_version") != "mysql_algorithm_b_pg_outer_scene_state_v31":
            raise RuntimeError("Algorithm B v31 scene state schema differs")
        while (
            int(state["accepted"]) < policy.ACCEPTED_TARGET
            and int(state["round_index"]) < policy.MAX_SEARCH_ROUNDS
        ):
            state["round_index"] += 1
            state["phase"] = "offline_algorithm_b_grid_ranking"
            rankings = [
                copy.deepcopy(dict(row))
                for row in self.adapter.generate_rankings(
                    state["scene"], state, int(state["round_index"]),
                )
            ]
            admitted, admission = policy.admit_partial_topk(
                rankings,
                incumbent_params=state["incumbent_params"],
                previously_accepted_signatures=set(
                    state["previously_accepted_parameter_signatures"]
                ),
                hard_pruned_plan_fingerprints=set(
                    state["hard_pruned_plan_fingerprints"]
                ),
            )
            state["last_outer_admission"] = admission

            cached = self.adapter.cached_valid_rows(
                state["scene"], state, rankings, int(state["round_index"]),
            )
            accepted, cached_valid = self._evaluate_and_accept(state, cached)
            if accepted:
                continue

            physical_rows = self._replay_candidates(
                state, admitted, batch_kind="base_top5",
            ) if admitted else []
            accepted, physical_valid_count = self._evaluate_and_accept(
                state, physical_rows,
            )
            if accepted:
                continue

            # Re-decide cached valid evidence after the new batch, as PG does.
            cached_after = self.adapter.cached_valid_rows(
                state["scene"], state, rankings, int(state["round_index"]),
            )
            accepted, cached_after_valid = self._evaluate_and_accept(
                state, cached_after,
            )
            if accepted:
                continue

            decision_evidence = (
                cached_valid + physical_valid_count + cached_after_valid
            )
            attempted_rows = len(physical_rows)
            accepted, valid, attempted = self._expansion_attempt(
                state, rankings, scope="fallback_plan_change",
            )
            decision_evidence += valid
            attempted_rows += attempted
            if accepted:
                continue
            if any(
                state["ranges"][name] != state["initial_ranges"][name]
                for name in state["active_params"]
            ):
                accepted, valid, attempted = self._expansion_attempt(
                    state, rankings, scope="full_range_plan_basin_escape",
                )
                decision_evidence += valid
                attempted_rows += attempted
                if accepted:
                    continue

            if decision_evidence <= 0:
                if not rankings and attempted_rows == 0:
                    state["status"] = "CANDIDATE_SPACE_EXHAUSTED_NON_CONVERGED"
                    state["phase"] = "candidate_space_exhausted_clean"
                    return state
                state["phase"] = "technical_failure_stopped_clean"
                raise policy.TechnicalRoundFailure(
                    "round has no valid replay; accepted/incumbent/ranges unchanged"
                )
            state["consecutive_missed_outer_rounds"] += 1
            state["phase"] = "formal_search"

        if int(state["accepted"]) < policy.ACCEPTED_TARGET:
            state["status"] = "NON_CONVERGED_MAX_ROUNDS"
            state["phase"] = "nonconvergence_max_rounds_clean"
            self.adapter.restore_clean_baseline(
                state["scene"], state, "formal_max_rounds_nonconvergence",
            )
            return state
        state["final_champion"] = copy.deepcopy(
            state["best_feasible_champion"]
        )
        if state["final_champion"] is None:
            raise RuntimeError("accepted target reached without a feasible champion")
        state["status"] = "COMPLETE"
        state["phase"] = "complete"
        state["completion_reason"] = "accepted_count_reached_10"
        state["post_search_stability_confirmation"] = False
        self.adapter.restore_clean_baseline(
            state["scene"], state, "accepted_target_completion",
        )
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-contract", action="store_true")
    args = parser.parse_args()
    if not args.print_contract:
        parser.error(
            "v31 has no frozen physical MySQL adapter; only --print-contract is allowed"
        )
    print(json.dumps(controller_contract(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
