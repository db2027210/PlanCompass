#!/usr/bin/env python3
"""Zero-call preflight for the exact XP v43 cont007 recovery boundary."""

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
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v42_automatic_transition_family_patch as v42
import xp_v43_pending_lineage_gate_patch as v43


EXPECTED_STATE_SHA256 = "784b35282ec23b712640bd39250ae1ead10f1ef18f3c1f8d82c9ba19e2486dd4"
EXPECTED_REPEAT_SHA256 = "6150844886753676f2c3f864cfa58cb0debeab3f2cd9eb915e2312543c99422f"
EXPECTED_REPLAY_SHA256 = "9bafd829462c8b7d9fd8282f6043dddaa76c965e53abeb01aafab29c02b9b601"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v43 non-object JSON: {path}")
    return value


def main() -> int:
    state_path = CAMPAIGN / "state.json"
    repeat_path = CAMPAIGN / "batches/cont007/exact_plan_repeat/selection.json"
    replay_path = CAMPAIGN / "batches/cont006/replay_summary.json"
    if sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v43 state SHA changed; fail closed")
    if sha256(repeat_path) != EXPECTED_REPEAT_SHA256:
        raise RuntimeError("XP v43 repeat selection SHA changed; fail closed")
    if sha256(replay_path) != EXPECTED_REPLAY_SHA256:
        raise RuntimeError("XP v43 source replay SHA changed; fail closed")

    state = load(state_path)
    phase_state = state.get("active_phase") or {}
    if not (
        state.get("status") == "blocked_preworkload"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 7
        and phase_state.get("batch_id") == "cont007"
        and phase_state.get("stage") == "offline_reexploration"
        and int(phase_state.get("physical_workload_calls", -1)) == 0
        and phase_state.get("terminal_status") == "blocked_preworkload"
    ):
        raise RuntimeError("XP v43 authority state is not the exact zero-call boundary")
    if (CAMPAIGN / "batches/cont007/selection.json").exists():
        raise RuntimeError("XP v43 top-level cont007 selection already exists")
    if (CAMPAIGN / "batches/cont007/replay_summary.json").exists():
        raise RuntimeError("XP v43 cont007 replay already exists")

    raw = load(repeat_path)
    selected = pending.sanitize_pending_selection(raw)
    phase_module = importlib.reload(phase)
    pending.install_phase_gate(phase_module)
    v42.install_phase_gate(phase_module)
    failing = copy.deepcopy(phase_module._selection_gate(selected))
    if failing.get("passed") is not False:
        raise RuntimeError("XP v43 no longer reproduces the stale v42 role-stage failure")
    if (failing.get("checks") or {}).get("role_stage_contract") is not False:
        raise RuntimeError("XP v43 reproduced a different failure")
    v43.install_phase_gate(phase_module, pending)
    gate = phase_module._selection_gate(selected)
    if gate.get("passed") is not True:
        raise RuntimeError("XP v43 corrected pending-stage gate did not pass")

    result = {
        "status": "passed_zero_call_ready_to_resume",
        "contract": v43.CONTRACT,
        "state_sha256": EXPECTED_STATE_SHA256,
        "repeat_selection_sha256": EXPECTED_REPEAT_SHA256,
        "source_replay_sha256": EXPECTED_REPLAY_SHA256,
        "accepted_count": 0,
        "physical_workload_calls_added": 0,
        "explain_calls_added": 0,
        "formal_state_mutations": 0,
        "manual_candidate_injection": False,
        "corrected_gate": gate,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
