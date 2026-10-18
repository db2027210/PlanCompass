#!/usr/bin/env bash
set -euo pipefail

LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
SOURCE="$LANE/launch_hybench_ap_attempt31.sh"
SOURCE_SHA256=0b59c9271b3ff1d4bdb64f79cef2f96c3185cb49845abc8c8148505e02de073a
CALIBRATION_V3_SHA256=3cb5e9ec2830e1363b499ef2847f127d8549bf12596bb043e07c0fd7c5f79f3e
RUNNER_SHA256=2bdc2425b4ba1024c66ae818308169599a40e9799e3045f802c68c6209dbaf66
SEQUENCE_SHA256=379d3060d61ab1bf906eac6bae6f08632b54db122cb1802c58e1fcf81e65b27d
PATCH_BUILDER_SHA256=ece7eb38fa2ba15138ca9c0bc8b301f26f702b56221e8ec92fe235b28d202266
PATCH_JAR_SHA256=6e25069927261b5eb6e2dc9b9b3412ffa8f9e60553715503d6eb560f6d1a6a90
PATCH_SUMMARY_SHA256=2aa64b17af6b98b61aba14dec1bb3784fb6600c24d4ccec781eb14aa18923ea0

test "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$SOURCE_SHA256"
test "$(sha256sum "$LANE/launch_hybench_ap_calibration_v3.sh" | awk '{print $1}')" = "$CALIBRATION_V3_SHA256"
test "$(sha256sum "$LANE/baseline_once.py" | awk '{print $1}')" = "$RUNNER_SHA256"
test "$(sha256sum "$LANE/ap_query_sequence_v1.json" | awk '{print $1}')" = "$SEQUENCE_SHA256"
test "$(sha256sum "$LANE/build_apclient_stride4_patch.py" | awk '{print $1}')" = "$PATCH_BUILDER_SHA256"
test "$(sha256sum "$LANE/private_hybench/build/apclient-sequence-stride4.jar" | awk '{print $1}')" = "$PATCH_JAR_SHA256"
test "$(sha256sum "$LANE/private_hybench/build/apclient-sequence-stride4.summary.json" | awk '{print $1}')" = "$PATCH_SUMMARY_SHA256"
test "$(jq -r '.target_attempt' "$LANE/attempt34_resource_envelope.json")" = attempt_34
test "$(jq -r '.resource_contract.cpu_quota_cores' "$LANE/attempt34_resource_envelope.json")" = 5.5
test "$(jq -r '.resource_contract.memory_bytes' "$LANE/attempt34_resource_envelope.json")" = 223155000
test "$(jq -r '.private_runner_sha256' "$LANE/attempt34_resource_envelope.json")" = "$RUNNER_SHA256"
test "$(jq -r '.ap_query_sequence_contract_sha256' "$LANE/attempt34_resource_envelope.json")" = "$SEQUENCE_SHA256"
test "$(jq -r '.apclient_patch_jar_sha256' "$LANE/attempt34_resource_envelope.json")" = "$PATCH_JAR_SHA256"
test "$(jq -r '.stability_contract.cross_window_cv_max_inclusive' "$LANE/attempt34_resource_envelope.json")" = 0.1
test "$(jq -r '.formal_db_qps_contract.source' "$LANE/attempt34_resource_envelope.json")" = pg_stat_statements_sum_calls_delta_per_second
test "$(jq -r '.formal_db_qps_contract.ap_qps_substitution_allowed' "$LANE/attempt34_resource_envelope.json")" = false

sed \
  -e 's/attempt31/attempt34/g' \
  -e 's/attempt_31/attempt_34/g' \
  -e 's|CALIBRATION="$LANE/launch_hybench_ap_calibration.sh"|CALIBRATION="$LANE/launch_hybench_ap_calibration_v3.sh"|' \
  -e "s/CALIBRATION_SHA256=8dc219c82dba8eadd3e47aa56f63ea5bdbc5a99cae4f992cfd8d554e5117c2fb/CALIBRATION_SHA256=$CALIBRATION_V3_SHA256/" \
  -e "s/RUNNER_SHA256=0adefb99d8db027bc4e1edfee3b3e4e509089c5cceda8bd8b557d9efb5468f07/RUNNER_SHA256=$RUNNER_SHA256/" \
  -e 's/243269632/223155000/g' \
  -e 's/6\.68/5.5/g' \
  "$SOURCE" | bash
