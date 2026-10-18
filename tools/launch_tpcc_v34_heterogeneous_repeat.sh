#!/usr/bin/env bash
set -euo pipefail

project=/home/anonymous/program/resource_adaptive
target=${project}/history/tpcc_final_algorithm_fresh0_v34_20260803
manifest=${target}/manifest.json
controller=${project}/tools/tpcc_autonomous_target10_controller_v34_heterogeneous_repeat.py
preflight=${project}/tools/preflight_tpcc_v34_heterogeneous_repeat.py
python=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
session=tpcc_fresh0_v34
manifest_sha=10741e90a8b5f6e03b4f3dbf9d394be6cf8a19a31816a68414335a26022b9289
controller_sha=2419cd3b05a57a44c2b25364a8b03537e9d8c1a4bdeb6a51744b812d56f6d33d
preflight_sha=9ba88ef8f2a32206c42677050789be4ed6ecbf72c51468a35f8deabb051a461e

test "$(sha256sum "${manifest}" | awk '{print $1}')" = "${manifest_sha}"
test "$(sha256sum "${controller}" | awk '{print $1}')" = "${controller_sha}"
test "$(sha256sum "${preflight}" | awk '{print $1}')" = "${preflight_sha}"

"${python}" -u "${preflight}" "${manifest}" \
  --manifest-sha256 "${manifest_sha}"

if [[ "${1:-}" == "--preflight-only" ]]; then
  echo "TPC-C v34 heterogeneous-repeat preflight passed"
  exit 0
fi
[[ "$#" -eq 0 ]]

if tmux has-session -t "${session}" 2>/dev/null; then
  echo "TPC-C v34 tmux session already exists" >&2
  exit 73
fi

tmux new-session -d -s "${session}" \
  "exec '${python}' -u '${controller}' '${manifest}' --armed --manifest-sha256 '${manifest_sha}' >> '${target}/controller.log' 2>&1"
sleep 2
tmux has-session -t "${session}"
pane_pid="$(tmux list-panes -t "${session}" -F '#{pane_pid}')"
test -n "${pane_pid}"
kill -0 "${pane_pid}"
printf '%s\n' "${pane_pid}" > "${target}/controller.pid"
printf 'TPCC_V34_STARTED session=%s pid=%s\n' "${session}" "${pane_pid}"
