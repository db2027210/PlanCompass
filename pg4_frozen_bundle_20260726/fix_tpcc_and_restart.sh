#!/usr/bin/env bash
set -euo pipefail

# Run on compute00. Payload files are shipped beside this script.
BUNDLE_ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
BASE=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
RUNNER=/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/shell/run_tpcc_sensitive_mix.py
TEST=/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/shell/test_run_tpcc_sensitive_mix_deadline.py
PYTHON=/home/anonymous/miniconda3/bin/python3
UNIT=pg5-live-tpcc-a12-uncertainty.service
EXPECTED=0de61169178fc39e936a34f4a8ac2440fd8c276e47eeca4f948538bba3105fbb

payload="$BUNDLE_ROOT/tpcc_attempt12_private_mix_runner.py"
payload_test="$BUNDLE_ROOT/test_tpcc_attempt12_private_mix_runner.py"
[[ "$(sha256sum "$payload" | awk '{print $1}')" == "$EXPECTED" ]]

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$RUNNER.backup_frozen_$stamp"
cp -p "$RUNNER" "$backup"
trap 'rc=$?; if (( rc != 0 )); then cp -p "$backup" "$RUNNER"; fi; exit $rc' EXIT
install -m 0755 "$payload" "$RUNNER"
install -m 0644 "$payload_test" "$TEST"
"$PYTHON" -m py_compile "$RUNNER" "$TEST"
"$PYTHON" "$TEST"
actual=$(sha256sum "$RUNNER" | awk '{print $1}')
[[ "$actual" == "$EXPECTED" ]]

sudo systemctl stop "$UNIT" 2>/dev/null || true
sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
sudo systemd-run --unit="$UNIT" --uid=anonymous --gid=anonymous \
    --working-directory="$BASE" --property=KillMode=control-group \
    --property=Restart=no "$BASE/run_with_joblib.sh"
sleep 3
active=$(sudo systemctl show "$UNIT" -p ActiveState --value)
pid=$(sudo systemctl show "$UNIT" -p MainPID --value)
[[ "$active" == active && "$pid" =~ ^[1-9][0-9]*$ ]]
trap - EXIT
printf 'TPCC_STARTED runner_sha=%s tests=pass state=%s pid=%s unit=%s\n' "$actual" "$active" "$pid" "$UNIT"
