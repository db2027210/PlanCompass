#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
suite=${project_root}/shell/run_pg_mysql_external_baseline_publication_suite_v1.sh

: "${GEMINI_API_KEY:?Set GEMINI_API_KEY to an authorized Google Gemini API key}"

export GPTUNER_LLM_PROVIDER=gemini-api
export GPTUNER_LLM_MODEL=${GPTUNER_LLM_MODEL:-gemini-2.5-flash}
export GPTUNER_ALLOW_MODEL_SUBSTITUTION=1
export EXTERNAL_BASELINE_REPLICATES=${EXTERNAL_BASELINE_REPLICATES:-1}

echo "Starting explicitly labelled GPTuner Google Gemini variant" >&2
echo "model=${GPTUNER_LLM_MODEL}" >&2
echo "provider=google-gemini-api" >&2
echo "paper_model_equivalent=false" >&2

exec bash "${suite}"
