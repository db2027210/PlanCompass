#!/usr/bin/env python3
"""Publish v9/v12 with coordinated point-gate lease proof."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OLD_OPT=ROOT/"config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v8/optimization_contract.json"
NEW_OPT=ROOT/"config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v9/optimization_contract.json"
OLD_COORD=ROOT/"config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v11/coordination_contract.json"
NEW_COORD=ROOT/"config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v12/coordination_contract.json"
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def rebind(v:dict)->None:
 for k,r in list(v.items()):
  if k.endswith("_path") and k[:-5]+"_sha256" in v:v[k[:-5]+"_sha256"]=sha((ROOT/str(r)).resolve())
def write(p:Path,v:dict)->None:
 p.parent.mkdir(parents=True,exist_ok=False);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n");p.with_name(p.name+".sha256").write_text(f"{sha(p)}  {p.name}\n")
def main()->int:
 o=json.loads(OLD_OPT.read_text());o["schema_version"]="mysql_algorithm_c_outer_runtime_optimization_v9";o["predecessor"]={"path":str(OLD_OPT.relative_to(ROOT)),"sha256":sha(OLD_OPT)};o["successor_reason"]="coordinated_lease_proof_for_r0_and_algorithm_point_gates";o["formal_baseline_transition"]["point_gate_idle_controller_allowance_requires_live_exclusive_lease"]=True;rebind(o["bindings"]);write(NEW_OPT,o)
 c=json.loads(OLD_COORD.read_text());c["predecessor"]={"path":str(OLD_COORD.relative_to(ROOT)),"sha256":sha(OLD_COORD)};c["successor_reason"]="bind_r0_and_outer_point_gate_lease_proof_sources";c["source_bindings"]["r0_source_path"]="scripts/run_mysql_four_scene_r0_v5.py";c["source_bindings"]["r0_source_sha256"]=sha(ROOT/c["source_bindings"]["r0_source_path"]);c["source_bindings"]["v18_r0_wrapper_path"]="scripts/run_mysql_four_scene_r0_v18.py";c["source_bindings"]["v18_r0_wrapper_sha256"]=sha(ROOT/c["source_bindings"]["v18_r0_wrapper_path"]);rebind(c["source_bindings"]);write(NEW_COORD,c);print(json.dumps({"optimization_sha256":sha(NEW_OPT),"coordination_sha256":sha(NEW_COORD)}));return 0
if __name__=="__main__":raise SystemExit(main())
