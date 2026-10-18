#!/usr/bin/env bash
set -euo pipefail

while systemctl --user is-active --quiet pg4-xp-scope07.service; do
    sleep 30
done

exec /home/anonymous/program/resource_adaptive/pg4_relaunch_20260727/run_xp_retrain_20260727.sh
