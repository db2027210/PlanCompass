#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r3
gptuner_root=${campaign_root}/gptuner_disk1_r3
aqetuner_root=${campaign_root}/aqetuner_disk1_r3
figure_root=${project_root}/figures/algorithm_abc_mysql_bc_refresh_20260825
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
plot_python=/home/anonymous/miniconda3/envs/py39/bin/python
ppt_python=/home/anonymous/miniconda3/envs/py313/bin/python
comparison_json=${project_root}/docs/mysql_bc_gptuner_aqetuner_final_comparison_20260826.json
comparison_md=${project_root}/docs/mysql_bc_gptuner_aqetuner_final_comparison_20260826.md

for method_root in "${gptuner_root}" "${aqetuner_root}"; do
    state=${method_root}/state.json
    test -f "${state}"
    test "$(jq -r '.status' "${state}")" = "COMPLETE"
    test "$(jq -r '.phase' "${state}")" = "COMPLETE"
    test -f "${method_root}/authority_manifest.json"
done

PYTHONPATH="${project_root}/shell:${project_root}" \
    "${python_bin}" -B "${project_root}/shell/build_mysql_external_baseline_comparison.py" \
    --external-root "${campaign_root}" \
    --json-output "${comparison_json}" \
    --markdown-output "${comparison_md}"

PYTHONPATH="${project_root}/shell:${project_root}" \
    "${python_bin}" -B "${project_root}/shell/test_build_mysql_external_baseline_comparison.py"

MPLCONFIGDIR=/tmp/mysql_external_baseline_mpl_20260826 \
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
    --arg campaign_root "${campaign_root}" \
    --arg comparison "${comparison_json}" \
    --arg pptx "${figure_root}/algorithm_abc_external_none_mysql_formal_results_20260826.pptx" \
    --arg rendered "${figure_root}/rendered_20260826" \
    --arg contact "${figure_root}/rendered_20260826_contact.png" \
    '{contract: "mysql_external_comparison_artifact_pipeline_v1",
      phase: "generated_pending_visual_qa",
      mysql_external_campaign_root: $campaign_root,
      comparison_json: $comparison,
      presentation: $pptx,
      rendered_slides: $rendered,
      contact_sheet: $contact,
      completed_at: $completed_at}' \
    > "${figure_root}/final_artifact_pipeline_state.json"

echo "MySQL comparison, figures, and PPT generated at $(date --iso-8601=seconds)"
