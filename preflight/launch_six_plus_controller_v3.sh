#!/usr/bin/env bash

set -euo pipefail

CODEX_BIN="$(command -v codex)"
PG_ROOT="/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated"
MYSQL_ROOT="/home/anonymous/program/resource_adaptive_mysql"
PG_PROMPTS="$MYSQL_ROOT/preflight/pg_three_algorithm_session_prompts_and_goals_20260721.md"
MYSQL_PROMPTS="$MYSQL_ROOT/preflight/mysql_three_algorithm_session_prompts_goals_20260721.md"
CONTROLLER_PROMPT="$MYSQL_ROOT/preflight/total_controller_prompt_and_goal_20260721.md"

SESSIONS=(
    recost_pg_brute_20260721_v3
    recost_pg_random_20260721_v3
    recost_pg_recost_20260721_v3
    recost_mysql_brute_20260721_v3
    recost_mysql_random_20260721_v3
    recost_mysql_recost_20260721_v3
    recost_controller_20260721_v3
)

for session_name in "${SESSIONS[@]}"; do
    if tmux has-session -t "=$session_name" 2>/dev/null; then
        echo "refusing to replace existing tmux session: $session_name" >&2
        exit 1
    fi
done

launch_codex() {
    local session_name="$1"
    local workdir="$2"
    local prompt="$3"
    local command=""
    local argument
    local -a arguments=(
        "$CODEX_BIN"
        --dangerously-bypass-approvals-and-sandbox
        --dangerously-bypass-hook-trust
        --no-alt-screen
        -m gpt-5.6-sol
        -c 'model_reasoning_effort="medium"'
        -C "$workdir"
        "$prompt"
    )

    for argument in "${arguments[@]}"; do
        printf -v command '%s %q' "$command" "$argument"
    done
    tmux new-session -d -s "$session_name" -c "$workdir" "$command"
}

launch_codex recost_pg_brute_20260721_v3 "$PG_ROOT" \
    " v3  worker $PG_PROMPTS3 PostgreSQL  Goal  Prompt frozen_formal_config_20260721_v3 Agent "
launch_codex recost_pg_random_20260721_v3 "$PG_ROOT" \
    " v3  worker $PG_PROMPTS4 PostgreSQL  Goal  Prompt frozen_formal_config_20260721_v3 Agent "
launch_codex recost_pg_recost_20260721_v3 "$PG_ROOT" \
    " v3  worker $PG_PROMPTS5 PostgreSQL Recost  Goal  Prompt frozen_formal_config_20260721_v3recost_estimator.json  SHA Agent "
launch_codex recost_mysql_brute_20260721_v3 "$MYSQL_ROOT" \
    " v3  worker $MYSQL_PROMPTS3 MySQL  Goal  Prompt frozen_formal_config_20260721_v3 Agent "
launch_codex recost_mysql_random_20260721_v3 "$MYSQL_ROOT" \
    " v3  worker $MYSQL_PROMPTS4 MySQL  Goal  Prompt frozen_formal_config_20260721_v3 Agent "
launch_codex recost_mysql_recost_20260721_v3 "$MYSQL_ROOT" \
    " v3  worker $MYSQL_PROMPTS5 MySQL Recost  Goal  Prompt frozen_formal_config_20260721_v3recost_estimator.json  SHA Agent "
launch_codex recost_controller_20260721_v3 "$MYSQL_ROOT" \
    " v3  $CONTROLLER_PROMPT Goal  PromptConfiguration"

tmux list-sessions -F '#{session_name}' | grep 'recost_.*20260721_v3' | sort
