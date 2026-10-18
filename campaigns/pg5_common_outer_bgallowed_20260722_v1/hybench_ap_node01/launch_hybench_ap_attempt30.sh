#!/usr/bin/env bash
set -euo pipefail

LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
SOURCE="$LANE/launch_hybench_ap_attempt29.sh"
SOURCE_SHA256=fe1d585f39a67da6c9eaba628189f5d9f8c24f7682bdae0ee47e9cab921ef70a

test "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$SOURCE_SHA256"
sed \
  -e 's/attempt_29/attempt_30/g' \
  -e 's/attempt29/attempt30/g' \
  -e 's/209715200/243269632/g' \
  -e 's/RESIDENT_BYTES=11534336/RESIDENT_BYTES=31457280/' \
  "$SOURCE" | bash
