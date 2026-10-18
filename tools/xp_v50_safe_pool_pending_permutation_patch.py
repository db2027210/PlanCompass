#!/usr/bin/env python3
"""XP v50: validate safe-pool pending bindings as a sealed permutation.

The exact-point repeat ranks candidates by predicted trusted order, so its
rank order is intentionally different from the source probe5 order.  v47
incorrectly required those two ordered fingerprint lists to be identical.
This patch requires a one-to-one permutation and validates every repeat/source
binding without changing the selected rank order or any candidate value.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v50_v47_safe_pool_pending_permutation_binding_v1"


def install(v47: Any, pending_module: Any) -> Any:
    """Replace only v47's overly strict ordered-lineage seal."""

    def seal_safe_pool_pending_selection(
        pending: Any,
        selection: Mapping[str, Any],
    ) -> dict[str, Any]:
        if pending is not pending_module:
            raise RuntimeError("XP v50 pending module binding changed")
        selected = pending.sanitize_pending_selection(selection)
        pending_audit = selected.get("v39_pending_trust") or {}
        v46_audit = selected.get("v46_safe_pool_routing") or {}
        if (
            pending_audit.get("contract") != v47.PENDING_CONTRACT
            or v46_audit.get("contract") != v47.V46_CONTRACT
            or selected.get("v42_automatic_transition_family") not in (None, {})
        ):
            raise RuntimeError("XP v50 selection is not an unambiguous v46 pending lineage")

        source = pending_audit.get("source") or {}
        source_selection_path = Path(str(source.get("source_selection") or ""))
        source_replay_path = Path(str(source.get("source_replay") or ""))
        source_selection = v47._load(source_selection_path, "source selection")
        source_replay = v47._load(source_replay_path, "source replay")
        source_top5 = list(source_selection.get("top5") or [])
        source_ids = [v47._fingerprint(item) for item in source_top5]
        replay_ids = [
            str(item.get("fingerprint_id") or "")
            for item in source_replay.get("successful") or []
        ]
        repeat_top5 = list(selected.get("top5") or [])
        bindings = [
            copy.deepcopy(item.get("v39_exact_point_binding") or {})
            for item in repeat_top5
        ]
        binding_source_ids = [
            str(item.get("source_fingerprint_id") or "") for item in bindings
        ]
        repeat_ids = [v47._fingerprint(item) for item in repeat_top5]
        binding_repeat_ids = [
            str(item.get("repeat_fingerprint_id") or "") for item in bindings
        ]
        source_v46 = source_selection.get("v46_safe_pool_routing") or {}
        binding_paths_valid = all(
            Path(str(binding.get("source_selection") or ""))
            == source_selection_path
            and str(binding.get("source_selection_sha256") or "")
            == v47._sha256(source_selection_path)
            and Path(str(binding.get("source_replay") or "")) == source_replay_path
            and str(binding.get("source_replay_sha256") or "")
            == v47._sha256(source_replay_path)
            for binding in bindings
        )
        checks = {
            "source_selection_sha_bound": bool(
                source.get("source_selection_sha256")
                == v47._sha256(source_selection_path)
            ),
            "source_replay_sha_bound": bool(
                source.get("source_replay_sha256") == v47._sha256(source_replay_path)
            ),
            "source_clean_probe5": bool(
                len(source_top5) == 5
                and len(set(source_ids)) == 5
                and source_ids == replay_ids
                and source_ids == list(v46_audit.get("selected_fingerprint_ids") or [])
                and source_ids == list(source_v46.get("selected_fingerprint_ids") or [])
                and [str(item.get("selection_role") or "") for item in source_top5]
                == ["maximin_uncertain_probe"] * 5
                and pending._clean_diagnostic_replay(source_replay)
            ),
            "repeat_binding_is_exact_permutation": bool(
                len(bindings) == 5
                and len(set(binding_source_ids)) == 5
                and set(binding_source_ids) == set(source_ids)
                and repeat_ids == binding_repeat_ids
                and len(set(repeat_ids)) == 5
                and binding_paths_valid
            ),
            "v46_lineage_exactly_retained": bool(
                source_v46 == v46_audit
                and v46_audit.get("diagnostic_only") is True
                and v46_audit.get("first_probe_replay_cannot_be_formal") is True
                and v46_audit.get("pending_trust_requires_clean_exact_point_repeat") is True
                and v46_audit.get("trusted_promotion_applied") is False
                and v46_audit.get("formal_state_mutation") is False
                and v46_audit.get("manual_candidate_injection") is False
                and v46_audit.get("resource_envelope_relaxed") is False
                and v46_audit.get("parameter_step_limit_relaxed") is False
                and int(v46_audit.get("new_workload_calls", -1)) == 0
            ),
            "v42_transition_lineage_not_applicable": bool(
                int(v46_audit.get("source_safe_transition_count", -1)) == 0
                and selected.get("v42_automatic_transition_family") in (None, {})
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(f"XP v50 safe-pool lineage failed: {checks}")
        selected["v47_safe_pool_pending_lineage"] = {
            "contract": v47.CONTRACT,
            "source_selection": str(source_selection_path),
            "source_selection_sha256": v47._sha256(source_selection_path),
            "source_replay": str(source_replay_path),
            "source_replay_sha256": v47._sha256(source_replay_path),
            "source_v46_audit_sha256": v47._digest(source_v46),
            "source_fingerprint_ids": source_ids,
            "repeat_fingerprint_ids": repeat_ids,
            "checks": checks,
            "v46_probe5_role_gate_reapplied": False,
            "v42_transition_audit_required": False,
            "formal_state_mutation": False,
            "manual_candidate_injection": False,
            "new_workload_calls": 0,
            "v50_permutation_binding": {
                "contract": CONTRACT,
                "source_order_required_to_equal_repeat_order": False,
                "one_to_one_permutation_required": True,
                "candidate_order_changed": False,
                "candidate_values_changed": False,
                "threshold_relaxation": False,
            },
        }
        return selected

    v47.seal_safe_pool_pending_selection = seal_safe_pool_pending_selection
    v47.XP_V50_SAFE_POOL_PENDING_PERMUTATION_CONTRACT = CONTRACT
    return v47

