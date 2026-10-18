#!/usr/bin/env python3
"""Verify one GPTuner/AQETuner PostgreSQL external-baseline authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import initial_params  # noqa: E402
from shell.pg_fixed_guc_guard import canonical_sha256  # noqa: E402
from shell.run_pg_algorithm_c_scene import ACTIVE_PARAMS, DEFAULT_RANGES  # noqa: E402


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_param_signature(params: Mapping[str, Any]) -> str:
    return canonical_sha256(
        {
            name: float(format(float(params[name]), ".15g"))
            for name in ACTIVE_PARAMS
        }
    )


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def integer_field(value: Mapping[str, Any], name: str, missing: int = -1) -> int:
    """Return an integer field without treating a legitimate zero as missing."""
    raw = value.get(name)
    return missing if raw is None else int(raw)


def verify(root: Path, config_path: Path) -> Dict[str, Any]:
    root = root.resolve()
    config_path = config_path.resolve()
    authority_path = root / "authority_manifest.json"
    state_path = root / "state.json"
    restore_path = root / "clean_restore_receipt.json"
    errors: List[str] = []
    for path in (authority_path, state_path, restore_path, config_path):
        require(path.is_file(), f"missing required file: {path}", errors)
    if errors:
        return {"passed": False, "errors": errors, "root": str(root)}

    authority = load_json(authority_path)
    state = load_json(state_path)
    config = load_json(config_path)
    restore = load_json(restore_path)
    stored_authority_sha = str(authority.get("authority_sha256") or "")
    authority_payload = dict(authority)
    authority_payload.pop("authority_sha256", None)
    require(
        canonical_sha256(authority_payload) == stored_authority_sha,
        "authority semantic SHA-256 mismatch",
        errors,
    )
    require(
        authority.get("contract") == "pg_external_four_scene_baseline_authority_v2",
        "wrong authority contract",
        errors,
    )
    require(authority.get("phase") == "complete", "authority phase is not complete", errors)
    require(state.get("phase") == "complete", "state phase is not complete", errors)
    require(authority.get("scene") == "xp_io", "scene is not xp_io", errors)
    require(authority.get("target") == "io", "target is not io", errors)
    require(authority.get("method") in {"gptuner", "aqetuner"}, "unexpected method", errors)
    require(state.get("method") == authority.get("method"), "state method mismatch", errors)
    require(authority.get("parameter_names") == list(ACTIVE_PARAMS), "parameter set/order mismatch", errors)
    require(state.get("parameter_names") == list(ACTIVE_PARAMS), "state parameter set/order mismatch", errors)
    expected_ranges = {name: list(DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS}
    require(authority.get("parameter_ranges") == expected_ranges, "parameter ranges mismatch", errors)
    require(int(authority.get("candidate_budget") or 0) == 35, "candidate budget is not 35", errors)
    require(
        int(authority.get("candidate_physical_invocations") or 0) == 35,
        "candidate physical count is not 35",
        errors,
    )
    require(
        int(state.get("candidate_physical_invocations") or 0) == 35,
        "state candidate physical count is not 35",
        errors,
    )
    require(authority.get("replay_parallelism") == 2, "replay parallelism is not two", errors)
    require(authority.get("manual_mutation_after_start") is False, "manual mutation flag is not false", errors)
    require(state.get("manual_mutation_after_start") is False, "state manual mutation flag is not false", errors)
    require(state.get("inflight_wave") is None, "state has an inflight wave", errors)

    defaults = {name: float(value) for name, value in initial_params(config).items() if name in ACTIVE_PARAMS}
    baseline = authority.get("canonical_baseline") or {}
    require(set(baseline.get("params") or {}) == set(ACTIVE_PARAMS), "baseline params are incomplete", errors)
    require(
        exact_param_signature(baseline.get("params") or defaults) == exact_param_signature(defaults),
        "canonical baseline is not the default seven-cost vector",
        errors,
    )
    baseline_workload = baseline.get("workload_completeness") or {}
    require(baseline_workload.get("passed") is True, "baseline workload is incomplete", errors)
    require(baseline_workload.get("workload") == "hybench_xp", "baseline workload is not native XP", errors)
    baseline_workers = baseline.get("parallel_worker_aggregate") or {}
    require(
        baseline_workers.get("worker_count") == 4,
        "baseline does not contain two repetitions on each of two workers",
        errors,
    )
    require(
        sorted(baseline_workers.get("worker_keys") or []) == ["w1", "w1", "w2", "w2"],
        "baseline repetitions are not balanced across w1+w2",
        errors,
    )

    observations = list(state.get("observations") or [])
    require(len(observations) == 35, "observation count is not 35", errors)
    signatures = []
    trials = []
    for index, observation in enumerate(observations):
        params = observation.get("params") or {}
        require(set(params) == set(ACTIVE_PARAMS), f"observation {index} params incomplete", errors)
        if set(params) == set(ACTIVE_PARAMS):
            signature = exact_param_signature(params)
            signatures.append(signature)
            require(observation.get("param_signature") == signature, f"observation {index} signature mismatch", errors)
        trials.append(int(observation.get("trial") or 0))
    require(len(set(signatures)) == 35, "candidate parameter vectors are not unique", errors)
    require(len(set(trials)) == 35, "candidate trial IDs are not unique", errors)

    frozen = authority.get("frozen_search_champion") or {}
    validated = authority.get("validated_champion") or {}
    require(set(frozen.get("params") or {}) == set(ACTIVE_PARAMS), "frozen champion params incomplete", errors)
    require(validated.get("params") == frozen.get("params"), "validated champion differs from frozen champion", errors)
    require(validated.get("physical_valid") is True, "validated champion is not physically valid", errors)
    champion_workload = validated.get("workload_completeness") or {}
    require(champion_workload.get("passed") is True, "champion workload is incomplete", errors)
    require(champion_workload.get("workload") == "hybench_xp", "champion workload is not native XP", errors)
    champion_workers = validated.get("parallel_worker_aggregate") or {}
    require(champion_workers.get("worker_count") == 2, "champion is not a two-worker aggregate", errors)
    require(champion_workers.get("worker_keys") == ["w1", "w2"], "champion worker keys are not w1+w2", errors)

    restore_payload = dict(restore)
    stored_restore_sha = str(restore_payload.pop("receipt_sha256", ""))
    require(canonical_sha256(restore_payload) == stored_restore_sha, "clean restore semantic SHA mismatch", errors)
    require(
        authority.get("clean_restore_receipt_sha256") == stored_restore_sha,
        "authority clean restore SHA mismatch",
        errors,
    )
    require(len(restore.get("workers") or []) == 2, "clean restore does not cover two workers", errors)

    guard = authority.get("execution_domain_guard") or {}
    final_guard = authority.get("final_execution_domain_receipts") or {}
    require(
        guard.get("contract") == "pg_external_search_execution_domain_guard_v1",
        "missing execution-domain guard",
        errors,
    )
    resource_sha = str(guard.get("container_resource_semantic_sha256") or "")
    guc_sha = str(guard.get("fixed_guc_semantic_sha256") or "")
    require(len(resource_sha) == 64, "missing container resource fingerprint", errors)
    require(len(guc_sha) == 64, "missing fixed-GUC fingerprint", errors)
    require(
        final_guard.get("container_resource_semantic_sha256") == resource_sha,
        "container resources drifted",
        errors,
    )
    require(final_guard.get("fixed_guc_semantic_sha256") == guc_sha, "fixed GUCs drifted", errors)
    receipt_files = sorted((root / "execution_domain_receipts").glob("*.json"))
    require(bool(receipt_files), "execution-domain boundary receipts are missing", errors)
    for receipt_file in receipt_files:
        receipt = load_json(receipt_file)
        require(receipt.get("passed") is True, f"guard receipt failed: {receipt_file}", errors)
        if receipt_file.name.endswith("_container.json"):
            require(
                receipt.get("semantic_sha256") == resource_sha,
                f"resource fingerprint mismatch: {receipt_file}",
                errors,
            )
        else:
            require(
                receipt.get("semantic_sha256") == guc_sha,
                f"fixed-GUC fingerprint mismatch: {receipt_file}",
                errors,
            )

    physical = int(state.get("physical_workload_invocations") or 0)
    valid = int(state.get("valid_replays") or 0)
    technical = int(state.get("technical_failures") or 0)
    require(
        physical == integer_field(authority, "physical_workload_invocations"),
        "physical count mismatch",
        errors,
    )
    require(
        valid == integer_field(authority, "valid_replays"),
        "valid count mismatch",
        errors,
    )
    require(
        technical == integer_field(authority, "technical_failures"),
        "technical count mismatch",
        errors,
    )
    require(physical == valid + technical, "physical count does not equal valid plus technical", errors)

    return {
        "contract": "pg_external_baseline_authority_verification_v1",
        "root": str(root),
        "passed": not errors,
        "errors": errors,
        "method": authority.get("method"),
        "scene": authority.get("scene"),
        "authority_file_sha256": file_sha256(authority_path),
        "authority_semantic_sha256": stored_authority_sha,
        "state_file_sha256": file_sha256(state_path),
        "physical_workload_invocations": physical,
        "valid_replays": valid,
        "technical_failures": technical,
        "container_resource_semantic_sha256": resource_sha,
        "fixed_guc_semantic_sha256": guc_sha,
        "execution_domain_receipt_count": len(receipt_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root, args.config)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="", flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
