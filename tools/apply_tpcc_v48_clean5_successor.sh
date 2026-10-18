#!/usr/bin/env bash
set -euo pipefail

project=/home/anonymous/program/resource_adaptive

require_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

require_sha "$project/tools/audit_tpcc_v36_postrun_events.py" ad159630130c27de2696ca24d123db7bcdc4ac1e5143fffd8a67b5433250f9bd
require_sha "$project/tools/build_tpcc_v44_plan_regime_score_separation.py" 0b60511496aca865a7743dce5e95eff477eb76cfa4a7cb8bdc52ee4a7dd3de3c
require_sha "$project/tools/tpcc_v48_reconcile_orphan_reserved.py" f6009746ea4d12da30692cefa2cbbeacadc980d31d15a2f59861e4fc9d5eef62
require_sha "$project/tools/tpcc_v48_supersede_premature_technical.py" 5aaeaed9f2b9c0ec17e5141bed5fd3e61c3724bae40867da518eb65e7010912a
require_sha "$project/tools/build_tpcc_v48_clean5_failed_gate_successor.py" 1c52c2f2fbc2bfeb05c8f5f9d99b1b48eb7a34d99981cf71e8a994362d976136
require_sha "$project/tools/run_tpcc_v48_clean5_successor_strict_serial.py" eb3ce9a6411eefd4d11dce9fca84e037b84c94ddf22651568539a4e5f9c0a1fe
require_sha "$project/tools/run_tpcc_v48_autonomous_clean5_successor_loop.py" 34d732b13e1fec6feabc0ec65df07e3e994a7b568591a62bff3e2a81bbb5a286
require_sha "$project/history/tpcc_v48_clean5_failed_gate_successor_package_20260804/top5.json" f04b396e748b62bf7e43105f05d4385bf6660a408cb4d05c1953b66a037ef7bc
require_sha "$project/history/tpcc_v48_clean5_failed_gate_successor_package_20260804/postrun_gate.json" 8607a32d9c4f1b98c037bf41f35a5bbfe121611150faa66aac4e5851f83a54aa
require_sha "$project/history/tpcc_v48_clean5_failed_gate_successor_package_20260804/manifest.json" a64bce421d8a3d7d397bc836dc6643e36e10e264df6d1d0c98da7d0ae7f87da1
require_sha "$project/history/tpcc_v48_late_completion_supersession_package_20260804/completed.json" 3e31c9c1d987fc99f8d5111cc3ed941b642c1c4acbf2d8a53305bf8255008a3f
require_sha "$project/history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json" 62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0

echo '{"status":"tpcc_v48_apply_verified","workload_calls_added":0,"launch_started":false}'
