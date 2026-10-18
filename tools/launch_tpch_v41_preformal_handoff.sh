#!/usr/bin/env bash
set -euo pipefail

PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
HANDOFF=/tmp/tpch_v41_preformal_handoff.py
FORMAL_RUNNER=/tmp/run_tpch_formal_exact_plan_regime_batch_v41_robust4.py
ANCHOR_ROOT=${BASE}/tpch_v38_current_anchor_revalidation_20260803
ANCHOR_STATE=${ANCHOR_ROOT}/current_anchor_state.v2.json
V40_ROOT=${BASE}/tpch_final_algorithm_fresh0_v40_retest_compat_20260803
V40_COUNTER=${V40_ROOT}/formal_search_accepted_counter.txt
V41_ROOT=${BASE}/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803
V40_CONTROLLER_PID=${V40_CONTROLLER_PID:-113686}

test "$(sha256sum "${HANDOFF}" | awk '{print $1}')" = "bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41"
test "$(sha256sum "${FORMAL_RUNNER}" | awk '{print $1}')" = "2e8daea587b6d158269dbbe8767b086c0882aa0cd92650a3860c39f38e649eeb"
test "$(sed -n '1p' "${V40_COUNTER}")" = "0"
test ! -e "${V41_ROOT}"

test -r "/proc/${V40_CONTROLLER_PID}/stat"
test -r "/proc/${V40_CONTROLLER_PID}/cmdline"
case "$(ps -o stat= -p "${V40_CONTROLLER_PID}" | awk '{print $1}')" in
    T*) ;;
    *) echo "v40 controller is not SIGSTOP: ${V40_CONTROLLER_PID}" >&2; exit 3 ;;
esac
tr '\0' ' ' < "/proc/${V40_CONTROLLER_PID}/cmdline" |
    grep -Fq '/tmp/tpch_fresh0_target10_v38_bounded_retest.py'

ANCHOR_SHA=$(sha256sum "${ANCHOR_STATE}" | awk '{print $1}')
"${PYTHON}" -u "${HANDOFF}" \
    --current-anchor-state "${ANCHOR_STATE}" \
    --current-anchor-state-sha256 "${ANCHOR_SHA}" \
    --preflight-only

exec "${PYTHON}" -u "${HANDOFF}" \
    --current-anchor-state "${ANCHOR_STATE}" \
    --current-anchor-state-sha256 "${ANCHOR_SHA}" \
    --wait-seconds 21600
