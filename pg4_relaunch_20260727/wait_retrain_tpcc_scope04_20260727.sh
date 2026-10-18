#!/usr/bin/env bash
set -euo pipefail

while systemctl --user is-active --quiet pg4-tpcc-attempt15-scope04.service; do
    sleep 30
done

exec /home/anonymous/program/resource_adaptive/pg4_relaunch_20260727/run_tpcc_attempt15_retrain_only_20260727.sh
