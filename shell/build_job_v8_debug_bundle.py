#!/usr/bin/env python3
"""Publish an immutable one-round JOB Algorithm C debug bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_ALL_SUBSET_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "source-v24-6e3baf1d78a00ce039491cb44695ad3d8578f8a1950dc4a07bfe0a55d79abd50"
)
BASE_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_v34_debug"
    / "c968855caf48582213292761f4e9547229d268582dad9f713fca5243a48bba7b"
)
BASE_SOURCE = Path(
    json.loads(
        (BASE_BUNDLE / "bundle_manifest.json").read_text(encoding="utf-8")
    )["source_directory"]
)
BASE_READINESS = next(BASE_BUNDLE.glob("readiness_manifest-*.json"))
BASE_METHOD = BASE_BUNDLE / "experience_table_sgd_method_manifest.json"
ROOT_VERIFIER = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
R0_POINT = (
    ROOT
    / "history/mysql_gradient_debug/job_local_r0_nvme_storage_v2"
    / "production_points/alg_c_job_r0_20260728T121609_bd0acc17"
)
STORAGE_MANIFEST = Path("/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json")
JOB_SQL_AUTHORITY = (
    ROOT
    / "history/mysql_sgd_env_agents_20260723/job/"
    "job_strict_plan_capture_20260723_0642/artifacts/sql"
)
JOB_SQL_RELATIVE_ROOT = Path(
    "config/formal/mysql_sgd_production/workloads/job_v1"
)
MANUAL_R0_RELATIVE = "shell/manual_mysql_sgd_r0_campaign.py"
SOURCE_DESTINATION = (
    ROOT / "history/mysql_gradient_debug/readiness_job_v8_debug_v1"
)
BUNDLE_DESTINATION = ROOT / "artifacts/mysql_job_v8_debug"
SUPERSEDES_BUNDLE_IDS = [
    "a98fde4c3a31b4a1285770c5c1b6449723d287abb2a47d7bc3b95e90b595bdeb",
    "0f5f249da59d9579ff630f55e6597c14aaf516547b40201c0c9ee8e52750305f",
]
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "job_v8_original_gradient_fresh_r0_debug_20260729"
)
OVERRIDES = {
    "gradient_descent_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "gradient_descent_optimizer.py",
    "experience_table_subset_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "experience_table_subset_optimizer.py",
    "mysql_parameter_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_parameter_optimizer.py",
    "mysql_plan_clustering.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_plan_clustering.py",
    "mysql_three_level_fingerprint.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_three_level_fingerprint.py",
    "formal_mysql_sgd_campaign.py": ROOT / "formal_mysql_sgd_campaign.py",
    "formal_job_continuous_session.py":
        ROOT / "formal_job_continuous_session.py",
    "mysql_algorithm_c_local_services.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "shell/mysql_algorithm_c_local_services_v25.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "shell/mysql_algorithm_c_local_services_v26.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v26.py",
    "mysql_process_classification.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    "shell/mysql_process_classification_v10.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    (
        "config/formal/mysql_sgd_production/"
        "job_memory.experience_table_sgd.optimizer.json"
    ): (
        ROOT
        / "config/formal/mysql_sgd_production/"
        "job_memory.experience_table_sgd.optimizer.json"
    ),
    "config/mysql_optimizer_config.json":
        ROOT / "config/mysql_optimizer_config.json",
    "tests/test_mysql_parameter_optimizer_baseline_binding.py":
        ROOT / "tests/test_mysql_parameter_optimizer_baseline_binding.py",
    "tests/test_formal_runtime_estimator_baseline.py":
        ROOT / "tests/test_formal_runtime_estimator_baseline.py",
    "tests/test_formal_job_continuous_session.py":
        ROOT / "tests/test_formal_job_continuous_session.py",
    "tests/test_mysql_local_preparation_interference_gate.py":
        ROOT / "tests/test_mysql_local_preparation_interference_gate.py",
    "tests/test_mysql_process_classification_v10.py":
        ROOT / "tests/test_mysql_process_classification_v10.py",
    "tests/test_mysql_benchmark_monitor_observer_signature_v1.py":
        ROOT
        / "tests/test_mysql_benchmark_monitor_observer_signature_v1.py",
    "test_experience_table_gradient_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "test_experience_table_gradient_optimizer.py",
    "test_experience_table_subset_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "test_experience_table_subset_optimizer.py",
    "test_mysql_three_level_fingerprint.py":
        RAW_ALL_SUBSET_SOURCE / "test_mysql_three_level_fingerprint.py",
}


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def entry(path: Path, sha256: str, role: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256,
        "role": role,
        "scenario_scope": ["job_memory"],
    }


def source_payload(relative: str) -> bytes:
    source = OVERRIDES.get(relative, BASE_SOURCE / relative)
    payload = source.read_bytes()
    if relative != MANUAL_R0_RELATIVE:
        return payload
    text = payload.decode("utf-8")
    old_query_root = """JOB_QUERY_DIR = (
    HISTORY / "job/job_strict_plan_capture_20260723_0642/artifacts/sql"
)"""
    new_query_root = (
        "JOB_QUERY_DIR = ROOT / "
        f"{str(JOB_SQL_RELATIVE_ROOT)!r}"
    )
    if text.count(old_query_root) != 1:
        raise RuntimeError("cannot bind frozen JOB SQL root exactly once")
    text = text.replace(old_query_root, new_query_root)
    old_replay_contract = (
        '"real_replay_recall": '
        '"one_global_deduplicated_fixed_top_5"'
    )
    new_replay_contract = (
        '"real_replay_recall": '
        '"one_global_raw_all_subset_fixed_top_5"'
    )
    if text.count(old_replay_contract) != 1:
        raise RuntimeError("cannot correct JOB runtime replay contract exactly once")
    return text.replace(
        old_replay_contract, new_replay_contract
    ).encode("utf-8")


def validate_inputs() -> dict[str, Any]:
    job_sql_paths = sorted(JOB_SQL_AUTHORITY.glob("*.sql"))
    required = [
        BASE_SOURCE / "source_manifest.json",
        BASE_READINESS,
        BASE_METHOD,
        ROOT_VERIFIER,
        R0_POINT / "r0_result.json",
        R0_POINT / "campaign_runtime.json",
        R0_POINT / "effective_resource_readback_v2.json",
        R0_POINT / "storage_readback_v2.json",
        STORAGE_MANIFEST,
        *OVERRIDES.values(),
        *job_sql_paths,
    ]
    for path in required:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"authority input is absent or symlinked: {path}")
    base = json.loads(
        (BASE_SOURCE / "source_manifest.json").read_text(encoding="utf-8")
    )
    for relative, expected in base["files"].items():
        candidate = BASE_SOURCE / relative
        if candidate.is_symlink() or digest(candidate) != expected:
            raise RuntimeError(f"base source closure mismatch: {relative}")
    r0 = json.loads((R0_POINT / "r0_result.json").read_text(encoding="utf-8"))
    gate = r0.get("r0_profile_gate") or {}
    selected = (r0.get("rolling_measurement_control") or {}).get(
        "selected_observation"
    ) or {}
    if (
        gate.get("passed") is not True
        or (r0.get("correctness") or {}).get("passed") is not True
        or not (selected.get("database_throughput") or {}).get("reliable")
        or not (selected.get("stability") or {}).get("stable")
    ):
        raise RuntimeError("JOB R0 authority is not a passed, stable real R0")
    storage = json.loads(
        (R0_POINT / "storage_readback_v2.json").read_text(encoding="utf-8")
    )
    if (
        storage.get("verified") is not True
        or storage.get("manifest_sha256") != digest(STORAGE_MANIFEST)
    ):
        raise RuntimeError("JOB storage manifest differs from valid R0")
    config = json.loads(
        (
            OVERRIDES[
                "config/formal/mysql_sgd_production/"
                "job_memory.experience_table_sgd.optimizer.json"
            ]
        ).read_text(encoding="utf-8")
    )
    queries = {
        item["query_id"]: item["sql"].strip().rstrip(";")
        for item in config["estimator"]["feature_source"]["queries"]
    }
    observed: dict[str, str] = {}
    for path in job_sql_paths:
        query_id = path.stem.split("_", 1)[1]
        observed[query_id] = path.read_text(
            encoding="utf-8"
        ).strip().rstrip(";")
    if len(job_sql_paths) != 9 or observed != queries:
        raise RuntimeError(
            "frozen JOB SQL directory differs from the nine estimator queries"
        )
    return base


def publish_source(base: dict[str, Any]) -> tuple[Path, dict[str, str], str]:
    files: dict[str, str] = {}
    for relative, expected in sorted(base["files"].items()):
        files[relative] = digest_bytes(source_payload(relative))
        if (
            relative not in OVERRIDES
            and relative != MANUAL_R0_RELATIVE
            and files[relative] != expected
        ):
            raise RuntimeError(f"unmodified base source changed: {relative}")
    for relative, source in sorted(OVERRIDES.items()):
        files[relative] = digest_bytes(source_payload(relative))
    for path in sorted(JOB_SQL_AUTHORITY.glob("*.sql")):
        relative = str(JOB_SQL_RELATIVE_ROOT / path.name)
        files[relative] = digest(path)
    identity = {
        "schema_version": "mysql_job_v8_debug_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "base_source_bundle_id": base["bundle_id"],
        "original_gradient_only": True,
        "runtime_fresh_r0_estimator_calibration": True,
        "files": files,
    }
    source_id = digest_bytes(canonical(identity))
    final = SOURCE_DESTINATION / f"source-{source_id}"
    if final.exists():
        observed = json.loads(
            (final / "source_manifest.json").read_text(encoding="utf-8")
        )
        if observed != {**identity, "bundle_id": source_id}:
            raise RuntimeError("existing JOB source identity differs")
        for relative, expected in files.items():
            if digest(final / relative) != expected:
                raise RuntimeError(f"existing JOB source differs: {relative}")
        return final, files, source_id

    SOURCE_DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = SOURCE_DESTINATION / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative in sorted(files):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative.startswith(f"{JOB_SQL_RELATIVE_ROOT}/"):
                shutil.copyfile(
                    JOB_SQL_AUTHORITY / Path(relative).name, destination
                )
            else:
                destination.write_bytes(source_payload(relative))
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


def launcher_payload(source: Path) -> bytes:
    debug = source / "shell/debug_mysql_gradient_round_v25.py"
    return f"""#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

ROOT = Path({str(ROOT)!r})
SOURCE = Path({str(source)!r})
VERIFIER = Path({str(ROOT_VERIFIER)!r})
DEBUG = Path({str(debug)!r})
os.chdir("/tmp")
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell"), str(ROOT / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen readiness verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(ROOT), str(SOURCE))
]
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def config_payload(source: Path) -> bytes:
    config = json.loads(
        (
            source
            / "config/formal/mysql_sgd_production/"
            "job_memory.experience_table_sgd.optimizer.json"
        ).read_text(encoding="utf-8")
    )
    config.update(
        {
            "exp_id": "mysql_algorithm_c.job_memory.v8_one_round_debug",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "accepted_round_authorized": False,
            "outer_execution_authorized": True,
            "profile": str(
                source
                / "config/formal/mysql_sgd_production/profiles/job_memory.json"
            ),
            "local_storage_profile_id":
                "job_local_storage_v2_nvme_fullcopy",
            "local_storage_manifest": str(STORAGE_MANIFEST),
            "local_storage_manifest_sha256": digest(STORAGE_MANIFEST),
            "measurement_seconds": 600,
            "minimum_measurement_seconds": 180,
            "minimum_client_completions": 1,
            "workload": {
                "host_control_cpuset": "176-183,432-439",
                "fresh_copy_mode":
                    "full_physical_copy_no_overlay_no_reflink",
                "fresh_full_physical_copy_required": True,
                "query_directory": str(source / JOB_SQL_RELATIVE_ROOT),
                "query_count": 9,
                "query_files_sha256": {
                    path.name: digest(path)
                    for path in sorted(JOB_SQL_AUTHORITY.glob("*.sql"))
                },
            },
        }
    )
    config["output"]["directory"] = str(OUTPUT_ROOT)
    config["production"].update(
        {
            "launch_authorized": True,
            "service_factory":
                "shell.mysql_algorithm_c_local_services_v26:build",
        }
    )
    config["optimizer"].update(
        {
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
            "plateau_score_epsilon": 1e-12,
        }
    )
    config["estimator"]["type"] = "plan_physics"
    config["estimator"].pop("uncalibrated_transition_log_step", None)
    config["three_level_fingerprint"] = {
        "schema_version": "mysql_candidate_three_level_fingerprint_v1",
        "l1_version": "mysql_plan_structure_l1_v2",
        "duplicate_rule":
            "exact_parameter_signature_and_equal_l1_and_approximate_l2_l3_v1",
        "l2_required_metrics": [
            "workers",
            "hash_batches",
            "sort_mode",
            "spill_mode",
            "spill_bytes",
            "bitmap_lossy_pages",
            "intermediate_rows",
        ],
        "l3_profile": "job",
        "l3_primary_metrics": [
            "memory_peak_bytes",
            "dynamic_execution_memory_bytes",
            "spill_bytes",
        ],
        "l3_supplementary_metrics": ["cpu_time_ms"],
        "tolerance": {
            "l2_relative": 0.05,
            "l2_absolute": 1.0,
            "l3_relative": 0.1,
            "l3_absolute": 1e-9,
        },
        "missing_evidence_policy": "independent_singleton",
        "qps_role": "outer_acceptance_only",
    }
    config["formal_outer_protocol"].update(
        {
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "max_outer_attempts": 1,
            "accepted_shrink_count": 0,
            "rejected_count": 0,
        }
    )
    config["host02_job_xp_mutex"].update(
        {
            "required": True,
            "mode": "isolated_parallel_dynamic_gate",
            "scenario": "job_memory",
            "lock_path": str(
                ROOT
                / "history/mysql_gradient_debug/host_control/"
                "job_xp_pressure.lock"
            ),
            "journal_path": str(
                ROOT
                / "history/mysql_gradient_debug/host_control/"
                "job_xp_lease_journal.jsonl"
            ),
        }
    )
    return canonical(config)


def verify_readiness(path: Path) -> dict[str, int]:
    sys.path.insert(0, str(ROOT))
    try:
        from shell.verify_mysql_sgd_v4_readiness import verify_document
    finally:
        sys.path.pop(0)
    checks = verify_document(
        json.loads(path.read_text(encoding="utf-8")), ROOT
    )
    failed = [check for check in checks if not check.passed]
    if failed:
        detail = [
            (item.requirement, item.item, item.reason) for item in failed[:8]
        ]
        raise RuntimeError(f"published JOB readiness failed closed: {detail}")
    return {"verifier_passed": len(checks), "verifier_failed": 0}


def main() -> int:
    base = validate_inputs()
    source, source_files, source_id = publish_source(base)
    config = config_payload(source)
    launcher = launcher_payload(source)
    readback = json.loads(
        (R0_POINT / "effective_resource_readback_v2.json").read_text(
            encoding="utf-8"
        )
    )
    effective = readback["effective"]
    normalized = {
        "cpu_isolation_degree": 16,
        "cpu_quota_cores": effective["cpu_quota_us"] / 100000.0,
        "io_read_bps": effective["io_read_bps"],
        "io_write_bps": effective["io_write_bps"],
        "memory_bytes": effective["memory_bytes"],
        "memory_swap_bytes": effective["memory_bytes"],
        "numa_isolation_degree": 1,
        "pids_max": effective["pids_max"],
    }
    closure = {
        "schema_version": "mysql_job_v8_debug_bundle_closure_v2",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": SUPERSEDES_BUNDLE_IDS,
        "runtime_interference_gate_revision": 2,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "source_bundle_id": source_id,
        "source_manifest_sha256": digest(source / "source_manifest.json"),
        "config_sha256": digest_bytes(config),
        "launcher_sha256": digest_bytes(launcher),
        "r0_result_sha256": digest(R0_POINT / "r0_result.json"),
        "r0_readback_sha256":
            digest(R0_POINT / "effective_resource_readback_v2.json"),
        "storage_manifest_sha256": digest(STORAGE_MANIFEST),
        "normalized_envelope": normalized,
        "builder_sha256": digest(Path(__file__)),
    }
    bundle_id = digest_bytes(canonical(closure))
    final = BUNDLE_DESTINATION / bundle_id
    if final.exists():
        readiness = next(final.glob("readiness_manifest-*.json"))
        result = verify_readiness(readiness)
        print(json.dumps({
            "status": "EXISTS_VERIFIED",
            "bundle_id": bundle_id,
            "source_bundle_id": source_id,
            "directory": str(final),
            "config": str(final / "config.json"),
            "readiness_manifest": str(readiness),
            "launcher": str(final / "launch_job_v8_debug.py"),
            **result,
        }, sort_keys=True))
        return 0

    BUNDLE_DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_DESTINATION / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        (stage / "config.json").write_bytes(config)
        (stage / "launch_job_v8_debug.py").write_bytes(launcher)
        (stage / "launch_job_v8_debug.py").chmod(0o555)

        final_config = final / "config.json"
        final_launcher = final / "launch_job_v8_debug.py"
        source_readback = final / "job_v8_source_mysql_readback.json"
        source_readback_payload = canonical({
            "schema_version": "mysql_normalized_resource_envelope_readback_v1",
            "scenario": "job_memory",
            "engine": "mysql",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "normalized_envelope": normalized,
            "raw_effective_readback": {
                "path": str(R0_POINT / "effective_resource_readback_v2.json"),
                "sha256": closure["r0_readback_sha256"],
            },
            "swap_normalization": {
                "raw_memory_swap_bytes": effective["memory_swap_bytes"],
                "normalized_memory_swap_bytes": effective["memory_bytes"],
                "mode": "host_swap_disabled_no_memsw_controller",
                "additional_swap_available": False,
            },
        })
        (stage / source_readback.name).write_bytes(source_readback_payload)

        scope_readback = final / "job_v8_mysql_only_envelope_readback.json"
        scope_readback_payload = canonical({
            "schema_version": "mysql_only_scope_bound_envelope_readback_v1",
            "scenario": "job_memory",
            "engine": "mysql",
            "formal_scope_name": "mysql_only",
            "freeze_id": "mysql_job_v8_debug_from_valid_local_r0",
            "postgresql_counterpart_required": False,
            "cross_engine_envelope_equality_required": False,
            "cross_engine_comparison_prohibited": True,
            "normalized_envelope": normalized,
            "source_mysql_readback": {
                "path": str(source_readback),
                "sha256": digest_bytes(source_readback_payload),
            },
            "status": "SCOPE_BOUND_FROM_AUTHENTIC_MYSQL_READBACK",
        })
        (stage / scope_readback.name).write_bytes(scope_readback_payload)

        r0_result = json.loads(
            (R0_POINT / "r0_result.json").read_text(encoding="utf-8")
        )
        selected = r0_result["rolling_measurement_control"][
            "selected_observation"
        ]
        r0_evidence = final / "job_v8_r0_calibration_evidence.json"
        r0_evidence_payload = canonical({
            "schema_version": "mysql_sgd_r0_calibration_evidence_v1",
            "scenario": "job_memory",
            "benchmark": "job",
            "target_resource": "memory",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "passed": True,
            "latest_window_seconds": 180,
            "latest_window_count": 6,
            "sample_coverage": selected["resource_sample_coverage"],
            "resource_utilization": selected["resource_means"],
            "selected_measurement_seconds":
                r0_result["rolling_measurement_control"][
                    "selected_measurement_seconds"
                ],
            "stop_reason":
                r0_result["rolling_measurement_control"]["stop_reason"],
            "stability": selected["stability"],
            "correctness": r0_result["correctness"],
            "database_throughput": selected["database_throughput"],
            "resource_envelope_normalized": normalized,
            "source": {
                "r0_result": {
                    "path": str(R0_POINT / "r0_result.json"),
                    "sha256": closure["r0_result_sha256"],
                },
                "campaign_runtime": {
                    "path": str(R0_POINT / "campaign_runtime.json"),
                    "sha256": digest(R0_POINT / "campaign_runtime.json"),
                },
                "effective_resource_readback": {
                    "path": str(
                        R0_POINT / "effective_resource_readback_v2.json"
                    ),
                    "sha256": closure["r0_readback_sha256"],
                },
            },
        })
        (stage / r0_evidence.name).write_bytes(r0_evidence_payload)

        entries = [
            entry(source / relative, expected, "source")
            for relative, expected in sorted(source_files.items())
        ]
        entries.extend([
            entry(
                source / "source_manifest.json",
                digest(source / "source_manifest.json"),
                "source_manifest",
            ),
            entry(final_config, digest_bytes(config), "debug_config_authority"),
            entry(final_launcher, digest_bytes(launcher), "launch_wrapper"),
            entry(
                ROOT_VERIFIER,
                digest(ROOT_VERIFIER),
                "root_evidence_verifier",
            ),
            entry(
                R0_POINT / "r0_result.json",
                closure["r0_result_sha256"],
                "validated_fresh_r0_result",
            ),
            entry(
                R0_POINT / "effective_resource_readback_v2.json",
                closure["r0_readback_sha256"],
                "validated_fresh_r0_effective_readback",
            ),
            entry(
                STORAGE_MANIFEST,
                closure["storage_manifest_sha256"],
                "frozen_storage_manifest",
            ),
            entry(
                source_readback,
                digest_bytes(source_readback_payload),
                "normalized_source_mysql_readback",
            ),
            entry(
                scope_readback,
                digest_bytes(scope_readback_payload),
                "mysql_only_scope_bound_envelope_readback",
            ),
            entry(
                r0_evidence,
                digest_bytes(r0_evidence_payload),
                "validated_fresh_r0_calibration_evidence",
            ),
        ])

        method = json.loads(BASE_METHOD.read_text(encoding="utf-8"))
        method.update({
            "registered_sources": entries,
            "formal_launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_authorized": False,
            "runtime_binding": {
                "entrypoint": str(final_launcher),
                "source_directory": str(source),
                "config": str(final_config),
                "profile": str(
                    source
                    / "config/formal/mysql_sgd_production/profiles/"
                    "job_memory.json"
                ),
                "service_factory":
                    "shell.mysql_algorithm_c_local_services_v26:build",
                "accepted_round_authorized": False,
                "debug_outer_attempts": 1,
            },
        })
        method_payload = canonical(method)
        method_name = "experience_table_sgd_method_manifest.json"
        (stage / method_name).write_bytes(method_payload)

        readiness = json.loads(
            BASE_READINESS.read_text(encoding="utf-8")
        )
        method_bundle = readiness["global_requirements"]["method_bundle"]
        method_bundle["ready"] = True
        method_bundle["registered_sources"] = [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in entries
        ]
        method_bundle["method_manifest"] = {
            "path": str(final / method_name),
            "sha256": digest_bytes(method_payload),
        }
        job = readiness["scenarios"]["job_memory"]
        job["environment"].update({
            "current_state": "local_storage_v2_fresh_r0_verified",
            "device": effective["storage_device"],
            "host_address": "127.0.0.1",
            "host_id": "compute00",
            "mount": "/disk1/ra_mysql_isolated_v2",
        })
        job["mysql_envelope"] = {
            "freeze_id": "mysql_job_v8_debug_from_valid_local_r0",
            "normalized": normalized,
            "readback": {
                "path": str(scope_readback),
                "sha256": digest_bytes(scope_readback_payload),
            },
        }
        job["r0_calibration"] = {
            "passed": True,
            "evidence": {
                "path": str(r0_evidence),
                "sha256": digest_bytes(r0_evidence_payload),
            },
        }
        readiness.update({
            "status": "READY",
            "launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "debug_authority": {
                "schema_version": "mysql_job_v8_debug_authority_v1",
                "scenario": "job_memory",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "outer_attempts": 1,
                "accepted_round_authorized": False,
                "fresh_in_band_r0_required": True,
                "historical_r0_role": "readiness_only",
                "resource_envelope": {
                    **normalized,
                    "cpuset_cpus": effective["cpuset_cpus"],
                    "cpuset_mems": effective["cpuset_mems"],
                    "device": effective["storage_device"],
                },
            },
        })
        readiness_payload = canonical(readiness)
        readiness_sha = digest_bytes(readiness_payload)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_payload)
        (stage / "closure.json").write_bytes(canonical(closure))

        files = {
            path.name: digest(path)
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        (stage / "bundle_manifest.json").write_bytes(canonical({
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(OUTPUT_ROOT),
            "files": files,
            "publication": "staged_then_atomic_rename",
        }))
        readonly(stage)
        stage.rename(final)
        try:
            result = verify_readiness(final / readiness_name)
        except BaseException:
            final.rename(
                BUNDLE_DESTINATION
                / f".{bundle_id}.failed.{os.getpid()}"
            )
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(json.dumps({
        "status": "PUBLISHED_VERIFIED",
        "bundle_id": bundle_id,
        "source_bundle_id": source_id,
        "directory": str(final),
        "config": str(final / "config.json"),
        "readiness_manifest": str(final / readiness_name),
        "launcher": str(final / "launch_job_v8_debug.py"),
        **result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
