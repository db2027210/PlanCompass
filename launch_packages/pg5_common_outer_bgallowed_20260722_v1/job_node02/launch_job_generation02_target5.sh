#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_5_formal_tsgd_v1
GENERATION=${ATTEMPT}/generations/generation_02
GENERATION_RUN=${GENERATION}/run_04
WORKSPACE=${LANE_ROOT}/workspace_outer_attempt4_formal_dbqps_v1_retry3
LAUNCH=${LANE_ROOT}/launch_outer_attempt4_formal_dbqps_v1_retry3
FROZEN=${LANE_ROOT}/bundle/attempt8_frozen
CONFIG=${GENERATION}/config_v5_accepted_gate/memory/job.json
MANIFEST=${GENERATION}/generation_02_manifest_v5_accepted_gate.json
CONTROL_ROOT=${LANE_ROOT}/work/control_attempt_5_formal_tsgd_generation02_v5_target5
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
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_attempt_5_formal_tsgd_generation02_v5_target5"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_tsgd_generation02_v5_target5 RA_PG_OUTER_ATTEMPT=5
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130 RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3 RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_R0_MEAN_PROFILE_GATE=0
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

"${PYTHON_BIN}" - "${CONFIG}" "${MANIFEST}" "${WORKSPACE}" "${ATTEMPT}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, manifest_path, workspace, attempt = map(Path, sys.argv[1:5])
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads(manifest_path.read_text())
config = json.loads(config_path.read_text())
if manifest.get("status") != "sealed_ready_v5_accepted_gate_shrunken_scope_only":
    raise SystemExit("generation_02 target5 manifest is not sealed")
if manifest.get("config_sha256") != sha(config_path):
    raise SystemExit("generation_02 config SHA mismatch")
if manifest.get("parameter_optimizer_sha256") != sha(workspace / "parameter_optimizer.py"):
    raise SystemExit("generation_02 optimizer SHA mismatch")
checkpoint = Path(manifest["checkpoint_path"])
if manifest.get("checkpoint_sha256") != sha(checkpoint):
    raise SystemExit("generation_02 checkpoint SHA mismatch")
outer = config["formal_outer_protocol"]
checks = {
    "global_replay_ordinal_at_binding": 48,
    "formal_search_replay_count_start": 6,
    "formal_search_accepted_count_start": 1,
    "formal_search_accepted_target": 5,
    "accepted_shrink_target": 5,
    "all_nonempty_subset_count": 127,
    "formal_search_initial_replay_top_k": 5,
    "formal_search_second_replay_top_k": 10,
    "formal_search_max_replay_top_k": 16,
    "normalized_width_stop": 1.0 / 32.0,
}
for key, expected in checks.items():
    if outer.get(key) != expected:
        raise SystemExit(f"protocol mismatch {key}: {outer.get(key)!r} != {expected!r}")
if outer.get("generation_02_rerun_all_127_subsets") is not True:
    raise SystemExit("generation_02 does not require all 127 subsets")
if outer.get("generation_02_reuse_old_round1_ranking") is not False:
    raise SystemExit("generation_02 attempts to reuse old round1")
counter_checks = {
    "replay_counter.txt": 48,
    "formal_search_replay_counter.txt": 6,
    "formal_search_accepted_counter.txt": 1,
    "formal_startup_baseline_counter.txt": 1,
}
for name, expected in counter_checks.items():
    actual = int((attempt / name).read_text().strip())
    if actual != expected:
        raise SystemExit(f"counter mismatch {name}: {actual} != {expected}")
if (attempt / "pause_before_next_formal_claim.json").exists():
    raise SystemExit("formal replay pause marker remains present")
print(json.dumps({
    "status": "generation_02_v5_target5_preflight_passed",
    "machine": "anonymous@192.0.2.4",
    "disk": "/mnt/nvme2n1",
    "generation_root": str(manifest_path.parent),
    "run_root": manifest["run_root"],
    "counters": counter_checks,
    "accepted": "1/5",
    "next_point": 49,
    "subsets": 127,
    "barriers": [5, 10, 16],
}, sort_keys=True), flush=True)
PY

if [[ "${RA_PG_PREFLIGHT_ONLY:-0}" == "1" ]]; then
    exit 0
fi

mkdir -p "${GENERATION_RUN}"

if docker ps -a --format '{{.Names}}' | grep -qx ra_pg5_job_node02_v1_outerpoint; then
    echo "formal point container unexpectedly exists" >&2
    exit 1
fi
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" RA_PG_OUTPUT_ROOT="${ATTEMPT}" "${LAUNCH}/setup_job_outer_control.sh"

cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s","phase":"generation_02_v5_target5","completion_requires_accepted_target_or_explicit_no_representative_convergence":true}\n' "${rc}" "$(date -Is)" >"${GENERATION_RUN}/launcher_exit.json"
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT
printf '%s\n' "$$" >"${GENERATION_RUN}/launcher.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
