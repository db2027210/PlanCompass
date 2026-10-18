#!/usr/bin/env python3
"""XP v45 compatibility for the v44 stage-specific gate.

The legacy generator reads ``checks.trusted_spearman_positive`` while the v44
audit intentionally exposed the clearer ``trusted_only_spearman_positive``.
Keep both names bound to the same recomputed boolean.  This is a schema alias,
not a relaxed threshold and not a changed gate result.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


CONTRACT = "xp_v45_v44_gate_schema_compatibility_v1"


def install_adapter(adapter: Any) -> Any:
    original = adapter._diagnostic_entry_gate

    def diagnostic_entry_gate(
        selection_path: Path,
        replay_path: Path,
    ) -> dict[str, Any]:
        gate = copy.deepcopy(dict(original(selection_path, replay_path)))
        if gate.get("contract") != "xp_v44_pending_second_observation_post_replay_gate_v1":
            return gate
        checks = dict(gate.get("checks") or {})
        trusted_only = checks.get("trusted_only_spearman_positive")
        if not isinstance(trusted_only, bool):
            raise RuntimeError("XP v45 v44 gate lacks trusted-only Spearman result")
        checks["trusted_spearman_positive"] = trusted_only
        gate["checks"] = checks
        gate["v45_schema_compatibility"] = {
            "contract": CONTRACT,
            "source_key": "trusted_only_spearman_positive",
            "legacy_consumer_key": "trusted_spearman_positive",
            "values_identical": True,
            "gate_passed_changed": False,
            "threshold_relaxation": False,
            "manual_candidate_injection": False,
            "new_workload_calls": 0,
        }
        return gate

    adapter._diagnostic_entry_gate = diagnostic_entry_gate
    adapter.XP_V45_GATE_SCHEMA_COMPATIBILITY_CONTRACT = CONTRACT
    return adapter
