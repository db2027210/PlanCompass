#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
algorithm=${1:?usage: run_pg_algorithm_bc_monotonic_top10_20260822_lane.sh B_OR_C LANE}
lane=${2:?usage: run_pg_algorithm_bc_monotonic_top10_20260822_lane.sh B_OR_C LANE}
base_runner=${project_root}/shell/run_pg_algorithm_bc_last_accepted_20260822_lane.sh

case "${algorithm}" in
    C)
        export PG_ALGORITHM_BC_C_ROOT=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r74
        export PG_ALGORITHM_BC_C_COHORT_PREFIX=20260822_algorithm_c_monotonic_top10_r74
        ;;
    B)
        export PG_ALGORITHM_BC_B_ROOT=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r10
        export PG_ALGORITHM_BC_B_COHORT_PREFIX=20260822_algorithm_b_monotonic_top10_r10
        ;;
    *)
        echo "unknown algorithm: ${algorithm}" >&2
        exit 2
        ;;
esac

export BONECT_ALGORITHM_C_MIN_TMP_AVAILABLE_BYTES=$((4 * 1024 * 1024 * 1024))

exec "${base_runner}" "${algorithm}" "${lane}"
