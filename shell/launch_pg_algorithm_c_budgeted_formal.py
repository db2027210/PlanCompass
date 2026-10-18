#!/usr/bin/env python3
"""Preflight and launch a sealed post-pilot Algorithm C formal campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.launch_pg_algorithm_c_budgeted_trial import (  # noqa: E402
    launch,
    preflight,
)
from shell.prepare_pg_algorithm_c_budgeted_formal import (  # noqa: E402
    FORMAL_ROLE,
    INNER_RESOURCE_ANCHOR_CONTRACT,
    PAPER_C_RESOURCE_ANCHOR_SOURCE,
    RESOURCE_MODEL_C_ONLY,
    RESOURCE_MODEL_MODES,
    RESOURCE_MODEL_PAPER_ABC,
    SCENES,
    verify_no_external_baseline_information,
    verify_paper_abc_config_boundary,
    verify_paper_abc_manifest_boundary,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formal_preflight(root: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    root = root.resolve()
    manifest_path = root / "budgeted_launch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = []
    resource_model_mode = manifest.get("resource_model_mode", RESOURCE_MODEL_C_ONLY)
    if resource_model_mode not in RESOURCE_MODEL_MODES:
        errors.append("supported_resource_model_mode_required")
    if manifest.get("campaign_role") != FORMAL_ROLE:
        errors.append("formal_campaign_role_required")
    if not str(manifest.get("algorithm_revision") or "").strip():
        errors.append("algorithm_revision_required")
    design_sha = str(manifest.get("algorithm_design_sha256") or "")
    if len(design_sha) != 64:
        errors.append("algorithm_design_sha256_required")
    if manifest.get("uninterrupted_execution_required") is not True:
        errors.append("uninterrupted_execution_contract_required")
    if (
        manifest.get("historical_algorithm_c_baseline_resource_anchor_imported")
        is not True
    ):
        errors.append("paper_c_resource_anchor_required")
    if manifest.get("historical_algorithm_c_baseline_resource_anchor_contract") != (
        INNER_RESOURCE_ANCHOR_CONTRACT
    ):
        errors.append("paper_c_resource_anchor_contract_mismatch")
    anchor_source_sha256 = (manifest.get("source_sha256") or {}).get(
        str(PAPER_C_RESOURCE_ANCHOR_SOURCE)
    )
    if (
        manifest.get("historical_algorithm_c_baseline_resource_anchor_source_sha256")
        != anchor_source_sha256
    ):
        errors.append("paper_c_resource_anchor_source_mismatch")
    try:
        if resource_model_mode == RESOURCE_MODEL_PAPER_ABC:
            verify_paper_abc_manifest_boundary(manifest, "formal_manifest")
        else:
            verify_no_external_baseline_information(manifest, "formal_manifest")
    except RuntimeError as exc:
        errors.append(str(exc))

    base_manifest, configs, receipt = preflight(root)
    errors.extend(receipt.get("errors") or [])
    if set(configs) != set(SCENES):
        errors.append("exact_four_scene_configuration_required")
    for scene, config in configs.items():
        algorithm_cfg = config.get("algorithm_c", {})
        policy = algorithm_cfg.get("budgeted_trial", {})
        scene_manifest = manifest.get("scenes", {}).get(scene, {})
        if scene_manifest.get("resource_model_mode") != resource_model_mode:
            errors.append(f"resource_model_mode_mismatch:{scene}")
        runtime_model = algorithm_cfg.get("runtime_resource_model_policy", {})
        if runtime_model.get("resource_model_mode") != resource_model_mode:
            errors.append(f"runtime_resource_model_mode_mismatch:{scene}")
        if algorithm_cfg.get("offline_resource_training_audit") != (
            scene_manifest.get("model_training_audit")
        ):
            errors.append(f"resource_model_training_audit_mismatch:{scene}")
        if policy.get("execution_role") != FORMAL_ROLE:
            errors.append(f"formal_execution_role_required:{scene}")
        if policy.get("algorithm_revision") != manifest.get("algorithm_revision"):
            errors.append(f"algorithm_revision_mismatch:{scene}")
        if policy.get("algorithm_design_sha256") != design_sha:
            errors.append(f"algorithm_design_binding_mismatch:{scene}")
        if "budget" in policy:
            errors.append(f"fixed_budget_forbidden:{scene}")
        if not isinstance(policy.get("adaptive_search"), dict):
            errors.append(f"adaptive_search_policy_required:{scene}")
        anchor = policy.get("inner_resource_anchor")
        if not isinstance(anchor, dict):
            errors.append(f"paper_c_resource_anchor_required:{scene}")
        else:
            if anchor.get("contract") != INNER_RESOURCE_ANCHOR_CONTRACT:
                errors.append(f"paper_c_resource_anchor_contract_mismatch:{scene}")
            if anchor.get("qps_used") is not False:
                errors.append(f"paper_c_resource_anchor_must_be_qps_blind:{scene}")
            if anchor.get("physical_validity_uses_actual_resources") is not True:
                errors.append(f"actual_resource_validation_required:{scene}")
            if anchor != scene_manifest.get("inner_resource_anchor"):
                errors.append(f"paper_c_resource_anchor_binding_mismatch:{scene}")
        config_path = Path(str(scene_manifest.get("config") or ""))
        if not config_path.is_file() or sha256(config_path) != (
            scene_manifest.get("config_sha256")
        ):
            errors.append(f"runtime_config_missing_or_changed:{scene}")
        model_path = Path(str(algorithm_cfg.get("experience_table_path") or ""))
        if not model_path.is_file() or sha256(model_path) != (
            manifest.get("scenes", {}).get(scene, {}).get("model_sha256")
        ):
            errors.append(f"resource_model_missing_or_changed:{scene}")
        try:
            if resource_model_mode == RESOURCE_MODEL_PAPER_ABC:
                verify_paper_abc_config_boundary(config, f"runtime_config:{scene}")
            else:
                verify_no_external_baseline_information(config, f"runtime_config:{scene}")
        except RuntimeError as exc:
            errors.append(str(exc))

    receipt.pop("trial_only_not_publication_comparison", None)
    receipt.update({
        "contract": "algorithm_c_post_pilot_formal_preflight_v1",
        "campaign_role": FORMAL_ROLE,
        "algorithm_revision": manifest.get("algorithm_revision"),
        "resource_model_mode": resource_model_mode,
        "formal_candidate_run": True,
        "external_baseline_information_used": (
            resource_model_mode == RESOURCE_MODEL_PAPER_ABC
        ),
        "external_baseline_information_role": (
            "offline_resource_training_only"
            if resource_model_mode == RESOURCE_MODEL_PAPER_ABC
            else "none"
        ),
        "external_baseline_resource_training_used": (
            resource_model_mode == RESOURCE_MODEL_PAPER_ABC
        ),
        "external_baseline_runtime_or_candidate_information_used": False,
        "external_baseline_qps_or_parameters_used": False,
        "historical_algorithm_c_baseline_resource_anchor_used": True,
        "historical_algorithm_c_qps_or_parameters_used": False,
        "normal_fixed_search_or_wall_budget": False,
        "uninterrupted_execution_required": True,
        "publication_claim_allowed_before_terminal_formal_audit": False,
        "checked_at_epoch_s": time.time(),
        "errors": sorted(set(errors)),
        "passed": not errors,
    })
    (root / "formal_launch_preflight.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return base_manifest, configs, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    root = args.campaign_root.resolve()
    manifest, configs, receipt = formal_preflight(root)
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    if not receipt["passed"]:
        return 2
    if args.launch:
        return launch(root, manifest, configs, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
