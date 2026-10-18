#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [STAGE_DIRECTORY]" >&2
  exit 64
fi

stage=${1:-$(cd "$(dirname "$0")" && pwd)}
controller_name=tpch_v46_postclean5_gate_binding_and_absorption.py
runner_name=run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py
launcher_name=launch_tpch_v46_postclean5_gate_binding.sh
v32_name=tpch_fresh10_target10_loop_v32_v8_gate_binding.py

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$stage/$controller_name" 877a4a517ea0290c8f7cb96ebb1742e40f5a80f00137d78b24e008f79c52f47f
verify_sha "$stage/$runner_name" af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2
verify_sha "$stage/$launcher_name" 18e9a0bbe6175a8e085944a2b6dbf7d0b57c4a8e1322befae8ac96288d9b85f1
verify_sha "$stage/$v32_name" a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69

verify_sha /tmp/tpch_v41_preformal_handoff.py bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41
verify_sha /tmp/tpch_fresh10_target10_loop_v32_v7_checkpointed.py f5c735de117c129eed442cbbb7caecc07ba34fe6c68d23eb04a56ef654ffeb61
verify_sha /tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py 8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
verify_sha /tmp/build_tpch_autonomous_successor_top5_v10_stability_acquisition.py da43bfb66ee99f60702685d4b39018ab1ea9d365d3210009c85b6afb6612fc9b

if ps -eo stat=,args= | awk '
  $1 !~ /^Z/ &&
  $0 ~ /tpch_(v4[1-6]|fresh10_target10_loop|round29_diagnostic)/ &&
  $0 !~ /deploy_tpch_v46/ { found=1 }
  END { exit(found ? 0 : 1) }
'; then
  echo "active TPC-H controller/diagnostic detected; refusing deployment" >&2
  exit 73
fi

for name in "$controller_name" "$runner_name" "$launcher_name" "$v32_name"; do
  source_path=$stage/$name
  destination=/tmp/$name
  if [[ -e "$destination" ]]; then
    if [[ "$(sha256sum "$destination" | awk '{print $1}')" != "$(sha256sum "$source_path" | awk '{print $1}')" ]]; then
      echo "destination exists with different SHA: $destination" >&2
      exit 74
    fi
    continue
  fi
  temporary=${destination}.deploy.$$
  cp "$source_path" "$temporary"
  [[ "$(sha256sum "$temporary" | awk '{print $1}')" == "$(sha256sum "$source_path" | awk '{print $1}')" ]]
  chmod "$(stat -c '%a' "$source_path")" "$temporary"
  mv "$temporary" "$destination"
done

verify_sha /tmp/$controller_name 877a4a517ea0290c8f7cb96ebb1742e40f5a80f00137d78b24e008f79c52f47f
verify_sha /tmp/$runner_name af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2
verify_sha /tmp/$launcher_name 18e9a0bbe6175a8e085944a2b6dbf7d0b57c4a8e1322befae8ac96288d9b85f1
verify_sha /tmp/$v32_name a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69

echo '{"status":"deployed_zero_call","new_workload_calls":0,"next":"preflight-postclean5-gate"}'
