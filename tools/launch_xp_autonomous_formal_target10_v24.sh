#!/usr/bin/env bash
set -euo pipefail

XP_PROJECT=/home/anonymous/program/resource_adaptive
XP_CONTROLLER=${XP_PROJECT}/history/xp_autonomous_formal_target10_v1_20260731
XP_BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v7_entry_gate_only
XP_EXPECTED_MANIFEST_SHA=473cb2471857490d7aced85597f5e86b87558d46dcd4ff9b7e957b1ab44a8c10
XP_MANIFEST=${XP_BUNDLE}/bundle_manifest_v24_safe_frontier.json

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

exec >>"${XP_CONTROLLER}/launch_v24.log" 2>&1
export XP_LAUNCH_PACKAGE_VERSION=v24
export XP_LAUNCH_BUNDLE="${XP_BUNDLE}"
export XP_EXPECTED_PACKAGE_SHA_V24="${XP_EXPECTED_MANIFEST_SHA}"
exec "${XP_PROJECT}/tools/launch_xp_autonomous_formal_target10_v20.sh"
