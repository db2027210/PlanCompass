#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
ROOT=${BASE}/tpch_final_algorithm_fresh10_v58_20260731T2340
LOOP=${ROOT}/target10_loop_v26
CYCLE=${LOOP}/cycle_005
OUTPUT=${CYCLE}/dynamic_expansion_v28
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
EXPANDER=/tmp/tpch_exact_regime_neighbor_expand_v2.py
SELECTOR=/tmp/tpch_round19_plan_regime_select.py
HISTORY=${BASE}/gradient_debug_round39_latest_epoch_exact_v2_20260731T0725_package/expanded_analysis.json
CAPTURE=${BASE}/gradient_debug_round37_corrected3p2_diagnosticfix_v1_20260731T0525/query_capture_exact.json
CONFIG=${CYCLE}/config.a.json
EVIDENCE=${LOOP}/cycle_004/exact_repeat_top5.a.json
SUMMARY=${LOOP}/cycle_004/diagnostic_exact_repeat/diagnostic_summary.json
STATE=${LOOP}/controller_state.json
FAILED_LOG=${CYCLE}/probe_builder_a.log
COUNTER=${ROOT}/formal_search_accepted_counter.txt

sha() {
    sha256sum "$1" | awk '{print $1}'
}

test "$(sha "${EXPANDER}")" = a4fb3fd997cc92d7c7c217086627d30c72caf3d2a7f74347ad28be7b0ca54857
test "$(sha "${SELECTOR}")" = 68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab
test "$(sha "${HISTORY}")" = 9152445d78867ab371a0673c6a4037fa7ce486e440c4510ee349b047ecd3b641
test "$(sha "${CAPTURE}")" = 8021855b122cfc27c3f709abbdaa620d72c69b6ac77238d26abb8a4d575ad66d
test "$(sha "${CONFIG}")" = 0708698830d20441c2dcceed1ccc98f66527c41cdad6d4d577011bfc5f93da06
test "$(sha "${EVIDENCE}")" = 18d68555399ac153c8826e44216ad46171bb068ff92da132f4b1810cbdcedbad
test "$(sha "${SUMMARY}")" = 03f3362383be7fbf52b680ad3681e679975b9c43b124c0e0fb80a1c278d69796
test "$(sha "${STATE}")" = 58f0b2df6a74b26fefe8041c6e9213a9f21f14e07acac46eec7a586808c03df5
test "$(sha "${FAILED_LOG}")" = b1f5ef1550fabde64ac1dffb7f70007fc7b4206a1580867d61363ba43c316af5
test "$(cat "${COUNTER}")" = 0

jq -e '
    .schema_version == "tpch_fresh10_target10_loop_v27" and
    .status == "preparing_cycle" and
    .accepted_count == 0 and
    .next_cycle == 5 and
    (.cycles | length) == 1 and
    .cycles[0].cycle == 4 and
    .cycles[0].accepted_before == 0 and
    .cycles[0].accepted_after == 0 and
    .cycles[0].formal_started == false and
    .cycles[0].technical_failures == 0 and
    .cycles[0].thresholds_relaxed == false
' "${STATE}" >/dev/null

grep -Fq 'selected Top-5 score span does not exceed replay noise: span=0 bound=0.002' \
    "${FAILED_LOG}"
test ! -e "${CYCLE}/probe_top5.a.json"
test ! -e "${CYCLE}/diagnostic_probe"
test ! -e "${OUTPUT}"
test -z "$(pgrep -f '^/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11 -u /tmp/tpch_fresh10_target10_loop_v2[678].py' || true)"

mkdir -m 700 "${OUTPUT}"

run_expand() {
    local destination=$1
    env -u LD_LIBRARY_PATH -u PYTHONHOME \
        PYTHONPATH="${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
        PYTHONNOUSERSITE=1 \
        LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
        PGPASSWORD=postgres \
        timeout 1800s "${PYTHON}" -u "${EXPANDER}" \
            --history "${HISTORY}" \
            --evidence-top5 "${EVIDENCE}" \
            --config "${CONFIG}" \
            --capture "${CAPTURE}" \
            --selector "${SELECTOR}" \
            --code-root "${BASE}/code" \
            --allow-live-epoch-transition \
            --output "${destination}"
}

run_expand "${OUTPUT}/expanded_candidates.a.json"
run_expand "${OUTPUT}/expanded_candidates.b.json"
cmp -s "${OUTPUT}/expanded_candidates.a.json" "${OUTPUT}/expanded_candidates.b.json"

jq -e '
    .diagnostic_only == true and
    .explain_only == true and
    .new_workload_calls == 0 and
    .accepted_update_allowed == false and
    .incumbent_update_allowed == false and
    .range_update_allowed == false and
    .gradient_update_allowed == false and
    .qps_used_for_inner_scoring == false and
    (.ranked_candidates | length) >= 5
' "${OUTPUT}/expanded_candidates.a.json" >/dev/null

jq -n \
    --arg contract tpch_cycle005_dynamic_explain_expansion_v28 \
    --arg status complete \
    --arg source_state_sha "$(sha "${STATE}")" \
    --arg source_failure_sha "$(sha "${FAILED_LOG}")" \
    --arg expansion_sha "$(sha "${OUTPUT}/expanded_candidates.a.json")" \
    --argjson ranked_candidates "$(jq '.ranked_candidates | length' "${OUTPUT}/expanded_candidates.a.json")" \
    --argjson same_regime_neighbors "$(jq '[.neighbor_audit[].accepted_neighbor_count] | add' "${OUTPUT}/expanded_candidates.a.json")" \
    '{
        contract: $contract,
        status: $status,
        source_state_sha256: $source_state_sha,
        source_failure_sha256: $source_failure_sha,
        expansion_sha256: $expansion_sha,
        deterministic_double_generation: true,
        ranked_candidate_count: $ranked_candidates,
        same_regime_neighbor_count: $same_regime_neighbors,
        physical_workload_calls: 0,
        formal_state_mutated: false,
        accepted_count: 0,
        thresholds_relaxed: false
    }' > "${OUTPUT}/audit.json"

test "$(cat "${COUNTER}")" = 0
printf '%s\n' "TPC-H cycle005 dynamic EXPLAIN expansion complete"
