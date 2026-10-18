#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as b
b.OUTPUT=ROOT/"config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v17"; b.CONTROLLER=ROOT/"scripts/run_mysql_algorithm_b_xp_top4_async_v8.py"; b.COORDINATION=b.OUTPUT/"coordination_contract.json"; b.CONTRACT=b.OUTPUT/"algorithm_b_top4_contract.json"; b.BOOTSTRAP_VERSION="v17"
b.SOURCES.update({"controller":b.CONTROLLER,"controller_predecessor":ROOT/"scripts/run_mysql_algorithm_b_xp_top4_async_v6.py","four_lane_scheduler":ROOT/"scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py","dynamic_noninterference_gate":ROOT/"scripts/mysql_cross_project_wave_coordination_v7.py","xp_four_worker_resource_freeze":ROOT/"config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json","current_authority_refresh":ROOT/"scripts/refresh_mysql_xp_combined_v4_live_authority_v1.py","slot_transition_capture":ROOT/"scripts/capture_mysql_xp_cpu_disjoint_slot_transition_v4.py","scene_launcher":ROOT/"scripts/launch_mysql_xp_top4_algorithm_b_scene_v8.py"})
def main():
 r=b.main(); c=b.load(b.CONTRACT); c.update({"baseline_accounting":{"physical_lane_count":4,"aggregate_divisor":"validated_lane_count","two_lane_literal_prohibited":True},"calibration_candidate_batches":[[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,15]],"calibration_replays_per_scene":15,"calibration_parallelism":4,"verified_baseline_technical_continuation":{"allowed":False}}); b.atomic(b.CONTRACT,c)
 for s in ("cpu","io"):
  p=b.HISTORY/f"v161_to_b_top4_fresh_bootstrap_20260826_v17_{s}"/"bootstrap_manifest.json"; m=b.load(p); m["release_contract_sha256"]=b.sha(b.CONTRACT); b.atomic(p,m)
 return r
if __name__=="__main__": raise SystemExit(main())
