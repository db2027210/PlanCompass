#!/usr/bin/env bash
set -euo pipefail

campaign_id="pg5_common_outer_bgallowed_20260722_v1"
lane_root="/mnt/nvme2n1/anonymous/resource_adaptive/${campaign_id}/tpch_node04"
container="pg5_tpch_s04_20260722"
port="64304"
database="fullgrid_tpch"
template_database="template_fullgrid_tpch_pg5"
python_root="${lane_root}/runtime/python311"
output_dir="${lane_root}/baseline_r0_attempt_4"
log_path="${lane_root}/logs/baseline_r0_attempt_4.log"
pid_path="${lane_root}/runtime/baseline_r0_attempt_4.pid"
evidence_dir="${lane_root}/evidence/r0_attempt_4"
scoring_contract="${lane_root}/code/common_scoring_contract.json"
r0_gate_contract="${lane_root}/code/r0_resource_gate_contract.json"

if [[ -e "${output_dir}" ]]; then
    echo "attempt4 output already exists: ${output_dir}" >&2
    exit 2
fi
if [[ -f "${pid_path}" ]]; then
    old_pid="$(cat "${pid_path}")"
    if kill -0 "${old_pid}" 2>/dev/null; then
        echo "attempt4 runner is already active: ${old_pid}" >&2
        exit 3
    fi
fi
if ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "missing owned TPC-H container: ${container}" >&2
    exit 4
fi

mkdir -p "${evidence_dir}"

{
    printf '%s  %s\n' \
        "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588" \
        "${scoring_contract}"
    printf '%s  %s\n' \
        "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e" \
        "${r0_gate_contract}"
} > "${evidence_dir}/contract_checksums.sha256"
sha256sum -c "${evidence_dir}/contract_checksums.sha256" \
    > "${evidence_dir}/contract_checksum_readback.txt"

# Attempt 1 averaged 1,726,838,060 bytes working set and at most six query
# processes can run (two leaders plus two workers per leader). Twelve CPU
# quota cores keep CPU in the non-target 30%-50% range; 2 GiB makes the
# observed working set a Memory bottleneck (>80%). Eight MiB/s per direction
# is the first physical IO calibration for the non-target 30%-50% range.
# OOM/restart remains a hard failure.
docker update \
    --cpuset-cpus 0-15,36-51 \
    --cpus 12 \
    --memory 2g \
    --memory-swap 2g \
    "${container}" > "${evidence_dir}/docker_update.txt"

container_id="$(docker inspect -f '{{.Id}}' "${container}")"
blkio_root="/sys/fs/cgroup/blkio/docker/${container_id}"
printf '259:3 8388608\n' \
    | sudo tee "${blkio_root}/blkio.throttle.read_bps_device" >/dev/null
printf '259:3 8388608\n' \
    | sudo tee "${blkio_root}/blkio.throttle.write_bps_device" >/dev/null
{
    printf 'read_bps_device='
    cat "${blkio_root}/blkio.throttle.read_bps_device"
    printf 'write_bps_device='
    cat "${blkio_root}/blkio.throttle.write_bps_device"
} > "${evidence_dir}/blkio_readback.txt"

docker inspect "${container}" > "${evidence_dir}/container_inspect.json"
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${database}" -Atc \
    "select current_setting('server_version'), current_setting('shared_buffers'), current_setting('work_mem'), current_setting('max_parallel_workers_per_gather');" \
    > "${evidence_dir}/postgresql_readback.txt"

{
    docker exec "${container}" psql \
        -p "${port}" -U postgres -d postgres -v ON_ERROR_STOP=1 -Atc \
        "select pg_terminate_backend(pid) from pg_stat_activity where datname='${database}' and pid <> pg_backend_pid();"
    docker exec "${container}" dropdb \
        -p "${port}" -U postgres --if-exists "${database}"
    docker exec "${container}" createdb \
        -p "${port}" -U postgres -T "${template_database}" "${database}"
    docker exec "${container}" psql \
        -p "${port}" -U postgres -d "${database}" -v ON_ERROR_STOP=1 -Atc \
        "select current_database(), current_setting('server_version'), current_setting('shared_preload_libraries');"
} > "${evidence_dir}/template_restore.txt"

python3 - "${evidence_dir}/resource_refreeze.json" <<'PY'
import json
import sys
from datetime import datetime

payload = {
    "schema_version": 1,
    "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
    "benchmark": "tpch",
    "attempt": 4,
    "created_at": datetime.now().astimezone().isoformat(),
    "supersedes": [
        "baseline_r0",
        "r0_attempt_2_launch_failure",
        "r0_attempt_3_launcher_python_incompatibility"
    ],
    "reason": {
        "attempt_1_cpu_avg": 0.993970593521304,
        "attempt_1_memory_avg": 0.2680405447218153,
        "attempt_1_raw_memory_avg_bytes": 1726838060.3733332,
        "attempt_1_raw_memory_max_bytes": 1797931008.0,
    },
    "resource_envelope": {
        "cpu_quota_cores": 12,
        "cpuset_cpus": "0-15,36-51",
        "cpuset_mems": "0",
        "memory_limit_bytes": 2147483648,
        "memory_swap_limit_bytes": 2147483648,
        "io_read_mib_s": 8,
        "io_write_mib_s": 8,
        "pids_limit": 512,
        "workers": 2,
    },
    "r0_gate": {
        "target_resource": "memory",
        "target_min_pct_exclusive": 80.0,
        "non_target_min_pct_inclusive": 30.0,
        "non_target_max_pct_inclusive": 50.0,
    },
    "scoring_contract_sha256": "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588",
    "r0_gate_contract_sha256": "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e",
    "hard_gates_unchanged": True,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

nohup env PYTHONPATH="${lane_root}/code" \
    "${python_root}/bin/python" \
    "${lane_root}/code/run_pg_weighted_sql_baseline.py" \
    --port "${port}" \
    --database "${database}" \
    --container "${container}" \
    --sql-dir "${lane_root}/code/queries" \
    --output-dir "${output_dir}" \
    --workers 2 \
    --cpu-limit-cores 12 \
    --memory-limit-bytes 2147483648 \
    --io-limit-mib-s 8 \
    > "${log_path}" 2>&1 < /dev/null &
runner_pid="$!"
echo "${runner_pid}" > "${pid_path}"

sleep 2
if ! kill -0 "${runner_pid}" 2>/dev/null; then
    echo "attempt4 runner exited during launch; inspect ${log_path}" >&2
    exit 5
fi

printf 'TPC-H R0 attempt4 started: pid=%s output=%s\n' \
    "${runner_pid}" "${output_dir}"
