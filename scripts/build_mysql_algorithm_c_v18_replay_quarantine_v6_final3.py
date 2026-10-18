#!/usr/bin/env python3
from __future__ import annotations
import copy,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parents[1];S=R/"history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final2_20260813T1855p0800/replay_quarantine.json";C=R/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v5_20260813T1920p0800/prepared_binding.json";D=R/"history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final3_20260813T1925p0800/replay_quarantine.json"
def h(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 v=copy.deepcopy(json.loads(S.read_text()));v["evidence"]["checkpoint"]={"path":str(C.resolve()),"sha256":h(C)};v["evidence"]["source_quarantine"]={"path":str(S.resolve()),"sha256":h(S)};v["builder_source_path"]=str(Path(__file__).resolve());v["builder_source_sha256"]=h(Path(__file__).resolve());D.parent.mkdir(parents=True,exist_ok=False);D.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n");D.with_name(D.name+".sha256").write_text(f"{h(D)}  {D.name}\n");print(json.dumps({"path":str(D),"sha256":h(D)}));return 0
if __name__=="__main__":raise SystemExit(main())
