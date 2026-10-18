#!/usr/bin/env python3
"""XP v47 gate routing from v46 safe-pool probe5 to v39 pending repeat.

The v46 audit is first-observation provenance after the exact-point repeat is
constructed.  It must not re-apply the original probe5 role schema to the
pending 3T+2P diagnostic, and a v42 transition-family audit is not applicable
to a safe-pool lineage which explicitly had zero transition candidates.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v47_v46_safe_pool_to_v39_pending_lineage_gate_v1"
V46_CONTRACT = "xp_v46_minimal_safe_pool_routing_probe5_v1"
PENDING_CONTRACT = "xp_v39_exact_point_pending_trust_second_diagnostic_v1"
EXPECTED_ROLES = [
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "trusted_calibrated_rank",
    "maximin_uncertain_probe",
    "maximin_uncertain_probe",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError(f"XP v47 missing sealed {label}: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v47 {label} is not a JSON object")
    return value


def _fingerprint(candidate: Mapping[str, Any]) -> str:
    return str((candidate.get("three_level_fingerprint") or {}).get("id") or "")


def seal_safe_pool_pending_selection(
    pending_module: Any,
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    """Sanitize and SHA-bind one v46 first-observation exact repeat."""

    selected = pending_module.sanitize_pending_selection(selection)
    pending = selected.get("v39_pending_trust") or {}
    v46_audit = selected.get("v46_safe_pool_routing") or {}
    if (
        pending.get("contract") != PENDING_CONTRACT
        or v46_audit.get("contract") != V46_CONTRACT
        or selected.get("v42_automatic_transition_family") not in (None, {})
    ):
        raise RuntimeError("XP v47 selection is not an unambiguous v46 pending lineage")
    source = pending.get("source") or {}
    source_selection_path = Path(str(source.get("source_selection") or ""))
    source_replay_path = Path(str(source.get("source_replay") or ""))
    source_selection = _load(source_selection_path, "source selection")
    source_replay = _load(source_replay_path, "source replay")
    source_top5 = list(source_selection.get("top5") or [])
    source_ids = [_fingerprint(item) for item in source_top5]
    replay_ids = [
        str(item.get("fingerprint_id") or "")
        for item in source_replay.get("successful") or []
    ]
    bindings = [
        copy.deepcopy(item.get("v39_exact_point_binding") or {})
        for item in selected.get("top5") or []
    ]
    binding_source_ids = [str(item.get("source_fingerprint_id") or "") for item in bindings]
    source_v46 = source_selection.get("v46_safe_pool_routing") or {}
    checks = {
        "source_selection_sha_bound": bool(
            source.get("source_selection_sha256") == _sha256(source_selection_path)
        ),
        "source_replay_sha_bound": bool(
            source.get("source_replay_sha256") == _sha256(source_replay_path)
        ),
        "source_clean_probe5": bool(
            len(source_top5) == 5
            and source_ids == replay_ids
            and source_ids == binding_source_ids
            and source_ids == list(v46_audit.get("selected_fingerprint_ids") or [])
            and source_ids == list(source_v46.get("selected_fingerprint_ids") or [])
            and [str(item.get("selection_role") or "") for item in source_top5]
            == ["maximin_uncertain_probe"] * 5
            and pending_module._clean_diagnostic_replay(source_replay)
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
        raise RuntimeError(f"XP v47 safe-pool lineage failed: {checks}")
    selected["v47_safe_pool_pending_lineage"] = {
        "contract": CONTRACT,
        "source_selection": str(source_selection_path),
        "source_selection_sha256": _sha256(source_selection_path),
        "source_replay": str(source_replay_path),
        "source_replay_sha256": _sha256(source_replay_path),
        "source_v46_audit_sha256": _digest(source_v46),
        "source_fingerprint_ids": source_ids,
        "repeat_fingerprint_ids": [
            _fingerprint(item) for item in selected.get("top5") or []
        ],
        "checks": checks,
        "v46_probe5_role_gate_reapplied": False,
        "v42_transition_audit_required": False,
        "formal_state_mutation": False,
        "manual_candidate_injection": False,
        "new_workload_calls": 0,
    }
    return selected


def safe_pool_pending_gate(
    pending_module: Any,
    selection: Mapping[str, Any],
    replayed_fingerprints: Any = (),
) -> dict[str, Any]:
    selected = seal_safe_pool_pending_selection(pending_module, selection)
    gate = pending_module.pending_selection_gate(
        lambda *_args, **_kwargs: {},
        selected,
        replayed_fingerprints,
    )
    top5 = list(selected.get("top5") or [])
    lineage = selected["v47_safe_pool_pending_lineage"]
    checks = dict(gate.get("checks") or {})
    checks.update(
        {
            "role_stage_contract": [
                str(item.get("selection_role") or "") for item in top5
            ]
            == EXPECTED_ROLES,
            "v46_safe_pool_audit_retained_as_lineage_only": all(
                bool(value) for value in lineage["checks"].values()
            ),
            "v42_transition_lineage_not_required_for_v46_source": bool(
                lineage["v42_transition_audit_required"] is False
            ),
            "three_trusted_remain_pending_nonformal": all(
                item.get("pending_trusted") is True
                and item.get("formal_trusted_evidence_eligible") is False
                and item.get("exact_plan_regime_validated") is False
                and item.get("used_for_gradient") is False
                and item.get("used_for_inner_incumbent_update") is False
                for item in top5[:3]
            ),
            "two_probes_not_upgraded": all(
                item.get("pending_trusted") is False
                and item.get("formal_trusted_evidence_eligible") is False
                and item.get("exact_plan_regime_validated") is False
                and item.get("used_for_gradient") is False
                and item.get("used_for_inner_incumbent_update") is False
                for item in top5[3:]
            ),
            "diagnostic_only_not_formal": selected.get("batch_mode")
            == "diagnostic_validation",
        }
    )
    gate.update(
        {
            "contract": CONTRACT,
            "checks": checks,
            "passed": all(bool(value) for value in checks.values()),
            "selection_stage": "v46_safe_pool_exact_point_pending_second_diagnostic",
            "v47_safe_pool_pending_lineage": copy.deepcopy(lineage),
            "formal_trusted_evidence_count": 0,
            "manual_candidate_injection": False,
            "maximum_physical_calls": 5,
        }
    )
    return gate


def install_phase_gate(phase_module: Any, pending_module: Any) -> Any:
    """Install after v46 so source-specific pending routing wins."""

    original = phase_module._selection_gate

    def selection_gate(
        selection: Mapping[str, Any],
        replayed_fingerprints: Any = (),
    ) -> dict[str, Any]:
        pending = selection.get("v39_pending_trust") or {}
        v46_audit = selection.get("v46_safe_pool_routing") or {}
        if (
            pending.get("contract") == PENDING_CONTRACT
            and v46_audit.get("contract") == V46_CONTRACT
        ):
            return safe_pool_pending_gate(
                pending_module,
                selection,
                replayed_fingerprints,
            )
        return original(selection, replayed_fingerprints)

    phase_module._selection_gate = selection_gate
    phase_module.XP_V47_SAFE_POOL_PENDING_LINEAGE_CONTRACT = CONTRACT
    return phase_module
