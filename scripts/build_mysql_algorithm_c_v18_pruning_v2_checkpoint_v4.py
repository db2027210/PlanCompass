#!/usr/bin/env python3
"""Rebind final v18 checkpoint to runtime v8."""
from __future__ import annotations
import copy, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpointmod
SOURCE=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v3_20260813T1830p0800/prepared_binding.json"
OPT=ROOT/"config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v8/optimization_contract.json"
OUTPUT=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v4_20260813T1850p0800/prepared_binding.json"
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
    v=copy.deepcopy(checkpointmod.verify_checkpoint(SOURCE));v.pop("checkpoint_sha256",None)
    v["source_run_root"]=str(OUTPUT.parent.resolve());v["plan_replay_pruning_v2"]["optimization_contract"]={"path":str(OPT.resolve()),"sha256":sha(OPT)}
    v["evidence"]={"source_checkpoint":{"path":str(SOURCE.resolve()),"sha256":sha(SOURCE)},"baseline":v["evidence"]["baseline"],"outers":{}}
    v["builder_source_path"]=str(Path(__file__).resolve());v["builder_source_sha256"]=sha(Path(__file__).resolve());v["checkpoint_sha256"]=checkpointmod._canonical_sha256(v)
    OUTPUT.parent.mkdir(parents=True,exist_ok=False);OUTPUT.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n");OUTPUT.with_name(OUTPUT.name+".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n");checkpointmod.verify_checkpoint(OUTPUT);print(json.dumps({"path":str(OUTPUT),"sha256":sha(OUTPUT)}));return 0
if __name__=="__main__":raise SystemExit(main())
