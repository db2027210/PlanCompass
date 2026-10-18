#!/usr/bin/env python3
"""Publish immutable JOB/TPC-H debug successors for strict elapsed sampling.

This is an offline, fail-closed builder.  It never launches a workload.  Each
successor copies its predecessor source closure, replaces the continuous JOB
sampler and its tests, binds the shared formal10 contract, and retains the
predecessor's sole resource and storage authorities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
BUILDER = Path(__file__).resolve()
SAMPLER = ROOT / "shell/formal_job_continuous_session_sampling_v2.py"
SAMPLER_TEST = ROOT / "tests/test_formal_job_continuous_session_sampling_v2.py"
FORMAL10_CONTRACT = (
    ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
)
EXPECTED_CONTRACT_SHA256 = (
    "2f3d7775e005a31dc5b950277ec5153c2d4b76b67ca7bb42a4f52b5026ae5641"
)


SCENARIOS: dict[str, dict[str, Any]] = {
    "job": {
        "predecessor_id":
            "d812b811e0fdbed0c8bbb966b907134946ade1ce63600eb46619db8b23cad454",
        "predecessor_root": ROOT / "artifacts/mysql_job_v29_debug",
        "successor_root": ROOT / "artifacts/mysql_job_v30_debug",
        "source_root": ROOT / (
            "history/mysql_gradient_debug/"
            "readiness_mysql_job_sampling_v2_successor"
        ),
        "output_root": ROOT / (
            "history/mysql_gradient_debug/"
            "job_v30_original_gradient_fresh_r0_debug_20260729"
        ),
        "scenario": "job_memory",
        "bundle_schema": "mysql_job_v30_sampling_bundle_closure_v1",
        "source_schema": "mysql_job_v30_sampling_source_v1",
        "launcher": "launch_job_v29_debug.py",
        "readback": "job_v29_mysql_only_resource_readback.json",
        "parameter_count": 4,
        "candidate_count": 15,
    },
    "tpch": {
        "predecessor_id":
            "6ed4de90b5a97b439da6c0f6d2293ddfd3d0813ffb4d82ea0b2cec765591c82d",
        "predecessor_root": ROOT / "artifacts/mysql_tpch_v35_debug",
        "successor_root": ROOT / "artifacts/mysql_tpch_v36_debug",
        "source_root": ROOT / (
            "history/mysql_gradient_debug/"
            "readiness_mysql_tpch_sampling_v2_successor"
        ),
        "output_root": ROOT / (
            "history/mysql_gradient_debug/"
            "tpch_v36_original_gradient_fresh_r0_debug_20260729"
        ),
        "scenario": "tpch_memory",
        "bundle_schema": "mysql_tpch_v36_sampling_bundle_closure_v1",
        "source_schema": "mysql_tpch_v36_sampling_source_v1",
        "launcher": "launch_tpch_v35_debug.py",
        "readback": "tpch_v35_mysql_only_resource_readback.json",
        "parameter_count": 4,
        "candidate_count": 15,
    },
}


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise BuildError(f"{label} is absent or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{label} root is not an object: {path}")
    return value


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def replace_strings(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result
    if isinstance(value, list):
        return [replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_strings(item, replacements)
            for key, item in value.items()
        }
    return value


def verify_contract() -> dict[str, Any]:
    contract = load_object(FORMAL10_CONTRACT, "formal10 contract")
    if sha(FORMAL10_CONTRACT) != EXPECTED_CONTRACT_SHA256:
        raise BuildError("formal10 contract checksum differs")
    required = {
        ("schema_version",):
            "mysql_algorithm_c_formal10_promotion_contract_v1",
        ("formal_scope",): "mysql_only",
        ("cross_engine_comparison_prohibited",): True,
        ("measurement", "minimum_measurement_seconds"): 180,
        ("measurement", "maximum_measurement_seconds"): 600,
        ("measurement", "resource_sample_minimum_coverage_ratio"): 0.9,
        ("measurement", "missing_or_timed_out_sampling_action"):
            "FAIL_CLOSED",
        ("algorithm", "original_gradient_only"): True,
        ("algorithm", "candidate_score_qps_input_prohibited"): True,
        ("candidate_space", "sealed_replay_top_k"): 5,
        ("formal10", "target_accepted_rounds"): 10,
        ("publication", "content_addressed"): True,
        ("publication", "transactional"): True,
        ("publication", "idempotent"): True,
    }
    for path, expected in required.items():
        current: Any = contract
        for key in path:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current != expected:
            raise BuildError(
                "formal10 contract differs at "
                f"{'.'.join(path)}: {current!r} != {expected!r}"
            )
    return contract


def validate_predecessor(
    spec: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    predecessor = Path(spec["predecessor_root"]) / str(spec["predecessor_id"])
    manifest_path = predecessor / "bundle_manifest.json"
    manifest = load_object(manifest_path, "predecessor bundle manifest")
    if (
        manifest.get("bundle_id") != spec["predecessor_id"]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
        or not isinstance(
            manifest.get("execution_resource_authority"), Mapping
        )
        or not isinstance(
            manifest.get("execution_storage_authority"), Mapping
        )
    ):
        raise BuildError("predecessor bundle authority differs")
    for relative, expected in manifest.get("files", {}).items():
        path = predecessor / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise BuildError(
                f"predecessor bundle closure differs: {path}"
            )
    source = Path(str(manifest.get("source_directory") or ""))
    source_manifest_path = source / "source_manifest.json"
    source_manifest = load_object(
        source_manifest_path, "predecessor source manifest"
    )
    if source_manifest.get("bundle_id") != manifest.get("source_bundle_id"):
        raise BuildError("predecessor source identity differs")
    for relative, expected in source_manifest.get("files", {}).items():
        path = source / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise BuildError(
                f"predecessor source closure differs: {path}"
            )
    return predecessor, manifest, source, source_manifest


def source_test_payload() -> bytes:
    payload = SAMPLER_TEST.read_text(encoding="utf-8")
    payload = payload.replace(
        "from shell.formal_job_continuous_session_sampling_v2 import (",
        "from formal_job_continuous_session import (",
    )
    return payload.encode("utf-8")


def publish_source(
    spec: Mapping[str, Any],
    predecessor_source: Path,
    predecessor_manifest: Mapping[str, Any],
) -> tuple[Path, str]:
    files = dict(predecessor_manifest.get("files") or {})
    replacements: dict[str, bytes] = {
        "formal_job_continuous_session.py": SAMPLER.read_bytes(),
        "test_formal_job_continuous_session.py": source_test_payload(),
        "shell/build_mysql_job_tpch_sampling_successors.py":
            BUILDER.read_bytes(),
        "preflight/mysql_algorithm_c_formal10_contract_20260729.json":
            FORMAL10_CONTRACT.read_bytes(),
    }
    for relative, payload in replacements.items():
        files[relative] = sha_bytes(payload)
    identity = {
        "schema_version": spec["source_schema"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "predecessor_source_bundle_id":
            predecessor_manifest["bundle_id"],
        "supersedes_debug_bundle_id": spec["predecessor_id"],
        "sampling_protocol": {
            "schema_version": "mysql_one_second_elapsed_sampling_v2",
            "minimum_interval_seconds": 0.5,
            "maximum_interval_seconds": 1.5,
            "maximum_recoverable_delay_seconds": 3.5,
            "nominal_boundary_recovery_tolerance_seconds": 0.25,
            "coverage_dimensions": [
                "usable_sample_count",
                "usable_elapsed_seconds",
                "observed_boundary_span",
            ],
            "minimum_effective_coverage_ratio": 0.9,
            "catchup_requires_recovered_nominal_boundary": True,
            "missing_or_unpaired_gap_action": "FAIL_CLOSED",
        },
        "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
        "original_gradient_only": True,
        "enumerate_all_nonempty_subspaces": True,
        "parameter_count": spec["parameter_count"],
        "candidate_count": spec["candidate_count"],
        "fixed_top_k_real_replay": 5,
        "three_level_fingerprint_required": True,
        "files": dict(sorted(files.items())),
    }
    source_id = sha_bytes(canonical(identity))
    expected_manifest = {**identity, "bundle_id": source_id}
    source_root = Path(spec["source_root"])
    final = source_root / f"source-{source_id}"
    if final.exists():
        if load_object(final / "source_manifest.json", "source manifest") != (
            expected_manifest
        ):
            raise BuildError("existing successor source manifest differs")
        for relative, expected in files.items():
            if sha(final / relative) != expected:
                raise BuildError(
                    f"existing successor source file differs: {relative}"
                )
        return final, source_id

    source_root.mkdir(parents=True, exist_ok=True)
    stage = source_root / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative in sorted(files):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative in replacements:
                destination.write_bytes(replacements[relative])
            else:
                shutil.copyfile(predecessor_source / relative, destination)
        (stage / "source_manifest.json").write_bytes(
            canonical(expected_manifest)
        )
        run_source_tests(stage)
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, source_id


def run_source_tests(source: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source)
    completed = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "pytest",
            "-q",
            str(source / "test_formal_job_continuous_session.py"),
        ],
        cwd="/tmp",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0 or "9 passed" not in completed.stdout:
        raise BuildError(
            "successor source sampling tests failed:\n"
            + completed.stdout[-8000:]
        )


def successor_config(
    spec: Mapping[str, Any],
    predecessor: Path,
    predecessor_source: Path,
    source: Path,
) -> dict[str, Any]:
    config = load_object(predecessor / "config.json", "predecessor config")
    config = replace_strings(config, {
        str(predecessor_source): str(source),
        str(config.get("output", {}).get("directory") or ""):
            str(spec["output_root"]),
    })
    config["accepted_round_authorized"] = False
    config["outer_execution_authorized"] = True
    outer = dict(config.get("formal_outer_protocol") or {})
    outer.update({
        "max_outer_attempts": 1,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "target_accepted_shrinks": 10,
        "formal_checkpoint_path": str(
            Path(spec["output_root"]) / "formal_outer_checkpoint.json"
        ),
        "formal_real_r0_checkpoint_path": str(
            Path(spec["output_root"]) / "formal_real_r0.json"
        ),
    })
    config["formal_outer_protocol"] = outer
    config["measurement_seconds"] = 600
    config["minimum_measurement_seconds"] = 180
    config["output"] = {
        **dict(config.get("output") or {}),
        "directory": str(spec["output_root"]),
    }
    config["sampling_successor"] = {
        "schema_version": "mysql_one_second_elapsed_sampling_v2",
        "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
        "fresh_in_band_r0_required": True,
        "debug_only": True,
        "accepted_round_authorized": False,
        "minimum_effective_coverage_ratio": 0.9,
        "coverage_dimensions": [
            "usable_sample_count",
            "usable_elapsed_seconds",
            "observed_boundary_span",
        ],
        "catchup_requires_recovered_nominal_boundary": True,
        "missing_or_unpaired_gap_action": "FAIL_CLOSED",
    }
    return config


def publish_bundle(
    spec: Mapping[str, Any],
    predecessor: Path,
    predecessor_manifest: Mapping[str, Any],
    predecessor_source: Path,
    source: Path,
    source_id: str,
) -> Path:
    resource = replace_strings(
        predecessor_manifest["execution_resource_authority"],
        {str(predecessor_source): str(source)},
    )
    storage = predecessor_manifest["execution_storage_authority"]
    closure = {
        "schema_version": spec["bundle_schema"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "supersedes_bundle_ids": [spec["predecessor_id"]],
        "source_bundle_id": source_id,
        "source_manifest_sha256": sha(source / "source_manifest.json"),
        "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
        "accepted_round_authorized": False,
        "debug_outer_attempts": 1,
        "fresh_in_band_r0_required": True,
        "original_gradient_only": True,
        "enumerate_all_nonempty_subspaces": True,
        "parameter_count": spec["parameter_count"],
        "candidate_count": spec["candidate_count"],
        "fixed_top_k_real_replay": 5,
        "three_level_fingerprint_required": True,
        "sampling_protocol": {
            "schema_version": "mysql_one_second_elapsed_sampling_v2",
            "minimum_interval_seconds": 0.5,
            "maximum_interval_seconds": 1.5,
            "maximum_recoverable_delay_seconds": 3.5,
            "nominal_boundary_recovery_tolerance_seconds": 0.25,
            "minimum_effective_coverage_ratio": 0.9,
            "coverage_dimensions": [
                "usable_sample_count",
                "usable_elapsed_seconds",
                "observed_boundary_span",
            ],
            "catchup_requires_recovered_nominal_boundary": True,
            "missing_or_unpaired_gap_action": "FAIL_CLOSED",
        },
        "execution_resource_authority": resource,
        "execution_storage_authority": storage,
        "single_cpu_resource_authority": True,
        "single_local_storage_authority": True,
        "workload_start_requires_runtime_gate_ready": True,
        "pre_session_cpu_device_first_clean_sample": True,
        "publication": "staged_then_atomic_rename",
        "idempotency": "content_addressed_existing_verified",
        "builder_sha256": sha(BUILDER),
    }
    bundle_id = sha_bytes(canonical(closure))
    bundle_root = Path(spec["successor_root"])
    final = bundle_root / bundle_id
    if final.exists():
        verify_bundle(final, spec, bundle_id, source_id)
        return final

    bundle_root.mkdir(parents=True, exist_ok=True)
    stage = bundle_root / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        replacements = {
            str(predecessor): str(final),
            str(predecessor_source): str(source),
        }
        config = successor_config(
            spec, predecessor, predecessor_source, source
        )
        (stage / "config.json").write_bytes(canonical(config))

        method = replace_strings(
            load_object(
                predecessor / "experience_table_sgd_method_manifest.json",
                "predecessor method manifest",
            ),
            replacements,
        )
        runtime = dict(method.get("runtime_binding") or {})
        runtime.update({
            "config": str(final / "config.json"),
            "entrypoint": str(final / spec["launcher"]),
            "source_directory": str(source),
            "accepted_round_authorized": False,
            "debug_outer_attempts": 1,
            "sampling_protocol": closure["sampling_protocol"],
            "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
        })
        method["runtime_binding"] = runtime
        method["sampling_successor"] = closure["sampling_protocol"]
        method["formal10_contract_sha256"] = EXPECTED_CONTRACT_SHA256
        method_path = stage / "experience_table_sgd_method_manifest.json"
        method_path.write_bytes(canonical(method))

        launcher_payload = (
            predecessor / spec["launcher"]
        ).read_text(encoding="utf-8")
        launcher_payload = launcher_payload.replace(
            str(predecessor_source), str(source)
        )
        (stage / spec["launcher"]).write_text(
            launcher_payload, encoding="utf-8"
        )

        readback = replace_strings(
            load_object(
                predecessor / spec["readback"],
                "predecessor resource readback",
            ),
            replacements,
        )
        readback["sampling_successor"] = closure["sampling_protocol"]
        readback["formal10_contract_sha256"] = EXPECTED_CONTRACT_SHA256
        (stage / spec["readback"]).write_bytes(canonical(readback))

        predecessor_readiness_paths = list(
            predecessor.glob("readiness_manifest-*.json")
        )
        if len(predecessor_readiness_paths) != 1:
            raise BuildError("predecessor readiness cardinality differs")
        readiness = replace_strings(
            load_object(
                predecessor_readiness_paths[0],
                "predecessor readiness",
            ),
            replacements,
        )
        execution = dict(readiness.get("execution_authority") or {})
        execution.update({
            "bundle_id": bundle_id,
            "source_bundle_id": source_id,
            "accepted_round_authorized": False,
            "supersedes_debug_bundle_id": spec["predecessor_id"],
            "sampling_protocol": closure["sampling_protocol"],
            "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
        })
        readiness["execution_authority"] = execution
        readiness["sampling_successor"] = {
            **closure["sampling_protocol"],
            "source_bundle_id": source_id,
            "supersedes_debug_bundle_id": spec["predecessor_id"],
        }
        readiness["formal10_contract_sha256"] = EXPECTED_CONTRACT_SHA256
        method_bundle = dict(readiness.get("method_bundle") or {})
        for key, filename in (
            ("config", "config.json"),
            ("launcher", spec["launcher"]),
            ("method_manifest", "experience_table_sgd_method_manifest.json"),
        ):
            pointer = dict(method_bundle.get(key) or {})
            pointer.update({
                "path": str(final / filename),
                "sha256": sha(stage / filename),
            })
            method_bundle[key] = pointer
        readiness["method_bundle"] = method_bundle
        scenario = dict(
            (readiness.get("scenarios") or {}).get(spec["scenario"]) or {}
        )
        scenario["accepted_round_authorized"] = False
        scenarios = dict(readiness.get("scenarios") or {})
        scenarios[spec["scenario"]] = scenario
        readiness["scenarios"] = scenarios
        readiness_payload = canonical(readiness)
        readiness_name = (
            "readiness_manifest-"
            + sha_bytes(readiness_payload)
            + ".json"
        )
        (stage / readiness_name).write_bytes(readiness_payload)

        (stage / "closure.json").write_bytes(canonical(closure))
        files = {
            path.name: sha(path)
            for path in stage.iterdir()
            if path.is_file()
        }
        manifest = {
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(spec["output_root"]),
            "config_sha256": files["config.json"],
            "launcher_sha256": files[spec["launcher"]],
            "files": dict(sorted(files.items())),
        }
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        verify_bundle(stage, spec, bundle_id, source_id)
        readonly(stage)
        stage.rename(final)
        verify_bundle(final, spec, bundle_id, source_id)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final


def verify_bundle(
    bundle: Path,
    spec: Mapping[str, Any],
    bundle_id: str,
    source_id: str,
) -> None:
    manifest = load_object(bundle / "bundle_manifest.json", "bundle manifest")
    if (
        manifest.get("bundle_id") != bundle_id
        or manifest.get("schema_version") != spec["bundle_schema"]
        or manifest.get("source_bundle_id") != source_id
        or manifest.get("supersedes_bundle_ids")
        != [spec["predecessor_id"]]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("formal10_contract_sha256")
        != EXPECTED_CONTRACT_SHA256
        or manifest.get("sampling_protocol", {}).get(
            "coverage_dimensions"
        )
        != [
            "usable_sample_count",
            "usable_elapsed_seconds",
            "observed_boundary_span",
        ]
    ):
        raise BuildError("successor bundle manifest authority differs")
    if sha(bundle / "closure.json") != bundle_id:
        raise BuildError("successor closure content address differs")
    for relative, expected in manifest.get("files", {}).items():
        path = bundle / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise BuildError(f"successor bundle file differs: {relative}")
    config = load_object(bundle / "config.json", "successor config")
    if (
        config.get("accepted_round_authorized") is not False
        or config.get("formal_outer_protocol", {}).get("max_outer_attempts")
        != 1
        or config.get("formal_outer_protocol", {}).get(
            "formal_search_initial_replay_top_k"
        )
        != 5
        or config.get("formal_outer_protocol", {}).get(
            "formal_search_max_replay_top_k"
        )
        != 5
        or config.get("measurement_seconds") != 600
        or config.get("minimum_measurement_seconds") != 180
        or config.get("execution_resource_authority")
        != manifest.get("execution_resource_authority")
        or config.get("execution_storage_authority")
        != manifest.get("execution_storage_authority")
    ):
        raise BuildError("successor config execution policy differs")
    source = Path(str(manifest.get("source_directory") or ""))
    if (
        sha(source / "formal_job_continuous_session.py") != sha(SAMPLER)
        or sha(source / "source_manifest.json")
        != manifest.get("source_manifest_sha256")
    ):
        raise BuildError("successor source sampling closure differs")


def build_one(name: str) -> Path:
    if name not in SCENARIOS:
        raise BuildError(f"unknown scenario: {name}")
    verify_contract()
    spec = SCENARIOS[name]
    predecessor, predecessor_manifest, predecessor_source, source_manifest = (
        validate_predecessor(spec)
    )
    source, source_id = publish_source(
        spec, predecessor_source, source_manifest
    )
    return publish_bundle(
        spec,
        predecessor,
        predecessor_manifest,
        predecessor_source,
        source,
        source_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=("job", "tpch", "all"),
        default="all",
    )
    args = parser.parse_args()
    names = ("job", "tpch") if args.scenario == "all" else (args.scenario,)
    result = {}
    for name in names:
        bundle = build_one(name)
        result[name] = {
            "bundle": str(bundle),
            "bundle_id": bundle.name,
            "manifest_sha256": sha(bundle / "bundle_manifest.json"),
            "status": "DEBUG_SUCCESSOR_PUBLISHED_ACCEPTED_FALSE",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
