#!/usr/bin/env bash
set -euo pipefail

# compute00-local handover. v24 finishes cont006, whose tightly clustered trusted
# scores have a nonpositive Spearman. A regular-file cont007 barrier prevents
# any additional workload. The watcher proves state neutrality, preflights the
# deterministic score-span repeat, archives the barrier, and starts v25.

PROJECT=/home/anonymous/program/resource_adaptive
ROOT=${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731
STATE=${ROOT}/state.json
BATCHES=${ROOT}/batches
CONTROLLER_PID=2992296
BARRIER=${BATCHES}/cont007/explain_only
ARCHIVE=${BATCHES}/cont007/explain_only_handover_barrier_v24_20260731T1845
LAUNCHER=${PROJECT}/tools/launch_xp_autonomous_formal_target10_v25.sh
BASE_LAUNCHER=${PROJECT}/tools/launch_xp_autonomous_formal_target10_v20.sh
MANIFEST=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v7_entry_gate_only/bundle_manifest_v25_safe_frontier.json
REPEAT_BUILDER=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v7_entry_gate_only/build_xp_exact_plan_repeat_selection.py
FORMAL_COUNTER=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt
LOCK=${ROOT}/handover_v25.lock
LOG=${ROOT}/handover_v25.log

EXPECTED_BARRIER_SHA=63a5547a1da87a7a654e698cb9798b4a88751a42027f5bb093490feb183d650e
EXPECTED_STATE_SUBSET_SHA=d45b2885bcb3c111dac4203b6a93d2cb6c679869082c34fe3cc26037e4e12285
EXPECTED_LAUNCHER_SHA=78e8ecde69847b9b4ef5fdac5ecd89aa21a0a3d17037b78279a0f59f3b1fdd5e
EXPECTED_BASE_LAUNCHER_SHA=10a1268fc53a895b6ac38df8ce1f30ce8b7a082526c0c7a8a25c11c9048b7881
EXPECTED_MANIFEST_SHA=09f59f84a3da2c68643a00c8527f9b98810dee85d74d0a89147f72620dd80404
EXPECTED_REPEAT_BUILDER_SHA=57a7d59949338ae02cf558a2f364e7bb7fd0b4e7ec7587284350d79646e3bbd7

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "XP v25 handover watcher lock is already held" >&2
    exit 73
fi
exec >>"${LOG}" 2>&1

test "$(sha256sum "${BARRIER}" | awk '{print $1}')" = "${EXPECTED_BARRIER_SHA}"
test ! -e "${ARCHIVE}"
test "$(sha256sum "${LAUNCHER}" | awk '{print $1}')" = "${EXPECTED_LAUNCHER_SHA}"
test "$(sha256sum "${BASE_LAUNCHER}" | awk '{print $1}')" = "${EXPECTED_BASE_LAUNCHER_SHA}"
test "$(sha256sum "${MANIFEST}" | awk '{print $1}')" = "${EXPECTED_MANIFEST_SHA}"
test "$(sha256sum "${REPEAT_BUILDER}" | awk '{print $1}')" = "${EXPECTED_REPEAT_BUILDER_SHA}"
test "$(tr -d '[:space:]' < "${FORMAL_COUNTER}")" = "4"

for _ in $(seq 1 1080); do
    if ! kill -0 "${CONTROLLER_PID}" 2>/dev/null; then
        break
    fi
    COMMAND="$(ps -p "${CONTROLLER_PID}" -o args=)"
    case "${COMMAND}" in
        *bundle_fix_v7_entry_gate_only/xp_unattended_launch.py*) ;;
        *) echo "XP v24 PID command mismatch" >&2; exit 74 ;;
    esac
    sleep 10
done
if kill -0 "${CONTROLLER_PID}" 2>/dev/null; then
    echo "XP v24 exceeded handover wait limit" >&2
    exit 75
fi

# The old process may have completed both scheduled diagnostic batches, or it
# may have failed closed earlier during candidate generation.  In either case
# no accepted state is permitted to change before v24 starts.
OBSERVED_SUBSET_SHA="$(
    jq -cS '{accepted_count,incumbent_score,incumbent_params,parameter_ranges,range_state}' "${STATE}" |
        sha256sum | awk '{print $1}'
)"
test "${OBSERVED_SUBSET_SHA}" = "${EXPECTED_STATE_SUBSET_SHA}"
test "$(tr -d '[:space:]' < "${FORMAL_COUNTER}")" = "4"
jq -e '
    .accepted_count == 4 and
    .accepted_target == 10 and
    .status == "blocked_contract" and
    .next_batch_index == 7 and
    (.last_error | type == "string")
' "${STATE}" >/dev/null

for batch in cont006; do
    if test -f "${BATCHES}/${batch}/replay_summary.json"; then
        jq -e '
            .batch_mode == "diagnostic_validation" and
            .diagnostic_only == true and
            .physical_workload_invocation_count == 5 and
            .successful_real_replay_count == 5 and
            .technical_failure_count == 0 and
            .baseline_restore_verified == true and
            .accepted_count_delta == 0 and
            .incumbent_updated == false and
            .range_updated == false and
            .gradient_update_applied == false
        ' "${BATCHES}/${batch}/replay_summary.json" >/dev/null
    fi
done

# v24 must have reached the deliberate zero-workload cont007 barrier.
test ! -e "${BATCHES}/cont007/serial_replay"

# Determinism and score-span routing are proven before v25 is armed.
PRE_A=${BATCHES}/cont007/score_span_preflight_a.json
PRE_B=${BATCHES}/cont007/score_span_preflight_b.json
python3 -u "${REPEAT_BUILDER}" \
    --selection "${BATCHES}/cont006/selection.json" \
    --replay "${BATCHES}/cont006/replay_summary.json" \
    --output "${PRE_A}"
python3 -u "${REPEAT_BUILDER}" \
    --selection "${BATCHES}/cont006/selection.json" \
    --replay "${BATCHES}/cont006/replay_summary.json" \
    --output "${PRE_B}"
cmp "${PRE_A}" "${PRE_B}"
jq -e '
    .batch_mode == "diagnostic_validation" and
    .exact_plan_regime_repeat_validation.source_trusted_spearman <= 0 and
    .exact_plan_regime_repeat_validation.trusted_selection_strategy ==
        "best_median_worst_score_span_after_nonpositive_spearman" and
    ([.top5[].selection_role] == [
        "trusted_calibrated_rank",
        "trusted_calibrated_rank",
        "trusted_calibrated_rank",
        "maximin_uncertain_probe",
        "maximin_uncertain_probe"
    ]) and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5
' "${PRE_A}" >/dev/null

mv "${BARRIER}" "${ARCHIVE}"
test "$(sha256sum "${ARCHIVE}" | awk '{print $1}')" = "${EXPECTED_BARRIER_SHA}"
chmod +x "${LAUNCHER}" "${BASE_LAUNCHER}"
exec "${LAUNCHER}"
