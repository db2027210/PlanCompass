#!/usr/bin/env bash
set -euo pipefail

: "${RA_ATTEMPT4_VARIANT:?}"
: "${RA_ATTEMPT4_RUN_ID:?}"
: "${RA_ATTEMPT4_WORKSPACE:?}"
: "${RA_ATTEMPT4_LAUNCH:?}"
: "${RA_ATTEMPT4_GLOBAL_BEFORE:?}"
: "${RA_ATTEMPT4_EXPECTED_POINT_ID:?}"
: "${RA_ATTEMPT4_EXPECTED_RUNNER_SHA256:?}"
: "${RA_ATTEMPT4_EXPECTED_ENGINE_SHA256:?}"

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
WORKSPACE=${LANE_ROOT}/${RA_ATTEMPT4_WORKSPACE}
OUTPUT=${LANE_ROOT}/history/job/outer/${RA_ATTEMPT4_VARIANT}
LAUNCH=${LANE_ROOT}/${RA_ATTEMPT4_LAUNCH}
CONTROL_ROOT=${LANE_ROOT}/work/control_${RA_ATTEMPT4_VARIANT}
VENV_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PYTHON_BIN=/home/anonymous/anaconda3/bin/python3

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}:${VENV_SITE}" PYTHONNOUSERSITE=1
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${OUTPUT}" RA_PG_CONTROL_ROOT="${CONTROL_ROOT}"
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen" RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_${RA_ATTEMPT4_VARIANT}"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1 RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480 RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID="${RA_ATTEMPT4_EXPECTED_POINT_ID%_*}" RA_PG_OUTER_ATTEMPT=4
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458 RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar" RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search RA_PG_FORMAL_SEARCH_MAX_REPLAYS=10
export RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=1
export RA_PG_STARTUP_BASELINE_GLOBAL_ORDINAL="${RA_ATTEMPT4_GLOBAL_BEFORE}"
export RA_PG_EXPLICIT_COST_PARAMETERS_JSON='{"seq_page_cost":1.0,"random_page_cost":4.0,"cpu_tuple_cost":0.01,"cpu_index_tuple_cost":0.005,"cpu_operator_cost":0.0025,"parallel_setup_cost":1000.0,"parallel_tuple_cost":0.1}'
export RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256=0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341
export RA_PG_EXPERIENCE_TABLE_TABLE_SHA256=c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d

mkdir -p "${OUTPUT}" "${RA_PG_WORK_ROOT}"
test ! -e "${LAUNCH}/formal_pg_point_runner.py"
cd "${WORKSPACE}"
"${PYTHON_BIN}" - "${WORKSPACE}/formal_pg_point_runner.py" "${RA_ATTEMPT4_EXPECTED_RUNNER_SHA256}" "${WORKSPACE}/formal_pg_engine.py" "${RA_ATTEMPT4_EXPECTED_ENGINE_SHA256}" <<'PY'
import hashlib
from pathlib import Path
import subprocess
import sys
import p24_pg_recost_runner as p24
import formal_pg_point_runner as runner
import formal_pg_engine as engine

expected_path = Path(sys.argv[1]).resolve()
expected_sha = sys.argv[2]
expected_engine_path = Path(sys.argv[3]).resolve()
expected_engine_sha = sys.argv[4]
actual_path = Path(runner.__file__).resolve()
actual_sha = hashlib.sha256(actual_path.read_bytes()).hexdigest()
actual_engine_path = Path(engine.__file__).resolve()
actual_engine_sha = hashlib.sha256(actual_engine_path.read_bytes()).hexdigest()
if actual_path != expected_path or actual_sha != expected_sha:
    raise SystemExit(f"runner import/SHA mismatch: {actual_path} {actual_sha}")
if actual_engine_path != expected_engine_path or actual_engine_sha != expected_engine_sha:
    raise SystemExit(
        f"engine import/SHA mismatch: {actual_engine_path} {actual_engine_sha}"
    )
if not Path(runner.CACHE_EVICTOR).is_file():
    raise SystemExit(f"cache evictor missing: {runner.CACHE_EVICTOR}")
# Mirror formal_job_outer_replay.py's module configuration before reading the
# frozen per-campaign PostgreSQL settings.
def preflight_lane_run(command, *, check=True, capture=True, **kwargs):
    argv = list(command)
    if argv and argv[0] == "sudo":
        argv = argv[1:]
    return subprocess.run(
        argv,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=kwargs.get("timeout"),
        cwd=str(kwargs["cwd"]) if kwargs.get("cwd") is not None else None,
        input=kwargs.get("input_text"),
    )


p24.run = preflight_lane_run
p24.configure_modules("job")
arguments = engine.fixed_postgres_arguments("memory")
for required in (
    "shared_preload_libraries=pg_stat_statements",
    "compute_query_id=on",
    "pg_stat_statements.track=all",
):
    if arguments.count(required) != 1:
        raise SystemExit(f"formal DB-QPS PostgreSQL setting missing/duplicated: {required}")
print(
    f"runner_import={actual_path} runner_sha256={actual_sha} "
    f"engine_import={actual_engine_path} engine_sha256={actual_engine_sha} "
    f"cache_evictor={runner.CACHE_EVICTOR} dbqps_preload_contract=passed",
    flush=True,
)
PY
if ! docker inspect ra_pg5_job_node02_v1_outercontrol >/dev/null 2>&1; then
    RA_PG_CONTROL_ROOT="${CONTROL_ROOT}" RA_PG_OUTPUT_ROOT="${OUTPUT}" "${LAUNCH}/setup_job_outer_control.sh"
fi
test "$(docker inspect --format '{{.State.Health.Status}}' ra_pg5_job_node02_v1_outercontrol)" = healthy
"${PYTHON_BIN}" -u "${LAUNCH}/verify_job_outer_attempt4_formal_dbqps_v1.py"
printf '%s\n' "$$" >"${OUTPUT}/startup_baseline_launcher.pid"
"${PYTHON_BIN}" -u "${LAUNCH}/formal_job_outer_replay.py" 2>&1 | tee "${OUTPUT}/startup_baseline_launcher.log"
"${PYTHON_BIN}" -u "${LAUNCH}/finalize_job_outer_attempt4_startup_baseline.py" | tee "${OUTPUT}/startup_baseline_validation.log"
