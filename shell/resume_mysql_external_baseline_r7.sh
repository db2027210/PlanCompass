#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
mysql_root=/home/anonymous/program/resource_adaptive_mysql
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
binding=${project_root}/config/formal/mysql_external_baseline_independent_workers_20260826_v1.json
binding_sha=30cd52c2c82f0b7b08b29ea9d24a9d78bab998cbc61601b3d210647db023ed68
campaign_root=/disk2/resource_adaptive_mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_r7
runner=${project_root}/shell/run_mysql_four_scene_external_baselines.py
gptuner_state=${campaign_root}/gptuner/state.json

cd "${project_root}"
test "$(sha256sum "${binding}" | awk '{print $1}')" = "${binding_sha}"
test -f "${gptuner_state}"
state_status=$(jq -r '.status' "${gptuner_state}")
if test "${state_status}" = "RUNNING"; then
    jq -e '
        .clean_stop_requested == true
        and ((.clean_stop_signal == 15) or (.clean_stop_signal == 2))
        and (.phase | startswith("SEARCH_"))
    ' "${gptuner_state}" >/dev/null
    completed_search_waves=$(jq -r '.completed_search_waves' "${gptuner_state}")
    expected_stage=$(printf 'SEARCH_WAVE_%03d' "$((completed_search_waves + 1))")
    test "$(jq -r '.inflight_wave.stage' "${gptuner_state}")" = "${expected_stage}"
    predecessor_pid=$(jq -r '.pid' "${gptuner_state}")
    if kill -0 "${predecessor_pid}" 2>/dev/null; then
        echo "interrupted predecessor PID is still alive: ${predecessor_pid}" >&2
        exit 1
    fi
else
    test "${state_status}" = "FAILED_CLOSED" || test "${state_status}" = "CLEAN_STOPPED"
fi
jq -e '
    (.completed_search_waves | type == "number")
    and (.completed_search_waves >= 1)
    and (.completed_search_waves < 9)
    and ([.observations[] | length] | unique | length == 1)
    and (([.observations[] | length] | unique | .[0]) == (.completed_search_waves * 2))
    and (([.observations[] | length] | unique | .[0]) < .candidate_budget_per_scene)
' "${gptuner_state}" >/dev/null

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
    --output-dir "${campaign_root}/gptuner" \
    --candidate-budget 18 \
    --seed 20260826 \
    --resume

if test ! -f "${campaign_root}/aqetuner/authority_manifest.json"; then
    "${python_bin}" -B -u "${runner}" \
        --method aqetuner \
        --worker-binding "${binding}" \
        --output-dir "${campaign_root}/aqetuner" \
        --candidate-budget 18 \
        --seed 20260830
fi
