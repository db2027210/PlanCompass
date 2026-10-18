#!/usr/bin/env python3
"""Schema-only rebind of passed v3 XP authorities to resource v4."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import prepare_mysql_xp4_live_authority_v1 as io

H=ROOT/"history/mysql_four_scene_20260809"
V3=ROOT/"config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v3/resource_successor.json"
V4=ROOT/"config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json"
SOURCES={"hybench_xp_cpu":H/"v211_to_xp_cpu_current_gap_live_authority_20260826/hybench_xp_cpu","hybench_xp_io":H/"v217_to_xp_io_combined_live_authority_20260826/hybench_xp_io"}
OUT=H/"v226_to_xp_combined_v4_schema_rebind_20260826"

def main():
 a=io.load(V3); b=io.load(V4); aa=copy.deepcopy(a); bb=copy.deepcopy(b); aa.pop("schema_version",None); bb.pop("schema_version",None); bb.pop("resource_successor_revision",None)
 if aa!=bb: raise RuntimeError("v3/v4 resource content differs beyond schema revision")
 for scene,root in SOURCES.items():
  dest=OUT/scene
  if dest.exists(): raise RuntimeError(f"fresh rebind required: {dest}")
  source_live=io.load(root/"live_authority.json")
  if source_live.get("status")!="PASS" or source_live.get("resource_freeze_sha256")!=io.sha(V3): raise RuntimeError(f"{scene} source authority differs")
  paths={}
  for name in ("transition_preflight.json","prepared_binding.json","benchmark_accounts_binding.json","r0_transition_waiver.json"):
   src=root/"authority"/name; d=io.load(src); d["freeze_path"]=str(V4); d["freeze_sha256"]=io.sha(V4); d["schema_only_resource_rebind"]={"source_path":str(src),"source_sha256":io.sha(src),"v3_resource_sha256":io.sha(V3),"resource_fields_changed":False,"rolling_gate_reexecuted":False,"original_passed_gate_preserved":True}
   if name=="prepared_binding.json": d["preflight_path"]=str(paths["preflight"]); d["preflight_sha256"]=io.sha(paths["preflight"])
   if name=="benchmark_accounts_binding.json": d["prepared_binding_path"]=str(paths["prepared"]); d["prepared_binding_sha256"]=io.sha(paths["prepared"])
   if name=="r0_transition_waiver.json":
    d["preflight_path"]=str(paths["preflight"]); d["preflight_sha256"]=io.sha(paths["preflight"]); d["prepared_binding_path"]=str(paths["prepared"]); d["prepared_binding_sha256"]=io.sha(paths["prepared"]); d["benchmark_accounts_path"]=str(paths["accounts"]); d["benchmark_accounts_sha256"]=io.sha(paths["accounts"])
   out=dest/"authority"/name; io.atomic(out,d); paths[{"transition_preflight.json":"preflight","prepared_binding.json":"prepared","benchmark_accounts_binding.json":"accounts","r0_transition_waiver.json":"r0"}[name]]=out
  live=copy.deepcopy(source_live); live["resource_freeze_path"]=str(V4); live["resource_freeze_sha256"]=io.sha(V4); live["artifacts"]={k:str(v) for k,v in paths.items()}; live["schema_only_resource_rebind"]={"source_path":str(root/"live_authority.json"),"source_sha256":io.sha(root/"live_authority.json"),"resource_fields_changed":False,"accepted_state_credit_increment":0}; io.atomic(dest/"live_authority.json",live)
 print(json.dumps({"status":"PASS","output":str(OUT)},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
