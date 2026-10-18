#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
mysql_root=/home/anonymous/program/resource_adaptive_mysql
llm_python=/home/anonymous/miniconda3/envs/py313/bin/python
knowledge_runner=${project_root}/shell/prepare_gptuner_online_knowledge.py
model=${GPTUNER_LLM_MODEL:-gpt-5.4-mini}
codex_home=${GPTUNER_CODEX_HOME:?GPTUNER_CODEX_HOME is required}
suite_root=${EXTERNAL_BASELINE_SUITE_ROOT:-/disk1/resource_adaptive/pg_mysql_external_baselines_20260904/codex_account_${model}_suite_v1}
pg_knowledge_root=${suite_root}/online_knowledge/postgres
mysql_knowledge_root=${suite_root}/online_knowledge/mysql
pg_config_root=${PG_EXTERNAL_CONFIG_ROOT:-/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75}
mysql_binding=${MYSQL_EXTERNAL_BINDING:-${project_root}/config/formal/mysql_external_baseline_independent_workers_disk1_20260826_v4.json}
mysql_parameter_root=${mysql_root}/config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1
max_scene_tokens=${GPTUNER_MAX_REPORTED_TOKENS_PER_SCENE:-2000000}

test -d "${codex_home}"
codex_login_status=$(env CODEX_HOME="${codex_home}" codex login status 2>&1)
case "${codex_login_status}" in
    *"Logged in using ChatGPT"*) ;;
    *) echo "isolated Codex account is not logged in" >&2; exit 2 ;;
esac
test -f "${knowledge_runner}"
test -f "${mysql_binding}"
mkdir -p "${pg_knowledge_root}" "${mysql_knowledge_root}"

check_snapshot() {
    local snapshot=${1:?snapshot required}
    local scene=${2:?scene required}
    local dbms=${3:?dbms required}
    jq -e \
        --arg scene "${scene}" \
        --arg dbms "${dbms}" \
        --arg model "${model}" \
        --argjson max_tokens "${max_scene_tokens}" \
        '.contract == "gptuner_online_knowledge_snapshot_v1"
         and .status == "complete"
         and .scene == $scene
         and .dbms == $dbms
         and .model == $model
         and .provider == "codex-chatgpt-account"
         and .paper_model == "gpt-4"
         and .paper_model_equivalent == false
         and .stages.prompt_ensemble_votes == 5
         and .llm_call_count > 0
         and .total_tokens <= $max_tokens' \
        "${snapshot}/manifest.json" >/dev/null
}

prepare_pg_scene() {
    local scene=${1:?scene required}
    local seed=${2:?seed required}
    local snapshot=${pg_knowledge_root}/${scene}
    if test -f "${snapshot}/manifest.json"; then
        check_snapshot "${snapshot}" "${scene}" postgres
        return
    fi
    "${llm_python}" -B -u "${knowledge_runner}" \
        --scene "${scene}" \
        --dbms postgres \
        --config "${pg_config_root}/${scene}/optimizer_config.json" \
        --output-dir "${snapshot}" \
        --provider codex-cli \
        --model "${model}" \
        --allow-model-substitution \
        --codex-home "${codex_home}" \
        --codex-reasoning-effort low \
        --seed "${seed}" \
        > "${suite_root}/knowledge_postgres_${scene}.log" 2>&1
    check_snapshot "${snapshot}" "${scene}" postgres
}

declare -A mysql_parameter_tables=(
    [job_memory]=T.job_memory.operator_physics_v1.json
    [tpch_sf4_memory]=T.tpch_memory.operator_physics_v1.json
    [hybench_xp_cpu]=T.hybench_xp_cpu.top4.operator_physics_v1.json
    [hybench_xp_io]=T.hybench_xp_io.top4.operator_physics_v1.json
)

prepare_mysql_scene() {
    local scene=${1:?scene required}
    local seed=${2:?seed required}
    local snapshot=${mysql_knowledge_root}/${scene}
    if test -f "${snapshot}/manifest.json"; then
        check_snapshot "${snapshot}" "${scene}" mysql
        return
    fi
    "${llm_python}" -B -u "${knowledge_runner}" \
        --scene "${scene}" \
        --dbms mysql \
        --worker-binding "${mysql_binding}" \
        --parameter-table "${mysql_parameter_root}/${mysql_parameter_tables[$scene]}" \
        --output-dir "${snapshot}" \
        --provider codex-cli \
        --model "${model}" \
        --allow-model-substitution \
        --codex-home "${codex_home}" \
        --codex-reasoning-effort low \
        --seed "${seed}" \
        > "${suite_root}/knowledge_mysql_${scene}.log" 2>&1
    check_snapshot "${snapshot}" "${scene}" mysql
}

# The first full scene is the account-capacity gate; later scenes run only if it passes.
prepare_pg_scene job_memory 20261011
prepare_pg_scene tpch_memory 20261012
prepare_pg_scene xp_cpu 20261013
prepare_pg_scene xp_io 20261014

prepare_mysql_scene job_memory 20262001
prepare_mysql_scene tpch_sf4_memory 20262002
prepare_mysql_scene hybench_xp_cpu 20262003
prepare_mysql_scene hybench_xp_io 20262004

jq -n \
    --arg model "${model}" \
    --arg root "${suite_root}" \
    --arg completed_at "$(date --iso-8601=seconds)" \
    '{contract: "gptuner_codex_account_knowledge_campaign_v1",
      status: "complete",
      model: $model,
      provider: "codex-chatgpt-account",
      paper_model: "gpt-4",
      paper_model_equivalent: false,
      dbms: ["postgres", "mysql"],
      scenes_per_dbms: 4,
      root: $root,
      completed_at: $completed_at}' \
    > "${suite_root}/knowledge_campaign.json.tmp"
mv "${suite_root}/knowledge_campaign.json.tmp" \
    "${suite_root}/knowledge_campaign.json"

echo "GPTuner Codex-account knowledge campaign complete" >&2
