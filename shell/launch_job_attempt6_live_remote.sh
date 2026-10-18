#!/usr/bin/env bash
set -euo pipefail

lane=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
attempt="$lane/history/job/outer/attempt_6_live_target_relative_20260723"
workspace="$attempt/code"
legacy_workspace="$lane/workspace_outer_attempt4_formal_dbqps_v1_retry3"
launch="$lane/launch_outer_attempt4_formal_dbqps_v1_retry3"
frozen="$lane/bundle/attempt8_frozen"
control="$lane/work/control_job_live_attempt6"
venv_site="$lane/runtime/python/lib/python3.11/site-packages"
sealed_source="$lane/history/job/outer/attempt_5_formal_tsgd_v1/sealed_resource_models_p31_v1"
sealed_destination="$attempt/sealed_resource_models_p31_v1"

ensure_sealed_model() {
    local name="$1"
    local expected_sha256="$2"
    local source_path="$sealed_source/$name"
    local destination_path="$sealed_destination/$name"
    local actual_sha256=""

    if [[ -f "$destination_path" ]]; then
        actual_sha256="$(sha256sum "$destination_path" | awk '{print $1}')"
    fi
    if [[ "$actual_sha256" == "$expected_sha256" ]]; then
        return
    fi

    if [[ ! -f "$source_path" ]]; then
        echo "missing sealed JOB model source: $source_path" >&2
        exit 1
    fi
    actual_sha256="$(sha256sum "$source_path" | awk '{print $1}')"
    if [[ "$actual_sha256" != "$expected_sha256" ]]; then
        echo "sealed JOB model source checksum mismatch: $source_path" >&2
        exit 1
    fi
    install -m 0444 "$source_path" "$destination_path"
    actual_sha256="$(sha256sum "$destination_path" | awk '{print $1}')"
    if [[ "$actual_sha256" != "$expected_sha256" ]]; then
        echo "sealed JOB model destination checksum mismatch: $destination_path" >&2
        exit 1
    fi
}

mkdir -p "$sealed_destination"
ensure_sealed_model \
    cpu_resource_models.json \
    23ccf93ec42b85925db8367957d9066dbe553129c69a5c60a3c2185981e7f306
ensure_sealed_model \
    io_resource_models.json \
    4706567aeffd8e3d80935d1169d2c2807f38c31bfa615e07e0e0216655224257
ensure_sealed_model \
    memory_resource_models.json \
    85188f9c936b3d989adab751e36bd3d8237a104d082199e2dedd05fb1812b23c
ensure_sealed_model \
    resource_model_retrain_summary.json \
    63553471142b82bb1f6206618ac59d30eeef13ab41c0df5d6af0841178fe3dfa

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="$workspace:$legacy_workspace:$venv_site"
export PYTHONNOUSERSITE=1
export OPTIMIZER_CONFIG="$attempt/config.json"
export METRICS_SOURCE=plan-estimator
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1
export BONECT_OPTIMIZER_LOCK_PATH="$lane/lease/job_live_attempt6.lock"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="$attempt"
export RA_PG_CONTROL_ROOT="$control"
export RA_PG_FROZEN_ROOT="$frozen"
export RA_PG_TEMPLATE_ROOT="$lane/template"
export RA_PG_WORK_ROOT="$lane/work/runtime_job_live_attempt6"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53 RA_PG_CPUSET_MEMS=0 RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480
export RA_PG_SHARED_BUFFERS=2GB RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728 RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_live_attempt6
export RA_PG_OUTER_ATTEMPT=6
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="$lane/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="$lane/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=130
export RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=130
export RA_PG_UNSTABLE_REPLICATE_TOTAL=3
export RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=0
export RA_PG_R0_MEAN_PROFILE_GATE=0

if [[ "${1:-}" == "--preflight-only" ]]; then
    /home/anonymous/anaconda3/bin/python3 - "$OPTIMIZER_CONFIG" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
config = json.loads(config_path.read_text(encoding="utf-8"))
estimator = config["metrics"]["plan_estimator"]
physical = estimator["physical_model"]
direction_gate = estimator["direction_validity_gate"]
if not physical.get("enabled"):
    raise SystemExit("JOB preflight failed: physical_model.enabled is false")
if physical.get("error_bounds_calibrated"):
    raise SystemExit(
        "JOB preflight failed: uncalibrated physical error bounds must not prune"
    )
if not direction_gate.get("enabled"):
    raise SystemExit("JOB preflight failed: direction_validity_gate.enabled is false")
if direction_gate.get("target_resource") != "memory":
    raise SystemExit("JOB preflight failed: direction target is not memory")
print("JOB attempt6 launch preflight: PASS", flush=True)
PY
    exit 0
fi
if [[ $# -ne 0 ]]; then
    echo "usage: $0 [--preflight-only]" >&2
    exit 2
fi

RA_PG_CONTROL_ROOT="$control" "$launch/cleanup_job_outer_control.sh" || true
RA_PG_CONTROL_ROOT="$control" RA_PG_OUTPUT_ROOT="$attempt" \
    "$launch/setup_job_outer_control.sh"
exec /home/anonymous/anaconda3/bin/python3 -u "$workspace/parameter_optimizer.py"
