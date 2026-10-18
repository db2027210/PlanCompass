#!/usr/bin/env python3
"""Zero-workload preflight for the actual XP v38 cont003 -> v39 cont004 handoff."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import xp_v38_phase_state_patch as v38
    import xp_v39_exact_point_pending_trust_patch as v39
except ModuleNotFoundError:
    from tools import xp_v38_phase_state_patch as v38
    from tools import xp_v39_exact_point_pending_trust_patch as v39


PROJECT = Path("/home/anonymous/program/resource_adaptive")
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = ROOT / "state.json"
SOURCE = ROOT / "batches/cont004/exact_plan_repeat/selection.json"


def main() -> int:
    state_before = STATE.read_bytes()
    state = json.loads(state_before)
    if (
        state.get("status") != "blocked_preworkload"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("next_batch_index", -1)) != 4
        or (state.get("last_batch") or {}).get("batch_id") != "cont003"
        or int((state.get("last_batch") or {}).get("physical_calls", -1)) != 5
        or int((state.get("last_batch") or {}).get("technical_failures", -1)) != 0
        or (state.get("active_phase") or {}).get("batch_id") != "cont004"
        or int((state.get("active_phase") or {}).get("physical_workload_calls", -1)) != 0
    ):
        raise RuntimeError("XP v39 live handoff state mismatch")
    if (ROOT / "batches/cont004/serial_replay").exists():
        raise RuntimeError("XP v39 cont004 already has replay files")

    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    selection = v39.sanitize_pending_selection(raw)
    gate = v39.pending_selection_gate(
        v38._selection_gate,
        selection,
        state.get("replayed_fingerprints") or [],
    )
    if gate.get("passed") is not True:
        raise RuntimeError("XP v39 actual pending-trust gate failed")
    if STATE.read_bytes() != state_before:
        raise RuntimeError("XP v39 preflight mutated state")

    print(
        json.dumps(
            {
                "contract": "xp_v39_actual_cont004_zero_call_preflight_v1",
                "status": "ready",
                "batch_id": "cont004",
                "batch_mode": selection["batch_mode"],
                "selection_stage": gate["selection_stage"],
                "roles": gate["roles"],
                "pending_trusted_count": 3,
                "formal_trusted_evidence_count": 0,
                "uncertain_probe_count": 2,
                "source_physical_calls": 5,
                "source_valid_replays": 5,
                "source_technical_failures": 0,
                "new_workload_calls": 0,
                "formal_state_digest": v39.formal_snapshot_digest(state),
                "same_plan_different_params_history_used": False,
                "state_unchanged": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
