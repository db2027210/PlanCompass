#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

"${python_bin}" "${project_root}/shell/prepare_pg_algorithm_bc_last_accepted_20260822.py"
"${project_root}/shell/launch_pg_algorithm_c_last_accepted_r67.sh"
tmux new-session -d -s pg_qpsprev_c_to_b_handoff -c "${project_root}" \
    "exec ${python_bin} -B -u ${project_root}/shell/handoff_pg_algorithm_c_r67_to_b_r3.py >> /disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260822_algorithm_c_last_accepted_qps_clean_r67/cohort_support/handoff.log 2>&1"

tmux list-sessions -F '#S' | sort | grep -E '^(pgc_qpsprev_r67_|pg_qpsprev_c_to_b_handoff)'
