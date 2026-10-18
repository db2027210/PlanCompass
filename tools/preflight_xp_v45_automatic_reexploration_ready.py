#!/usr/bin/env python3
"""Validate the recovered XP v45 state before controller launch."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
for path in (TOOLS, BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import recover_xp_v45_blocked_preworkload_zero_call as recovery
import xp_unattended_adapters as adapter
import xp_v37_robust_state_patch as robust
import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v44_pending_post_replay_patch as v44
import xp_v45_recovery_compat_patch as v45


EXPECTED_STATE_SHA256 = "6682932359b7e5c480d7de14f72cf9b99f1f67d61fbd81cefa729da952de6086"


def main() -> int:
    state_path = CAMPAIGN / "state.json"
    if recovery.sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v45 recovered state SHA mismatch")
    state = recovery.load(state_path)
    phase_state = state.get("active_phase") or {}
    audit = state.get("v45_zero_call_recovery") or {}
    if not (
        state.get("status") == "automatic_reexploration"
        and state.get("last_error") is None
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("next_batch_index", -1)) == 8
        and phase_state.get("batch_id") == "cont008"
        and phase_state.get("stage") == "offline_reexploration"
        and "terminal_status" not in phase_state
        and int(phase_state.get("physical_workload_calls", -1)) == 0
        and audit.get("contract") == recovery.CONTRACT
        and audit.get("actual_best_top3_gate_bypassed") is False
        and audit.get("threshold_relaxation") is False
        and audit.get("manual_candidate_injection") is False
        and int(audit.get("physical_workload_calls_added", -1)) == 0
        and audit.get("formal_state_mutation") is False
    ):
        raise RuntimeError("XP v45 recovered state contract mismatch")
    if (CAMPAIGN / "batches/cont008/selection.json").exists():
        raise RuntimeError("XP v45 cont008 selection already exists")
    if (CAMPAIGN / "batches/cont008/replay_summary.json").exists():
        raise RuntimeError("XP v45 cont008 replay already exists")

    module = importlib.reload(adapter)
    robust.install(module)
    phase.install_adapter(module)
    pending.install_adapter(module)
    v44.install_adapter(module, pending)
    v45.install_adapter(module)
    gate = module._diagnostic_entry_gate(
        CAMPAIGN / "batches/cont007/selection.json",
        CAMPAIGN / "batches/cont007/replay_summary.json",
    )
    if not (
        gate.get("passed") is False
        and gate.get("failed_checks") == ["actual_best_in_predicted_top3"]
        and int(gate.get("actual_best_predicted_rank", -1)) == 4
        and (gate.get("checks") or {}).get("trusted_spearman_positive") is True
        and (gate.get("checks") or {}).get("trusted_only_spearman_positive") is True
        and (gate.get("v45_schema_compatibility") or {}).get(
            "gate_passed_changed"
        )
        is False
    ):
        raise RuntimeError("XP v45 strict post-replay gate changed")

    result = {
        "status": "ready_for_automatic_explain_reexploration",
        "contract": recovery.CONTRACT,
        "state_sha256": EXPECTED_STATE_SHA256,
        "accepted_count": 0,
        "actual_best_predicted_rank": 4,
        "actual_best_top3_gate_bypassed": False,
        "trusted_spearman_positive_schema_alias": True,
        "physical_workload_calls_added": 0,
        "formal_state_mutation": False,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
