#!/usr/bin/env bash
# Mirror the two goal-owned XP experiment states into the workspace.
#
# The experiment logs live on /disk1 and /disk2, which are outside the
# interactive workspace sandbox.  Run this monitor once from the host user;
# consumers can then read the JSON below without repeatedly inspecting either
# external mount or Docker.
set -euo pipefail

workspace=${1:?usage: mirror_mysql_xp_gd_status.sh WORKSPACE_STATUS_JSON}
workspace_dir=$(dirname "$workspace")
a30_root=/disk2/resource_adaptive_mysql/gd_20260718/formal_dynamic_retry198_a30_isolated_disk2_cost25_cal090
b9_root=/disk1/resource_adaptive_mysql/gd_20260719/formal_dynamic_retry199_b9r1_isolated_disk1_cost10_cal1113

mkdir -p "$workspace_dir"

measurement_count() {
  local root=$1
  local count=0
  shopt -s nullglob
  local paths=("$root"/artifacts/*/*/measurements/hybench_xp_*)
  shopt -u nullglob
  count=${#paths[@]}
  printf '%s' "$count"
}

controller_alive() {
  local marker=$1
  pgrep -f "run_mysql_gd_matrix.py.*${marker}" >/dev/null && printf 'true' || printf 'false'
}

result_ready() {
  local result=$1
  [[ -f "$result" ]] && printf 'true' || printf 'false'
}

while true; do
  tmp_path="${workspace}.tmp.$$"
  {
    printf '{\n'
    printf '  "updated_at": "%s",\n' "$(date -Is)"
    printf '  "a30": {"measurement_dirs": %s, "controller_alive": %s, "result_ready": %s},\n' \
      "$(measurement_count "$a30_root")" "$(controller_alive retry198)" "$(result_ready "$a30_root/A30-r1.json")"
    printf '  "b9_r1": {"measurement_dirs": %s, "controller_alive": %s, "result_ready": %s}\n' \
      "$(measurement_count "$b9_root")" "$(controller_alive retry199)" "$(result_ready "$b9_root/B9-r1.json")"
    printf '}\n'
  } >"$tmp_path"
  mv "$tmp_path" "$workspace"
  sleep 15
done
