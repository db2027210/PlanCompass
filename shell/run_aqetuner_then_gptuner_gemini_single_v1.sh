#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
aqetuner_runner=${project_root}/shell/run_pg_mysql_aqetuner_publication_single_v1.sh
gptuner_runner=${project_root}/shell/run_pg_mysql_external_baseline_gemini_suite_v1.sh
suite_root=${EXTERNAL_BASELINE_SUITE_ROOT:-/disk1/resource_adaptive/pg_mysql_external_baselines_20260904/google_gemini-2.5-flash_suite_v1}

: "${GEMINI_API_KEY:?GEMINI_API_KEY is required}"

export EXTERNAL_BASELINE_SUITE_ROOT=${suite_root}
export AQETUNER_SINGLE_SUITE_ROOT=${suite_root}
export EXTERNAL_BASELINE_REPLICATES=1

bash "${aqetuner_runner}"

# AQETuner leaves the MySQL workers at the audited defaults. Reuse exactly
# those workers for GPTuner so the binding identity remains cross-method equal.
export MYSQL_EXTERNAL_REUSE_PREPARED_WORKERS=1
exec bash "${gptuner_runner}"
