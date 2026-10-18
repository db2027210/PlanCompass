#!/usr/bin/env bash
set -euo pipefail

TPCH_BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
TPCH_PACKAGE=${TPCH_BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package
TPCH_RUN=${TPCH_BASE}/gradient_debug_round46_topology_v2_corrected_validation_v4_20260731T1620_run
TPCH_FORMAL=${TPCH_BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
TPCH_RUNNER_PID=11606
TPCH_ANALYZER=/tmp/analyze_top5_validation_gate_v2.py
TPCH_GATE_BUILDER=/tmp/build_tpch_formal_entry_gate_v2.py
TPCH_PYTHON=/home/anonymous/miniconda3/bin/python
TPCH_LOCK=${TPCH_RUN}.gate_controller.lock
TPCH_LOG=${TPCH_RUN}/gate_controller.log

TPCH_SELECTION_SHA=cc2fadcfc29a327304e65d9fa07e594d441e0e174304ee6cd9f5cb8b25157238
TPCH_FORMAL_SHA=335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
TPCH_ANALYZER_SHA=49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30
TPCH_GATE_BUILDER_SHA=013c6df94cfd1bf7abf6e05bd5c39a66de430e6feb6735a4dd2ed22a41f7dd9b

exec 9>"${TPCH_LOCK}"
if ! flock -n 9; then
    echo "TPC-H round46 gate controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${TPCH_LOG}") 2>&1

test "$(sha256sum "${TPCH_ANALYZER}" | awk '{print $1}')" = \
    "${TPCH_ANALYZER_SHA}"
test "$(sha256sum "${TPCH_GATE_BUILDER}" | awk '{print $1}')" = \
    "${TPCH_GATE_BUILDER_SHA}"
test "$(sha256sum "${TPCH_PACKAGE}/top5.json" | awk '{print $1}')" = \
    "${TPCH_SELECTION_SHA}"
test "$(sha256sum "${TPCH_FORMAL}" | awk '{print $1}')" = \
    "${TPCH_FORMAL_SHA}"
test -x "${TPCH_PYTHON}"

for _ in $(seq 1 720); do
    if ! kill -0 "${TPCH_RUNNER_PID}" 2>/dev/null; then
        break
    fi
    TPCH_RUNNER_COMMAND="$(ps -p "${TPCH_RUNNER_PID}" -o args=)"
    case "${TPCH_RUNNER_COMMAND}" in
        *run_tpch_round29_diagnostic_exact_plan_regime_batch.py*round46_topology_v2_corrected_validation_v4*)
            ;;
        *)
            echo "TPC-H runner PID command mismatch" >&2
            exit 74
            ;;
    esac
    sleep 10
done
if kill -0 "${TPCH_RUNNER_PID}" 2>/dev/null; then
    echo "TPC-H round46 runner exceeded gate wait limit" >&2
    exit 75
fi

test -f "${TPCH_RUN}/diagnostic_summary.json"
test -f "${TPCH_RUN}/diagnostic_state.json"
jq -e '
    .status == "complete" and
    .physical_call_count == 5 and
    .valid_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_restored == true and
    .diagnostic_only == true and
    .accepted_update_allowed == false and
    .incumbent_update_allowed == false and
    .range_update_allowed == false and
    .gradient_update_allowed == false
' "${TPCH_RUN}/diagnostic_state.json" >/dev/null
test "$(sha256sum "${TPCH_FORMAL}" | awk '{print $1}')" = \
    "${TPCH_FORMAL_SHA}"

"${TPCH_PYTHON}" -u "${TPCH_ANALYZER}" \
    --selection "${TPCH_PACKAGE}/top5.json" \
    --observations "${TPCH_RUN}/diagnostic_summary.json" \
    --output "${TPCH_RUN}/entry_gate_analysis.json"
jq -e '.passed == true' "${TPCH_RUN}/entry_gate_analysis.json" >/dev/null

"${TPCH_PYTHON}" -u "${TPCH_GATE_BUILDER}" \
    --analysis "${TPCH_RUN}/entry_gate_analysis.json" \
    --diagnostic-state "${TPCH_RUN}/diagnostic_state.json" \
    --selection "${TPCH_PACKAGE}/top5.json" \
    --formal-state "${TPCH_FORMAL}" \
    --formal-state-sha256 "${TPCH_FORMAL_SHA}" \
    --output "${TPCH_RUN}/formal_entry_gate.json"
jq -e '.passed == true' "${TPCH_RUN}/formal_entry_gate.json" >/dev/null
sha256sum "${TPCH_RUN}/formal_entry_gate.json" > \
    "${TPCH_RUN}/formal_entry_gate.sha256"
