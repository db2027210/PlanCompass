#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r3
postprocess=${project_root}/shell/postprocess_mysql_external_baselines_r7.sh

while true; do
    gptuner_state=${campaign_root}/gptuner_disk1_r3/state.json
    if test -f "${gptuner_state}"; then
        gptuner_status=$(jq -r '.status // ""' "${gptuner_state}")
        if test "${gptuner_status}" = "FAILED_CLOSED"; then
            echo "GPTuner failed closed; refusing automatic postprocess" >&2
            exit 1
        fi
    fi

    aqetuner_state=${campaign_root}/aqetuner_disk1_r3/state.json
    if test -f "${aqetuner_state}"; then
        aqetuner_status=$(jq -r '.status // ""' "${aqetuner_state}")
        if test "${aqetuner_status}" = "FAILED_CLOSED"; then
            echo "AQETuner failed closed; refusing automatic postprocess" >&2
            exit 1
        fi
        if test "${gptuner_status:-}" = "COMPLETE" \
            && test "${aqetuner_status}" = "COMPLETE"; then
            exec "${postprocess}"
        fi
    fi
    sleep 20
done
