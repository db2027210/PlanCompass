#!/usr/bin/env python3
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/"scripts/launch_mysql_algorithm_c_top4_io_xp4_recovery_v1.py").read_text()
repl={"frozen_mysql_algorithm_c_xp4_handoff_20260826_v2":"frozen_mysql_xp_cpu_disjoint_slots_20260826_v4","xp_four_worker_resource_successor.json":"resource_successor.json",'HISTORY / "v164_to_xp4_live_authority_20260826" / SCENE':'HISTORY / "v226_to_xp_combined_v4_schema_rebind_20260826" / SCENE','HISTORY / "v172_to_c_top4_io_xp4_recovery_20260826"':'HISTORY / "v225_to_c_top4_io_xp4_recovery_20260826"','"v172_to_c_top4_io_xp4_20260826_attempt006_xp_io"':'"v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"','ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"':'ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v15.py"','"mysql-algc-top4-io-xp4-v172.service"':'"mysql-algc-top4-io-xp4-v225.service"'}
for old,new in repl.items():
 if source.count(old)!=1: raise RuntimeError(f"C v22 launcher fragment differs: {old}")
 source=source.replace(old,new)
f='"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'; n='"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n        "/usr/bin/env", f"MYSQL_C_VERIFIED_BASELINE={VERIFIED_BASELINE}",\n        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'
if source.count(f)!=1: raise RuntimeError("C v22 baseline fragment differs")
source=source.replace(f,n); impl:dict[str,Any]={"__file__":str(Path(__file__).resolve()),"__name__":"scripts._c_v22_launch","__package__":"scripts"}; exec(compile(source,str(Path(__file__).resolve()),"exec"),impl); impl["VERIFIED_BASELINE"]=impl["HISTORY"]/"v186_to_c_top4_io_xp4_20260826_attempt013_xp_io/formal_incumbent_baseline/batch_result.json"
if __name__=="__main__": raise SystemExit(int(impl["main"]()))
