#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v9 as p
b=p.predecessor; b.OUTPUT=ROOT/"config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v22"; b.SUCCESSOR=ROOT/"config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json"; b.RUNTIME=ROOT/"scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v9.py"; b.CONTROLLER=ROOT/"scripts/run_mysql_algorithm_c_async_scene_xp4_v15.py"
def main():
 r=p.main(); gate=ROOT/"scripts/mysql_cross_project_wave_coordination_v7.py"; refresh=ROOT/"scripts/refresh_mysql_xp_combined_v4_live_authority_v1.py"; cp=b.OUTPUT/"coordination_contract.json"; c=b.release.load(cp); sb=c.setdefault("source_bindings",{}); sb["coordination_source_path"]=str(gate.relative_to(ROOT)); sb["coordination_source_sha256"]=b.release.sha(gate); sb["authority_refresh_path"]=str(refresh.relative_to(ROOT)); sb["authority_refresh_sha256"]=b.release.sha(refresh); b.release.atomic(cp,c)
 for n in ("algorithm_c_xp4_async_contract.json","transition_contract.json"):
  q=b.OUTPUT/n; d=b.release.load(q); s=d.setdefault("source_bindings",{}); s["dynamic_gate"]=b.bind(gate); s["current_authority_refresh"]=b.bind(refresh); s["coordination_contract"]=b.bind(cp); b.release.atomic(q,d)
 return r
if __name__=="__main__": raise SystemExit(main())
