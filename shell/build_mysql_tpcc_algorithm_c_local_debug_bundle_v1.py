#!/usr/bin/env python3
"""Publish the immutable compute00 TPC-C Algorithm C one-round debug bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_v8_debug_v1/"
    "source-14bec2633be1c001e4320d2b310bd6687542d2be6aaccfb4fb5d9c12ac591c83"
)
CAPTURE_BUNDLE = (
    ROOT / "artifacts/mysql_tpcc_typed_runtime_v1/"
    "eb1b515a7371fb1c4cae26062dbf5279"
    "aba2da776959eb5c03db186454cc153c"
)
SOURCE_ROOT = (
    ROOT / "history/mysql_gradient_debug/readiness_tpcc_algorithm_c_local_v1"
)
DESTINATION = ROOT / "artifacts/mysql_tpcc_algorithm_c_local_debug_v1"
OUTPUT_ROOT = (
    ROOT / "history/mysql_gradient_debug/"
    "tpcc_algorithm_c_local_one_round_debug_v1"
)
LAUNCHER = ROOT / "shell/mysql_tpcc_algorithm_c_local_debug_launcher_v1.py"
BASE_CONFIG = (
    ROOT / "config/formal/mysql_sgd_production/"
    "tpcc_cpu.algorithm_c.optimizer.json"
)
BASE_ENVELOPE = (
    ROOT / "config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/"
    "paired_resource_envelopes.json"
)
OVERRIDES = {
    "shell/manual_mysql_sgd_r0_campaign.py":
        ROOT / "shell/manual_mysql_sgd_r0_campaign.py",
    "mysql_algorithm_c_local_services.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "shell/mysql_algorithm_c_local_services_v25.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "mysql_process_classification.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    "shell/mysql_process_classification_v10.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    "shell/formal_tpcc_composite_workload.py":
        ROOT / "shell/formal_tpcc_composite_workload.py",
    "tests/test_mysql_local_preparation_interference_gate.py":
        ROOT / "tests/test_mysql_local_preparation_interference_gate.py",
    "tests/test_mysql_benchmark_monitor_observer_signature_v1.py":
        ROOT / "tests/test_mysql_benchmark_monitor_observer_signature_v1.py",
}


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def source_bytes(relative: str, source: Path) -> bytes:
    payload = source.read_bytes()
    if relative != "shell/manual_mysql_sgd_r0_campaign.py":
        return payload
    text = payload.decode("utf-8")
    old = """TPCC_SENSITIVE_SQL = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
    "config/workloads/mysql_tpcc_sensitive_item_semijoin.sql"
)"""
    new = (
        'TPCC_SENSITIVE_SQL = ROOT / '
        '"config/mysql_tpcc_sensitive_item_semijoin.sql"'
    )
    if text.count(old) != 1:
        raise RuntimeError("TPC-C sensitive SQL source anchor differs")
    return text.replace(old, new).encode("utf-8")


def verify_capture_bundle() -> dict[str, Any]:
    manifest = json.loads(
        (CAPTURE_BUNDLE / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    if (
        CAPTURE_BUNDLE.name != manifest.get("bundle_id")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("capture_only") is not True
        or (manifest.get("contract") or {}).get(
            "dynamic_observer_authority", {}
        ).get("proc_cwd_resolved") is not True
        or (manifest.get("contract") or {}).get(
            "dynamic_gate_diagnostics", {}
        ).get("deadline_summary_required") is not True
        or (manifest.get("contract") or {}).get(
            "dynamic_gate_diagnostics", {}
        ).get("qemu_vm_data_plane_classification_required") is not True
    ):
        raise RuntimeError("superseding typed capture bundle differs")
    for relative, expected in manifest["files"].items():
        path = CAPTURE_BUNDLE / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"typed capture closure differs: {relative}")
    return manifest


def publish_source() -> tuple[Path, dict[str, str], str]:
    base = json.loads(
        (BASE_SOURCE / "source_manifest.json").read_text(encoding="utf-8")
    )
    files: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
    for relative in sorted(base["files"]):
        source = OVERRIDES.get(relative, BASE_SOURCE / relative)
        payload = source_bytes(relative, source)
        payloads[relative] = payload
        files[relative] = sha256_bytes(payload)
    sensitive_relative = "config/mysql_tpcc_sensitive_item_semijoin.sql"
    sensitive_source = (
        CAPTURE_BUNDLE / "config/mysql_tpcc_sensitive_item_semijoin.sql"
    )
    payloads[sensitive_relative] = sensitive_source.read_bytes()
    files[sensitive_relative] = sha256(sensitive_source)
    identity = {
        "schema_version": "mysql_tpcc_algorithm_c_local_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "base_source_id": base["bundle_id"],
        "external_driver_allowed": False,
        "files": files,
    }
    source_id = sha256_bytes(canonical(identity))
    final = SOURCE_ROOT / f"source-{source_id}"
    if final.exists():
        observed = json.loads(
            (final / "source_manifest.json").read_text(encoding="utf-8")
        )
        if observed != {**identity, "bundle_id": source_id}:
            raise RuntimeError("existing TPC-C Algorithm C source differs")
        for relative, expected in files.items():
            if sha256(final / relative) != expected:
                raise RuntimeError(f"existing source file differs: {relative}")
        return final, files, source_id
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = SOURCE_ROOT / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (stage / "source_manifest.json").write_bytes(
            canonical({**identity, "bundle_id": source_id})
        )
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, files, source_id


def config_payload(source: Path, typed_manifest_sha: str) -> bytes:
    config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    config.update({
        "schema_version": "mysql_tpcc_algorithm_c_local_debug_config_v1",
        "exp_id": "mysql_algorithm_c.tpcc_cpu.local_one_round_debug_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "outer_execution_authorized": True,
        "fresh_r0_required": True,
        "profile": "__BUNDLE_PROFILE__",
        "measurement_seconds": 600,
        "minimum_measurement_seconds": 180,
        "minimum_client_completions": 1,
    })
    config["mysql"].update({
        "host": "127.0.0.1",
        "port": 65206,
        "database": "tpcc",
    })
    config["output"]["directory"] = str(OUTPUT_ROOT)
    config["production"] = {
        "launch_authorized": True,
        "service_factory": "mysql_algorithm_c_local_services:build",
        "external_driver_allowed": False,
    }
    config["workload"] = {
        "host_control_cpuset": "208-215,464-471",
        "terminals": 32,
        "warehouses": 143,
        "production_mix": "45,43,4,4,4",
        "random_seed": 4508333,
        "sensitive_interval_seconds": 15,
        "driver": "bundle_formal_benchbase_plus_sensitive_composite",
        "external_driver_allowed": False,
    }
    config["optimizer"].update({
        "original_gradient_only": True,
        "max_iter": 20,
        "patience": 3,
        "subset_max_workers": 1,
        "boundary_aware_eta_enabled": True,
        "boundary_search_to_param_limit_enabled": True,
        "boundary_probe_max_steps": 32,
        "boundary_eta_growth_factor": 2.0,
        "plateau_exploration_enabled": False,
        "min_param_delta_rel_pct": 5.0,
        "max_param_delta_rel_pct": 32.0,
    })
    config["estimator"].update({
        "type": "plan_physics",
        "baseline_real": {"cpu": 0, "memory": 0, "io": 0},
        "baseline_real_status": "fresh_local_r0_required",
    })
    config["estimator"]["feature_source"].update({
        "workload_registry_path": "__BUNDLE_TYPED_MANIFEST__",
        "workload_registry_sha256": typed_manifest_sha,
        "binding_status": "AUTHORIZED_ONLY_BY_SELECTED_CAPTURE_COMPLETION",
        "execution_authorized": True,
    })
    config["workload_registry"].update({
        "path": "__BUNDLE_TYPED_MANIFEST__",
        "sha256": typed_manifest_sha,
        "immutable": True,
        "execution_authorized": True,
    })
    config["formal_outer_protocol"].update({
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "max_outer_attempts": 1,
        "target_accepted_shrinks": 10,
        "accepted_shrink_count": 0,
        "rejected_count": 0,
    })
    config["scoring"].update({
        "qps_role": "outer_acceptance_only",
        "score_mode": "resource_only",
    })
    config["three_level_fingerprint"] = {
        "schema_version": "mysql_candidate_three_level_fingerprint_v1",
        "duplicate_rule":
            "exact_parameter_signature_and_equal_l1_and_approximate_l2_l3_v1",
        "l1_required": [
            "operator_types", "join_order", "scan_types", "parallel_structure",
        ],
        "l2_required_metrics": [
            "workers", "hash_batches", "sort_mode", "spill_mode",
            "bitmap_lossy_pages", "intermediate_rows",
        ],
        "l3_profile": "tpcc",
        "l3_required_by_transaction": [
            "cpu_time_ms", "lock_wait_ms", "io_read_bytes",
            "io_write_bytes", "latency_p50_ms", "latency_p95_ms",
            "latency_p99_ms",
        ],
        "transaction_types": [
            "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel",
        ],
        "missing_evidence_policy": "independent_singleton",
        "qps_role": "outer_acceptance_only",
    }
    config["debug_contract"] = {
        "schema_version": "mysql_tpcc_original_gradient_one_round_v1",
        "parameter_count": 4,
        "all_nonempty_subset_count": 15,
        "global_candidate_score_sort": True,
        "fixed_real_replay_top_k": 5,
        "outer_attempt_cap": 1,
        "accepted_round_authorized": False,
        "gradient_event_required_fields": [
            "feature_ratio", "predicted_resource", "utilization",
            "dScore/dU", "T", "confidence", "g_j", "eta",
            "delta_param", "candidate_score",
        ],
    }
    config["early_stop_source_bundle"] = {
        "source": str(source),
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
    }
    return canonical(config)


def main() -> int:
    capture_manifest = verify_capture_bundle()
    source, source_files, source_id = publish_source()
    typed_manifest = CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json"
    profile = {
        "schema_version": "mysql_formal_resource_profile_v1",
        "scenario": "tpcc_cpu",
        "benchmark": "tpcc",
        "host": "local://compute00",
        "hostname": "compute00",
        "device": "/dev/sda",
        "cpuset": "208-215,464-471",
        "mems": "1",
        "cpu_cores": 4,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 0,
        "pids": 512,
        "read_bps": 1205862400,
        "write_bps": 1205862400,
        "buffer_pool_bytes": 4294967296,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority":
            "selected_diagnostic_capture_effective_readback_then_fresh_r0",
    }
    config = config_payload(source, sha256(typed_manifest))
    payloads = {
        "config.json": config,
        "profile.json": canonical(profile),
        "launcher.py": LAUNCHER.read_bytes(),
        "typed_capture_bundle_manifest.json":
            (CAPTURE_BUNDLE / "bundle_manifest.json").read_bytes(),
        "typed_capture_collector.py":
            (CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py").read_bytes(),
        "typed_binding_manifest.json": typed_manifest.read_bytes(),
        "capture_requirement.json": canonical({
            "schema_version": "mysql_tpcc_capture_requirement_v1",
            "required_capture_bundle_id": CAPTURE_BUNDLE.name,
            "required_capture_bundle_manifest_sha256": sha256(
                CAPTURE_BUNDLE / "bundle_manifest.json"
            ),
            "required_collector_sha256": sha256(
                CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py"
            ),
            "binding_count": 34,
            "transaction_types": [
                "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel",
            ],
            "qps_resource_input_prohibited": True,
            "capture_result_is_r0": False,
            "accepted_round_authorized": False,
        }),
        "preflight_contract.json": canonical({
            "schema_version": "mysql_tpcc_algorithm_c_local_preflight_v1",
            "status":
                "BLOCKED_UNTIL_SELECTED_DIAGNOSTIC_CAPTURE_COMPLETION",
            "offline_command":
                "launcher.py --mode preflight --bundle B "
                "--capture-authority A --run-token TOKEN",
            "r0_only_and_outer_separate": True,
            "external_driver_allowed": False,
            "accepted_round_authorized": False,
        }),
    }
    files = {
        relative: sha256_bytes(payload)
        for relative, payload in sorted(payloads.items())
    }
    identity = {
        "schema_version": "mysql_tpcc_algorithm_c_local_debug_bundle_v1",
        "supersedes_bundle_ids": [
            "ad459dc91eecd667296a3af9c55c5d38"
            "5d6dada77cc36a7b03a87f36c19f4e4a"
        ],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "capture_result_is_r0": False,
        "fresh_r0_authorized": True,
        "outer_requires_separate_seal": True,
        "accepted_round_authorized": False,
        "one_debug_round": True,
        "source_directory": str(source),
        "source_id": source_id,
        "source_file_count": len(source_files),
        "capture_bundle_id": CAPTURE_BUNDLE.name,
        "resource_envelope": profile,
        "algorithm_contract": {
            "original_gradient_only": True,
            "parameter_count": 4,
            "all_nonempty_subset_count": 15,
            "global_fixed_top_k": 5,
            "qps_role": "outer_acceptance_only",
            "three_level_fingerprint": True,
            "missing_fingerprint_evidence_policy": "independent_singleton",
        },
        "output_root": str(OUTPUT_ROOT),
        "files": files,
    }
    bundle_id = sha256_bytes(canonical(identity))
    final = DESTINATION / bundle_id
    if final.exists():
        observed = json.loads(
            (final / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        if observed != {**identity, "bundle_id": bundle_id}:
            raise RuntimeError("existing TPC-C Algorithm C bundle differs")
        for relative, expected in files.items():
            if sha256(final / relative) != expected:
                raise RuntimeError(f"existing bundle file differs: {relative}")
        print(json.dumps({
            "status": "EXISTS_VERIFIED", "bundle_id": bundle_id,
            "directory": str(final),
        }, sort_keys=True))
        return 0
    DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = DESTINATION / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            (stage / relative).write_bytes(payload)
        (stage / "bundle_manifest.json").write_bytes(
            canonical({**identity, "bundle_id": bundle_id})
        )
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({
        "status": "PUBLISHED_VERIFIED", "bundle_id": bundle_id,
        "directory": str(final), "source_id": source_id,
        "capture_authority_selected": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
