#!/usr/bin/env python3
"""Prepare a fresh adaptive Algorithm C campaign with an audited resource model."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_adaptive_search import (  # noqa: E402
    AdaptiveSearchPolicy,
    adaptive_policy_audit,
)
from algorithm_c_budget_policy import CONTRACT  # noqa: E402
from algorithm_c_execution_role import FORMAL_ROLE  # noqa: E402
from algorithm_c_plan_portfolio import CONTRACT as PORTFOLIO_CONTRACT, PortfolioPolicy  # noqa: E402
from shell.run_pg_algorithm_c_budgeted_scene import (  # noqa: E402
    INNER_RESOURCE_ANCHOR_CONTRACT,
)


SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
CONTROLLER_CPUSETS: Mapping[str, str] = {
    "job_memory": "104-107",
    "tpch_memory": "108-111",
    "xp_cpu": "176-179",
    "xp_io": "200-215",
}
# Preserve the 40-CPU XP-IO worker allocation while avoiding physical cores
# 234-237, which are owned by an unrelated long-lived PostgreSQL container.
# The replacement cores 216-219 are on the same NUMA socket and are otherwise
# outside every worker and controller set in this four-scene campaign.
WORKER_CPUSET_OVERRIDES: Mapping[str, str] = {
    # Preserve the paper JOB workers' 20-CPU and 8:12 NUMA split while avoiding
    # physical cores 160-175, which are owned by an active external container.
    "ra_fullgrid_generic_mem_20260718_w1": (
        "48-55,136-139,144-151"
    ),
    "ra_fullgrid_generic_mem_20260718_w2": (
        "56-63,140-143,152-159"
    ),
    "ra_fullgrid_hybench_none_20260718_w1": (
        "32-39,88-95,132-133,184-189,196-197,216-219,228-233,238-241"
    ),
}
DEFAULT_C_ONLY_RELEASE_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_calibration_releases_v1"
)
DEFAULT_PAPER_ABC_MODEL_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260823_algorithm_c_training_boundary_clean_r75"
)
RESOURCE_MODEL_C_ONLY = "c_only"
RESOURCE_MODEL_PAPER_ABC = "paper_abc"
RESOURCE_MODEL_MODES: Tuple[str, ...] = (
    RESOURCE_MODEL_C_ONLY,
    RESOURCE_MODEL_PAPER_ABC,
)
PAPER_ABC_MODEL_FILENAME = "experience_table_T_abc_c_holdout_admitted.json"
PAPER_C_RESOURCE_ANCHOR_SOURCE = Path(
    "config/formal/pg_algorithm_c_paper_resource_anchors_20260826.json"
)
ALGORITHM_CORE_SOURCES: Tuple[str, ...] = (
    "algorithm_c_plan_portfolio.py",
    "shell/pg_algorithm_c_portfolio_adapter.py",
    "algorithm_c_adaptive_search.py",
    "algorithm_c_budget_policy.py",
    "algorithm_c_execution_role.py",
    "shell/run_pg_algorithm_c_budgeted_scene.py",
    "shell/run_pg_algorithm_c_scene.py",
)
FORMAL_ORCHESTRATION_SOURCES: Tuple[str, ...] = (
    "shell/launch_pg_algorithm_c_budgeted_formal.py",
    "shell/launch_pg_algorithm_c_budgeted_trial.py",
    "shell/run_pg_algorithm_c_budgeted_support.py",
    "shell/run_pg_algorithm_c_cohort_support_supervisor.py",
)
FORMAL_RUNTIME_SOURCES: Tuple[str, ...] = (
    *ALGORITHM_CORE_SOURCES,
    *FORMAL_ORCHESTRATION_SOURCES,
    "algorithm_c_calibration_release.py",
    "binary_plan_resource_tuner.py",
    "bounded_process_runner.py",
    "common_outer_loop.py",
    "gradient_descent_optimizer.py",
    "experience_table_subset_optimizer.py",
    "plan_resource_estimator.py",
    "resource_utility.py",
    "shell/algorithm_c_parameter_support.py",
    "shell/algorithm_c_training_boundary.py",
    str(PAPER_C_RESOURCE_ANCHOR_SOURCE),
)
EXTERNAL_BASELINE_TOKENS: Tuple[str, ...] = (
    "algorithm_a",
    "algorithm_b",
    "pg_algorithm_a_",
    "pg_algorithm_b_",
)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_hashes() -> Dict[str, str]:
    return {
        relative: sha256(ROOT / relative)
        for relative in FORMAL_RUNTIME_SOURCES
    }


def algorithm_design_sha256(hashes: Mapping[str, str]) -> str:
    payload = {relative: hashes[relative] for relative in ALGORITHM_CORE_SOURCES}
    return canonical_sha256(payload)


def contains_external_baseline_token(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in EXTERNAL_BASELINE_TOKENS)


def strip_external_baseline_fields(value: Any) -> Any:
    """Remove even negative A/B firewall declarations from runtime input."""
    if isinstance(value, dict):
        return {
            key: strip_external_baseline_fields(child)
            for key, child in value.items()
            if not contains_external_baseline_token(str(key))
        }
    if isinstance(value, list):
        return [
            strip_external_baseline_fields(child)
            for child in value
            if not (isinstance(child, str) and contains_external_baseline_token(child))
        ]
    return value


def string_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from string_values(child)
    elif isinstance(value, str):
        yield value


def verify_no_external_baseline_information(value: Any, label: str) -> None:
    violations = sorted({
        item for item in string_values(value) if contains_external_baseline_token(item)
    })
    if violations:
        raise RuntimeError(f"{label} contains external baseline information: {violations[:10]}")


def replace_path_prefix(value: Any, source: Path, destination: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: replace_path_prefix(child, source, destination)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [replace_path_prefix(child, source, destination) for child in value]
    if isinstance(value, str) and value.startswith(str(source)):
        return str(destination) + value[len(str(source)):]
    return value


def c_only_model_source_audit(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    fit = artifact.get("fit") if isinstance(artifact.get("fit"), Mapping) else {}
    source_audit = fit.get("source_audit") if isinstance(fit.get("source_audit"), Mapping) else {}
    source_counts = source_audit.get("source_counts")
    if not isinstance(source_counts, Mapping) or not source_counts:
        raise RuntimeError("C-only model has no auditable training sources")
    sources = [str(path) for path in source_counts]
    if any(contains_external_baseline_token(path) for path in sources):
        raise RuntimeError("C-only model imports an external baseline source")
    if any("algorithm_c" not in path.lower() for path in sources):
        raise RuntimeError("C-only model contains a non-C training source")
    return {
        "contract": "algorithm_c_only_resource_training_v1",
        "training_source_algorithm": "algorithm_c",
        "source_count": len(sources),
        "source_sha256": canonical_sha256(sorted(sources)),
        "qps_used": False,
        "external_baseline_sources_used": False,
    }


def select_latest_c_only_model(release_root: Path, scene: str) -> Tuple[Path, Dict[str, Any]]:
    candidates = sorted(
        (release_root / scene).glob("*/*/experience_table.json"),
        key=lambda path: (path.stat().st_mtime, str(path)),
        reverse=True,
    )
    errors = []
    for path in candidates:
        try:
            artifact = read_json(path)
            if (artifact.get("formal_admission") or {}).get("ready") is not True:
                raise RuntimeError("formal admission is not ready")
            audit = c_only_model_source_audit(artifact)
            verify_no_external_baseline_information(artifact, f"model:{scene}")
            payload = copy.deepcopy(artifact)
            declared = str(payload.pop("artifact_sha256", ""))
            if not declared or canonical_sha256(payload) != declared:
                raise RuntimeError("internal artifact SHA-256 mismatch")
            return path, audit
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{path}:{exc}")
    raise RuntimeError(f"no admitted C-only model for {scene}: {errors[:3]}")


def paper_abc_model_source_audit(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the frozen paper ABC resource model and its C holdout boundary."""

    if (artifact.get("formal_admission") or {}).get("ready") is not True:
        raise RuntimeError("paper ABC model formal admission is not ready")
    expected_sources = {"algorithm_a", "algorithm_b", "algorithm_c"}
    training_boundary = artifact.get("training_boundary")
    if not isinstance(training_boundary, Mapping):
        raise RuntimeError("paper ABC model has no training boundary")
    observed_sources = {
        str(value)
        for value in training_boundary.get("allowed_training_source_algorithms") or []
    }
    if observed_sources != expected_sources:
        raise RuntimeError(
            f"paper ABC model source mismatch: {sorted(observed_sources)}"
        )
    source_counts = training_boundary.get("observed_training_source_counts")
    if not isinstance(source_counts, Mapping) or set(source_counts) != expected_sources:
        raise RuntimeError("paper ABC model source counts are incomplete")
    if any(int(source_counts[source]) <= 0 for source in expected_sources):
        raise RuntimeError("paper ABC model contains an empty training source")
    forbidden_training_flags = (
        "accept_reject_labels_used_for_resource_model",
        "historical_parameter_identity_used_as_model_feature",
        "historical_plan_fingerprint_used_as_model_feature",
        "qps_present_in_resource_training_rows",
        "qps_used_as_model_feature_or_target",
        "source_algorithm_used_as_model_feature",
    )
    if any(training_boundary.get(field) is not False for field in forbidden_training_flags):
        raise RuntimeError("paper ABC model violates the resource-only training boundary")
    if artifact.get("qps_used_for_inner_scoring") is not False:
        raise RuntimeError("paper ABC model uses QPS for inner scoring")
    holdout = artifact.get("holdout")
    if not isinstance(holdout, Mapping):
        raise RuntimeError("paper ABC model has no holdout audit")
    if holdout.get("holdout_source_algorithm") != "algorithm_c":
        raise RuntimeError("paper ABC model holdout is not Algorithm C")
    if holdout.get("independent_from_fit") is not True:
        raise RuntimeError("paper ABC model holdout is not independent from fit")
    if holdout.get("qps_used_for_selection_prediction") is not False:
        raise RuntimeError("paper ABC model holdout selection uses QPS")
    firewall = artifact.get("runtime_candidate_prior_firewall")
    if not isinstance(firewall, Mapping):
        raise RuntimeError("paper ABC model has no runtime candidate firewall")
    forbidden_runtime_flags = (
        "qps_used_as_model_feature_or_target",
        "qps_used_for_candidate_ranking",
        "qps_used_for_candidate_score",
        "qps_used_for_gradient",
        "source_algorithm_used_as_model_feature",
        "training_source_accept_reject_used_as_runtime_candidates",
        "training_source_parameters_used_as_runtime_candidates",
        "training_source_plan_fingerprints_used_as_runtime_candidates",
        "training_source_rankings_used_as_runtime_candidates",
    )
    if any(firewall.get(field) is not False for field in forbidden_runtime_flags):
        raise RuntimeError("paper ABC model violates the runtime candidate firewall")
    return {
        "contract": "algorithm_c_abc_resource_training_c_holdout_audit_v1",
        "training_sources": ["algorithm_a", "algorithm_b", "algorithm_c"],
        "training_source_counts": {
            source: int(source_counts[source]) for source in sorted(expected_sources)
        },
        "holdout_source_algorithm": "algorithm_c",
        "holdout_independent_from_fit": True,
        "qps_used": False,
        "accept_reject_labels_used": False,
        "historical_candidate_parameters_used": False,
        "source_algorithm_used_as_model_feature": False,
        "external_baseline_resource_training_used": True,
        "external_baseline_runtime_inputs_used": False,
    }


def select_paper_abc_model(model_root: Path, scene: str) -> Tuple[Path, Dict[str, Any]]:
    path = model_root / scene / PAPER_ABC_MODEL_FILENAME
    if not path.is_file():
        raise RuntimeError(f"missing paper ABC model for {scene}: {path}")
    artifact = read_json(path)
    audit = paper_abc_model_source_audit(artifact)
    payload = copy.deepcopy(artifact)
    declared = str(payload.pop("artifact_sha256", ""))
    if not declared or canonical_sha256(payload) != declared:
        raise RuntimeError(f"paper ABC model internal SHA-256 mismatch: {scene}")
    return path, audit


def verify_paper_abc_config_boundary(value: Mapping[str, Any], label: str) -> None:
    """Allow only audited offline ABC model declarations in a runtime config."""

    config = copy.deepcopy(value)
    algorithm_cfg = config.get("algorithm_c")
    if not isinstance(algorithm_cfg, dict):
        raise RuntimeError(f"{label} has no Algorithm C configuration")
    audit = algorithm_cfg.pop("offline_resource_training_audit", None)
    if not isinstance(audit, Mapping):
        raise RuntimeError(f"{label} has no paper ABC training audit")
    if audit.get("contract") != "algorithm_c_abc_resource_training_c_holdout_audit_v1":
        raise RuntimeError(f"{label} has an unexpected paper ABC training contract")
    if set(audit.get("training_sources") or []) != {
        "algorithm_a", "algorithm_b", "algorithm_c"
    }:
        raise RuntimeError(f"{label} has incomplete paper ABC training sources")
    if audit.get("qps_used") is not False:
        raise RuntimeError(f"{label} paper ABC training uses QPS")
    workload = algorithm_cfg.get("workload_contract")
    if isinstance(workload, dict):
        workload.pop("training_runtime_boundary", None)
    verify_no_external_baseline_information(config, label)


def verify_paper_abc_manifest_boundary(value: Mapping[str, Any], label: str) -> None:
    """Allow audited ABC training provenance, never A/B runtime or candidate inputs."""

    manifest = copy.deepcopy(value)
    training_sources = manifest.pop("external_baseline_training_sources", None)
    if set(training_sources or []) != {"algorithm_a", "algorithm_b"}:
        raise RuntimeError(f"{label} has an unexpected external training source set")
    for scene, entry in (manifest.get("scenes") or {}).items():
        audit = entry.pop("model_training_audit", None)
        if not isinstance(audit, Mapping):
            raise RuntimeError(f"{label} has no model audit for {scene}")
        if audit.get("contract") != "algorithm_c_abc_resource_training_c_holdout_audit_v1":
            raise RuntimeError(f"{label} has an unexpected model audit for {scene}")
        if set(audit.get("training_sources") or []) != {
            "algorithm_a", "algorithm_b", "algorithm_c"
        }:
            raise RuntimeError(f"{label} has incomplete model sources for {scene}")
    verify_no_external_baseline_information(manifest, label)


def paper_c_resource_anchors() -> Dict[str, Dict[str, Any]]:
    """Load only old Algorithm C final utilization, never its QPS or parameters."""

    source = ROOT / PAPER_C_RESOURCE_ANCHOR_SOURCE
    observed_sha256 = sha256(source)
    payload = read_json(source)
    if payload.get("contract") != (
        "algorithm_c_paper_baseline_resource_anchor_evidence_v1"
    ):
        raise RuntimeError("unexpected paper Algorithm C resource anchor contract")
    if any(
        payload.get(field) is not False
        for field in (
            "qps_fields_included",
            "parameter_fields_included",
            "accept_reject_fields_included",
        )
    ):
        raise RuntimeError("paper Algorithm C resource anchor contains forbidden fields")
    anchors: Dict[str, Dict[str, Any]] = {}
    for scene, row in (payload.get("scenes") or {}).items():
        if scene not in SCENES or scene in anchors:
            raise RuntimeError(f"invalid paper Algorithm C anchor scene: {scene}")
        utilization = row.get("utilization_pct")
        if not isinstance(utilization, Mapping):
            raise RuntimeError(f"missing paper Algorithm C utilization: {scene}")
        normalized: Dict[str, float] = {}
        source_resources = row.get("resources")
        source_capacity = row.get("capacity")
        if not isinstance(source_resources, Mapping) or not isinstance(
            source_capacity, Mapping
        ):
            raise RuntimeError(f"missing paper Algorithm C baseline data: {scene}")
        for resource in ("cpu", "memory", "io"):
            value = float(utilization[resource])
            if not 0.0 < value <= 100.0:
                raise RuntimeError(
                    f"invalid paper Algorithm C utilization: {scene}:{resource}={value}"
                )
            derived = 100.0 * float(source_resources[resource]) / float(
                source_capacity[resource]
            )
            if not math.isclose(value, derived, rel_tol=1e-12, abs_tol=1e-12):
                raise RuntimeError(
                    f"paper Algorithm C baseline ratio mismatch: {scene}:{resource}"
                )
            normalized[resource] = value
        source_state = Path(str(row.get("source_state") or ""))
        source_state_sha256 = str(row.get("source_state_sha256") or "")
        if not source_state.is_file() or sha256(source_state) != source_state_sha256:
            raise RuntimeError(f"paper Algorithm C baseline source changed: {scene}")
        anchors[scene] = {
            "enabled": True,
            "contract": INNER_RESOURCE_ANCHOR_CONTRACT,
            "source_role": "paper_algorithm_c_canonical_baseline_utilization",
            "source_document": str(PAPER_C_RESOURCE_ANCHOR_SOURCE),
            "source_document_sha256": observed_sha256,
            "source_state": str(source_state),
            "source_state_sha256": source_state_sha256,
            "utilization_pct": normalized,
            "application": (
                "baseline_anchor_times_c_only_predicted_ratio_from_current_incumbent"
            ),
            "maximum_old_new_utilization_difference_pct_points": 2.0,
            "qps_used": False,
            "old_candidate_parameters_imported": False,
            "old_accept_reject_labels_imported": False,
            "outer_qps_reference": (
                "own_fresh_baseline_then_previous_accepted_point"
            ),
            "physical_validity_uses_actual_resources": True,
        }
    if set(anchors) != set(SCENES):
        raise RuntimeError(
            "paper Algorithm C anchors must cover exactly four scenes: "
            f"observed={sorted(anchors)}"
        )
    return anchors


def set_strict_increase_outer_qps(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"qps_threshold_mode", "intermediate_acceptance_threshold_mode"}:
                value[key] = "static_floor"
                changed += 1
            elif key in {"min_db_qps_gain_pct", "static_floor_pct"}:
                value[key] = 0.0
            elif key == "qps_reference_mode":
                value[key] = "last_accepted"
            else:
                changed += set_strict_increase_outer_qps(child)
    elif isinstance(value, list):
        for child in value:
            changed += set_strict_increase_outer_qps(child)
    return changed


def prepare(
    source_config_root: Path,
    destination: Path,
    algorithm_revision: str,
    c_only_release_root: Path = DEFAULT_C_ONLY_RELEASE_ROOT,
    *,
    plan_portfolio: bool = False,
    resource_model_mode: str = RESOURCE_MODEL_C_ONLY,
    paper_abc_model_root: Path = DEFAULT_PAPER_ABC_MODEL_ROOT,
    selected_scenes: Tuple[str, ...] = SCENES,
    preserve_worker_cpus: bool = False,
    startup_warmup: bool = False,
    baseline_repetitions_per_worker: int = 2,
    baseline_repeatability_mode: str = "hard_stop",
    peer_overlap_mode: str = "hard_stop",
) -> Dict[str, Any]:
    source_config_root = source_config_root.resolve()
    destination = destination.resolve()
    c_only_release_root = c_only_release_root.resolve()
    paper_abc_model_root = paper_abc_model_root.resolve()
    revision = algorithm_revision.strip()
    if not revision:
        raise RuntimeError("a non-empty algorithm revision is required")
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite existing formal campaign: {destination}")
    if resource_model_mode not in RESOURCE_MODEL_MODES:
        raise RuntimeError(f"unsupported resource model mode: {resource_model_mode}")
    if baseline_repetitions_per_worker not in {2, 3}:
        raise ValueError("baseline repetitions must be 2 or 3")
    if baseline_repeatability_mode not in {"hard_stop", "advisory"}:
        raise ValueError("baseline repeatability mode must be hard_stop or advisory")
    if peer_overlap_mode not in {"hard_stop", "record_only"}:
        raise ValueError("peer overlap mode must be hard_stop or record_only")
    if not selected_scenes or len(set(selected_scenes)) != len(selected_scenes) or set(selected_scenes) - set(SCENES):
        raise ValueError("selected scenes must be a nonempty unique subset of the supported scenes")

    hashes = source_hashes()
    design_sha = algorithm_design_sha256(hashes)
    adaptive_policy = AdaptiveSearchPolicy()
    resource_anchors = paper_c_resource_anchors()
    destination.mkdir(parents=True)
    (destination / "cohort_support").mkdir()
    (destination / "benchbase_runtime").mkdir()
    manifest: Dict[str, Any] = {
        "contract": CONTRACT,
        "campaign_role": FORMAL_ROLE,
        "algorithm_revision": revision,
        "peer_overlap_mode": peer_overlap_mode,
        "resource_model_mode": resource_model_mode,
        "inner_search_contract": PORTFOLIO_CONTRACT if plan_portfolio else "legacy_subset_search",
        "algorithm_design_sha256": design_sha,
        "created_at_epoch_s": time.time(),
        "campaign_root": str(destination),
        "source_sha256": hashes,
        "algorithm_core_sources": list(ALGORITHM_CORE_SOURCES),
        "formal_orchestration_sources": list(FORMAL_ORCHESTRATION_SOURCES),
        "adaptive_policy_audit": adaptive_policy_audit(adaptive_policy),
        "fresh_baseline_required": True,
        "historical_candidates_imported": False,
        "historical_algorithm_c_baseline_resource_anchor_imported": True,
        "historical_algorithm_c_baseline_resource_anchor_contract": (
            INNER_RESOURCE_ANCHOR_CONTRACT
        ),
        "historical_algorithm_c_baseline_resource_anchor_source": str(
            PAPER_C_RESOURCE_ANCHOR_SOURCE
        ),
        "historical_algorithm_c_baseline_resource_anchor_source_sha256": (
            hashes[str(PAPER_C_RESOURCE_ANCHOR_SOURCE)]
        ),
        "external_baseline_runtime_inputs": [],
        "external_baseline_candidate_inputs": [],
        "external_baseline_training_sources": (
            []
            if resource_model_mode == RESOURCE_MODEL_C_ONLY
            else ["algorithm_a", "algorithm_b"]
        ),
        "qps_target_used_for_inner_search": False,
        "qps_used_for_outer_interval_control": True,
        "same_two_workers_per_scene": True,
        "uninterrupted_execution_required": True,
        "publication_claim_requires_terminal_formal_audit": True,
        "final_validation_enabled": False,
        "postterminal_abc_validation_enabled": False,
        "scenes": {},
    }

    for scene in selected_scenes:
        source_scene = source_config_root / scene
        source_config_path = source_scene / "optimizer_config.json"
        if not source_config_path.is_file():
            raise RuntimeError(f"missing source Algorithm C configuration: {scene}")
        config = strip_external_baseline_fields(read_json(source_config_path))
        scene_dir = destination / scene
        scene_dir.mkdir()
        config = replace_path_prefix(config, source_scene.resolve(), scene_dir)

        if resource_model_mode == RESOURCE_MODEL_C_ONLY:
            source_model, model_audit = select_latest_c_only_model(c_only_release_root, scene)
            model = scene_dir / "experience_table_c_only.json"
            model_contract = "algorithm_c_only_resource_model_v1"
            training_runtime_boundary = {
                "contract": model_contract,
                "offline_c_resource_training_only": True,
                "qps_role": "outer_real_replay_acceptance_only",
            }
            runtime_boundary = (
                "fresh C runtime evidence, C-only resource training, and sealed "
                "paper-C canonical-baseline inner-scoring prior"
            )
        else:
            source_model, model_audit = select_paper_abc_model(
                paper_abc_model_root, scene
            )
            model = scene_dir / "experience_table_paper_abc.json"
            model_contract = "algorithm_c_new_abc_resource_model_c_holdout_admitted_v1"
            training_runtime_boundary = {
                "contract": "algorithm_c_offline_resource_training_boundary_v1",
                "offline_abc_resource_training_only": True,
                "independent_c_holdout_required": True,
                "qps_role": "outer_real_replay_acceptance_only",
                "historical_candidate_parameters_allowed": False,
            }
            runtime_boundary = (
                "fresh C runtime evidence, frozen paper ABC resource training with "
                "independent C holdout, and sealed paper-C canonical-baseline "
                "inner-scoring prior"
            )
        shutil.copy2(source_model, model)
        algorithm_cfg = config["algorithm_c"]
        # Authoritative adaptive rules replace stale inherited description fields.
        algorithm_cfg.pop("outer_round_budget_policy", None)
        algorithm_cfg["adaptive_outer_control"] = adaptive_policy_audit(adaptive_policy)
        algorithm_cfg["same_plan_different_parameters_retained"] = True
        algorithm_cfg["same_plan_retention_scope"] = "post_coverage_numeric_recovery"
        workload = algorithm_cfg.get("workload_contract")
        if isinstance(workload, dict):
            workload["training_runtime_boundary"] = training_runtime_boundary
            outer_search = workload.get("formal_outer_search")
            if isinstance(outer_search, dict):
                outer_search["convergence_contract"] = "algorithm_c_interval_control_v7"
                outer_search["rejected_batches_before_expansion"] = 1
                outer_search["rejected_batches_before_shrink"] = 2
                outer_search["final_validation_enabled"] = False
        algorithm_cfg["controller_client_cpuset"] = CONTROLLER_CPUSETS[scene]
        experiment_meta = config["binary_plan_tuning"].setdefault("experiment_meta", {})
        experiment_meta["controller_client_cpuset"] = CONTROLLER_CPUSETS[scene]
        experiment_meta["controller_cpu_reassignment_contract"] = (
            "same_socket_same_core_count_disjoint_from_active_bound_peers_v1"
        )
        algorithm_cfg.pop("historical_valid_replay_seed_path", None)
        algorithm_cfg["experience_table_path"] = str(model)
        algorithm_cfg["offline_resource_training_audit"] = model_audit
        algorithm_cfg["runtime_resource_model_policy"] = {
            "contract": model_contract,
            "experience_table_path": str(model),
            "resource_model_mode": resource_model_mode,
            "external_baseline_resource_training_allowed": (
                resource_model_mode == RESOURCE_MODEL_PAPER_ABC
            ),
            "external_baseline_runtime_inputs_allowed": False,
            "qps_used": False,
        }
        algorithm_cfg["runtime_candidate_prior_firewall"] = {
            "contract": "algorithm_c_runtime_candidate_prior_firewall_v2",
            "external_candidate_parameters_allowed": False,
            "external_candidate_rankings_allowed": False,
            "historical_accept_reject_labels_allowed": False,
            "qps_role": "outer_real_replay_acceptance_only",
            "qps_used_for_candidate_ranking": False,
            "qps_used_for_candidate_score": False,
            "qps_used_for_gradient": False,
            "qps_used_for_resource_prediction": False,
        }
        policy = {
            "contract": CONTRACT,
            "execution_role": FORMAL_ROLE,
            "algorithm_revision": revision,
            "algorithm_design_sha256": design_sha,
            "adaptive_search": asdict(adaptive_policy),
            "baseline_repeatability_gate": {
                "enabled": True,
                "mode": baseline_repeatability_mode,
                "maximum_worker_repeat_ratio": 1.10,
                "historical_qps_used": False,
            },
            "startup_warmup": {
                "contract": "algorithm_c_full_replay_startup_warmup_v1",
                "enabled": bool(startup_warmup),
                "repetitions_per_worker": 1 if startup_warmup else 0,
                "qps_used_for_baseline_or_candidate_scoring": False,
            },
            "baseline_repetition_policy": {
                "contract": "algorithm_c_fixed_predeclared_baseline_burn_in_v1",
                "repetitions_per_worker": baseline_repetitions_per_worker,
                "burn_in_repetitions": baseline_repetitions_per_worker - 2,
                "selection_based_on_observed_qps": False,
            },
            "baseline_drift_limit": 0.15,
            "final_validation_enabled": False,
            "postterminal_abc_validation_enabled": False,
            "finalist_replays": 0,
            "post_baseline_replays": 0,
            "region_plan_probes": 32,
            "normal_fixed_search_or_wall_budget": False,
            "fixed_operational_replay_or_wall_guard_enabled": False,
            "runtime_boundary": runtime_boundary,
            "inner_resource_anchor": copy.deepcopy(resource_anchors[scene]),
        }
        if resource_model_mode == RESOURCE_MODEL_PAPER_ABC:
            policy["inner_resource_anchor"]["application"] = (
                "baseline_anchor_times_paper_abc_predicted_ratio_from_current_incumbent"
            )
        algorithm_cfg["budgeted_trial"] = policy
        config["binary_plan_tuning"]["output_dir"] = str(scene_dir)
        workers = config["binary_plan_tuning"]["validation"]["docker_workers"][
            "workers"
        ]
        worker_cpuset_overrides = {}
        for worker in workers:
            container = str(worker["container"])
            replacement = None if preserve_worker_cpus else WORKER_CPUSET_OVERRIDES.get(container)
            if replacement is not None:
                worker["cpuset_cpus"] = replacement
                worker_cpuset_overrides[container] = replacement
        if plan_portfolio:
            algorithm_cfg["inner_search"] = {
                "enabled": True, "contract": PORTFOLIO_CONTRACT, "policy": asdict(PortfolioPolicy()),
            }
        else:
            algorithm_cfg.pop("inner_search", None)
        threshold_changes = set_strict_increase_outer_qps(config)
        if threshold_changes < 1:
            raise RuntimeError(f"outer QPS threshold mode is missing: {scene}")
        algorithm_cfg.setdefault("qps_noise_gate", {}).update({"enabled": False, "static_floor_pct": 0.0})
        config["binary_plan_tuning"]["binary_search"].setdefault("nested_loop", {}).setdefault(
            "outer_acceptance", {}
        ).update({"qps_threshold_mode": "static_floor", "min_db_qps_gain_pct": 0.0,
                  "qps_reference_mode": "last_accepted"})
        if resource_model_mode == RESOURCE_MODEL_C_ONLY:
            verify_no_external_baseline_information(config, f"runtime_config:{scene}")
        else:
            verify_paper_abc_config_boundary(config, f"runtime_config:{scene}")
        config_path = scene_dir / "optimizer_config.json"
        write_json(config_path, config)
        manifest["scenes"][scene] = {
            "config": str(config_path),
            "config_sha256": sha256(config_path),
            "policy": copy.deepcopy(policy),
            "controller_cpus": algorithm_cfg["controller_client_cpuset"],
            "controller_cpu_reassignment_reason": (
                "avoid_active_physical_core_overlap_at_formal_preflight"
            ),
            "worker_names": [
                worker["container"]
                for worker in workers
            ],
            "worker_cpuset_overrides": worker_cpuset_overrides,
            "model_sha256": sha256(model),
            "model_source": str(source_model),
            "resource_model_mode": resource_model_mode,
            "model_training_audit": model_audit,
            "strict_increase_outer_qps_fields_changed": threshold_changes,
            "inner_resource_anchor": copy.deepcopy(policy["inner_resource_anchor"]),
        }

    if resource_model_mode == RESOURCE_MODEL_C_ONLY:
        verify_no_external_baseline_information(manifest, "formal_manifest")
    else:
        verify_paper_abc_manifest_boundary(manifest, "formal_manifest")
    write_json(destination / "budgeted_launch_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--algorithm-revision", required=True)
    parser.add_argument("--plan-portfolio", action="store_true", help="Enable the resource-only staged C inner search")
    parser.add_argument("--startup-warmup", action="store_true", help="Run one synchronized full-workload startup warmup before measured baselines")
    parser.add_argument("--baseline-repetitions-per-worker", type=int, choices=(2, 3), default=2)
    parser.add_argument("--baseline-repeatability-mode", choices=("hard_stop", "advisory"), default="hard_stop")
    parser.add_argument("--peer-overlap-mode", choices=("hard_stop", "record_only"), default="hard_stop")
    parser.add_argument("--c-only-release-root", type=Path, default=DEFAULT_C_ONLY_RELEASE_ROOT)
    parser.add_argument(
        "--resource-model-mode",
        choices=RESOURCE_MODEL_MODES,
        default=RESOURCE_MODEL_C_ONLY,
    )
    parser.add_argument(
        "--paper-abc-model-root",
        type=Path,
        default=DEFAULT_PAPER_ABC_MODEL_ROOT,
    )
    args = parser.parse_args()
    manifest = prepare(
        args.source_config_root,
        args.output_root,
        args.algorithm_revision,
        args.c_only_release_root,
        plan_portfolio=args.plan_portfolio,
        resource_model_mode=args.resource_model_mode,
        paper_abc_model_root=args.paper_abc_model_root,
        startup_warmup=args.startup_warmup,
        baseline_repetitions_per_worker=args.baseline_repetitions_per_worker,
        baseline_repeatability_mode=args.baseline_repeatability_mode,
        peer_overlap_mode=args.peer_overlap_mode,
    )
    print(json.dumps({
        "campaign_root": manifest["campaign_root"],
        "campaign_role": manifest["campaign_role"],
        "algorithm_revision": manifest["algorithm_revision"],
        "algorithm_design_sha256": manifest["algorithm_design_sha256"],
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
