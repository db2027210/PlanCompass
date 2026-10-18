#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
r4_pid=${PG_COMMON_DOMAIN_R4_PID:?PG_COMMON_DOMAIN_R4_PID is required}
r4_root=${PG_COMMON_DOMAIN_R4_ROOT:-/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r4_corrected_xpio}
xpio_external_root=${PG_XPIO_EXTERNAL_ROOT:-/disk1/resource_adaptive/pg_external_xpio_correct_domain_20260826/20260826_xpio_40cpu24g_r1}
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
plot_python=/home/anonymous/miniconda3/envs/py39/bin/python
ppt_python=/home/anonymous/miniconda3/envs/py313/bin/python
figure_root=${project_root}/figures/algorithm_abc_mysql_bc_refresh_20260825

while kill -0 "${r4_pid}" 2>/dev/null; do
    sleep 20
done

phase=$(jq -r '.phase // ""' "${r4_root}/state.json")
if [[ "${phase}" != complete ]]; then
    echo "r4 terminal phase=${phase}; refusing final artifact generation" >&2
    exit 1
fi

PYTHONPATH=${project_root} "${python_bin}" -B \
    "${project_root}/shell/verify_pg_common_domain_frozen_champions.py" \
    "${r4_root}" \
    --output "${project_root}/docs/pg_common_domain_frozen_champion_verification_20260826.json"

PYTHONPATH=${project_root} "${python_bin}" -B \
    "${project_root}/shell/build_pg_external_baseline_comparison.py" \
    --common-domain-root "${r4_root}" \
    --xpio-external-root "${xpio_external_root}" \
    --json-output "${project_root}/docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.json" \
    --markdown-output "${project_root}/docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.md"

MPLCONFIGDIR=/tmp/pg_external_baseline_mpl_20260826 \
    "${plot_python}" -B "${figure_root}/gen_external_baseline_figures.py"
"${ppt_python}" -B "${figure_root}/build_final_presentation.py"
"${ppt_python}" -B "${figure_root}/verify_presentation.py"
"${ppt_python}" -B "${figure_root}/verify_presentation_content.py"
"${ppt_python}" -B "${figure_root}/render_presentation_approx.py" \
    "${figure_root}/algorithm_abc_external_none_mysql_formal_results_20260826.pptx" \
    "${figure_root}/rendered_20260826" \
    --width 1600 \
    --contact-columns 4

jq -n \
    --arg completed_at "$(date --iso-8601=seconds)" \
    --arg r4_root "${r4_root}" \
    --arg comparison "${project_root}/docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.json" \
    --arg pptx "${figure_root}/algorithm_abc_external_none_mysql_formal_results_20260826.pptx" \
    --arg rendered "${figure_root}/rendered_20260826" \
    --arg contact "${figure_root}/rendered_20260826_contact.png" \
    '{contract: "pg_external_comparison_artifact_pipeline_v1",
      phase: "generated_pending_visual_qa",
      common_domain_root: $r4_root,
      comparison_json: $comparison,
      presentation: $pptx,
      rendered_slides: $rendered,
      contact_sheet: $contact,
      completed_at: $completed_at}' \
    > "${figure_root}/final_artifact_pipeline_state.json"

echo "r4 verification, comparison, figures, and PPT generation complete at $(date --iso-8601=seconds)"
