#!/usr/bin/env bash
set -euo pipefail
LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
exec "$LANE/launch_hybench_ap_calibration.sh" attempt_27 6.68 209715200 67108864 36700160
