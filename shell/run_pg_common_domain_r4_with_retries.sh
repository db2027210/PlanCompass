#!/usr/bin/env bash
set -euo pipefail

project_root=${PROJECT_ROOT:-/home/anonymous/program/resource_adaptive}
python_bin=${PYTHON_BIN:-/home/anonymous/miniconda3/envs/py310/bin/python}
runner=${project_root}/shell/run_pg_common_domain_frozen_champions.py
final_root=${PG_COMMON_DOMAIN_R4_ROOT:?PG_COMMON_DOMAIN_R4_ROOT is required}
gptuner_authority=${PG_GPTUNER_XPIO_AUTHORITY:?PG_GPTUNER_XPIO_AUTHORITY is required}
aqetuner_authority=${PG_AQETUNER_XPIO_AUTHORITY:?PG_AQETUNER_XPIO_AUTHORITY is required}
max_campaign_attempts=${PG_COMMON_DOMAIN_MAX_CAMPAIGN_ATTEMPTS:-3}

if [[ ! ${max_campaign_attempts} =~ ^[1-9][0-9]*$ ]]; then
    echo "invalid PG_COMMON_DOMAIN_MAX_CAMPAIGN_ATTEMPTS=${max_campaign_attempts}" >&2
    exit 2
fi

for ((attempt = 1; attempt <= max_campaign_attempts; attempt++)); do
    if [[ -e ${final_root} ]]; then
        preserved_root=${final_root}_failed_attempt_$(printf '%02d' $((attempt - 1)))
        if [[ -e ${preserved_root} ]]; then
            preserved_root=${preserved_root}_$(date +%Y%m%dT%H%M%S)
        fi
        mv "${final_root}" "${preserved_root}"
        echo "preserved failed common-domain campaign at ${preserved_root}" >&2
    fi
    mkdir -p "${final_root}"
    printf '%s\n' "campaign_attempt=${attempt}/${max_campaign_attempts}" >> "${final_root}/controller.log"

    set +e
    "${python_bin}" -B -u "${runner}" \
        --output-dir "${final_root}" \
        --seed 20260827 \
        --max-stage-attempts 3 \
        --maximum-baseline-drift-pct 10 \
        --gptuner-xp-io-authority "${gptuner_authority}" \
        --aqetuner-xp-io-authority "${aqetuner_authority}" \
        >> "${final_root}/controller.log" 2>&1
    runner_rc=$?
    set -e

    # A signal-driven clean stop is an operator-approved campaign boundary,
    # not a technical failure that should immediately consume another retry.
    # In particular, this prevents a drift-contaminated campaign from being
    # relaunched while the external interference is still present.
    terminal_phase=""
    if [[ -f ${final_root}/state.json ]]; then
        terminal_phase=$(jq -r '.phase // ""' "${final_root}/state.json")
    fi
    if [[ ${terminal_phase} == "clean_stopped" || ${terminal_phase} == "clean_stop_requested" ]]; then
        jq -n \
            --arg root "${final_root}" \
            --arg stopped_at "$(date --iso-8601=seconds)" \
            --arg phase "${terminal_phase}" \
            --argjson attempt "${attempt}" \
            --argjson runner_rc "${runner_rc}" \
            '{contract: "pg_common_domain_r4_clean_stop_receipt_v1",
              phase: $phase,
              root: $root,
              attempt: $attempt,
              runner_rc: $runner_rc,
              stopped_at: $stopped_at,
              evidence_preserved: true,
              automatic_retry_suppressed: true}' \
            > "${final_root}/campaign_clean_stop_receipt.json"
        exit 3
    fi
    if [[ ${terminal_phase} == "failed_closed_adjacent_baseline_drift" ]]; then
        jq -n \
            --arg root "${final_root}" \
            --arg stopped_at "$(date --iso-8601=seconds)" \
            --arg phase "${terminal_phase}" \
            --argjson attempt "${attempt}" \
            --argjson runner_rc "${runner_rc}" \
            '{contract: "pg_common_domain_r4_drift_stop_receipt_v1",
              phase: $phase,
              root: $root,
              attempt: $attempt,
              runner_rc: $runner_rc,
              stopped_at: $stopped_at,
              evidence_preserved: true,
              automatic_retry_suppressed: true}' \
            > "${final_root}/campaign_drift_stop_receipt.json"
        exit 4
    fi

    if [[ ${runner_rc} -eq 0 ]] \
        && [[ -f ${final_root}/authority_manifest.json ]] \
        && jq -e '.contract == "pg_four_scene_adjacent_bracketed_frozen_champion_replay_v2" and .phase == "complete"' \
            "${final_root}/authority_manifest.json" >/dev/null; then
        jq -n \
            --arg root "${final_root}" \
            --arg completed_at "$(date --iso-8601=seconds)" \
            --argjson successful_attempt "${attempt}" \
            --argjson maximum_attempts "${max_campaign_attempts}" \
            '{contract: "pg_common_domain_r4_campaign_retry_receipt_v1",
              phase: "complete",
              successful_root: $root,
              successful_attempt: $successful_attempt,
              maximum_attempts: $maximum_attempts,
              failed_attempts_preserved: ($successful_attempt - 1),
              completed_at: $completed_at}' \
            > "${final_root}/campaign_retry_receipt.json"
        exit 0
    fi

    jq -n \
        --arg root "${final_root}" \
        --arg failed_at "$(date --iso-8601=seconds)" \
        --argjson attempt "${attempt}" \
        --argjson runner_rc "${runner_rc}" \
        '{contract: "pg_common_domain_r4_failed_campaign_receipt_v1",
          phase: "failed_closed",
          root: $root,
          attempt: $attempt,
          runner_rc: $runner_rc,
          failed_at: $failed_at,
          evidence_preserved: true}' \
        > "${final_root}/campaign_failure_receipt.json"
done

echo "all ${max_campaign_attempts} common-domain campaign attempts failed closed" >&2
exit 1
