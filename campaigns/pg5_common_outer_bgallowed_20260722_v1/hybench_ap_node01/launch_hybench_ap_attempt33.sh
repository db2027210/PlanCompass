#!/usr/bin/env bash
set -euo pipefail

LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
SOURCE="$LANE/launch_hybench_ap_attempt31.sh"
SOURCE_SHA256=0b59c9271b3ff1d4bdb64f79cef2f96c3185cb49845abc8c8148505e02de073a
CALIBRATION_V2_SHA256=3948cea2cb7391ab81c6e2090cfc58ed5cfed54dba19898ae368462db2a44ae2

test "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$SOURCE_SHA256"
test "$(sha256sum "$LANE/launch_hybench_ap_calibration_v2.sh" | awk '{print $1}')" = "$CALIBRATION_V2_SHA256"
sed \
  -e 's/attempt31/attempt33/g' \
  -e 's/attempt_31/attempt_33/g' \
  -e 's|CALIBRATION="$LANE/launch_hybench_ap_calibration.sh"|CALIBRATION="$LANE/launch_hybench_ap_calibration_v2.sh"|' \
  -e "s/CALIBRATION_SHA256=8dc219c82dba8eadd3e47aa56f63ea5bdbc5a99cae4f992cfd8d554e5117c2fb/CALIBRATION_SHA256=$CALIBRATION_V2_SHA256/" \
  -e 's/RUNNER_SHA256=0adefb99d8db027bc4e1edfee3b3e4e509089c5cceda8bd8b557d9efb5468f07/RUNNER_SHA256=76a1776eadd837132df777ac34ebb8a316fa8fd7310c6a4969490ed758996b41/' \
  -e 's/243269632/223155000/g' \
  -e 's/6\.68/5.5/g' \
  "$SOURCE" | bash
