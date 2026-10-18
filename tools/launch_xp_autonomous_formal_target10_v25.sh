#!/usr/bin/env bash
set -euo pipefail

XP_PROJECT=/home/anonymous/program/resource_adaptive
XP_CONTROLLER=${XP_PROJECT}/history/xp_autonomous_formal_target10_v1_20260731
XP_BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v7_entry_gate_only
XP_EXPECTED_MANIFEST_SHA=09f59f84a3da2c68643a00c8527f9b98810dee85d74d0a89147f72620dd80404
XP_MANIFEST=${XP_BUNDLE}/bundle_manifest_v25_safe_frontier.json

test "$(sha256sum "${XP_MANIFEST}" | awk '{print $1}')" = \
    "${XP_EXPECTED_MANIFEST_SHA}"
test "$(jq -r '.affinity.cpuset_cpus' "${XP_MANIFEST}")" = \
    "32-51,288-307"
test "$(jq -r '.affinity.excluded_mysql_cpus' "${XP_MANIFEST}")" = \
    "184-199,440-455"
test "$(jq -r '.formal_expected_improvement_required' "${XP_MANIFEST}")" = \
    false
test "$(jq -r '.formal_admission_contract' "${XP_MANIFEST}")" = \
    "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1"

exec >>"${XP_CONTROLLER}/launch_v25.log" 2>&1
export XP_LAUNCH_PACKAGE_VERSION=v25
export XP_LAUNCH_BUNDLE="${XP_BUNDLE}"
export XP_EXPECTED_PACKAGE_SHA_V25="${XP_EXPECTED_MANIFEST_SHA}"
exec "${XP_PROJECT}/tools/launch_xp_autonomous_formal_target10_v20.sh"
