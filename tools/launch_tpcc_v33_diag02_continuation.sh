#!/usr/bin/env bash
set -euo pipefail

project=/home/anonymous/program/resource_adaptive
target=${project}/history/tpcc_final_algorithm_fresh0_v33_20260803
manifest=${target}/manifest.json
controller=${project}/tools/tpcc_autonomous_target10_controller_v33_diag02.py
preflight=${project}/tools/preflight_tpcc_v33_diag02_continuation.py
python=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
session=tpcc_fresh0_v33
manifest_sha=719a583db11815f0e51a53c4b17c75aa802de08c408c631f1b750988a5449ce9
controller_sha=70723425292cc6d11421fdcae88943739423ebf59bd5ebdc1ce9bdbe13d2eca2
preflight_sha=b4d0d7a4624cbeccb6ef303f0398c17b713e0c1f0eba1a1d2b7660992db4fe3c

test "$(sha256sum "${manifest}" | awk '{print $1}')" = "${manifest_sha}"
test "$(sha256sum "${controller}" | awk '{print $1}')" = "${controller_sha}"
test "$(sha256sum "${preflight}" | awk '{print $1}')" = "${preflight_sha}"

"${python}" -u "${preflight}" "${manifest}" \
  --manifest-sha256 "${manifest_sha}"

if [[ "${1:-}" == "--preflight-only" ]]; then
  echo "TPC-C v33 diag02 continuation preflight passed"
  exit 0
fi
[[ "$#" -eq 0 ]]

if tmux has-session -t "${session}" 2>/dev/null; then
  echo "TPC-C v33 tmux session already exists" >&2
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
printf 'TPCC_V33_STARTED session=%s pid=%s\n' "${session}" "${pane_pid}"
