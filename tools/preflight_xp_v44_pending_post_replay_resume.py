#!/usr/bin/env python3
"""Zero-call preflight for the XP v44 cont007-clean/cont008-blocked boundary."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
for path in (TOOLS, BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import xp_unattended_adapters as adapter
import xp_v37_robust_state_patch as robust_state
import xp_v38_phase_state_patch as phase_state
import xp_v39_exact_point_pending_trust_patch as pending_trust
import xp_v44_pending_post_replay_patch as v44


EXPECTED_STATE_SHA256 = "b85def4d2d3837f24a5ac69c102da85aac0b2bdefde754d58d26b037389eff45"
EXPECTED_SELECTION_SHA256 = "bf0d54962de658b97d80fd0a5f0373c6180902938d17efef74d1f4372ca086ca"
EXPECTED_REPLAY_SHA256 = "e9931aac7742e76a810f25607ca1bedea36394ae1c80745b43dad0cf1364defc"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v44 non-object JSON: {path}")
    return value


def main() -> int:
    state_path = CAMPAIGN / "state.json"
    selection_path = CAMPAIGN / "batches/cont007/selection.json"
    replay_path = CAMPAIGN / "batches/cont007/replay_summary.json"
    expected = {
        state_path: EXPECTED_STATE_SHA256,
        selection_path: EXPECTED_SELECTION_SHA256,
        replay_path: EXPECTED_REPLAY_SHA256,
    }
    for path, expected_sha in expected.items():
        if sha256(path) != expected_sha:
            raise RuntimeError(f"XP v44 authority SHA changed: {path}")

    state = load(state_path)
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    if not (
        state.get("status") == "blocked_preworkload"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 8
        and phase.get("batch_id") == "cont008"
        and phase.get("stage") == "offline_reexploration"
        and int(phase.get("physical_workload_calls", -1)) == 0
        and phase.get("terminal_status") == "blocked_preworkload"
        and last.get("batch_id") == "cont007"
        and last.get("mode") == "diagnostic_validation"
        and last.get("status") == "complete"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and int(last.get("accepted", -1)) == 0
    ):
        raise RuntimeError("XP v44 state is not the exact zero-call recovery boundary")
    if (CAMPAIGN / "batches/cont008/selection.json").exists():
        raise RuntimeError("XP v44 cont008 top-level selection already exists")
    if (CAMPAIGN / "batches/cont008/replay_summary.json").exists():
        raise RuntimeError("XP v44 cont008 replay already exists")

    formal_before = v44.formal_snapshot_digest(state)
    module = importlib.reload(adapter)
    robust_state.install(module)
    phase_state.install_adapter(module)
    pending_trust.install_adapter(module)
    v44.install_adapter(module, pending_trust)
    gate = module._diagnostic_entry_gate(selection_path, replay_path)
    if gate.get("contract") != v44.CONTRACT:
        raise RuntimeError("XP v44 did not select the pending post-replay gate")
    if gate.get("passed") is not False:
        raise RuntimeError("XP v44 unexpectedly authorized formal admission")
    if gate.get("failed_checks") != ["actual_best_in_predicted_top3"]:
        raise RuntimeError("XP v44 clean5 has an unexpected quality failure")
    if not (
        float(gate.get("trusted_only_spearman")) > 0.0
        and int(gate.get("actual_best_predicted_rank", -1)) == 4
        and float(gate.get("maximum_trusted_resource_factor_error")) < 10.0
        and (gate.get("checks") or {}).get("baseline_restored") is True
        and (gate.get("checks") or {}).get("diagnostic_state_neutral") is True
        and gate.get("formal_state_mutation") is False
        and int(gate.get("new_workload_calls", -1)) == 0
    ):
        raise RuntimeError("XP v44 gate metrics do not match the sealed clean5")
    if v44.formal_snapshot_digest(state) != formal_before:
        raise RuntimeError("XP v44 preflight mutated formal state")

    result = {
        "status": "passed_zero_call_ready_for_automatic_reexploration",
        "contract": v44.CONTRACT,
        "state_sha256": EXPECTED_STATE_SHA256,
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "replay_sha256": EXPECTED_REPLAY_SHA256,
        "accepted_count": 0,
        "physical_workload_calls_added": 0,
        "explain_calls_added": 0,
        "formal_state_mutations": 0,
        "manual_candidate_injection": False,
        "post_replay_gate": copy.deepcopy(gate),
        "next_action": (
            "automatic_deterministic_explain_generation_of_five_new_"
            "noncorner_fingerprints"
        ),
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
