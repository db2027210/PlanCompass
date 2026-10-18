#!/usr/bin/env bash
set -euo pipefail

: "${RA_ATTEMPT4_VARIANT:?}"
: "${RA_ATTEMPT4_RUN_ID:?}"
: "${RA_ATTEMPT4_WORKSPACE:?}"
: "${RA_ATTEMPT4_LAUNCH:?}"
: "${RA_ATTEMPT4_RUN_ATTEMPT_ID:?}"
: "${RA_ATTEMPT4_EXPECTED_OPTIMIZER_SHA256:?}"
: "${RA_ATTEMPT4_EXPECTED_RUNNER_SHA256:?}"
: "${RA_ATTEMPT4_EXPECTED_ENGINE_SHA256:?}"
: "${RA_ATTEMPT4_EXPECTED_BRIDGE_SHA256:?}"

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
WORKSPACE=${LANE_ROOT}/${RA_ATTEMPT4_WORKSPACE}
OUTPUT=${LANE_ROOT}/history/job/outer/${RA_ATTEMPT4_VARIANT}
LAUNCH=${LANE_ROOT}/${RA_ATTEMPT4_LAUNCH}
CONFIG=${OUTPUT}/generated_config/${RA_ATTEMPT4_RUN_ID}/memory/job.json
CONTROL_ROOT=${LANE_ROOT}/work/control_${RA_ATTEMPT4_VARIANT}
VENV_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PYTHON_BIN=/home/anonymous/anaconda3/bin/python3

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}:${VENV_SITE}"
export PYTHONNOUSERSITE=1 OPTIMIZER_CONFIG="${CONFIG}" METRICS_SOURCE=plan-estimator
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1 BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_outer_parameter_optimizer.lock"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${OUTPUT}" RA_PG_CONTROL_ROOT="${CONTROL_ROOT}"
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen" RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_${RA_ATTEMPT4_VARIANT}"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID="${RA_ATTEMPT4_RUN_ATTEMPT_ID}" RA_PG_OUTER_ATTEMPT=4
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130
export RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3
export RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

test ! -e "${LAUNCH}/formal_pg_point_runner.py"
test ! -e "${LAUNCH}/formal_pg_engine.py"
test -f "${OUTPUT}/startup_baseline_formal_dbqps_validation.json"
test "$("${PYTHON_BIN}" -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["search_authorized"]).lower())' "${OUTPUT}/startup_baseline_formal_dbqps_validation.json")" = true
"${PYTHON_BIN}" - "${CONFIG}" "${OUTPUT}/attempt4_contract_manifest.json" \
    "${WORKSPACE}" "${LAUNCH}/formal_job_outer_replay.py" \
    "${RA_ATTEMPT4_EXPECTED_OPTIMIZER_SHA256}" \
    "${RA_ATTEMPT4_EXPECTED_RUNNER_SHA256}" \
    "${RA_ATTEMPT4_EXPECTED_ENGINE_SHA256}" \
    "${RA_ATTEMPT4_EXPECTED_BRIDGE_SHA256}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, manifest_path, workspace, bridge_path = map(Path, sys.argv[1:5])
expected_optimizer, expected_runner, expected_engine, expected_bridge = sys.argv[5:9]
manifest = json.loads(manifest_path.read_text())
config = json.loads(config_path.read_text())

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

actual = {
    "config_sha256": sha(config_path),
    "optimizer_code_sha256": sha(workspace / "parameter_optimizer.py"),
    "formal_runner_sha256": sha(workspace / "formal_pg_point_runner.py"),
    "formal_engine_sha256": sha(workspace / "formal_pg_engine.py"),
    "replay_bridge_sha256": sha(bridge_path),
}
expected = {
    "config_sha256": manifest["config_sha256"],
    "optimizer_code_sha256": expected_optimizer,
    "formal_runner_sha256": expected_runner,
    "formal_engine_sha256": expected_engine,
    "replay_bridge_sha256": expected_bridge,
}
if actual != expected:
    raise SystemExit(f"search source/config SHA mismatch: {actual} != {expected}")
if manifest.get("phase") != "startup_baseline_validated_search_armed":
    raise SystemExit("search manifest is not armed")
reuse = config.get("startup_baseline_reuse") or {}
if reuse.get("enabled") is not True or reuse.get("db_qps_source") != "formal_benchmark_db_qps":
    raise SystemExit("formal DB-QPS startup reuse is not armed")
protocol = config.get("formal_outer_protocol") or {}
resume_path_raw = str(protocol.get("formal_search_resume_checkpoint_path") or "").strip()
if resume_path_raw:
    resume_path = Path(resume_path_raw)
    if not resume_path.is_file():
        raise SystemExit("resume checkpoint is missing")
    resume_sha = sha(resume_path)
    if resume_sha != protocol.get("formal_search_resume_checkpoint_sha256"):
        raise SystemExit("resume checkpoint SHA mismatch")
    resume = json.loads(resume_path.read_text())
    expected_binding = (41, 10, 1, 1)
    actual_binding = (
        protocol.get("global_replay_ordinal_at_binding"),
        protocol.get("formal_search_replay_count_start"),
        protocol.get("formal_search_accepted_count_start"),
        protocol.get("formal_startup_baseline_count_start"),
    )
    if actual_binding != expected_binding:
        raise SystemExit(
            f"resume accounting binding mismatch: {actual_binding} != {expected_binding}"
        )
    resume_counters = resume.get("counters") or {}
    if (
        resume_counters.get("global_replay_count") != 41
        or resume_counters.get("formal_search_replay_count") != 10
        or resume_counters.get("accepted_shrink_count") != 1
        or resume_counters.get("startup_baseline_count") != 1
    ):
        raise SystemExit("resume checkpoint counters mismatch")
    if (
        protocol.get("formal_search_attempt_safety_cap") != 130
        or protocol.get("formal_search_maximum_replay_count") != 130
        or protocol.get("formal_search_max_replay_top_k") != 10
        or protocol.get("formal_search_accepted_target") != 10
        or protocol.get("unstable_candidate_replicate_total") != 3
        or protocol.get("first_stable_replicate_used_directly") is not True
        or protocol.get("first_unstable_requires_all_three_valid_replicates") is not True
        or protocol.get("later_stable_replicate_short_circuit") is not False
        or protocol.get("technical_failure_in_composite") is not False
    ):
        raise SystemExit("resume 5-to-10/global-130/replicate protocol mismatch")
else:
    if (
        protocol.get("global_replay_ordinal_at_binding") != 31
        or protocol.get("formal_search_replay_count_start") != 0
        or protocol.get("formal_search_accepted_count_start") != 0
        or protocol.get("formal_startup_baseline_count_start") != 1
    ):
        raise SystemExit("search accounting binding mismatch")
print(json.dumps({"status": "search_ready", **actual}, sort_keys=True), flush=True)
PY
test "$(docker inspect --format '{{.State.Health.Status}}' ra_pg5_job_node02_v1_outercontrol)" = healthy
if docker ps -a --format '{{.Names}}' | grep -qx ra_pg5_job_node02_v1_outerpoint; then
    echo "formal outer point container unexpectedly exists before search" >&2
    exit 1
fi
cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s"}\n' "${rc}" "$(date -Is)" >"${OUTPUT}/launcher_exit.json"
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" "${LAUNCH}/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT
printf '%s\n' "$$" >"${OUTPUT}/launcher.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
