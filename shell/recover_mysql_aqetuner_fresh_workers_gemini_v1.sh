#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
preparer=${project_root}/shell/prepare_mysql_external_baseline_clones.py
aqetuner_runner=${project_root}/shell/run_pg_mysql_aqetuner_publication_single_v1.sh
suite_root=/disk1/resource_adaptive/pg_mysql_external_baselines_20260904/google_gemini-2.5-flash_suite_v1
runtime_root=/disk1/resource_adaptive/mysql_external_baselines_20260904/gemini_aqetuner_gptuner_workers_v1
binding=${project_root}/config/formal/mysql_external_baseline_independent_workers_disk1_gemini_20260904_v3.json
prepare_log=${suite_root}/rep_01/mysql/fresh_worker_prepare.log

containers=(
    ra_pgssd_mysql_baseline_job_w1_20260826
    ra_pgssd_mysql_baseline_job_w2_20260826
    ra_pgssd_mysql_baseline_tpch_w1_20260826
    ra_pgssd_mysql_baseline_tpch_w2_20260826
    ra_pgssd_mysql_baseline_xp_cpu_w1_20260826
    ra_pgssd_mysql_baseline_xp_cpu_w2_20260826
    ra_pgssd_mysql_baseline_xp_io_w1_20260826
    ra_pgssd_mysql_baseline_xp_io_w2_20260826
)

mkdir -p "${suite_root}/rep_01/mysql"
if ! test -f "${binding}"; then
    if test -e "${runtime_root}"; then
        echo "fresh runtime root exists without completed binding: ${runtime_root}" >&2
        exit 2
    fi
    existing=$(docker ps -a --format '{{.Names}}')
    for container in "${containers[@]}"; do
        if printf '%s\n' "${existing}" | grep -Fxq "${container}"; then
            docker rm -f "${container}" >/dev/null
        fi
    done
    "${python_bin}" -B -u "${preparer}" \
        --runtime-root "${runtime_root}" \
        --binding-output "${binding}" \
        --copy-workers 8 \
        > "${prepare_log}" 2>&1
fi

jq -e '
    .schema_version == "mysql_external_baseline_independent_workers_v1"
    and .status == "PASS"
    and .runtime_root == "/disk1/resource_adaptive/mysql_external_baselines_20260904/gemini_aqetuner_gptuner_workers_v1"
    and (.workers | length) == 8
    and all(.workers[]; .ready == true)
' "${binding}" >/dev/null

export AQETUNER_SINGLE_SUITE_ROOT=${suite_root}
export MYSQL_EXTERNAL_BINDING=${binding}
export MYSQL_EXTERNAL_REUSE_PREPARED_WORKERS=1
exec bash "${aqetuner_runner}"
