#!/usr/bin/env python3
"""XP v49: bind a clean v48 probe5 selection to the fresh score epoch.

The v48 safe-pool builder intentionally reused an already sealed diagnostic
selection.  The replay summary was stamped with the v33 fresh-score epoch,
but the selection's three top-level epoch fields were absent.  The existing
normalizer therefore stopped before it could build the exact-point repeat.

This patch only supplies those missing top-level labels after verifying a
clean, state-neutral five-probe replay.  Candidate values, predictions,
roles, fingerprints and observation histories are not changed here; the
sealed v33 normalizer still validates every observation source afterwards.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


CONTRACT = "xp_v49_v48_probe5_fresh_selection_metadata_bridge_v1"
V48_SELECTION_CONTRACT = (
    "xp_round10_calibrated_top3_plus_two_safe_frontier_maximin_probes_v2"
)
FRESH_SCORE_REGIME = "xp_v33_fresh0_band_target_v1"
EXPECTED_CPUSET = "32-51,288-307"
EXPECTED_ROLE = "maximin_uncertain_probe"


def _clean_v48_probe5(
    selection: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> bool:
    top5 = list(selection.get("top5") or [])
    ledger = list(replay.get("ledger") or [])
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    return bool(
        selection.get("selection_contract") == V48_SELECTION_CONTRACT
        and selection.get("batch_mode") == "diagnostic_validation"
        and selection.get("replay_authorized") is True
        and len(top5) == 5
        and len(ledger) == 5
        and len(set(identifiers)) == 5
        and all(value.startswith("three-level-sha256:") for value in identifiers)
        and all(item.get("selection_role") == EXPECTED_ROLE for item in top5)
        and all(item.get("trusted") is not True for item in top5)
        and replay.get("selection_contract") == V48_SELECTION_CONTRACT
        and replay.get("batch_mode") == "diagnostic_validation"
        and replay.get("diagnostic_only") is True
        and replay.get("cpuset_cpus") == EXPECTED_CPUSET
        and int(replay.get("physical_workload_invocation_count", -1)) == 5
        and int(replay.get("successful_real_replay_count", -1)) == 5
        and int(replay.get("technical_failure_count", -1)) == 0
        and replay.get("technical_failures") == []
        and replay.get("baseline_restore_verified") is True
        and int(replay.get("accepted_count_delta", -1)) == 0
        and replay.get("accepted_update_applied") is False
        and replay.get("gradient_update_applied") is False
        and replay.get("incumbent_updated") is False
        and replay.get("range_updated") is False
        and replay.get("qps_used_for_inner_scoring") is False
        and replay.get("score_regime") == FRESH_SCORE_REGIME
        and replay.get("score_admission_eligible") is True
        and replay.get("historical_scores_used_for_fresh_admission") is False
        and all(item.get("technical_failure") is False for item in ledger)
        and all(item.get("baseline_restore_verified") is True for item in ledger)
        and all(item.get("accepted_update_applied") is False for item in ledger)
        and all(item.get("gradient_update_applied") is False for item in ledger)
        and all(item.get("incumbent_update_applied") is False for item in ledger)
        and all(item.get("range_update_applied") is False for item in ledger)
    )


def install_adapter(adapter: Any) -> Any:
    """Install the narrow fresh-epoch bridge around the sealed normalizer."""

    original = adapter._normalize_fresh_score_metadata

    def normalize_fresh_score_metadata(
        selection: Mapping[str, Any],
        replay: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        missing = {
            name
            for name in (
                "score_regime",
                "score_admission_eligible",
                "historical_scores_used_for_fresh_admission",
            )
            if selection.get(name) is None
        }
        if not missing:
            return original(selection, replay)
        if missing != {
            "score_regime",
            "score_admission_eligible",
            "historical_scores_used_for_fresh_admission",
        } or not _clean_v48_probe5(selection, replay):
            return original(selection, replay)

        bridged = copy.deepcopy(dict(selection))
        bridged.update(
            {
                "score_regime": FRESH_SCORE_REGIME,
                "score_admission_eligible": True,
                "historical_scores_used_for_fresh_admission": False,
            }
        )
        normalized, audit = original(bridged, replay)
        audit = copy.deepcopy(dict(audit))
        audit["v49_fresh_selection_metadata_bridge"] = {
            "contract": CONTRACT,
            "source_selection_contract": V48_SELECTION_CONTRACT,
            "added_top_level_fields": {
                "score_regime": FRESH_SCORE_REGIME,
                "score_admission_eligible": True,
                "historical_scores_used_for_fresh_admission": False,
            },
            "candidate_values_changed": False,
            "candidate_roles_changed": False,
            "candidate_fingerprints_changed": False,
            "formal_state_mutation": False,
            "workload_calls": 0,
            "manual_candidate_injection": False,
            "threshold_relaxation": False,
        }
        return normalized, audit

    adapter._normalize_fresh_score_metadata = normalize_fresh_score_metadata
    adapter.XP_V49_FRESH_SELECTION_METADATA_BRIDGE_CONTRACT = CONTRACT
    return adapter
