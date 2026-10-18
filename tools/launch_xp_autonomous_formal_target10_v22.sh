#!/usr/bin/env bash
set -euo pipefail

XP_PROJECT=/home/anonymous/program/resource_adaptive
XP_CONTROLLER=${XP_PROJECT}/history/xp_autonomous_formal_target10_v1_20260731
XP_EXPECTED_MANIFEST_SHA=84a890dd85518d96cf06d45e36142f163ed2aecf13a0c128fb309f823f36f589
XP_MANIFEST=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6/bundle_manifest_v22_safe_frontier.json

test "$(sha256sum "${XP_MANIFEST}" | awk '{print $1}')" = \
    "${XP_EXPECTED_MANIFEST_SHA}"
test "$(jq -r '.affinity.cpuset_cpus' "${XP_MANIFEST}")" = \
    "32-51,288-307"
test "$(jq -r '.affinity.excluded_mysql_cpus' "${XP_MANIFEST}")" = \
    "184-199,440-455"

exec >>"${XP_CONTROLLER}/launch_v22.log" 2>&1
export XP_LAUNCH_PACKAGE_VERSION=v22
export XP_EXPECTED_PACKAGE_SHA_V22="${XP_EXPECTED_MANIFEST_SHA}"
exec "${XP_PROJECT}/tools/launch_xp_autonomous_formal_target10_v20.sh"
