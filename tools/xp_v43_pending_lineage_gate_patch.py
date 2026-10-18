#!/usr/bin/env python3
"""XP v43 gate routing for a v42 probe lineage entering v39 pending trust.

The v42 transition audit is provenance for the first probe-only observation.
It must remain attached to the exact-point repeat, but it must not re-apply the
probe5 role contract after v39 has converted the batch to pending 3T+2P.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


CONTRACT = "xp_v43_v42_lineage_to_v39_pending_gate_routing_v1"


def install_phase_gate(phase_module: Any, pending_module: Any) -> Any:
    """Route a sealed v39 pending selection to its stage-specific gate."""

    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any],
        replayed_fingerprints: Any = (),
    ) -> dict[str, Any]:
        pending = selection.get("v39_pending_trust") or {}
        if pending.get("contract") != pending_module.PENDING_CONTRACT:
            return original(selection, replayed_fingerprints)

        gate = pending_module.pending_selection_gate(
            original,
            selection,
            replayed_fingerprints,
        )
        checks = dict(gate.get("checks") or {})
        lineage = selection.get("v42_automatic_transition_family") or {}
        checks["v42_transition_audit_retained_as_lineage_only"] = bool(
            lineage.get("contract")
            == "xp_v42_automatic_safe_frontier_transition_family_probe5_v1"
            and lineage.get("formal_state_mutation") is False
            and lineage.get("trusted_promotion_applied") is False
            and lineage.get("manual_candidate_injection") is False
            and lineage.get("resource_envelope_relaxed") is False
            and lineage.get("parameter_step_limit_relaxed") is False
            and lineage.get("formal_range_relaxed") is False
        )
        checks["stage_specific_pending_gate_selected"] = True
        gate["checks"] = checks
        gate["passed"] = all(bool(value) for value in checks.values())
        gate["v43_gate_routing"] = {
            "contract": CONTRACT,
            "source_stage": "v42_automatic_safe_frontier_transition_probe5",
            "current_stage": "v39_exact_point_pending_trust_second_diagnostic",
            "v42_role_contract_reapplied": False,
            "v42_provenance_removed": False,
            "formal_state_mutation": False,
            "manual_candidate_injection": False,
        }
        return copy.deepcopy(gate)

    phase_module._selection_gate = selection_gate
    phase_module.XP_V43_PENDING_LINEAGE_GATE_CONTRACT = CONTRACT
    return phase_module
