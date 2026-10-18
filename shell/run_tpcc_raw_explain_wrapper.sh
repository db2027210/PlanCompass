#!/bin/sh
set -eu
echo "$$" > /tmp/tpcc_raw_explain.pid
cd /home/anonymous/program/resource_adaptive_mysql
exec env PYTHONPATH=. /home/anonymous/miniconda3/envs/py313/bin/python shell/run_tpcc_production_raw_explain.py
