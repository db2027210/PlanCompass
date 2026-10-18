#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
pipeline=${project_root}/shell/run_pg_four_scene_external_baselines.sh
baseline_root=${PG_EXTERNAL_BASELINE_ROOT:-/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/20260825_gptuner_aqetuner_four_r1}
original_pipeline_pid=${PG_EXTERNAL_ORIGINAL_PIPELINE_PID:-2338038}

gptuner_complete() {
    local scene authority
    for scene in job_memory tpch_memory xp_cpu xp_io; do
        authority=${baseline_root}/gptuner/${scene}/authority_manifest.json
        if [[ ! -f "${authority}" ]] || ! jq -e \
            '.phase == "complete"
             and .method == "gptuner"
             and .candidate_physical_invocations == .candidate_budget
             and .replay_parallelism == 2
             and .validated_champion.physical_valid == true
             and .validated_champion.parallel_worker_aggregate.worker_count == 2
             and (.validated_champion.params == .frozen_search_champion.params)' \
            "${authority}" >/dev/null; then
            return 1
        fi
    done
}

while ! gptuner_complete; do
    sleep 30
done

# The first pipeline remembers the pre-workload JOB/TPC-H failures in its local
# shell.  Let it exit after the two XP lanes finish, then start the patched
# resumable pipeline.  It skips all verified GPTuner scenes and starts the
# four-scene AQETuner cohort without duplicating any replay.
while kill -0 "${original_pipeline_pid}" 2>/dev/null; do
    sleep 10
done

exec "${pipeline}" >> "${baseline_root}/pipeline_handoff.log" 2>&1
