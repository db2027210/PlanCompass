#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_5_formal_tsgd_v1
WORKSPACE=${LANE_ROOT}/workspace_outer_attempt4_formal_dbqps_v1_retry3
LAUNCH=${LANE_ROOT}/launch_outer_attempt4_formal_dbqps_v1_retry3
CONFIG=${ATTEMPT}/generated_config/job_tsgd_fresh_v1_model_reuse_literal_sql/memory/job.json
CONTROL_ROOT=${LANE_ROOT}/work/control_attempt_5_formal_tsgd_v1
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
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen" RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_attempt_5_formal_tsgd_v1"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_tsgd_formal_initial_batch_v1 RA_PG_OUTER_ATTEMPT=5
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130 RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3 RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

"${PYTHON_BIN}" - "${CONFIG}" "${WORKSPACE}" "${ATTEMPT}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, workspace, attempt = map(Path, sys.argv[1:4])

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

expected = {
    config_path: "8436f6381f88ff3970ca10b24227942c6aebbe1177e5b43b02b3a5ded340a0da",
    attempt / "sealed_startup_baseline_sql_job8_literal_v2.json": "842413fda8fa783bc8750f6b438a6b6d2aa41d7adef629cf7becbba9f38ab7fb",
    attempt / "fresh_tsgd_generation_manifest_v1.json": "40811927a11d52381343e7a23168b7d9c28c45a3628a7e5235ef975d3c0fb37f",
    attempt / "initial_batch_release_manifest_40811927.json": "263df822345f9a510d7c88527d53d637edb14033017b4a4cb6ab240c73520fa5",
    attempt / "released_pause_marker_manifest_40811927.json": "60950741b7779fa3fd43d05888c70c92f2466a296f68cf0f438dd6e919c7aef0",
    workspace / "gradient_descent_optimizer.py": "687006473e87974d6b91a7b26308539c33554fafc715265699a2e61140f42914",
    workspace / "experience_table_subset_optimizer.py": "0df42da5cccd55f38d864114285e6b10122bb09c5955db8ebffb55b05da63aa0",
    workspace / "parameter_optimizer.py": "f0740d06272a69e8db84a08ad0ce39fe29dbf2992bcc3909f66116be69881095",
    workspace / "plan_resource_estimator.py": "d3b06a81382df0d536415680d9c442e1346e7f130c6ebb50a217ed6649fdc8c0",
    workspace / "common_outer_loop.py": "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631",
    workspace / "resource_utility.py": "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2",
    workspace / "formal_contracts.py": "85822a22364e1eb98498f680bc397c953b0e3337befd9cbad0d37b5bb49d5c91",
}
for path, digest in expected.items():
    actual = sha(path)
    if actual != digest:
        raise SystemExit(f"preflight SHA mismatch: {path}: {actual} != {digest}")
config = json.loads(config_path.read_text())
reuse = config["startup_baseline_reuse"]
if reuse.get("baseline_sql_snapshot_sha256") != expected[attempt / "sealed_startup_baseline_sql_job8_literal_v2.json"]:
    raise SystemExit("startup baseline SQL snapshot SHA binding mismatch")
if reuse.get("baseline_sql_expected_query_count") != 8:
    raise SystemExit("startup baseline SQL expected query count must be 8")
protocol = config["formal_outer_protocol"]
checks = {
    "global_replay_ordinal_at_binding": 42,
    "formal_search_replay_count_start": 0,
    "formal_search_accepted_count_start": 0,
    "formal_search_accepted_target": 10,
    "formal_search_initial_replay_top_k": 5,
    "formal_search_second_replay_top_k": 10,
    "formal_search_max_replay_top_k": 16,
    "all_nonempty_subset_count": 127,
}
for key, value in checks.items():
    if protocol.get(key) != value:
        raise SystemExit(f"protocol mismatch: {key}={protocol.get(key)!r} != {value!r}")
if protocol.get("formal_search_resume_checkpoint_path"):
    raise SystemExit("fresh T-SGD generation must not import a grid checkpoint")
counter_checks = {
    "replay_counter.txt": 42,
    "formal_search_replay_counter.txt": 0,
    "formal_search_accepted_counter.txt": 0,
    "formal_startup_baseline_counter.txt": 1,
}
for name, value in counter_checks.items():
    actual = int((attempt / name).read_text().strip())
    if actual != value:
        raise SystemExit(f"counter mismatch: {name}={actual} != {value}")
if (attempt / "pause_before_next_formal_claim.json").exists():
    raise SystemExit("formal initial batch remains paused")
release = json.loads((attempt / "initial_batch_release_manifest_40811927.json").read_text())
if (
    release.get("status") != "formal_initial_batch_released"
    or release.get("generation_manifest_sha256")
    != expected[attempt / "fresh_tsgd_generation_manifest_v1.json"]
    or release.get("initial_batch_size") != 5
    or release.get("initial_batch_barrier_required") is not True
    or release.get("single_candidate_early_accept_forbidden") is not True
):
    raise SystemExit("formal initial batch release audit is invalid")
if (attempt / "formal_points/point_0043").exists():
    raise SystemExit("new T-SGD p43 exists before generation")
print(json.dumps({"status": "tsgd_formal_initial_batch_preflight_passed", "subset_count": 127, "formal_claim_paused": False, "initial_batch_size": 5, "batch_barrier": True}, sort_keys=True), flush=True)
PY

if docker ps -a --format '{{.Names}}' | grep -qx ra_pg5_job_node02_v1_outerpoint; then
    echo "formal point container unexpectedly exists" >&2
    exit 1
fi
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" RA_PG_OUTPUT_ROOT="${ATTEMPT}" "${LAUNCH}/setup_job_outer_control.sh"

cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s","phase":"formal_initial_batch"}\n' "${rc}" "$(date -Is)" >"${ATTEMPT}/launcher_tsgd_formal_initial_batch_exit.json"
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT
printf '%s\n' "$$" >"${ATTEMPT}/launcher_tsgd_formal_initial_batch.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
