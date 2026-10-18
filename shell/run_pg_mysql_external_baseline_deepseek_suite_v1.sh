#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
suite=${project_root}/shell/run_pg_mysql_external_baseline_publication_suite_v1.sh
preflight=${project_root}/shell/verify_deepseek_api_access.py
llm_python=/home/anonymous/miniconda3/envs/py313/bin/python

: "${DEEPSEEK_API_KEY:?Set DEEPSEEK_API_KEY to an authorized official DeepSeek API key}"

export GPTUNER_LLM_PROVIDER=deepseek-api
export GPTUNER_LLM_MODEL=deepseek-v4-pro
export GPTUNER_ALLOW_MODEL_SUBSTITUTION=1
export DEEPSEEK_BASE_URL=https://api.deepseek.com
export EXTERNAL_BASELINE_REPLICATES=${EXTERNAL_BASELINE_REPLICATES:-1}

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

"${llm_python}" -B "${preflight}" \
    --model "${GPTUNER_LLM_MODEL}" \
    --api-key-env DEEPSEEK_API_KEY \
    --base-url "${DEEPSEEK_BASE_URL}"

echo "Starting explicitly labelled GPTuner DeepSeek V4 Pro variant" >&2
echo "model=${GPTUNER_LLM_MODEL}" >&2
echo "provider=deepseek-api" >&2
echo "paper_model_equivalent=false" >&2

exec bash "${suite}"
