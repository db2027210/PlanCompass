#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive
LANE="$BASE/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01"
SOURCE="$LANE/launch_hybench_ap_calibration.sh"
SOURCE_SHA256=8dc219c82dba8eadd3e47aa56f63ea5bdbc5a99cae4f992cfd8d554e5117c2fb
RUNNER_SHA=5116d3d77b27543f530e2a6a394bcdb4298deddde31441f1196dc706b3e125c4
RESOURCE_GATE_SHA=547ec566adbb78b51e290b69f9d5bf97e5f9c1cbbdffbe7eb8afac741fddcd16
OLD_RESOURCE_GATE_SHA=c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e

test "$#" -eq 5
test "$(sha256sum "$SOURCE" | awk '{print $1}')" = "$SOURCE_SHA256"
test "$(sha256sum "$LANE/baseline_once.py" | awk '{print $1}')" = "$RUNNER_SHA"
test "$(sha256sum "$LANE/r0_resource_gate_contract_v2.json" | awk '{print $1}')" = "$RESOURCE_GATE_SHA"

sed \
  -e "s/RUNNER_SHA256=[0-9a-f]\{64\}/RUNNER_SHA256=$RUNNER_SHA/" \
  -e 's|r0_resource_gate_contract.json|r0_resource_gate_contract_v2.json|g' \
  -e "s/$OLD_RESOURCE_GATE_SHA/$RESOURCE_GATE_SHA/g" \
  "$SOURCE" | bash -s -- "$@"
