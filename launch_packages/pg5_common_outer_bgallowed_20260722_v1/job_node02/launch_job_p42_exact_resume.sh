#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_4_formal_dbqps_v1_retry3
WORKSPACE=${LANE_ROOT}/workspace_outer_attempt4_formal_dbqps_v1_retry3
LAUNCH=${LANE_ROOT}/launch_outer_attempt4_formal_dbqps_v1_retry3
CONFIG=${ATTEMPT}/generated_config/formal_job_outer_attempt4_p42_exact_resume_v2/memory/job.json
CHECKPOINT=${ATTEMPT}/partial_resume_checkpoint_after_p42_exact_plan_v2.json
CONTROL_ROOT=${LANE_ROOT}/work/control_attempt_4_formal_dbqps_v1_retry3
VENV_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PYTHON_BIN=/home/anonymous/anaconda3/bin/python3
EXPECT_PAUSE=${RA_JOB_EXPECT_PAUSE:-0}
RUN_LABEL=${RA_JOB_RESUME_RUN_LABEL:-actual}

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}:${VENV_SITE}"
export PYTHONNOUSERSITE=1 OPTIMIZER_CONFIG="${CONFIG}" METRICS_SOURCE=plan-estimator
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1 BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_outer_parameter_optimizer.lock"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${ATTEMPT}" RA_PG_CONTROL_ROOT="${CONTROL_ROOT}"
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen" RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_outer_attempt_4_formal_dbqps_v1_retry3"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_experience_table_outer_attempt4_formal_dbqps_v1_retry3 RA_PG_OUTER_ATTEMPT=4
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130 RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3 RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

"${PYTHON_BIN}" - "${CONFIG}" "${CHECKPOINT}" "${WORKSPACE}" "${LAUNCH}" "${ATTEMPT}" "${EXPECT_PAUSE}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, checkpoint_path, workspace, launch, attempt = map(Path, sys.argv[1:6])
expect_pause = sys.argv[6] == "1"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

expected_files = {
    workspace / "parameter_optimizer.py": "f525ad38f67c9b6de5e9c609af5ee9e0b60acc8137be24e8944bba2e69c778d9",
    workspace / "common_outer_loop.py": "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631",
    workspace / "resource_utility.py": "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2",
    workspace / "formal_contracts.py": "85822a22364e1eb98498f680bc397c953b0e3337befd9cbad0d37b5bb49d5c91",
    workspace / "formal_pg_point_runner.py": "1743855faee3f0ab1bff2335b3ef975ab7b28bdba0b21c5463ea61339a11bb66",
    workspace / "formal_pg_engine.py": "b3cd68489fdfefc00e11ce569ccfa4e327919f20f0354b74746c44d4f6012430",
    launch / "formal_job_outer_replay.py": "93ce1ee7e09ef31517c04dfe77f1ccefd2bf15478ef5553b6b49cdc32726ea22",
    checkpoint_path: "51a9c22a95bfaefa64060221e766f2316e61eb61d2a68f083bc2583270956c5b",
    config_path: "edd7dcba012e27385301d506fc3c7e47893f9d2651ff66e48618884be9dab821",
}
for path, expected in expected_files.items():
    actual = sha(path)
    if actual != expected:
        raise SystemExit(f"sealed file SHA mismatch: {path}: {actual} != {expected}")

config = json.loads(config_path.read_text())
protocol = config.get("formal_outer_protocol") or {}
expected_protocol = {
    "global_replay_ordinal_at_binding": 42,
    "formal_search_replay_count_start": 11,
    "formal_search_accepted_count_start": 1,
    "formal_search_accepted_target": 10,
    "formal_search_initial_replay_top_k": 5,
    "formal_search_second_replay_top_k": 10,
    "formal_search_max_replay_top_k": 16,
}
for key, expected in expected_protocol.items():
    if protocol.get(key) != expected:
        raise SystemExit(f"resume protocol mismatch: {key}={protocol.get(key)!r} != {expected!r}")
if Path(protocol["formal_search_resume_checkpoint_path"]) != checkpoint_path:
    raise SystemExit("resume checkpoint path mismatch")
if protocol.get("formal_search_resume_checkpoint_sha256") != sha(checkpoint_path):
    raise SystemExit("resume checkpoint SHA binding mismatch")

checkpoint = json.loads(checkpoint_path.read_text())
plan = checkpoint.get("round1_replay_plan_event") or {}
if plan.get("selected_ranks") != [1, 2, 3, 4, 12]:
    raise SystemExit("sealed exact representative order mismatch")
if checkpoint.get("replayed_candidate_ranks") != [1] or checkpoint.get("resume_next_estimated_rank") != 2:
    raise SystemExit("partial resume prefix/next rank mismatch")
if (checkpoint.get("resume_contract") or {}).get("do_not_rerun_replayed_ranks") is not True:
    raise SystemExit("rank-1 no-rerun contract missing")

counter_expectations = {
    "replay_counter.txt": 42,
    "formal_search_replay_counter.txt": 11,
    "formal_search_accepted_counter.txt": 1,
    "formal_startup_baseline_counter.txt": 1,
}
for name, expected in counter_expectations.items():
    actual = int((attempt / name).read_text().strip())
    if actual != expected:
        raise SystemExit(f"prelaunch counter mismatch: {name}={actual} != {expected}")
if (attempt / "formal_points" / "point_0043").exists():
    raise SystemExit("p43 already exists before partial resume")
pause = attempt / "pause_before_next_formal_claim.json"
if pause.exists() != expect_pause:
    raise SystemExit(f"pause marker state mismatch: exists={pause.exists()} expected={expect_pause}")
print(json.dumps({"status": "p42_exact_resume_ready", "expect_pause": expect_pause, "next_rank": 2}, sort_keys=True), flush=True)
PY

if docker ps -a --format '{{.Names}}' | grep -qx ra_pg5_job_node02_v1_outerpoint; then
    echo "formal point container unexpectedly exists before resume" >&2
    exit 1
fi
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" RA_PG_OUTPUT_ROOT="${ATTEMPT}" "${LAUNCH}/setup_job_outer_control.sh"

cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s","label":"%s"}\n' "${rc}" "$(date -Is)" "${RUN_LABEL}" >"${ATTEMPT}/launcher_p42_exact_resume_${RUN_LABEL}_exit.json"
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT
printf '%s\n' "$$" >"${ATTEMPT}/launcher_p42_exact_resume_${RUN_LABEL}.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
