#!/usr/bin/env python3
"""Finalize JOB v5 resume after the shared parameter core moves to v5."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


OLD_PARAMETER_SHA256 = (
    "f0740d06272a69e8db84a08ad0ce39fe29dbf2992bcc3909f66116be69881095"
)
NEW_PARAMETER_SHA256 = (
    "043bdbd9877d994986c42463335fc9e41cefcc1f1eca71f9d6a87568cd793762"
)
FORMAL_CONTRACTS_SHA256 = (
    "245374b95e37a8676016ad19a80336c90e93258a5b410b536fc5325fa8be02cc"
)
FORMAL_MEASUREMENT_SHA256 = (
    "6bcffc34ab47c0f774ed6bdcf56eb5d82a3c752e16bb4dd8c509555e4b1fd592"
)
STABILITY_V5_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)
TRANSITION_PROTOCOL_SHA256 = (
    "295b32c2337b3ce11d5f166ff873f3f0f55edf135172183ae9d591116277df62"
)
TRANSITION_CONFIG_SHA256 = (
    "9595dcd6b3daf48b2cec63445fea0d8cdae478126558984aeb0a272c00c85e78"
)
TRANSITION_MANIFEST_SHA256 = (
    "d94f5bd649b56e9faa1f89e928c7e21b88b2c53ac43c6dbcb557adbbed545c84"
)
P43_AUDIT_SHA256 = (
    "7880e81317b27f1d37a493c83c0c35159b07b6feda8bb567709f8865fdee7d35"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(path: Path, expected: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"required file is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")


def write_json_new(path: Path, value: Any, *, mode: int = 0o444) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(mode)


def deploy(source: Path, destination: Path, expected: str, *, mode: int) -> None:
    require_sha(source, expected)
    temporary = destination.with_name(destination.name + ".core-v5.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"stale deployment temporary exists: {temporary}")
    shutil.copyfile(source, temporary)
    temporary.chmod(mode)
    temporary.replace(destination)
    require_sha(destination, expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--staged-parameter-optimizer", type=Path, required=True)
    args = parser.parse_args()

    lane = args.lane_root.resolve()
    attempt = lane / "history/job/outer/attempt_5_formal_tsgd_v1"
    workspace = lane / "workspace_outer_attempt4_formal_dbqps_v1_retry3"
    frozen = lane / "bundle/attempt8_frozen"
    parameter_path = workspace / "parameter_optimizer.py"
    contracts_path = workspace / "formal_contracts.py"
    measurement_path = workspace / "formal_measurement.py"
    v5_workspace_path = (
        workspace
        / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
        / "stability_gate_contract_v5.json"
    )
    v5_frozen_path = frozen / "stability_gate_contract.json"
    protocol_path = frozen / "measurement_protocol.json"
    transition_config = (
        attempt
        / "generated_config/job_tsgd_fresh_v1_v5_after_p43/memory/job.json"
    )
    transition_manifest = attempt / "job_tsgd_v5_resume_after_p43_manifest_v1.json"
    p43_audit = attempt / "p43_technical_failure_audit_v1.json"

    require_sha(args.staged_parameter_optimizer.resolve(), NEW_PARAMETER_SHA256)
    require_sha(parameter_path, OLD_PARAMETER_SHA256)
    require_sha(contracts_path, FORMAL_CONTRACTS_SHA256)
    require_sha(measurement_path, FORMAL_MEASUREMENT_SHA256)
    require_sha(v5_workspace_path, STABILITY_V5_SHA256)
    require_sha(v5_frozen_path, STABILITY_V5_SHA256)
    require_sha(protocol_path, TRANSITION_PROTOCOL_SHA256)
    require_sha(transition_config, TRANSITION_CONFIG_SHA256)
    require_sha(transition_manifest, TRANSITION_MANIFEST_SHA256)
    require_sha(p43_audit, P43_AUDIT_SHA256)
    for name, expected in {
        "replay_counter.txt": 43,
        "formal_search_replay_counter.txt": 1,
        "formal_search_accepted_counter.txt": 0,
        "formal_startup_baseline_counter.txt": 1,
    }.items():
        actual = int((attempt / name).read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"counter drift: {name}: {actual} != {expected}")
    if (attempt / "formal_points/point_0044").exists():
        raise RuntimeError("p44 already exists before final v5 core binding")

    archive_path = (
        attempt / "v5_transition_archive/parameter_optimizer_pre_core_v5_f0740d06.py"
    )
    if archive_path.exists() or archive_path.is_symlink():
        raise RuntimeError(f"refusing to overwrite archive: {archive_path}")
    shutil.copy2(parameter_path, archive_path)
    archive_path.chmod(0o444)
    require_sha(archive_path, OLD_PARAMETER_SHA256)

    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    core_sha256 = {
        "parameter_optimizer.py": NEW_PARAMETER_SHA256,
        "formal_contracts.py": FORMAL_CONTRACTS_SHA256,
        "formal_measurement.py": FORMAL_MEASUREMENT_SHA256,
    }
    protocol["runtime_core_sha256"] = copy.deepcopy(core_sha256)
    protocol.setdefault("job_tsgd_v5_revision", {})[
        "runtime_core_sha256"
    ] = copy.deepcopy(core_sha256)
    final_protocol_artifact = (
        attempt / "measurement_protocol_v17_job_target_memory_v5_core_043b.json"
    )
    write_json_new(final_protocol_artifact, protocol)
    final_protocol_sha = sha256(final_protocol_artifact)

    config = json.loads(transition_config.read_text(encoding="utf-8"))
    config["stability_gate_contract"] = {
        "path": str(v5_workspace_path),
        "sha256": STABILITY_V5_SHA256,
    }
    generation = config.setdefault("_tsgd_generation", {})
    generation.pop("campaign_rolling_contract_remains_separate", None)
    deployed = generation.setdefault("deployed_core_sha256", {})
    deployed.update(
        {
            "experience_table_subset_optimizer.py": (
                "0df42da5cccd55f38d864114285e6b10122bb09c5955db8ebffb55b05da63aa0"
            ),
            "parameter_optimizer.py": NEW_PARAMETER_SHA256,
            "plan_resource_estimator.py": (
                "d3b06a81382df0d536415680d9c442e1346e7f130c6ebb50a217ed6649fdc8c0"
            ),
            "formal_contracts.py": FORMAL_CONTRACTS_SHA256,
            "formal_measurement.py": FORMAL_MEASUREMENT_SHA256,
        }
    )
    formal_v5 = generation.setdefault("formal_measurement_v5", {})
    formal_v5.update(
        {
            "parameter_optimizer_path": str(parameter_path),
            "parameter_optimizer_sha256": NEW_PARAMETER_SHA256,
            "measurement_protocol_sha256": final_protocol_sha,
        }
    )
    generation["campaign_stability_contract_v5"] = {
        "path": str(v5_workspace_path),
        "sha256": STABILITY_V5_SHA256,
        "validated_by_parameter_optimizer_sha256": NEW_PARAMETER_SHA256,
    }
    config_output = (
        attempt
        / "generated_config/job_tsgd_fresh_v1_v5_core_043b_after_p43/memory/job.json"
    )
    write_json_new(config_output, config)
    config_sha = sha256(config_output)

    manifest_path = attempt / "job_tsgd_v5_core_043b_resume_after_p43_manifest_v2.json"
    manifest = {
        "schema_version": 2,
        "status": "sealed_ready_for_p44_with_final_v5_core_binding",
        "supersedes_transition_manifest_for_launch": str(transition_manifest),
        "transition_manifest_sha256": TRANSITION_MANIFEST_SHA256,
        "resume_config_path": str(config_output),
        "resume_config_sha256": config_sha,
        "p43_technical_failure_audit_path": str(p43_audit),
        "p43_technical_failure_audit_sha256": P43_AUDIT_SHA256,
        "final_runtime_binding": {
            "parameter_optimizer_path": str(parameter_path),
            "parameter_optimizer_sha256": NEW_PARAMETER_SHA256,
            "formal_contracts_path": str(contracts_path),
            "formal_contracts_sha256": FORMAL_CONTRACTS_SHA256,
            "formal_measurement_path": str(measurement_path),
            "formal_measurement_sha256": FORMAL_MEASUREMENT_SHA256,
            "stability_gate_contract_path": str(v5_frozen_path),
            "stability_gate_contract_sha256": STABILITY_V5_SHA256,
            "measurement_protocol_path": str(protocol_path),
            "measurement_protocol_sha256": final_protocol_sha,
        },
        "measurement_semantics": {
            "target_resource": "memory",
            "target_resource_field": "memory_utilization",
            "retained_resource_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],
            "stability_metric_fields": ["memory_utilization"],
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "window_seconds": 30,
            "rolling_window_count": 6,
            "coefficient_of_variation_max": 0.1,
            "throughput_stability_required": False,
            "candidate_profile_band_gate_enabled": False,
        },
        "resume_counters": {
            "global": 43,
            "formal_search": 1,
            "accepted": 0,
            "startup_baselines": 1,
            "valid_initial_batch": 0,
        },
        "next_replay": {"physical_ordinal": 44, "candidate_rank": 1},
        "initial_batch_barrier": {
            "required_valid_replays": 5,
            "p43_is_technical_failure_and_invalid": True,
            "single_candidate_early_accept_forbidden": True,
            "cumulative_batch_sizes": [5, 10, 16],
        },
    }
    write_json_new(manifest_path, manifest)
    manifest_sha = sha256(manifest_path)

    deploy(
        final_protocol_artifact,
        protocol_path,
        final_protocol_sha,
        mode=0o600,
    )
    deploy(
        args.staged_parameter_optimizer.resolve(),
        parameter_path,
        NEW_PARAMETER_SHA256,
        mode=0o644,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "config_path": str(config_output),
                "config_sha256": config_sha,
                "manifest_path": str(manifest_path),
                "manifest_sha256": manifest_sha,
                "measurement_protocol_sha256": final_protocol_sha,
                "parameter_optimizer_sha256": NEW_PARAMETER_SHA256,
                "next_point": 44,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
