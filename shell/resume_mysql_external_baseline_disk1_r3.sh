#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
mysql_root=/home/anonymous/program/resource_adaptive_mysql
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
binding=${project_root}/config/formal/mysql_external_baseline_independent_workers_disk1_20260826_v2.json
binding_sha=3861cc43b51d24cad7413606b23e35b559893aac82a5ad3f0c52851e84aa4a97
campaign_root=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r3
gptuner_root=${campaign_root}/gptuner_disk1_r3
aqetuner_root=${campaign_root}/aqetuner_disk1_r3
gptuner_state=${gptuner_root}/state.json
runner=${project_root}/shell/run_mysql_four_scene_external_baselines.py
postprocess=${project_root}/shell/postprocess_mysql_external_baselines_r7.sh

cd "${project_root}"
test "$(sha256sum "${binding}" | awk '{print $1}')" = "${binding_sha}"
test -f "${gptuner_state}"
jq -e '
    .status == "CLEAN_STOPPED"
    and .phase == "CLEAN_STOPPED"
    and .clean_stop_requested == true
    and ((.clean_stop_signal == 15) or (.clean_stop_signal == 2) or (.clean_stop_signal == 1))
    and .clean_restore.status == "PASS"
    and (.completed_search_waves >= 1)
    and (.completed_search_waves < 9)
    and ([.observations[] | length] | unique | length == 1)
    and (([.observations[] | length] | unique | .[0]) == (.completed_search_waves * 2))
    and (.physical_workload_invocations == (.valid_replays + .technical_failures))
    and ([.observations[][] | select(.technical_failure == true) | .valid] | all(. == false))
' "${gptuner_state}" >/dev/null

predecessor_pid=$(jq -r '.pid // 0' "${gptuner_state}")
if test "${predecessor_pid}" -gt 1 && kill -0 "${predecessor_pid}" 2>/dev/null; then
    echo "clean-stopped predecessor is still alive: ${predecessor_pid}" >&2
    exit 1
fi

while IFS=$'\t' read -r worker_id container host_port credentials; do
    running=$(timeout 30 docker inspect -f '{{.State.Running}}' "${container}")
    if test "${running}" != "true"; then
        echo "$(date --iso-8601=seconds) start ${worker_id} ${container}"
        timeout 300 docker start "${container}"
    fi
    deadline=$((SECONDS + 360))
    while true; do
        if timeout 20 mysql \
            --defaults-file="${credentials}" \
            --host=127.0.0.1 \
            --port="${host_port}" \
            --connect-timeout=10 \
            -NBe 'SELECT 1' >/dev/null 2>&1; then
            echo "$(date --iso-8601=seconds) ready ${worker_id}"
            break
        fi
        if (( SECONDS >= deadline )); then
            echo "${worker_id}: MySQL readiness timeout" >&2
            exit 1
        fi
        sleep 3
    done
done < <(
    jq -r '.workers[] | [.worker_id,.container,.host_port,.credential_defaults] | @tsv' "${binding}"
)

export PYTHONPATH="${mysql_root}:${project_root}/shell:${project_root}"
export PYTHONUNBUFFERED=1

"${python_bin}" -B -u "${runner}" \
    --method gptuner \
    --worker-binding "${binding}" \
    --output-dir "${gptuner_root}" \
    --candidate-budget 18 \
    --seed 20260826 \
    --resume

test "$(jq -r '.status' "${gptuner_state}")" = "COMPLETE"
if test ! -f "${aqetuner_root}/authority_manifest.json"; then
    "${python_bin}" -B -u "${runner}" \
        --method aqetuner \
        --worker-binding "${binding}" \
        --output-dir "${aqetuner_root}" \
        --candidate-budget 18 \
        --seed 20260830
fi

test "$(jq -r '.status' "${aqetuner_root}/state.json")" = "COMPLETE"
exec "${postprocess}"
