#!/usr/bin/env python3
"""Rebind empty v18 quarantine to checkpoint v4."""
from __future__ import annotations
import copy,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final_20260813T1835p0800/replay_quarantine.json"
CHECKPOINT=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v4_20260813T1850p0800/prepared_binding.json"
OUTPUT=ROOT/"history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final2_20260813T1855p0800/replay_quarantine.json"
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
    v=copy.deepcopy(json.loads(SOURCE.read_text()));v["evidence"]["checkpoint"]={"path":str(CHECKPOINT.resolve()),"sha256":sha(CHECKPOINT)};v["evidence"]["source_quarantine"]={"path":str(SOURCE.resolve()),"sha256":sha(SOURCE)};v["builder_source_path"]=str(Path(__file__).resolve());v["builder_source_sha256"]=sha(Path(__file__).resolve());OUTPUT.parent.mkdir(parents=True,exist_ok=False);OUTPUT.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n");OUTPUT.with_name(OUTPUT.name+".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n");print(json.dumps({"path":str(OUTPUT),"sha256":sha(OUTPUT)}));return 0
if __name__=="__main__":raise SystemExit(main())
