#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_5_formal_tsgd_v1
WORKSPACE=${LANE_ROOT}/workspace_outer_attempt4_formal_dbqps_v1_retry3
LAUNCH=${LANE_ROOT}/launch_outer_attempt4_formal_dbqps_v1_retry3
FROZEN=${LANE_ROOT}/bundle/attempt8_frozen
CONFIG=${ATTEMPT}/generated_config/job_tsgd_fresh_v1_v5_core_043b_after_p43/memory/job.json
CONTROL_ROOT=${LANE_ROOT}/work/control_attempt_5_formal_tsgd_v5_after_p43_v1
VENV_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PYTHON_BIN=/home/anonymous/anaconda3/bin/python3

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}:${VENV_SITE}"
export PYTHONNOUSERSITE=1 OPTIMIZER_CONFIG="${CONFIG}" METRICS_SOURCE=plan-estimator
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1 BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_outer_parameter_optimizer.lock"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${ATTEMPT}" RA_PG_CONTROL_ROOT="${CONTROL_ROOT}"
export RA_PG_FROZEN_ROOT="${FROZEN}" RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_attempt_5_formal_tsgd_v5_after_p43_v1"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_tsgd_formal_initial_batch_v5_after_p43_v1 RA_PG_OUTER_ATTEMPT=5
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130 RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3 RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_R0_MEAN_PROFILE_GATE=0
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

"${PYTHON_BIN}" - "${CONFIG}" "${WORKSPACE}" "${ATTEMPT}" "${FROZEN}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, workspace, attempt, frozen = map(Path, sys.argv[1:5])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


expected = {
    config_path: "9c1eef5c36abc76f4da7cdaa2ae74f122ea9b18d7565a97482b7fd0f5c455b0f",
    attempt / "sealed_startup_baseline_sql_job8_literal_v2.json": "842413fda8fa783bc8750f6b438a6b6d2aa41d7adef629cf7becbba9f38ab7fb",
    attempt / "fresh_tsgd_generation_manifest_v1.json": "40811927a11d52381343e7a23168b7d9c28c45a3628a7e5235ef975d3c0fb37f",
    attempt / "initial_batch_release_manifest_40811927.json": "263df822345f9a510d7c88527d53d637edb14033017b4a4cb6ab240c73520fa5",
    attempt / "released_pause_marker_manifest_40811927.json": "60950741b7779fa3fd43d05888c70c92f2466a296f68cf0f438dd6e919c7aef0",
    attempt / "p43_technical_failure_audit_v1.json": "7880e81317b27f1d37a493c83c0c35159b07b6feda8bb567709f8865fdee7d35",
    attempt / "job_tsgd_v5_resume_after_p43_manifest_v1.json": "d94f5bd649b56e9faa1f89e928c7e21b88b2c53ac43c6dbcb557adbbed545c84",
    attempt / "job_tsgd_v5_core_043b_resume_after_p43_manifest_v2.json": "f329c4eabcc495597db8990d58c17a48bf833ed04cc98106d3064ed3d0b70045",
    workspace / "gradient_descent_optimizer.py": "687006473e87974d6b91a7b26308539c33554fafc715265699a2e61140f42914",
    workspace / "experience_table_subset_optimizer.py": "0df42da5cccd55f38d864114285e6b10122bb09c5955db8ebffb55b05da63aa0",
    workspace / "parameter_optimizer.py": "043bdbd9877d994986c42463335fc9e41cefcc1f1eca71f9d6a87568cd793762",
    workspace / "plan_resource_estimator.py": "d3b06a81382df0d536415680d9c442e1346e7f130c6ebb50a217ed6649fdc8c0",
    workspace / "common_outer_loop.py": "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631",
    workspace / "resource_utility.py": "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2",
    workspace / "formal_contracts.py": "245374b95e37a8676016ad19a80336c90e93258a5b410b536fc5325fa8be02cc",
    workspace / "formal_measurement.py": "6bcffc34ab47c0f774ed6bdcf56eb5d82a3c752e16bb4dd8c509555e4b1fd592",
    frozen / "stability_gate_contract.json": "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692",
    frozen / "measurement_protocol.json": "efe26b93be11a2d36fce798f268a986c0f8c4a2fc50b898bed28dd8ac1458cb1",
}
for path, digest in expected.items():
    actual = sha(path)
    if actual != digest:
        raise SystemExit(f"preflight SHA mismatch: {path}: {actual} != {digest}")

config = json.loads(config_path.read_text())
reuse = config["startup_baseline_reuse"]
if reuse.get("baseline_sql_snapshot_sha256") != expected[
    attempt / "sealed_startup_baseline_sql_job8_literal_v2.json"
]:
    raise SystemExit("startup baseline SQL snapshot SHA binding mismatch")
if reuse.get("baseline_sql_expected_query_count") != 8:
    raise SystemExit("startup baseline SQL expected query count must be 8")
protocol = config["formal_outer_protocol"]
checks = {
    "global_replay_ordinal_at_binding": 43,
    "formal_search_replay_count_start": 1,
    "formal_search_accepted_count_start": 0,
    "valid_initial_batch_count_start": 0,
    "rank1_retry_physical_ordinal": 44,
    "initial_batch_valid_replay_barrier": 5,
    "formal_search_accepted_target": 10,
    "formal_search_initial_replay_top_k": 5,
    "formal_search_second_replay_top_k": 10,
    "formal_search_max_replay_top_k": 16,
    "all_nonempty_subset_count": 127,
}
for key, value in checks.items():
    if protocol.get(key) != value:
        raise SystemExit(f"protocol mismatch: {key}={protocol.get(key)!r} != {value!r}")
if protocol.get("single_candidate_early_accept_forbidden") is not True:
    raise SystemExit("single-candidate early acceptance is not explicitly forbidden")
if protocol.get("technical_failure_ordinal_43_excluded_from_valid_batch") is not True:
    raise SystemExit("p43 technical failure is not excluded from the valid batch")
if protocol.get("formal_search_resume_checkpoint_path"):
    raise SystemExit("fresh T-SGD generation must not import a grid checkpoint")

v5 = config["_tsgd_generation"]["formal_measurement_v5"]
v5_checks = {
    "target_resource": "memory",
    "target_resource_field": "memory_utilization",
    "stability_gate_contract_sha256": expected[frozen / "stability_gate_contract.json"],
    "measurement_protocol_sha256": expected[frozen / "measurement_protocol.json"],
    "formal_contracts_sha256": expected[workspace / "formal_contracts.py"],
    "formal_measurement_sha256": expected[workspace / "formal_measurement.py"],
    "parameter_optimizer_sha256": expected[workspace / "parameter_optimizer.py"],
    "minimum_measurement_seconds": 180,
    "decision_step_seconds": 30,
    "maximum_measurement_seconds": 600,
    "candidate_profile_band_gate_enabled": False,
    "throughput_stability_required": False,
}
for key, value in v5_checks.items():
    if v5.get(key) != value:
        raise SystemExit(f"v5 binding mismatch: {key}={v5.get(key)!r} != {value!r}")
if v5.get("retained_resource_fields") != [
    "cpu_utilization",
    "memory_utilization",
    "io_utilization",
]:
    raise SystemExit("v5 retained resource fields mismatch")
campaign_stability = config.get("stability_gate_contract") or {}
if (
    campaign_stability.get("sha256")
    != expected[frozen / "stability_gate_contract.json"]
    or sha(Path(str(campaign_stability.get("path") or "")))
    != expected[frozen / "stability_gate_contract.json"]
):
    raise SystemExit("parameter optimizer is not bound to campaign stability v5")

counter_checks = {
    "replay_counter.txt": 43,
    "formal_search_replay_counter.txt": 1,
    "formal_search_accepted_counter.txt": 0,
    "formal_startup_baseline_counter.txt": 1,
}
for name, value in counter_checks.items():
    actual = int((attempt / name).read_text().strip())
    if actual != value:
        raise SystemExit(f"counter mismatch: {name}={actual} != {value}")
if (attempt / "pause_before_next_formal_claim.json").exists():
    raise SystemExit("formal initial batch remains paused")
if not (attempt / "formal_points/point_0043").is_dir():
    raise SystemExit("p43 technical failure evidence directory is missing")
if (attempt / "formal_points/point_0044").exists():
    raise SystemExit("p44 exists before the v5 retry")

failure = json.loads((attempt / "p43_technical_failure_audit_v1.json").read_text())
if (
    failure.get("status")
    != "sealed_technical_failure_excluded_from_valid_batch"
    or failure.get("counts_toward_valid_initial_batch") is not False
    or failure.get("valid_initial_batch_count_after") != 0
    or (failure.get("retry") or {}).get("next_physical_ordinal") != 44
):
    raise SystemExit("p43 technical failure audit is invalid")
resume = json.loads(
    (attempt / "job_tsgd_v5_core_043b_resume_after_p43_manifest_v2.json").read_text()
)
barrier = resume.get("initial_batch_barrier") or {}
if (
    resume.get("status")
    != "sealed_ready_for_p44_with_final_v5_core_binding"
    or resume.get("resume_config_sha256") != expected[config_path]
    or (resume.get("next_replay") or {})
    != {"candidate_rank": 1, "physical_ordinal": 44}
    or barrier.get("required_valid_replays") != 5
    or barrier.get("p43_is_technical_failure_and_invalid") is not True
    or barrier.get("single_candidate_early_accept_forbidden") is not True
    or barrier.get("cumulative_batch_sizes") != [5, 10, 16]
):
    raise SystemExit("v5 resume manifest is invalid")
binding = resume.get("final_runtime_binding") or {}
if (
    binding.get("parameter_optimizer_sha256")
    != expected[workspace / "parameter_optimizer.py"]
    or binding.get("measurement_protocol_sha256")
    != expected[frozen / "measurement_protocol.json"]
    or binding.get("stability_gate_contract_sha256")
    != expected[frozen / "stability_gate_contract.json"]
):
    raise SystemExit("final v5 runtime binding manifest is invalid")
print(
    json.dumps(
        {
            "status": "tsgd_v5_p44_retry_preflight_passed",
            "counters": counter_checks,
            "next_point": 44,
            "candidate_rank": 1,
            "valid_initial_batch": "0/5",
            "accepted": "0/10",
            "target_stability_field": "memory_utilization",
            "first_decision_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_seconds": 600,
            "batch_barrier": True,
        },
        sort_keys=True,
    ),
    flush=True,
)
PY

if [[ "${RA_PG_PREFLIGHT_ONLY:-0}" == "1" ]]; then
    exit 0
fi

if docker ps -a --format '{{.Names}}' | grep -qx ra_pg5_job_node02_v1_outerpoint; then
    echo "formal point container unexpectedly exists" >&2
    exit 1
fi
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" RA_PG_OUTPUT_ROOT="${ATTEMPT}" "${LAUNCH}/setup_job_outer_control.sh"

cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s","phase":"formal_initial_batch_v5_after_p43"}\n' "${rc}" "$(date -Is)" >"${ATTEMPT}/launcher_tsgd_v5_after_p43_exit.json"
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT
printf '%s\n' "$$" >"${ATTEMPT}/launcher_tsgd_v5_after_p43.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
