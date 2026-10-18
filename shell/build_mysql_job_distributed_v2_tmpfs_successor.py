#!/usr/bin/env python3
"""Build the immutable JOB distributed-v2 qualification successor.

The builder is local-only.  It starts from the complete v30 source closure,
then replaces the distributed qualification layer with the *current* shared
collector/metric bridge plus the JOB-v2 bootstrap and handoff authority.  It
does not copy any payload from the invalid 87550/272405 bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
PREDECESSOR = (
    ROOT
    / "artifacts/mysql_job_v30_debug/"
    "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "3c028cdc5fa335c4276c5111a7813af850278a49399aea9824ca5fce7668db79"
)
BASE_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_job_sampling_v2_successor/"
    "source-7544c870c204cc08f0876ea2e435b4a9f5dc8a1f4ae8a4c9982152a8a729d204"
)
BASE_SOURCE_MANIFEST_SHA256 = (
    "e6656f63d358e11eeeb904e8cbbb227497c4187dacc267243a13f36211283f4f"
)
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "readiness_mysql_job_distributed_v2_successor"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v32_distributed_v2_tmpfs_successor"

CONTRACT = (
    ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
FORMAL10_CONTRACT = (
    ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
)
RECONCILIATION = (
    ROOT
    / "history/mysql_gradient_debug/job_v6/20260728T085336Z/"
    "formal_replay_authority_reconciliation_v1.json"
)
RECONCILIATION_SHA256 = (
    "96beed0c59d0f86ac8c4b517ce94a5af460be2b14089a3d40583a3ef6813df82"
)
AUTHORITY = ROOT / "shell/mysql_job_distributed_v2_authority_tmpfs_v2.py"
ADOPTED_SERVICES = (
    ROOT / "shell/mysql_job_distributed_v2_adopted_services.py"
)
ROLLING_MONITOR = (
    ROOT / "shell/mysql_job_distributed_v2_rolling_monitor.py"
)
FORMAL_JOB_WORKLOAD = ROOT / "shell/formal_job_multiquery_workload.py"
PHYSICAL_COPY = ROOT / "shell/mysql_job_physical_copy_v1.py"
MANUAL_CAMPAIGN = ROOT / "shell/manual_mysql_sgd_r0_campaign.py"
INVALIDATED_BUNDLE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_b7563277_invalidation_20260731.json"
)
INVALIDATED_BD316_BUNDLE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_bd316145_invalidation_20260731.json"
)
INVALIDATED_82E_BUNDLE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_82e2867d_invalidation_20260731.json"
)
INVALIDATED_4FD_BUNDLE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_4fd514932_invalidation_20260731.json"
)
INVALIDATED_2D237_BUNDLE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_2d237b84_invalidation_20260731.json"
)
INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json"
)
INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_449cd22_content_identity_invalidation_20260731.json"
)
INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_afe700_normalization_contract_invalidation_20260731.json"
)
INVALIDATED_F058_SQL_PERCENT_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
)
INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json"
)
INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
)
INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
)
INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json"
)
INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json"
)
INVALIDATED_A0F3E7_CLIENT_UNKNOWN_USER_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_a0f3e7_client_unknown_user_invalidation_20260731.json"
)
INVALIDATED_1093DF_COUNTER_COVERAGE_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_1093df_collect_only_counter_coverage_invalidation_20260731.json"
)
INVALIDATED_0626B1_SHOW_STATUS_NONINTEGER_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_0626b1_show_status_noninteger_invalidation_20260731.json"
)
INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY = (
    ROOT
    / "preflight/"
    "mysql_job_v32_0239_sql_query_native_contract_invalidation_20260731.json"
)
INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY = (
    ROOT
    / "preflight/"
    "mysql_job_v32_0c7a_cgroup_v1_oom_readback_invalidation_20260731.json"
)
INVALIDATED_350A_NO_PSI_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_350a_no_psi_invalidation_20260731.json"
)
INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_6bcd_collector_import_invalidation_20260731.json"
)
INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY = (
    ROOT / "preflight/"
    "mysql_job_v32_6bf_qualification_root_mount_attribution_invalidation_20260731.json"
)
INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_a50d_measurement_host_tmpfs_invalidation_20260731.json"
)
INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY = (
    ROOT / "preflight/mysql_job_v32_79b2_measurement_bind_ordering_invalidation_20260731.json"
)
COLLECTOR = ROOT / "shell/mysql_job_distributed_live_readback_qualification_v2.py"
ROOT_DOCKER_MOUNT_HELPER = ROOT / "shell/mysql_job_host_root_inventory_v1.py"
CLIENT_IMMUTABLE_IDENTITY = ROOT / "shell/mysql_tpch_client_immutable_identity_v1.py"
NATIVE_ADAPTER = (
    ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
)
SQL_ADAPTER = ROOT / "shell/mysql_sql_debug_metric_adapter_v1.py"
BOOTSTRAP_SPEC_RENDERER = (
    ROOT / "shell/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py"
)
QUALIFICATION_SPEC_RENDERER = (
    ROOT / "shell/render_mysql_job_v32_qualification_solo_tmpfs_v2.py"
)
RUNTIME_HELPER = ROOT / "shell/runtime_tree_content_identity_v3.py"
ADOPTED_SERVICE_DEPENDENCIES = (
    "benchmark_estimator.py",
    "common_outer_loop.py",
    "experience_table_escape_policy.py",
    "experience_table_subset_optimizer.py",
    "formal_acceptance_policy.py",
    "formal_benchbase_continuous_session.py",
    "formal_hybench_continuous_session.py",
    "formal_job_continuous_session.py",
    "formal_mysql_database_qps.py",
    "formal_mysql_random_walk_metrics.py",
    "formal_mysql_sgd_checkpoint.py",
    "formal_mysql_sgd_orchestration.py",
    "formal_recost_core.py",
    "formal_stability.py",
    "gradient_descent_optimizer.py",
    "logger_utils.py",
    "mysql_algorithm_b_production_services.py",
    "mysql_cost_adapter.py",
    "mysql_parameter_optimizer.py",
    "mysql_plan_clustering.py",
    "plan_resource_estimator.py",
    "resource_utility.py",
    "trained_model_estimator.py",
)
PYTHON_MANIFEST = (
    ROOT
    / "preflight/runtime_portable_manifests_v3_20260729/"
    "node04_python311_v3-"
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4.json"
)
PYTHON_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_BINARY_SHA256 = (
    "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
)

RUNTIME_ROOT = (
    "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
    "runtime_algorithm_c_successor_20260729"
)
PYTHON_ROOT = (
    RUNTIME_ROOT + "/runtime/python311-" + PYTHON_TREE_CONTENT_SHA256
)
SQL_NAMES = (
    "01_job_1a.sql",
    "02_job_5a.sql",
    "03_job_9a.sql",
    "04_job_12a.sql",
    "05_job_16a.sql",
    "06_job_20a.sql",
    "07_job_26a.sql",
    "08_job_33a.sql",
    "09_job_sensitive_title_cast_role5_movie100k.sql",
)


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"{label} is absent or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be an object")
    return value


def _safe_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if (
        not relative
        or relative.startswith("/")
        or ".." in path.parts
        or "__pycache__" in path.parts
        or relative.endswith((".pyc", ".pyo"))
    ):
        raise BuildError(f"unsafe or generated closure path: {relative!r}")


def verify_file_map(
    root: Path,
    files: Mapping[str, str],
    label: str,
    *,
    allow_generated_extras: bool = False,
) -> None:
    if not isinstance(files, Mapping) or not files:
        raise BuildError(f"{label} file map is empty")
    expected: set[str] = set()
    for relative, expected_sha in files.items():
        _safe_relative(str(relative))
        path = root / str(relative)
        if (
            not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or path.is_symlink()
            or not path.is_file()
            or sha256(path) != expected_sha
        ):
            raise BuildError(f"{label} closure differs at {relative}")
        expected.add(str(relative))
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and str(path.relative_to(root)) not in {
            "source_manifest.json",
            "bundle_manifest.json",
            "bundle_manifest.json.sha256",
        }
    }
    extras = actual - expected
    if allow_generated_extras:
        extras = {
            relative
            for relative in extras
            if "__pycache__" not in PurePosixPath(relative).parts
            and ".pytest_cache" not in PurePosixPath(relative).parts
            and not relative.endswith((".pyc", ".pyo"))
        }
    if extras or expected - actual:
        raise BuildError(
            f"{label} exact entry set differs: "
            f"{sorted(extras | (expected - actual))}"
        )


def _read_base_source() -> dict[str, bytes]:
    if sha256(BASE_SOURCE / "source_manifest.json") != BASE_SOURCE_MANIFEST_SHA256:
        raise BuildError("base source manifest identity differs")
    manifest = load(BASE_SOURCE / "source_manifest.json", "base source manifest")
    if manifest.get("bundle_id") != BASE_SOURCE.name.removeprefix("source-"):
        raise BuildError("base source content address differs")
    verify_file_map(
        BASE_SOURCE,
        manifest.get("files") or {},
        "base source",
        allow_generated_extras=True,
    )
    payloads = {
        str(relative): (BASE_SOURCE / str(relative)).read_bytes()
        for relative in manifest["files"]
    }
    return payloads


def _validate_authorities() -> None:
    if sha256(CONTRACT) != CONTRACT_SHA256:
        raise BuildError("distributed-v2 contract identity differs")
    contract = load(CONTRACT, "distributed-v2 contract")
    job = (contract.get("scenarios") or {}).get("job") or {}
    if (
        contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("postgresql_mutation_prohibited") is not True
        or job.get("scenario") != "job_memory"
        or (job.get("host") or {}).get("hostname") != "node02"
        or (job.get("storage") or {}).get("device") != "/dev/nvme1n1"
        or (job.get("storage") or {}).get("expected_live_major_minor")
        != "259:3"
        or (job.get("storage") or {}).get("full_physical_copy_required")
        is not True
        or (job.get("storage") or {}).get("overlay_or_reflink_prohibited")
        is not True
    ):
        raise BuildError("JOB distributed-v2 authority differs")
    if sha256(RECONCILIATION) != RECONCILIATION_SHA256:
        raise BuildError("JOB reconciliation identity differs")
    reconciliation = load(RECONCILIATION, "JOB reconciliation")
    if (
        reconciliation.get("status")
        != "RECOVERY_AUTHORITY_RECONCILED_NO_RERUN"
        or reconciliation.get("rerun_authorized") is not False
        or reconciliation.get("accepted_rounds_credit") != 0
        or reconciliation.get("replayed_candidate_ranks") != [1, 2, 3, 4, 5]
    ):
        raise BuildError("JOB reconciliation semantics differ")
    if sha256(PYTHON_MANIFEST) != PYTHON_MANIFEST_SHA256:
        raise BuildError("Python portable manifest identity differs")
    invalidation = load(
        INVALIDATED_BUNDLE_AUTHORITY, "invalidated JOB bundle authority"
    )
    if (
        invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidation.get("bundle_id")
        != "b7563277928bc7cc2105084ecdad8c2b426691f2c630c66fdbc05be9ac85e1b9"
        or invalidation.get("formal_launch_authorized") is not False
        or invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("invalidated JOB bundle authority differs")
    bd316_invalidation = load(
        INVALIDATED_BD316_BUNDLE_AUTHORITY,
        "bd316 invalidated JOB bundle authority",
    )
    if (
        bd316_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or bd316_invalidation.get("bundle_id")
        != "bd316145e38bfc3c50230a02cd06cfb39a568cd28c5b3b4e2a67c691796a01d3"
        or bd316_invalidation.get("bundle_manifest_sha256")
        != "95780401cd2d1838e38a0e9525efd5862e4f4978daee85ed5b709faf1157f08e"
        or bd316_invalidation.get("formal_launch_authorized") is not False
        or bd316_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("bd316 invalidated JOB bundle authority differs")
    current_invalidation = load(
        INVALIDATED_82E_BUNDLE_AUTHORITY,
        "82e invalidated JOB bundle authority",
    )
    if (
        current_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or current_invalidation.get("bundle_id")
        != "82e2867d1f1081c6a391a728ebe4d5bc915959e7a4c8353389391006f62a2f52"
        or current_invalidation.get("formal_launch_authorized") is not False
        or current_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("82e invalidated JOB bundle authority differs")
    logical_socket_invalidation = load(
        INVALIDATED_4FD_BUNDLE_AUTHORITY,
        "4fd logical-socket invalidated JOB bundle authority",
    )
    if (
        logical_socket_invalidation.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or logical_socket_invalidation.get("bundle_id")
        != "4fd514932738044f846007484ca9969f946b96284ddb52f84e441a08b6efd5ec"
        or logical_socket_invalidation.get("bundle_manifest_sha256")
        != "8ee2816ac52b6d37aa7f1ab9d7420d57f6eb0db3a6ecfe5222a1562877a506e1"
        or logical_socket_invalidation.get("database_or_client_started")
        is not False
        or logical_socket_invalidation.get("formal_launch_authorized")
        is not False
        or logical_socket_invalidation.get("accepted_round_authorized")
        is not False
    ):
        raise BuildError("4fd logical-socket invalidation differs")
    target_permission_invalidation = load(
        INVALIDATED_2D237_BUNDLE_AUTHORITY,
        "2d237 target-permission invalidated JOB bundle authority",
    )
    if (
        target_permission_invalidation.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or target_permission_invalidation.get("bundle_id")
        != "2d237b84b1fb699098214af959fc975bcd8cfdfb979e177f2f8602f0c1e9045a"
        or target_permission_invalidation.get("database_or_client_started")
        is not False
        or target_permission_invalidation.get("formal_launch_authorized")
        is not False
        or target_permission_invalidation.get("accepted_round_authorized")
        is not False
    ):
        raise BuildError("2d237 target-permission invalidation differs")
    target_closure_invalidation = load(
        INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY,
        "4d5 target-closure invalidated JOB bundle authority",
    )
    if (
        target_closure_invalidation.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or target_closure_invalidation.get("bundle_id")
        != "4d5c50aea338b42d34b5e5adf84f12532a1a1b7059091eeb57d04df149c41cc8"
        or target_closure_invalidation.get("bundle_manifest_sha256")
        != "ece0ed14439be9e659b2aa376670cc8da3bcd42968dd6be3f43d6e11a260f4fb"
        or target_closure_invalidation.get("failure_stage")
        != "post_physical_copy_pre_database_target_closure_audit"
        or target_closure_invalidation.get("database_or_client_started")
        is not False
        or target_closure_invalidation.get("database_pid") != 0
        or target_closure_invalidation.get("client_pid") != 0
        or target_closure_invalidation.get("accepted_rounds_credit") != 0
        or target_closure_invalidation.get("formal_launch_authorized")
        is not False
        or target_closure_invalidation.get("accepted_round_authorized")
        is not False
    ):
        raise BuildError("4d5 target-closure invalidation differs")
    content_identity_invalidation = load(
        INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY,
        "449 content-identity invalidated JOB bundle authority",
    )
    if (
        content_identity_invalidation.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or content_identity_invalidation.get("bundle_id")
        != "449cd22df33192958ecb98c96b2fbbeba0f69a281115e7e046bfdc48ed2c2bc1"
        or content_identity_invalidation.get("bundle_manifest_sha256")
        != "3523ffc3f28f9060352f820d28046fae796128bb547fb9a2a3023e51c6ce666e"
        or content_identity_invalidation.get("failure_stage")
        != "post_physical_copy_content_identity_before_database"
        or content_identity_invalidation.get("database_or_client_started")
        is not False
        or content_identity_invalidation.get("database_pid") != 0
        or content_identity_invalidation.get("client_pid") != 0
        or content_identity_invalidation.get("accepted_rounds_credit") != 0
        or content_identity_invalidation.get("formal_launch_authorized")
        is not False
        or content_identity_invalidation.get("accepted_round_authorized")
        is not False
    ):
        raise BuildError("449 content-identity invalidation differs")
    afe_normalization_invalidation = load(
        INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY,
        "afe normalization-contract invalidated JOB bundle authority",
    )
    if (
        afe_normalization_invalidation.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or afe_normalization_invalidation.get("bundle_id")
        != "afe70067652f8ce5babaabe6584a3d5196d526af0766d690567fdd3059fcb5c8"
        or afe_normalization_invalidation.get("bundle_manifest_sha256")
        != "9d0562ea639455cf67a9b96e0a2fc5592d3eb48232cb58d0111a9d61919ed052"
        or afe_normalization_invalidation.get("failure_stage")
        != "post_physical_copy_nonsharing_evidence_contract_before_database"
        or afe_normalization_invalidation.get("database_or_client_started")
        is not False
        or afe_normalization_invalidation.get("database_pid") != 0
        or afe_normalization_invalidation.get("client_pid") != 0
        or afe_normalization_invalidation.get("accepted_rounds_credit") != 0
        or afe_normalization_invalidation.get("formal_launch_authorized")
        is not False
        or afe_normalization_invalidation.get("accepted_round_authorized")
        is not False
    ):
        raise BuildError("afe normalization-contract invalidation differs")
    f058_invalidation = load(
        INVALIDATED_F058_SQL_PERCENT_AUTHORITY,
        "f058 SQL-percent invalidated JOB bundle authority",
    )
    if (
        f058_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or f058_invalidation.get("bundle_id")
        != "f0583a934806dc2125ea402bf0e85e30b0e637a55e424d3d1ea1580875c3774c"
        or f058_invalidation.get("bundle_manifest_sha256")
        != "2ca38c0e241cb22c5c0ec7deb3484ef0d0f18cb39473a41846a664a1a78c3846"
        or f058_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t195053z-07e47cc55f09d6a807e4eb07"
        or f058_invalidation.get("accepted_rounds_credit") != 0
        or f058_invalidation.get("formal_launch_authorized") is not False
        or f058_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("f058 SQL-percent invalidation differs")
    eightf_invalidation = load(
        INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY,
        "8f client-credentials invalidated JOB bundle authority",
    )
    if (
        eightf_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or eightf_invalidation.get("bundle_id")
        != "8f55631aaea99b6851c460822fe5099b8762f04183f71f0fdc37aae7b0d173f6"
        or eightf_invalidation.get("bundle_manifest_sha256")
        != "057b56c3b830ccba0c63d8f7558806e9eca42eb6e62ca69d9786cb406c5dde1e"
        or eightf_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t201329z-6425f9a8eb0ec2662cf111ba"
        or eightf_invalidation.get("database_pid") != 6088
        or eightf_invalidation.get("client_credentials_readable") is not False
        or eightf_invalidation.get("workload_started") is not False
        or eightf_invalidation.get("formal_r0_started") is not False
        or eightf_invalidation.get("accepted_rounds_credit") != 0
        or eightf_invalidation.get("formal_launch_authorized") is not False
        or eightf_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("8f client-credentials invalidation differs")
    fouree_invalidation = load(
        INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY,
        "4ee client-managed-mount invalidated JOB bundle authority",
    )
    if (
        fouree_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or fouree_invalidation.get("bundle_id")
        != "4ee58d435e830333b56f5a91876303a9f65d176a6ce0a760dbb51cd078112e7d"
        or fouree_invalidation.get("bundle_manifest_sha256")
        != "275e7a8e4af4663ffe110738953cf040b9d4ebf165d55e6532aa1db50ff597ab"
        or fouree_invalidation.get("failed_attempt_token")
        != "job-v2-20260731t100000z-4ee58d435e830333b56f5a"
        or fouree_invalidation.get("failure_stage")
        != "bootstrap_live_readback_client_mount_validation"
        or fouree_invalidation.get("workload_started") is not False
        or fouree_invalidation.get("formal_r0_started") is not False
        or fouree_invalidation.get("accepted_rounds_credit") != 0
        or fouree_invalidation.get("formal_launch_authorized") is not False
        or fouree_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("4ee client-managed-mount invalidation differs")
    f1c705_invalidation = load(
        INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY,
        "f1c705 anonymous-volume invalidated JOB bundle authority",
    )
    if (
        f1c705_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or f1c705_invalidation.get("bundle_id")
        != "f1c7051a70019ca6534a6687754349c6633e8f9ef1a43ac0a23564372207ccd6"
        or f1c705_invalidation.get("bundle_manifest_sha256")
        != "b4eaab902f18aed7b78df61a9c375391640314e2448d2c9d577841f4d6c22d27"
        or f1c705_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t205603z-f1c7051a70019ca6534a668"
        or f1c705_invalidation.get("failure_stage")
        != "bootstrap_live_readback_client_mount_validation"
        or f1c705_invalidation.get("workload_started") is not False
        or f1c705_invalidation.get("formal_r0_started") is not False
        or f1c705_invalidation.get("accepted_rounds_credit") != 0
        or f1c705_invalidation.get("formal_launch_authorized") is not False
        or f1c705_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("f1c705 anonymous-volume invalidation differs")
    sevene08_invalidation = load(
        INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY,
        "7e08bd bridge-identity invalidated JOB bundle authority",
    )
    if (
        sevene08_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or sevene08_invalidation.get("bundle_id")
        != "7e08bd2220b9f2c196163b71ba2d114d191b5c80377c2cd35e4778b7860a9d2f"
        or sevene08_invalidation.get("bundle_manifest_sha256")
        != "b34b06d71ac479e73891fe353ef467a1d7d0eaa155f1a02e985f5e0590c31afb"
        or sevene08_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t211048z-7e08bd2220b9f2c196163b71"
        or sevene08_invalidation.get("failure_stage")
        != "bootstrap_collect_only_database_counter_bridge_identity"
        or sevene08_invalidation.get("workload_started") is not False
        or sevene08_invalidation.get("formal_r0_started") is not False
        or sevene08_invalidation.get("accepted_rounds_credit") != 0
        or sevene08_invalidation.get("formal_launch_authorized") is not False
        or sevene08_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("7e08bd bridge-identity invalidation differs")
    twofee_invalidation = load(
        INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY,
        "2fe2ae inspect-hash invalidated JOB bundle authority",
    )
    if (
        twofee_invalidation.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or twofee_invalidation.get("bundle_id")
        != "2fe2ae0c618eb022109cefc4c95d0d424e29a7062cd47f14241c975f1f828042"
        or twofee_invalidation.get("bundle_manifest_sha256")
        != "e3b5c4b7af1181479fb6357e904332cf3f82c8a3f70d0098e6ff6131171c7458"
        or twofee_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t212752z-2fe2ae0c618eb022109cefc4"
        or twofee_invalidation.get("failure_stage")
        != "bootstrap_collect_only_database_counter_bridge_identity"
        or twofee_invalidation.get("workload_started") is not False
        or twofee_invalidation.get("formal_r0_started") is not False
        or twofee_invalidation.get("accepted_rounds_credit") != 0
        or twofee_invalidation.get("formal_launch_authorized") is not False
        or twofee_invalidation.get("accepted_round_authorized") is not False
    ):
        raise BuildError("2fe2ae inspect-hash invalidation differs")
    sql_native_invalidation = load(
        INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY,
        "0239 SQL-native contract invalidated JOB bundle authority",
    )
    if (
        sql_native_invalidation.get("status")
        != "INVALIDATED_DO_NOT_RERUN"
        or sql_native_invalidation.get("bundle_id")
        != "0239f5e17923776f23cd54b8c9c77e055d9a7470e600b32dc00d234c1ca40d46"
        or sql_native_invalidation.get("bundle_manifest_sha256")
        != "e0ce7927bfa44717de7c4ecea78ff1d31280486cc85bbb464a75ba9d80de6b76"
        or sql_native_invalidation.get("failed_attempt_token")
        != "job-v2-20260730t225433z-00998b59d9e1c5449e4f0541"
        or sql_native_invalidation.get("failure_stage")
        != "qualification_solo_workload_metric_parse"
        or sql_native_invalidation.get("accepted_rounds_credit") != 0
        or sql_native_invalidation.get("formal_r0_authorized") is not False
        or sql_native_invalidation.get("formal_launch_authorized") is not False
    ):
        raise BuildError("0239 SQL-native contract invalidation differs")
    cgroup_v1_oom_invalidation = load(
        INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY,
        "0c7a cgroup-v1 OOM-readback invalidated JOB bundle authority",
    )
    if (
        cgroup_v1_oom_invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or cgroup_v1_oom_invalidation.get("bundle_id")
        != "0c7a08ac60ab074dcab1abd088a8511621926fb2c701cd3fed4d4220e0444b2f"
        or cgroup_v1_oom_invalidation.get("bundle_manifest_sha256")
        != "37c6bea764118b2619d5285c0337055fe868858c1a87cdb03eccf2176be53d0e"
        or cgroup_v1_oom_invalidation.get("failure_stage")
        != "qualification_solo_pre_workload_cgroup_v1_memory_oom_readback"
        or cgroup_v1_oom_invalidation.get("workload_started") is not False
        or cgroup_v1_oom_invalidation.get("accepted_rounds_credit") != 0
        or cgroup_v1_oom_invalidation.get("formal_r0_authorized") is not False
        or cgroup_v1_oom_invalidation.get("formal_launch_authorized") is not False
    ):
        raise BuildError("0c7a cgroup-v1 OOM-readback invalidation differs")
    no_psi_invalidation = load(
        INVALIDATED_350A_NO_PSI_AUTHORITY,
        "350a no-PSI invalidated JOB bundle authority",
    )
    if (
        no_psi_invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or no_psi_invalidation.get("bundle_id")
        != "350a2b2a800a7fd4d9af032de980a4a537e51683890258587a280addd17a3ac9"
        or no_psi_invalidation.get("bundle_manifest_sha256")
        != "24c329fc8040d9d87011705b53c09441d7b344e2cd489bcc1a93f7f1e1bfc86c"
        or no_psi_invalidation.get("failure_stage")
        != "qualification_solo_pre_workload_host_cpu_psi_readback"
        or no_psi_invalidation.get("observed", {}).get("workload_started") is not False
        or no_psi_invalidation.get("accepted_rounds_credit") != 0
        or no_psi_invalidation.get("formal_r0_authorized") is not False
        or no_psi_invalidation.get("formal_launch_authorized") is not False
    ):
        raise BuildError("350a no-PSI invalidation differs")
    collector_import_invalidation = load(
        INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY,
        "6bcd collector-import invalidated JOB bundle authority",
    )
    if (
        collector_import_invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or collector_import_invalidation.get("bundle_id")
        != "6bcd66a5011081c31d8adc12372304f2ff93d511824114be06dc617ea1a6fa9f"
        or collector_import_invalidation.get("bundle_manifest_sha256")
        != "d668954a5b83b2f9e3351238660e7b49b6333425e21aeb0792541b32d0bf3a5b"
        or collector_import_invalidation.get("failure_stage")
        != "bootstrap_collect_only_collector_import_before_workload"
        or collector_import_invalidation.get("workload_started") is not False
        or collector_import_invalidation.get("accepted_rounds_credit") != 0
        or collector_import_invalidation.get("formal_r0_authorized") is not False
        or collector_import_invalidation.get("formal_launch_authorized") is not False
    ):
        raise BuildError("6bcd collector-import invalidation differs")
    root_mount_invalidation = load(
        INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY,
        "6bf root-mount-attribution invalidated JOB bundle authority",
    )
    if (
        root_mount_invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or root_mount_invalidation.get("bundle_id")
        != "6bf0d0a613ca8e063ccfd215921aba92907a145ceb73ad1e5d11bc8cda4c48d0"
        or root_mount_invalidation.get("bundle_manifest_sha256")
        != "e9f12315f2b951cb511fa65c50e828991baf2531f44a86be191c48e12287ce53"
        or root_mount_invalidation.get("attempt_token")
        != "job-v2-20260731t020855z-c2afc1dcc6525191e4173dfb"
        or root_mount_invalidation.get("failure_stage")
        != "qualification_solo_running_workload_root_owned_docker_mount_device_attribution"
        or root_mount_invalidation.get("workload_started") is not True
        or root_mount_invalidation.get("workload_completed") is not True
        or root_mount_invalidation.get("qualification_completed") is not False
        or root_mount_invalidation.get("accepted_rounds_credit") != 0
        or root_mount_invalidation.get("formal_r0_authorized") is not False
        or root_mount_invalidation.get("formal_launch_authorized") is not False
        or root_mount_invalidation.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("6bf root-mount-attribution invalidation differs")
    a50d_invalidation = load(
        INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY,
        "a50d measurement-host-tmpfs invalidation",
    )
    if (
        a50d_invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or a50d_invalidation.get("bundle_id")
        != "a50dd3633babccf609cf24c548743f3a8c7c5084f5745e7fdb508a051865bdfb"
        or a50d_invalidation.get("bundle_manifest_sha256")
        != "bc21b3f733f41d2c8ec136374432c6396ae4226e5e4a46622f8485926099c6bf"
        or a50d_invalidation.get("accepted_rounds_credit") != 0
        or a50d_invalidation.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("a50d measurement-host-tmpfs invalidation differs")
    invalidated_79b2 = load(
        INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY,
        "79b2 measurement-bind-ordering invalidation",
    )
    if (
        invalidated_79b2.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalidated_79b2.get("bundle_id")
        != "79b2b0018cc4367b27515907a961a830aa9b871e8a498614ee8b541f69182bbf"
        or invalidated_79b2.get("bundle_manifest_sha256")
        != "0bbcb5dd1811b440b17b49a99f7b4ce448098ec91122bce6883b42eb07aefb5f"
        or invalidated_79b2.get("accepted_rounds_credit") != 0
        or invalidated_79b2.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("79b2 measurement-bind-ordering invalidation differs")


def _source_payloads() -> dict[str, bytes]:
    payloads = _read_base_source()
    overlays = {
        "shell/mysql_job_distributed_v2_authority_tmpfs_v2.py": AUTHORITY,
        "shell/mysql_job_distributed_v2_adopted_services.py":
            ADOPTED_SERVICES,
        "shell/mysql_job_distributed_v2_rolling_monitor.py":
            ROLLING_MONITOR,
        "shell/formal_job_multiquery_workload.py": FORMAL_JOB_WORKLOAD,
        "shell/mysql_job_physical_copy_v1.py": PHYSICAL_COPY,
        "shell/manual_mysql_sgd_r0_campaign.py": MANUAL_CAMPAIGN,
        "shell/mysql_job_distributed_live_readback_qualification_v2.py": COLLECTOR,
        "shell/mysql_job_host_root_inventory_v1.py": ROOT_DOCKER_MOUNT_HELPER,
        "shell/mysql_tpch_client_immutable_identity_v1.py": CLIENT_IMMUTABLE_IDENTITY,
        "shell/mysql_distributed_workload_metric_adapter_v1.py": NATIVE_ADAPTER,
        "shell/mysql_sql_debug_metric_adapter_v1.py": SQL_ADAPTER,
        "shell/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py":
            BOOTSTRAP_SPEC_RENDERER,
        "shell/render_mysql_job_v32_qualification_solo_tmpfs_v2.py":
            QUALIFICATION_SPEC_RENDERER,
        "shell/runtime_tree_content_identity_v3.py": RUNTIME_HELPER,
        "shell/build_mysql_job_distributed_v2_successor.py": BUILDER,
        (
            "authority/"
            "mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
        ): CONTRACT,
        "authority/formal_replay_authority_reconciliation_v1.json":
            RECONCILIATION,
        "authority/mysql_algorithm_c_formal10_contract_20260729.json":
            FORMAL10_CONTRACT,
        "runtime/python_runtime_tree_manifest_v3.json": PYTHON_MANIFEST,
        "authority/mysql_job_v32_b7563277_invalidation_20260731.json":
            INVALIDATED_BUNDLE_AUTHORITY,
        "authority/mysql_job_v32_bd316145_invalidation_20260731.json":
            INVALIDATED_BD316_BUNDLE_AUTHORITY,
        "authority/mysql_job_v32_82e2867d_invalidation_20260731.json":
            INVALIDATED_82E_BUNDLE_AUTHORITY,
        "authority/mysql_job_v32_4fd514932_invalidation_20260731.json":
            INVALIDATED_4FD_BUNDLE_AUTHORITY,
        "authority/mysql_job_v32_2d237b84_invalidation_20260731.json":
            INVALIDATED_2D237_BUNDLE_AUTHORITY,
        "authority/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json":
            INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY,
        "authority/mysql_job_v32_449cd22_content_identity_invalidation_20260731.json":
            INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY,
        "authority/mysql_job_v32_afe700_normalization_contract_invalidation_20260731.json":
            INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY,
        "authority/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json":
            INVALIDATED_F058_SQL_PERCENT_AUTHORITY,
        "authority/mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json":
            INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY,
        "authority/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json":
            INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY,
        "authority/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json":
            INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY,
        "authority/mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json":
            INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY,
        "authority/mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json":
            INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY,
        "authority/mysql_job_v32_a0f3e7_client_unknown_user_invalidation_20260731.json":
            INVALIDATED_A0F3E7_CLIENT_UNKNOWN_USER_AUTHORITY,
        "authority/mysql_job_v32_1093df_collect_only_counter_coverage_invalidation_20260731.json":
            INVALIDATED_1093DF_COUNTER_COVERAGE_AUTHORITY,
        "authority/mysql_job_v32_0626b1_show_status_noninteger_invalidation_20260731.json":
            INVALIDATED_0626B1_SHOW_STATUS_NONINTEGER_AUTHORITY,
        "authority/mysql_job_v32_0239_sql_query_native_contract_invalidation_20260731.json":
            INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY,
        "authority/mysql_job_v32_0c7a_cgroup_v1_oom_readback_invalidation_20260731.json":
            INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY,
        "authority/mysql_job_v32_350a_no_psi_invalidation_20260731.json":
            INVALIDATED_350A_NO_PSI_AUTHORITY,
        "authority/mysql_job_v32_6bcd_collector_import_invalidation_20260731.json":
            INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY,
        "authority/mysql_job_v32_6bf_qualification_root_mount_attribution_invalidation_20260731.json":
            INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY,
        "authority/mysql_job_v32_a50d_measurement_host_tmpfs_invalidation_20260731.json":
            INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY,
        "authority/mysql_job_v32_79b2_measurement_bind_ordering_invalidation_20260731.json":
            INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY,
        "shell/client_mysql_datadir_mask/.sealed-empty":
            ROOT / "shell/client_mysql_datadir_mask/.sealed-empty",
    }
    for relative, path in overlays.items():
        payloads[relative] = path.read_bytes()
    for relative in payloads:
        _safe_relative(relative)
    return payloads


def _write_payloads(stage: Path, payloads: Mapping[str, bytes]) -> None:
    for relative, payload in sorted(payloads.items()):
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(
    parent: Path,
    target_name: str,
    payloads: Mapping[str, bytes],
    manifest_name: str,
    manifest_payload: bytes,
    expected_files: Mapping[str, str],
    label: str,
) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / target_name
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise BuildError(f"{label} target exists but is unsafe")
        manifest = load(target / manifest_name, f"{label} manifest")
        if manifest_payload != (target / manifest_name).read_bytes():
            raise BuildError(f"{label} existing manifest differs")
        verify_file_map(target, expected_files, label)
        return target
    stage = parent / (
        f".stage-{target_name}-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        stage.mkdir(mode=0o700)
        _write_payloads(stage, payloads)
        with (stage / manifest_name).open("xb") as stream:
            stream.write(manifest_payload)
            stream.flush()
            os.fsync(stream.fileno())
        if manifest_name == "bundle_manifest.json":
            sidecar = stage / "bundle_manifest.json.sha256"
            sidecar.write_text(
                f"{sha_bytes(manifest_payload)}  bundle_manifest.json\n",
                encoding="ascii",
            )
            with sidecar.open("rb") as stream:
                os.fsync(stream.fileno())
        verify_file_map(stage, expected_files, f"{label} private stage")
        _fsync_directory(stage)
        os.rename(stage, target)
        _fsync_directory(parent)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return target


def build_source() -> tuple[Path, dict[str, Any]]:
    payloads = _source_payloads()
    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(payloads.items())
    }
    identity = {
        "schema_version": "mysql_job_distributed_v2_source_manifest_v1",
        "status": "SEALED_QUALIFICATION_SOURCE_NOT_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "job_memory",
        "base_source_bundle_id":
            BASE_SOURCE.name.removeprefix("source-"),
        "base_source_manifest_sha256": BASE_SOURCE_MANIFEST_SHA256,
        "distributed_v2_contract_sha256": CONTRACT_SHA256,
        "reconciliation_sha256": RECONCILIATION_SHA256,
        "collector_sha256": sha256(COLLECTOR),
        "host_root_mount_inventory_helper_sha256": sha256(
            ROOT_DOCKER_MOUNT_HELPER
        ),
        "native_metric_adapter_sha256": sha256(NATIVE_ADAPTER),
        "sql_metric_adapter_sha256": sha256(SQL_ADAPTER),
        "bootstrap_handoff_authority_sha256": sha256(AUTHORITY),
        "adopted_services_sha256": sha256(ADOPTED_SERVICES),
        "rolling_monitor_sha256": sha256(ROLLING_MONITOR),
        "formal_job_workload_sha256": sha256(FORMAL_JOB_WORKLOAD),
        "physical_copy_sha256": sha256(PHYSICAL_COPY),
        "manual_campaign_sha256": sha256(MANUAL_CAMPAIGN),
        "bootstrap_spec_renderer_sha256": sha256(BOOTSTRAP_SPEC_RENDERER),
        "qualification_spec_renderer_sha256":
            sha256(QUALIFICATION_SPEC_RENDERER),
        "invalidated_predecessor_authority_sha256":
            sha256(INVALIDATED_BUNDLE_AUTHORITY),
        "invalidated_bd316_bundle_authority_sha256":
            sha256(INVALIDATED_BD316_BUNDLE_AUTHORITY),
        "invalidated_82e_bundle_authority_sha256":
            sha256(INVALIDATED_82E_BUNDLE_AUTHORITY),
        "invalidated_4fd_bundle_authority_sha256":
            sha256(INVALIDATED_4FD_BUNDLE_AUTHORITY),
        "invalidated_2d237_bundle_authority_sha256":
            sha256(INVALIDATED_2D237_BUNDLE_AUTHORITY),
        "invalidated_4d5_target_closure_authority_sha256":
            sha256(INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY),
        "invalidated_449_content_identity_authority_sha256":
            sha256(INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY),
        "invalidated_afe_normalization_contract_authority_sha256":
            sha256(INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY),
        "invalidated_0c7a_cgroup_v1_oom_readback_authority_sha256":
            sha256(INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY),
        "invalidated_350a_no_psi_authority_sha256":
            sha256(INVALIDATED_350A_NO_PSI_AUTHORITY),
        "invalidated_6bcd_collector_import_authority_sha256":
            sha256(INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY),
        "invalidated_6bf_root_mount_attribution_authority_sha256":
            sha256(INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY),
        "invalidated_a50d_measurement_host_tmpfs_authority_sha256":
            sha256(INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY),
        "invalidated_79b2_measurement_bind_ordering_authority_sha256":
            sha256(INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY),
        "builder_sha256": sha256(BUILDER),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": files,
    }
    source_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": source_id}
    target = _publish(
        SOURCE_ROOT,
        "source-" + source_id,
        payloads,
        "source_manifest.json",
        canonical(manifest),
        files,
        "JOB v2 source",
    )
    return target, manifest


def _runtime_contract() -> dict[str, Any]:
    contract = load(CONTRACT, "distributed-v2 contract")
    job = contract["scenarios"]["job"]
    return {
        "schema_version": "mysql_algorithm_c_distributed_runtime_contract_v2",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "job_memory",
        "host": job["host"],
        "storage": job["storage"],
        "resource_envelope": job["resource_envelope"],
        "client_resource_envelope": job["client_resource_envelope"],
        "measurement_host_bind": {
            "schema_version": "mysql_job_attempt_private_host_tmpfs_bind_v1",
            "host_path_rule": "/dev/shm/ra_mysql_job_v2/<attempt_token>",
            "container_destination": "/measurement",
            "filesystem": "tmpfs",
            "size_bytes": 1073741824,
            "uid": 1008,
            "gid": 1008,
            "mode": "0700",
            "mount_options": ["rw", "nosuid", "nodev", "noexec"],
            "client_docker_tmpfs_prohibited": True,
            "bootstrap_collector_renderer_same_host_bind_required": True,
        },
        "toolchain": {
            "java_required": False,
            "python_required": True,
            "python_runtime_root": PYTHON_ROOT,
            "python_runtime": PYTHON_ROOT + "/bin/python3.11",
            "python_binary_relative": "bin/python3.11",
            "python_binary_sha256": PYTHON_BINARY_SHA256,
            "python_tree_manifest":
                "runtime/python_runtime_tree_manifest_v3.json",
            "python_tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
            "python_tree_content_identity_algorithm":
                "sha256_canonical_relative_tree_entries_v3",
            "python_version_expected": "Python 3.11.11",
        },
        "bundle_remote_path_rule": RUNTIME_ROOT + "/bundles/<bundle_id>",
        "data_plane_launch_authority": "main_agent_only",
        "bootstrap_entrypoint":
            "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "same_live_production_services":
            "qualification/mysql_job_distributed_v2_adopted_services.py",
        "rolling_full_host_monitor":
            "qualification/mysql_job_distributed_v2_rolling_monitor.py",
        "formal_job_workload":
            "qualification/formal_job_multiquery_workload.py",
        "physical_copy_entrypoint":
            "qualification/mysql_job_physical_copy_v1.py",
        "database_counter_transport": {
            "schema_version":
                "mysql_algorithm_c_database_counter_transport_v1",
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "mysql_binary_in_client": "/usr/bin/mysql",
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "docker_socket_mount_prohibited": True,
            "target_datadir_mount_prohibited": True,
        },
        "collector_and_qualification_entrypoint":
            "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "collector_dependency_closure": {
            "root_helper": "mysql_tpch_client_immutable_identity_v1.py",
            "qualification_helper": "qualification/mysql_tpch_client_immutable_identity_v1.py",
            "helper_sha256": sha256(CLIENT_IMMUTABLE_IDENTITY),
            "root_and_qualification_bytes_identical": True,
            "empty_pythonpath_subprocess_import_smoke_required": True,
            "sealed_entrypoint_pycache_prohibited": True,
            "host_root_mount_inventory_helper": {
                "required": True,
                "helper_relative": "qualification/mysql_job_host_root_inventory_v1.py",
                "helper_sha256": sha256(ROOT_DOCKER_MOUNT_HELPER),
                "invocation": [
                    "sudo", "-n", "manifest_bound_python", "-B",
                    "sealed_helper", "--expected-helper-sha256",
                ],
                "read_only_actions": [
                    "docker ps --no-trunc --quiet", "docker inspect",
                    "realpath", "stat",
                ],
                "postgresql_read_only_inventory_allowed": True,
                "mutation_prohibited": True,
                "unprivileged_inspect_exact_crosscheck_required": True,
            },
        },
        "bootstrap_spec_renderer":
            "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
        "qualification_spec_renderer":
            "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py",
        "workload_metric_adapter":
            "qualification/mysql_sql_debug_metric_adapter_v1.py",
        "qualification_policy": {
            "duration_seconds": 180,
            "sample_period_seconds": 1,
            "minimum_samples_per_stream": 181,
            "rolling_full_host_inventory_required": True,
            "foreign_target_device_writer_bytes_max": 0,
            "database_counter_missing_or_timeout_result": "FAIL",
            "capture_r0_formal_accounting_prohibited": True,
            "cpu_contention_policy_version": "mysql_algorithm_c_cpu_contention_signal_v2",
            "cpu_contention_psi_preferred": True,
            "no_psi_cpu_fallback": "proc_stat_busy_steal_iowait_plus_loadavg_runnable_total_v1",
            "no_psi_memory_fallback": "cgroup_memory_occupancy_swap_oom_v1",
            "contention_sample_counter_monotonicity_required": True,
        },
        "same_live_planner_handoff": {
            "qualification_to_fresh_r0_to_debug_to_formal10_required": True,
            "new_campaign_or_planner_container_prohibited": True,
            "exact_container_id_pid_datadir_inspect_hash_required": True,
            "old_top5_rerun_authorized": False,
            "old_reconciliation_sha256": RECONCILIATION_SHA256,
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def _bundle_payloads(
    source: Path, source_manifest: Mapping[str, Any]
) -> dict[str, bytes]:
    if sha256(PREDECESSOR / "bundle_manifest.json") != PREDECESSOR_MANIFEST_SHA256:
        raise BuildError("JOB v30 predecessor manifest identity differs")
    predecessor_manifest = load(
        PREDECESSOR / "bundle_manifest.json", "JOB v30 predecessor manifest"
    )
    verify_file_map(
        PREDECESSOR,
        predecessor_manifest.get("files") or {},
        "JOB v30 predecessor",
    )
    payloads: dict[str, bytes] = {
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py":
            AUTHORITY.read_bytes(),
        "qualification/mysql_job_distributed_v2_adopted_services.py":
            ADOPTED_SERVICES.read_bytes(),
        "qualification/mysql_job_distributed_v2_rolling_monitor.py":
            ROLLING_MONITOR.read_bytes(),
        "qualification/formal_job_multiquery_workload.py":
            FORMAL_JOB_WORKLOAD.read_bytes(),
        "qualification/mysql_job_physical_copy_v1.py":
            PHYSICAL_COPY.read_bytes(),
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py":
            COLLECTOR.read_bytes(),
        "qualification/mysql_job_host_root_inventory_v1.py":
            ROOT_DOCKER_MOUNT_HELPER.read_bytes(),
        "mysql_tpch_client_immutable_identity_v1.py":
            CLIENT_IMMUTABLE_IDENTITY.read_bytes(),
        "qualification/mysql_tpch_client_immutable_identity_v1.py":
            CLIENT_IMMUTABLE_IDENTITY.read_bytes(),
        "qualification/mysql_distributed_workload_metric_adapter_v1.py":
            NATIVE_ADAPTER.read_bytes(),
        "qualification/mysql_sql_debug_metric_adapter_v1.py":
            SQL_ADAPTER.read_bytes(),
        "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py":
            BOOTSTRAP_SPEC_RENDERER.read_bytes(),
        "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py":
            QUALIFICATION_SPEC_RENDERER.read_bytes(),
        "qualification/runtime_tree_content_identity_v3.py":
            RUNTIME_HELPER.read_bytes(),
        "runtime/python_runtime_tree_manifest_v3.json":
            PYTHON_MANIFEST.read_bytes(),
        (
            "authority/"
            "mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
        ): CONTRACT.read_bytes(),
        "authority/formal_replay_authority_reconciliation_v1.json":
            RECONCILIATION.read_bytes(),
        "authority/mysql_algorithm_c_formal10_contract_20260729.json":
            FORMAL10_CONTRACT.read_bytes(),
        "authority/mysql_job_v32_b7563277_invalidation_20260731.json":
            INVALIDATED_BUNDLE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_bd316145_invalidation_20260731.json":
            INVALIDATED_BD316_BUNDLE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_82e2867d_invalidation_20260731.json":
            INVALIDATED_82E_BUNDLE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_4fd514932_invalidation_20260731.json":
            INVALIDATED_4FD_BUNDLE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_2d237b84_invalidation_20260731.json":
            INVALIDATED_2D237_BUNDLE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json":
            INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_449cd22_content_identity_invalidation_20260731.json":
            INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_afe700_normalization_contract_invalidation_20260731.json":
            INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json":
            INVALIDATED_F058_SQL_PERCENT_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json":
            INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json":
            INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json":
            INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json":
            INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json":
            INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_a0f3e7_client_unknown_user_invalidation_20260731.json":
            INVALIDATED_A0F3E7_CLIENT_UNKNOWN_USER_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_1093df_collect_only_counter_coverage_invalidation_20260731.json":
            INVALIDATED_1093DF_COUNTER_COVERAGE_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_0626b1_show_status_noninteger_invalidation_20260731.json":
            INVALIDATED_0626B1_SHOW_STATUS_NONINTEGER_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_0239_sql_query_native_contract_invalidation_20260731.json":
            INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_0c7a_cgroup_v1_oom_readback_invalidation_20260731.json":
            INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_a50d_measurement_host_tmpfs_invalidation_20260731.json":
            INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_79b2_measurement_bind_ordering_invalidation_20260731.json":
            INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_350a_no_psi_invalidation_20260731.json":
            INVALIDATED_350A_NO_PSI_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_6bcd_collector_import_invalidation_20260731.json":
            INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY.read_bytes(),
        "authority/mysql_job_v32_6bf_qualification_root_mount_attribution_invalidation_20260731.json":
            INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY.read_bytes(),
        "qualification/client_mysql_datadir_mask/.sealed-empty":
            b"sealed read-only client volume mask\n",
    }
    sql_root = (
        BASE_SOURCE
        / "config/formal/mysql_sgd_production/workloads/job_v1"
    )
    for name in SQL_NAMES:
        path = sql_root / name
        if path.is_symlink() or not path.is_file():
            raise BuildError(f"JOB sealed SQL is absent: {name}")
        payloads[f"qualification/sql/job/{name}"] = path.read_bytes()
    # The formal adopted-services entrypoint must resolve every dependency
    # from this immutable bundle, rather than the controller checkout.
    for relative in ADOPTED_SERVICE_DEPENDENCIES:
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise BuildError(
                "JOB adopted-service dependency is absent: %s" % relative
            )
        payloads["qualification/" + relative] = path.read_bytes()
    if MANUAL_CAMPAIGN.is_symlink() or not MANUAL_CAMPAIGN.is_file():
        raise BuildError("JOB manual campaign dependency is absent")
    payloads["qualification/shell/manual_mysql_sgd_r0_campaign.py"] = (
        MANUAL_CAMPAIGN.read_bytes()
    )
    for relative in predecessor_manifest["files"]:
        payloads[f"predecessor/{relative}"] = (
            PREDECESSOR / relative
        ).read_bytes()
    payloads["predecessor/bundle_manifest.json"] = (
        PREDECESSOR / "bundle_manifest.json"
    ).read_bytes()
    source_pointer = {
        "schema_version": "mysql_job_distributed_v2_source_pointer_v1",
        "source_directory": str(source),
        "source_bundle_id": source_manifest["bundle_id"],
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
    }
    payloads["source_pointer.json"] = canonical(source_pointer)
    payloads["distributed_runtime_contract.json"] = canonical(
        _runtime_contract()
    )
    config = {
        "schema_version": "mysql_job_distributed_v2_bootstrap_config_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "job_memory",
        "benchmark": "job",
        "parameter_count": 4,
        "candidate_count": 15,
        "enumerate_all_nonempty_subspaces": True,
        "original_gradient_only": True,
        "gradient_formula":
            "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
        "global_candidate_score_sort": True,
        "fixed_top_k_real_replay": 5,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "fingerprint_levels_required": ["L1", "L2", "L3"],
        "fresh_r0_required": True,
        "same_live_planner_handoff_required": True,
        "old_top5_rerun_authorized": False,
        "accepted_rounds": "0/10",
        "bootstrap_authorized": True,
        "qualification_authorized": True,
        "fresh_r0_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
    }
    payloads["config.json"] = canonical(config)
    method = load(
        PREDECESSOR / "experience_table_sgd_method_manifest.json",
        "predecessor method manifest",
    )
    payloads["experience_table_sgd_method_manifest.json"] = canonical(method)
    for relative in payloads:
        _safe_relative(relative)
    return payloads


def build_bundle(
    source: Path, source_manifest: Mapping[str, Any]
) -> tuple[Path, dict[str, Any]]:
    provisional = _bundle_payloads(source, source_manifest)
    # The runtime contract binds the final content-addressed path.  Resolve its
    # self-reference through a two-level identity: the content address covers
    # a placeholder token and the manifest records that frozen path rule.
    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(provisional.items())
    }
    closure = {
        "schema_version": "mysql_job_distributed_v2_bundle_closure_v1",
        "files": files,
        "source_bundle_id": source_manifest["bundle_id"],
        "contract_sha256": CONTRACT_SHA256,
        "collector_sha256": sha256(COLLECTOR),
        "host_root_mount_inventory_helper_sha256": sha256(
            ROOT_DOCKER_MOUNT_HELPER
        ),
        "sql_metric_bridge_collector_sha256": sha256(COLLECTOR),
        "reconciliation_sha256": RECONCILIATION_SHA256,
    }
    provisional["closure.json"] = canonical(closure)
    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(provisional.items())
    }
    identity = {
        "schema_version": "mysql_algorithm_c_distributed_successor_bundle_v1",
        "status":
            "SEALED_DEBUG_BOOTSTRAP_QUALIFICATION_NOT_R0_OR_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "job_memory",
        "source_bundle_id": source_manifest["bundle_id"],
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
        "predecessor_bundle_id": PREDECESSOR.name,
        "predecessor_bundle_manifest_sha256":
            PREDECESSOR_MANIFEST_SHA256,
        "distributed_v2_contract_sha256": CONTRACT_SHA256,
        "reconciliation_sha256": RECONCILIATION_SHA256,
        "collector_sha256": sha256(COLLECTOR),
        "host_root_mount_inventory_helper_sha256": sha256(
            ROOT_DOCKER_MOUNT_HELPER
        ),
        "native_metric_adapter_sha256": sha256(NATIVE_ADAPTER),
        "sql_metric_adapter_sha256": sha256(SQL_ADAPTER),
        "bootstrap_handoff_authority_sha256": sha256(AUTHORITY),
        "adopted_services_sha256": sha256(ADOPTED_SERVICES),
        "rolling_monitor_sha256": sha256(ROLLING_MONITOR),
        "formal_job_workload_sha256": sha256(FORMAL_JOB_WORKLOAD),
        "physical_copy_sha256": sha256(PHYSICAL_COPY),
        "manual_campaign_sha256": sha256(MANUAL_CAMPAIGN),
        "bootstrap_spec_renderer_sha256": sha256(BOOTSTRAP_SPEC_RENDERER),
        "qualification_spec_renderer_sha256":
            sha256(QUALIFICATION_SPEC_RENDERER),
        "invalidated_predecessor_authority_sha256":
            sha256(INVALIDATED_BUNDLE_AUTHORITY),
        "invalidated_bd316_bundle_authority_sha256":
            sha256(INVALIDATED_BD316_BUNDLE_AUTHORITY),
        "invalidated_82e_bundle_authority_sha256":
            sha256(INVALIDATED_82E_BUNDLE_AUTHORITY),
        "invalidated_4fd_bundle_authority_sha256":
            sha256(INVALIDATED_4FD_BUNDLE_AUTHORITY),
        "invalidated_2d237_bundle_authority_sha256":
            sha256(INVALIDATED_2D237_BUNDLE_AUTHORITY),
        "invalidated_4d5_target_closure_authority_sha256":
            sha256(INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY),
        "invalidated_449_content_identity_authority_sha256":
            sha256(INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY),
        "invalidated_afe_normalization_contract_authority_sha256":
            sha256(INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY),
        "invalidated_f058_sql_percent_authority_sha256":
            sha256(INVALIDATED_F058_SQL_PERCENT_AUTHORITY),
        "invalidated_8f_client_credentials_authority_sha256":
            sha256(INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY),
        "invalidated_4ee_client_managed_mount_authority_sha256":
            sha256(INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY),
        "invalidated_f1c705_client_anonymous_volume_authority_sha256":
            sha256(INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY),
        "invalidated_7e08bd_client_bridge_identity_authority_sha256":
            sha256(INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY),
        "invalidated_2fe2ae_transient_inspect_hash_authority_sha256":
            sha256(INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY),
        "invalidated_a0f3e7_client_unknown_user_authority_sha256":
            sha256(INVALIDATED_A0F3E7_CLIENT_UNKNOWN_USER_AUTHORITY),
        "invalidated_1093df_counter_coverage_authority_sha256":
            sha256(INVALIDATED_1093DF_COUNTER_COVERAGE_AUTHORITY),
        "invalidated_0626b1_show_status_noninteger_authority_sha256":
            sha256(INVALIDATED_0626B1_SHOW_STATUS_NONINTEGER_AUTHORITY),
        "invalidated_0239_sql_query_native_contract_authority_sha256":
            sha256(INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY),
        "invalidated_0c7a_cgroup_v1_oom_readback_authority_sha256":
            sha256(INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY),
        "invalidated_350a_no_psi_authority_sha256":
            sha256(INVALIDATED_350A_NO_PSI_AUTHORITY),
        "invalidated_6bcd_collector_import_authority_sha256":
            sha256(INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY),
        "invalidated_6bf_root_mount_attribution_authority_sha256":
            sha256(INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY),
        "invalidated_a50d_measurement_host_tmpfs_authority_sha256":
            sha256(INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY),
        "invalidated_79b2_measurement_bind_ordering_authority_sha256":
            sha256(INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY),
        "builder_sha256": sha256(BUILDER),
        "complete_predecessor_source_closure_copied": True,
        "complete_predecessor_bundle_closure_copied": True,
        "full_physical_copy_no_overlay_no_reflink": True,
        "database_and_client_exact_envelopes_frozen": True,
        "client_execution_principal": {
            "uid": 1008,
            "gid": 1008,
            "credentials_mode": "0600",
            "measurement_tmpfs_mode": "0700",
        },
        "rolling_full_host_inventory_gate_required": True,
        "same_live_planner_handoff_required": True,
        "old_top5_rerun_authorized": False,
        "fresh_r0_required": True,
        "bootstrap_authorized": True,
        "qualification_authorized": True,
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "launch_authorized": False,
        "accepted_round_authorized": False,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    target = _publish(
        BUNDLE_ROOT,
        bundle_id,
        provisional,
        "bundle_manifest.json",
        canonical(manifest),
        files,
        "JOB v2 bundle",
    )
    return target, manifest


def build() -> dict[str, Any]:
    _validate_authorities()
    source, source_manifest = build_source()
    bundle, bundle_manifest = build_bundle(source, source_manifest)
    # A second full verification is part of the publication transaction.
    verify_file_map(source, source_manifest["files"], "published JOB v2 source")
    verify_file_map(bundle, bundle_manifest["files"], "published JOB v2 bundle")
    return {
        "schema_version": "mysql_job_distributed_v2_build_result_v1",
        "status": "PASS_SEALED_BOOTSTRAP_QUALIFICATION_NOT_FORMAL_AUTHORITY",
        "source": str(source),
        "source_bundle_id": source_manifest["bundle_id"],
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
        "bundle": str(bundle),
        "bundle_id": bundle_manifest["bundle_id"],
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "collector_sha256": sha256(COLLECTOR),
        "host_root_mount_inventory_helper_sha256": sha256(
            ROOT_DOCKER_MOUNT_HELPER
        ),
        "sql_metric_adapter_sha256": sha256(SQL_ADAPTER),
        "bootstrap_handoff_authority_sha256": sha256(AUTHORITY),
        "adopted_services_sha256": sha256(ADOPTED_SERVICES),
        "rolling_monitor_sha256": sha256(ROLLING_MONITOR),
        "formal_job_workload_sha256": sha256(FORMAL_JOB_WORKLOAD),
        "physical_copy_sha256": sha256(PHYSICAL_COPY),
        "manual_campaign_sha256": sha256(MANUAL_CAMPAIGN),
        "bootstrap_spec_renderer_sha256": sha256(BOOTSTRAP_SPEC_RENDERER),
        "qualification_spec_renderer_sha256":
            sha256(QUALIFICATION_SPEC_RENDERER),
        "invalidated_predecessor_authority_sha256":
            sha256(INVALIDATED_BUNDLE_AUTHORITY),
        "invalidated_4fd_bundle_authority_sha256":
            sha256(INVALIDATED_4FD_BUNDLE_AUTHORITY),
        "invalidated_2d237_bundle_authority_sha256":
            sha256(INVALIDATED_2D237_BUNDLE_AUTHORITY),
        "invalidated_4d5_target_closure_authority_sha256":
            sha256(INVALIDATED_4D5_TARGET_CLOSURE_AUTHORITY),
        "invalidated_449_content_identity_authority_sha256":
            sha256(INVALIDATED_449_CONTENT_IDENTITY_AUTHORITY),
        "invalidated_afe_normalization_contract_authority_sha256":
            sha256(INVALIDATED_AFE_NORMALIZATION_CONTRACT_AUTHORITY),
        "invalidated_f058_sql_percent_authority_sha256":
            sha256(INVALIDATED_F058_SQL_PERCENT_AUTHORITY),
        "invalidated_8f_client_credentials_authority_sha256":
            sha256(INVALIDATED_8F_CLIENT_CREDENTIALS_AUTHORITY),
        "invalidated_4ee_client_managed_mount_authority_sha256":
            sha256(INVALIDATED_4EE_CLIENT_MANAGED_MOUNT_AUTHORITY),
        "invalidated_f1c705_client_anonymous_volume_authority_sha256":
            sha256(INVALIDATED_F1C705_CLIENT_ANONYMOUS_VOLUME_AUTHORITY),
        "invalidated_7e08bd_client_bridge_identity_authority_sha256":
            sha256(INVALIDATED_7E08BD_CLIENT_BRIDGE_IDENTITY_AUTHORITY),
        "invalidated_2fe2ae_transient_inspect_hash_authority_sha256":
            sha256(INVALIDATED_2FE2AE_TRANSIENT_INSPECT_HASH_AUTHORITY),
        "invalidated_0239_sql_query_native_contract_authority_sha256":
            sha256(INVALIDATED_0239_SQL_QUERY_NATIVE_CONTRACT_AUTHORITY),
        "invalidated_0c7a_cgroup_v1_oom_readback_authority_sha256":
            sha256(INVALIDATED_0C7A_CGROUP_V1_OOM_READBACK_AUTHORITY),
        "invalidated_350a_no_psi_authority_sha256":
            sha256(INVALIDATED_350A_NO_PSI_AUTHORITY),
        "invalidated_6bcd_collector_import_authority_sha256":
            sha256(INVALIDATED_6BCD_COLLECTOR_IMPORT_AUTHORITY),
        "invalidated_6bf_root_mount_attribution_authority_sha256":
            sha256(INVALIDATED_6BF_ROOT_MOUNT_ATTRIBUTION_AUTHORITY),
        "invalidated_a50d_measurement_host_tmpfs_authority_sha256":
            sha256(INVALIDATED_A50D_MEASUREMENT_HOST_TMPFS_AUTHORITY),
        "invalidated_79b2_measurement_bind_ordering_authority_sha256":
            sha256(INVALIDATED_79B2_MEASUREMENT_BIND_ORDERING_AUTHORITY),
        "reconciliation_sha256": RECONCILIATION_SHA256,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def main() -> int:
    value = build()
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
