#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
gptuner_runner=${project_root}/shell/run_pg_mysql_external_baseline_deepseek_suite_v1.sh
suite_root=${EXTERNAL_BASELINE_SUITE_ROOT:-/disk1/resource_adaptive/pg_mysql_external_baselines_20260904/google_gemini-2.5-flash_suite_v1}
aqetuner_authority=${suite_root}/aqetuner_single_campaign_authority.json
aqetuner_session=${AQETUNER_TMUX_SESSION:-aqetuner_pgmysql_gemini_suite_20260904}
mysql_binding=${MYSQL_EXTERNAL_BINDING:-${project_root}/config/formal/mysql_external_baseline_independent_workers_disk1_gemini_20260904_v3.json}

: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY is required}"

while true; do
    if test -f "${aqetuner_authority}" && jq -e '
        .contract == "pg_mysql_aqetuner_publication_single_v1"
        and .status == "complete"
        and .replicate_count == 1
        and .four_scenes_per_engine == true
        and .two_docker_workers_per_scene == true
    ' "${aqetuner_authority}" >/dev/null; then
        break
    fi

    if ! tmux has-session -t "${aqetuner_session}" 2>/dev/null; then
        echo "AQETuner session disappeared before its authority completed" >&2
        exit 1
    fi
    pane_dead=$(tmux display-message -p -t "${aqetuner_session}:0.0" '#{pane_dead}')
    if test "${pane_dead}" = 1; then
        echo "AQETuner exited before its authority completed" >&2
        exit 1
    fi
    sleep 30
done

export EXTERNAL_BASELINE_SUITE_ROOT=${suite_root}
export EXTERNAL_BASELINE_REPLICATES=1
export MYSQL_EXTERNAL_REUSE_PREPARED_WORKERS=1
export MYSQL_EXTERNAL_BINDING=${mysql_binding}
exec bash "${gptuner_runner}"
