#!/usr/bin/env python3
"""XP v36: one bounded resource-safe probe5 recovery from v35 cont001."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Optional

try:
    import resume_xp_fresh0_fixed_contract_target10_v35 as v35
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v35 as v35


EXPECTED_V35_SHA256 = (
    "b4f0ec2b59df319c2f68ad5d5f5861392f797d19ec9f5cb6448beef1cee98d38"
)
EXPECTED_BUNDLE_SHA256 = {
    "xp_unattended_adapters.py": (
        "7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a"
    ),
    "xp_round10_diagnostic_bundle.py": (
        "c91fa48a7e79f9a1307b2e3eec4a240809861e08472824aba7b8cfffaf71246e"
    ),
    "xp_unattended_continuation.py": (
        "6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604"
    ),
}
FALLBACK_CONTRACT = "xp_v36_resource_safe_maximin_probe5_fallback_v1"
FALLBACK_BUDGET_SCHEMA = "xp_v36_safe_probe_fallback_budget_v1"
MAX_FALLBACK_BATCHES_PER_CAMPAIGN = 1
EXPECTED_TRANSITION_GAP_PREFIX = (
    "transition backtracking requires at least 4 safe novel non-corner "
    "fingerprints after plan-level quarantine; got "
)
EXPECTED_EMPTY_TOP5_GAP = "selection requires exactly 5 replay points; got 0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def verify_sources(bundle_root: Path) -> None:
    if sha256(Path(v35.__file__).resolve()) != EXPECTED_V35_SHA256:
        raise RuntimeError("XP v36 v35 controller SHA mismatch")
    for name, expected in EXPECTED_BUNDLE_SHA256.items():
        path = bundle_root / name
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"XP v36 bundle SHA mismatch: {path}")


def _campaign_root(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    if resolved.name != "diagnostic_bundle" or resolved.parent.parent.name != "batches":
        raise RuntimeError("XP v36 fallback output is not a campaign batch")
    return resolved.parents[2]


def _eligible_gap_set(manifest: Mapping[str, Any]) -> bool:
    gaps = [str(value) for value in manifest.get("input_gaps") or []]
    return bool(
        len(gaps) == 2
        and any(value.startswith(EXPECTED_TRANSITION_GAP_PREFIX) for value in gaps)
        and EXPECTED_EMPTY_TOP5_GAP in gaps
    )


def _fallback_budget_allows(
    output_dir: Path,
    *,
    source_manifest_sha256: str,
) -> tuple[Path, Optional[dict[str, Any]]]:
    marker_path = _campaign_root(output_dir) / "v36_safe_probe_fallback_budget.json"
    if not marker_path.exists():
        return marker_path, None
    value = json.loads(marker_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("XP v36 fallback budget is not an object")
    if (
        value.get("schema_version") != FALLBACK_BUDGET_SCHEMA
        or int(value.get("maximum_fallback_batches", -1))
        != MAX_FALLBACK_BATCHES_PER_CAMPAIGN
        or int(value.get("fallback_batches_used", -1)) != 1
        or value.get("source_manifest_sha256") != source_manifest_sha256
    ):
        raise RuntimeError("XP v36 fallback budget contract mismatch")
    if value.get("diagnostic_output_dir") != str(output_dir.resolve()):
        return marker_path, value
    return marker_path, value


def apply_resource_safe_probe5_fallback(
    bundle_module: Any,
    manifest: dict[str, Any],
    *,
    selection_path: Path,
    replay_path: Path,
    candidate_path: Path,
    accepted_incumbent_path: Path,
    output_dir: Path,
    extra_excluded_ids: Optional[set[str]] = None,
    risk_utilization_upper: Optional[Mapping[str, float]] = None,
    risk_audit: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Replace only the v35 transition-family shortfall with safe maximin5."""

    if manifest.get("status") == "ready":
        return manifest
    if not _eligible_gap_set(manifest):
        return manifest
    discovery = manifest.get("diagnostic_discovery") or {}
    counts = manifest.get("counts") or {}
    if (
        discovery.get("enabled") is not True
        or int(discovery.get("trusted_count", -1)) != 0
        or int(counts.get("trusted_candidates", -1)) != 0
        or not 0 <= int(counts.get("safe_transition_backtracking_candidates", -1)) < 4
        or int(counts.get("safe_probe_candidates", -1)) < 5
        or int(counts.get("resource_safe_probe_candidates", -1)) < 5
        or int(counts.get("novel_non_corner_fingerprints", -1)) < 5
    ):
        return manifest

    source_manifest_sha = bundle_module._digest(manifest)
    marker_path, existing_budget = _fallback_budget_allows(
        output_dir,
        source_manifest_sha256=source_manifest_sha,
    )
    if existing_budget is not None:
        if existing_budget.get("diagnostic_output_dir") != str(output_dir.resolve()):
            return manifest
        selection_file = output_dir / "selection.json"
        if (
            not selection_file.is_file()
            or existing_budget.get("selection_sha256") != sha256(selection_file)
        ):
            raise RuntimeError("XP v36 sealed fallback selection changed")
        ready = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(ready, dict) or ready.get("status") != "ready":
            raise RuntimeError("XP v36 sealed fallback manifest is invalid")
        return ready

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    incumbent = json.loads(accepted_incumbent_path.read_text(encoding="utf-8"))
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    raw_candidates = bundle_module._ranking_candidates(candidate_path)
    excluded_ids = bundle_module._replayed_ids(replay)
    excluded_ids.update(extra_excluded_ids or set())
    calibration = bundle_module.build_calibration(selection, replay)
    baseline_authority = candidate_payload.get("baseline_plan_authority") or {}
    baseline_anchor = bundle_module._install_fresh_baseline_plan_anchor(
        calibration,
        raw_candidates,
        incumbent,
        baseline_authority,
    )
    active, corners, _excluded = bundle_module._calibrated_candidates(
        raw_candidates,
        calibration,
        excluded_ids,
    )
    explicit_probes = [item for item in active if bundle_module._is_probe(item)]
    unconstrained = explicit_probes if len(explicit_probes) >= 5 else list(active)
    safe_upper, _reference = bundle_module._clean_replay_relative_safe_upper(replay)
    for resource, raw_cap in (risk_utilization_upper or {}).items():
        safe_upper[resource] = min(safe_upper[resource], float(raw_cap))
    resource_safe = [
        item for item in unconstrained if bundle_module._safe_probe(item, safe_upper)
    ]
    safe = [
        item
        for item in resource_safe
        if bundle_module._stability_risk_safe(item, risk_audit)
    ]
    transition = [
        item
        for item in safe
        if (item.get("transition_search") or {}).get("contract")
        == "baseline_transition_backtracking_compensation_v1"
    ]
    if (
        len(safe) < 5
        or len(transition) >= 4
        or len({bundle_module.fingerprint_id(item) for item in safe}) != len(safe)
        or any(bundle_module._is_corner(item) for item in safe)
    ):
        raise RuntimeError("XP v36 safe-probe fallback reconstruction mismatch")
    indices = bundle_module._maximin_probe_indices(safe, 5)
    top5 = [copy.deepcopy(safe[index]) for index in indices]
    baseline_plan = str(baseline_anchor.get("plan") or "")
    baseline_identifier = None
    protocol_id = str((risk_audit or {}).get("protocol_environment_signature_id") or "")
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        candidate["selection_role"] = "maximin_uncertain_probe"
        candidate["probe_kind"] = "maximin_uncertain_probe"
        candidate["boundary_corner"] = False
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        parameter_fingerprint = bundle_module.exact_parameter_fingerprint(
            candidate.get("candidate_params") or candidate.get("optimized_params") or {}
        )
        candidate["exact_parameter_fingerprint"] = parameter_fingerprint
        plan_level = str(
            (candidate.get("three_level_fingerprint") or {}).get("plan_level") or ""
        )
        if protocol_id:
            candidate["protocol_environment_signature_id"] = protocol_id
            candidate["local_regime_id"] = bundle_module.local_regime_id(
                plan_level,
                parameter_fingerprint,
                protocol_id,
            )
        is_baseline = plan_level == baseline_plan and baseline_identifier is None
        candidate["baseline_plan_exploitation_probe"] = is_baseline
        if is_baseline:
            baseline_identifier = bundle_module.fingerprint_id(candidate)
    selected_ids = [bundle_module.fingerprint_id(item) for item in top5]
    if len(set(selected_ids)) != 5:
        raise RuntimeError("XP v36 maximin fallback is not five-way unique")

    fixed = copy.deepcopy(manifest)
    fixed["status"] = "ready"
    fixed["input_gaps"] = []
    fixed["counts"].update(
        {
            "uncertain_probes": 5,
            "selected_top5": 5,
            "safe_probe_candidates": len(safe),
            "resource_safe_probe_candidates": len(resource_safe),
            "safe_transition_backtracking_candidates": len(transition),
        }
    )
    recovery = {
        "contract": FALLBACK_CONTRACT,
        "trigger": "safe_transition_subset_below_four",
        "source_safe_probe_count": len(safe),
        "source_safe_transition_count": len(transition),
        "selected_fingerprint_ids": selected_ids,
        "selection_algorithm": "deterministic_parameter_space_maximin_v1",
        "all_candidates_remain_uncertain_probes": True,
        "trusted_promotion_applied": False,
        "resource_envelope_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "manual_candidate_injection": False,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
        "maximum_fallback_batches_per_campaign": 1,
    }
    fixed["diagnostic_discovery"].update(
        {
            "probe_count": 5,
            "baseline_plan_exploitation_fingerprint_id": baseline_identifier,
            "transition_backtracking_candidates_preferred": False,
            "transition_shortfall_policy": FALLBACK_CONTRACT,
            "resource_safe_maximin_fallback": recovery,
        }
    )
    fixed["formal_admission"].update(
        {
            "passed": False,
            "trusted_candidate_available": False,
        }
    )
    selected = copy.deepcopy(fixed["selected_selection"])
    selected.update(
        {
            "replay_authorized": True,
            "batch_mode": "diagnostic_validation",
            "formal_admission": copy.deepcopy(fixed["formal_admission"]),
            "diagnostic_discovery": copy.deepcopy(fixed["diagnostic_discovery"]),
            "top5": top5,
            "v36_resource_safe_maximin_fallback": recovery,
        }
    )
    fixed["selected_selection"] = selected
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(output_dir / "manifest.json", fixed)
    atomic_json(output_dir / "selection.json", selected)
    atomic_json(output_dir / "config_overlay.json", fixed["optimizer_options"])
    budget = {
        "schema_version": FALLBACK_BUDGET_SCHEMA,
        "status": "consumed",
        "maximum_fallback_batches": MAX_FALLBACK_BATCHES_PER_CAMPAIGN,
        "fallback_batches_used": 1,
        "diagnostic_output_dir": str(output_dir.resolve()),
        "source_manifest_sha256": source_manifest_sha,
        "selection_sha256": sha256(output_dir / "selection.json"),
        "manifest_sha256": sha256(output_dir / "manifest.json"),
        "selected_fingerprint_ids": selected_ids,
        "workload_calls": 0,
        "formal_state_mutations": 0,
    }
    atomic_json(marker_path, budget)
    return fixed


def install_bundle_fallback(bundle_root: Path) -> Any:
    verify_sources(bundle_root)
    if str(bundle_root) not in sys.path:
        sys.path.insert(0, str(bundle_root))
    module = importlib.import_module("xp_round10_diagnostic_bundle")
    original = module.build_bundle

    def bounded_build_bundle(**kwargs: Any) -> dict[str, Any]:
        manifest = original(**kwargs)
        return apply_resource_safe_probe5_fallback(
            module,
            manifest,
            **kwargs,
        )

    module.build_bundle = bounded_build_bundle
    return module


def _bundle_arg(argv: list[str]) -> Path:
    try:
        index = argv.index("--bundle-root") + 1
        return Path(argv[index]).resolve()
    except (ValueError, IndexError) as error:
        raise RuntimeError("XP v36 requires --bundle-root") from error


def main() -> int:
    install_bundle_fallback(_bundle_arg(sys.argv[1:]))
    return v35.main()


if __name__ == "__main__":
    raise SystemExit(main())
