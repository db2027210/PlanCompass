#!/usr/bin/env python3
"""Run MySQL Algorithm B with the current MySQL C outer state machine.

Only the candidate-ranking hook is replaced.  This file intentionally does
not fork or copy the outer loop: it delegates directly to
``run_mysql_four_scene_algorithm_c_boundary_resume_v2`` so that Top-5 replay,
acceptance, rejection, tier downgrade, binary shrink, technical-failure
isolation, and convergence cannot silently drift between B and C.

This is a controller implementation, not a frozen formal launch release.  A
formal launch still needs a new versioned checkpoint/replay-quarantine/source
binding and all mysql_only isolation gates required by AGENTS.md.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_four_scene_algorithm_b_pg_grid_v30 as inner
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v29 as current_c


METHOD = "mysql_algorithm_b_c_outer_pg_grid_v30"
runtime = current_c.runtime
OUTER_FRAMEWORK = "mysql_algorithm_c_boundary_resume_v29_over_v2"
STATE_SCHEMA = "mysql_four_scene_algorithm_b_c_outer_state_v30"
RANKING_SCHEMA = "mysql_four_scene_algorithm_b_c_outer_rankings_v30"

_installed = False


def strategy_contract() -> dict[str, Any]:
    return {
        "algorithm": "B",
        "method": METHOD,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "outer_framework": OUTER_FRAMEWORK,
        "only_algorithmic_difference_from_mysql_c": "inner_candidate_generation",
        "inner_candidate_generation": inner.search_contract(),
        "unchanged_outer_strategies": [
            "fixed_real_top5_serial_rank_waves",
            "full_plan_qps_history_soft_demote_and_two_round_hard_prune",
            "three_tier_acceptance_with_two_failure_downgrade",
            "first_predicted_rank_that_passes_current_tier",
            "technical_only_attempt_does_not_mutate_decision_state",
            "accepted_point_shrinks_all_active_log_intervals",
            "rejection_keeps_incumbent_and_intervals",
            "one_percent_interval_plus_two_accepted_stable_rounds",
            "ten_accepted_shrink_credit_target",
            "accepted_credit_capped_at_ten_with_up_to_three_stability_confirmations",
        ],
        "algorithm_c_gradient_subset_generator_enabled": False,
        "algorithm_c_outer_plan_history_enabled": True,
        "formal_launch_release_frozen": False,
    }


def install_runtime() -> None:
    """Install the B inner hook without replacing any C outer decision code."""
    global _installed
    if _installed:
        return
    original_state_document = runtime._state_document
    original_atomic = runtime.alg.atomic

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = original_state_document(**kwargs)
        document.update({
            "schema_version": STATE_SCHEMA,
            "algorithm": "B",
            "method": METHOD,
            "algorithm_b_controller_source_path": str(Path(__file__).resolve()),
            "algorithm_b_controller_source_sha256": runtime.alg.sha(
                Path(__file__).resolve()
            ),
            "algorithm_b_inner_source_path": str(Path(inner.__file__).resolve()),
            "algorithm_b_inner_source_sha256": runtime.alg.sha(
                Path(inner.__file__).resolve()
            ),
            "algorithm_b_strategy_contract": strategy_contract(),
        })
        return document

    def atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.name == "run_state.json":
            document.update({
                "schema_version": STATE_SCHEMA,
                "algorithm": "B",
                "method": METHOD,
                "algorithm_b_strategy_contract": strategy_contract(),
            })
        elif path.name == "predicted_rankings.json":
            document.update({
                "schema_version": RANKING_SCHEMA,
                "algorithm": "B",
                "method": METHOD,
                "inner_candidate_generation": inner.search_contract(),
                "outer_framework": OUTER_FRAMEWORK,
            })
        elif path.name.endswith(".decision.json"):
            document.update({
                "algorithm": "B",
                "method": METHOD,
                "outer_decision_implementation": OUTER_FRAMEWORK,
            })
        original_atomic(path, document)

    # These are the only executable strategy substitutions.  Both belong to
    # the inner candidate producer: generation plus merging its audit pools.
    runtime._rank_scene = inner.rank_scene
    runtime.alg.candidate_rankings = inner.candidate_rankings
    runtime.alg.merge_candidate_rankings = inner.merge_candidate_rankings
    runtime._state_document = state_document
    runtime.alg.atomic = atomic
    _installed = True


def main() -> int:
    install_runtime()
    return current_c.main()


if __name__ == "__main__":
    raise SystemExit(main())
