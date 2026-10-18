#!/usr/bin/env python3
from __future__ import annotations
import copy,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as m
S=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v4_20260813T1850p0800/prepared_binding.json";O=ROOT/"config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v9/optimization_contract.json";D=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v5_20260813T1920p0800/prepared_binding.json"
def h(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 v=copy.deepcopy(m.verify_checkpoint(S));v.pop("checkpoint_sha256",None);v["source_run_root"]=str(D.parent.resolve());v["plan_replay_pruning_v2"]["optimization_contract"]={"path":str(O.resolve()),"sha256":h(O)};v["evidence"]={"source_checkpoint":{"path":str(S.resolve()),"sha256":h(S)},"baseline":v["evidence"]["baseline"],"outers":{}};v["builder_source_path"]=str(Path(__file__).resolve());v["builder_source_sha256"]=h(Path(__file__).resolve());v["checkpoint_sha256"]=m._canonical_sha256(v);D.parent.mkdir(parents=True,exist_ok=False);D.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n");D.with_name(D.name+".sha256").write_text(f"{h(D)}  {D.name}\n");m.verify_checkpoint(D);print(json.dumps({"path":str(D),"sha256":h(D)}));return 0
if __name__=="__main__":raise SystemExit(main())
