#!/usr/bin/env bash
set -euo pipefail

campaign_id="pg5_common_outer_bgallowed_20260722_v1"
lane_root="/mnt/nvme2n1/anonymous/resource_adaptive/${campaign_id}/tpch_node04"
attempt_id="${ATTEMPT_ID:-29}"
cpu_limit_cores="${CPU_LIMIT_CORES:-8}"
memory_limit_bytes="${MEMORY_LIMIT_BYTES:-2147483648}"
memory_limit_docker="${MEMORY_LIMIT_DOCKER:-2048m}"
io_read_cap_bytes_s="${IO_READ_CAP_BYTES_S:-13312}"
io_write_cap_bytes_s="${IO_WRITE_CAP_BYTES_S:-13312}"
io_read_cap_mib_s="${IO_READ_CAP_MIB_S:-0.0126953125}"
io_write_cap_mib_s="${IO_WRITE_CAP_MIB_S:-0.0126953125}"
io_total_cap_mib_s="${IO_TOTAL_CAP_MIB_S:-0.025390625}"
workers="${WORKERS:-32}"
container="pg5_tpch_s04_20260722"
port="64304"
database="template_fullgrid_tpch_pg5"
python_root="${lane_root}/runtime/python311"
output_dir="${lane_root}/baseline_r0_attempt_${attempt_id}/point"
log_path="${lane_root}/logs/baseline_r0_attempt_${attempt_id}.log"
pid_path="${lane_root}/runtime/baseline_r0_attempt_${attempt_id}.pid"
evidence_dir="${lane_root}/evidence/r0_attempt_${attempt_id}"
code_dir="${lane_root}/code"

if [[ -e "${output_dir}" ]]; then
    echo "attempt${attempt_id} output already exists: ${output_dir}" >&2
    exit 2
fi
if ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "missing owned TPC-H container: ${container}" >&2
    exit 3
fi
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null; then
    echo "another owned TPC-H baseline runner is active" >&2
    exit 4
fi

mkdir -p "${evidence_dir}" "$(dirname "${log_path}")" "$(dirname "${pid_path}")"
{
    printf '%s  %s\n' \
        "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588" \
        "${code_dir}/common_scoring_contract.json"
    printf '%s  %s\n' \
        "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e" \
        "${code_dir}/r0_resource_gate_contract.json"
    printf '%s  %s\n' \
        "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064" \
        "${code_dir}/io_utilization_contract.json"
    printf '%s  %s\n' \
        "05f3e855407b23646f755b93b2035c2835007f2d25335168cc986ddb4cce8857" \
        "${code_dir}/stability_gate_contract_v3.json"
    printf '%s  %s\n' \
        "1c85672bb55250df2c471808e571c7ff7145e3a6c3c6b729076360e416c08d44" \
        "${code_dir}/formal_contracts.py"
    printf '%s  %s\n' \
        "2b1ffcd8326a024e47644debaed667cb3ab469c10863a3dd85a451d94304e452" \
        "${code_dir}/run_pg_weighted_sql_baseline.py"
} > "${evidence_dir}/contract_checksums.sha256"
sha256sum -c "${evidence_dir}/contract_checksums.sha256" \
    > "${evidence_dir}/contract_checksum_readback.txt"

docker update \
    --cpuset-cpus 0-15,36-51 \
    --cpus "${cpu_limit_cores}" \
    --memory "${memory_limit_docker}" \
    --memory-swap "${memory_limit_docker}" \
    "${container}" > "${evidence_dir}/docker_update.txt"

container_id="$(docker inspect -f '{{.Id}}' "${container}")"
blkio_root="/sys/fs/cgroup/blkio/docker/${container_id}"
printf '259:3 %s\n' "${io_read_cap_bytes_s}" \
    | sudo tee "${blkio_root}/blkio.throttle.read_bps_device" >/dev/null
printf '259:3 %s\n' "${io_write_cap_bytes_s}" \
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
    "select current_database(), current_setting('server_version'), current_setting('shared_buffers'), current_setting('work_mem'), current_setting('max_parallel_workers_per_gather');" \
    > "${evidence_dir}/postgresql_readback.txt"

nohup env PYTHONPATH="${code_dir}" \
    "${python_root}/bin/python" -u \
    "${code_dir}/run_pg_weighted_sql_baseline.py" \
    --port "${port}" \
    --database "${database}" \
    --container "${container}" \
    --sql-dir "${code_dir}/queries" \
    --output-dir "${output_dir}" \
    --workers "${workers}" \
    --session-work-mem 1MB \
    --cpu-limit-cores "${cpu_limit_cores}" \
    --memory-limit-bytes "${memory_limit_bytes}" \
    --io-total-limit-mib-s "${io_total_cap_mib_s}" \
    --io-read-cap-mib-s "${io_read_cap_mib_s}" \
    --io-write-cap-mib-s "${io_write_cap_mib_s}" \
    --r0-target-resource memory \
    > "${log_path}" 2>&1 < /dev/null &
runner_pid="$!"
echo "${runner_pid}" > "${pid_path}"

sleep 2
if ! kill -0 "${runner_pid}" 2>/dev/null; then
    echo "attempt${attempt_id} runner exited during launch; inspect ${log_path}" >&2
    exit 5
fi

printf 'TPC-H R0 attempt%s started: pid=%s output=%s\n' \
    "${attempt_id}" "${runner_pid}" "${output_dir}"
