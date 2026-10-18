#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PREP=${BASE}/gradient_debug_round47_stability_recovery_prep_v2_20260731T1655
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311

HISTORY=${BASE}/gradient_debug_round39_latest_epoch_exact_v2_20260731T0725_package/expanded_analysis.json
PREVIOUS_SELECTION=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package/top5.json
PREVIOUS_SUMMARY=${BASE}/gradient_debug_round46_topology_v2_corrected_validation_v4_20260731T1620_run/diagnostic_summary.json
CONFIG=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package/config.json
CAPTURE=${BASE}/gradient_debug_round37_corrected3p2_diagnosticfix_v1_20260731T0525/query_capture_exact.json
FORMAL_STATE=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json

EXPANDER=/tmp/tpch_exact_regime_neighbor_expand_v2.py
BUILDER=/tmp/build_tpch_autonomous_successor_top5_v2.py
SELECTOR=/tmp/tpch_round19_plan_regime_select.py

test ! -e "${PREP}"
test -x "${PYTHON}"
test "$(sha256sum "${HISTORY}" | awk '{print $1}')" = \
    9152445d78867ab371a0673c6a4037fa7ce486e440c4510ee349b047ecd3b641
test "$(sha256sum "${PREVIOUS_SELECTION}" | awk '{print $1}')" = \
    cc2fadcfc29a327304e65d9fa07e594d441e0e174304ee6cd9f5cb8b25157238
test "$(sha256sum "${PREVIOUS_SUMMARY}" | awk '{print $1}')" = \
    8cf0ff5da2e1bd260fb2a88cb1f4e3b181b74aa9f9758f0c96a260fd6537de7d
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = \
    0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
test "$(sha256sum "${CAPTURE}" | awk '{print $1}')" = \
    8021855b122cfc27c3f709abbdaa620d72c69b6ac77238d26abb8a4d575ad66d
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
test "$(sha256sum "${EXPANDER}" | awk '{print $1}')" = \
    a4fb3fd997cc92d7c7c217086627d30c72caf3d2a7f74347ad28be7b0ca54857
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = \
    28b5d008133db7963138a7dafccac15612ed53b7f232c34e920978dc3440e49f
test "$(sha256sum "${SELECTOR}" | awk '{print $1}')" = \
    68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab

mkdir -p "${PREP}"
exec > >(tee -a "${PREP}/controller.log") 2>&1

env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres \
    timeout 1800s "${PYTHON}" -u "${EXPANDER}" \
        --history "${HISTORY}" \
        --evidence-top5 "${PREVIOUS_SELECTION}" \
        --config "${CONFIG}" \
        --capture "${CAPTURE}" \
        --selector "${SELECTOR}" \
        --code-root "${BASE}/code" \
        --allow-live-epoch-transition \
        --output "${PREP}/expanded_candidates.json"

"${PYTHON}" -u "${BUILDER}" \
    --previous-selection "${PREVIOUS_SELECTION}" \
    --previous-summary "${PREVIOUS_SUMMARY}" \
    --expanded-candidates "${PREP}/expanded_candidates.json" \
    --output "${PREP}/top5_a.json"
"${PYTHON}" -u "${BUILDER}" \
    --previous-selection "${PREVIOUS_SELECTION}" \
    --previous-summary "${PREVIOUS_SUMMARY}" \
    --expanded-candidates "${PREP}/expanded_candidates.json" \
    --output "${PREP}/top5_b.json"
cmp -s "${PREP}/top5_a.json" "${PREP}/top5_b.json"
cp "${PREP}/top5_a.json" "${PREP}/top5.json"

jq -e '
    .selection_contract ==
        "exact_structural_plan_regime_stability_probe5_diagnostic_v1" and
    .automatic_recovery_mode == true and
    .automatic_recovery_reason ==
        "trusted_prediction_intervals_not_separable" and
    .diagnostic_only == true and
    .maximum_real_replay_invocations == 5 and
    .qps_used_for_inner_scoring == false and
    [.top5[].selection_role] == ["probe", "probe", "probe", "probe", "probe"] and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[].candidate_params] | unique | length) == 5 and
    all(
        .top5[];
        .boundary_corner == false and
        .boundary_plateau == false and
        .actual_parameter_noncorner_audit.reason ==
            "actual_parameter_space_noncorner" and
        .accepted_update_allowed == false and
        .incumbent_update_allowed == false and
        .range_update_allowed == false and
        .gradient_update_allowed == false and
        has("feature_ratio") and
        has("predicted_resource") and
        has("utilization") and
        has("dScore/dU") and
        has("T") and
        has("confidence") and
        has("g_j") and
        has("eta") and
        has("delta_param") and
        has("candidate_score")
    )
' "${PREP}/top5.json" >/dev/null

test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4

jq -n \
    --arg expanded_sha "$(sha256sum "${PREP}/expanded_candidates.json" | awk '{print $1}')" \
    --arg top5_sha "$(sha256sum "${PREP}/top5.json" | awk '{print $1}')" \
    --arg formal_state_sha "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" \
    '{
        schema_version: "tpch_stability_recovery_prep_manifest_v1",
        status: "ready",
        workload_calls: 0,
        deterministic_top5_rebuild_match: true,
        expanded_candidates_sha256: $expanded_sha,
        top5_sha256: $top5_sha,
        formal_state_sha256: $formal_state_sha
    }' >"${PREP}/manifest.json"
sha256sum "${PREP}/manifest.json" >"${PREP}/manifest.sha256"
cat "${PREP}/manifest.json"
