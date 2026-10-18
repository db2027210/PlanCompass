#!/usr/bin/env bash
set -euo pipefail
cd /home/anonymous/program/resource_adaptive
TS=${1:-20260713_164008}
ROOT=history/bonect_parallel_vm_replay
python3 shell/summarize_family_pair_results.py \
  "$ROOT/pg_bonect_p2_memory_sort_v124_wm32_pair_${TS}" \
  "$ROOT/pg_bonect_p2_memory_agg_v124_wm32_pair_${TS}" \
  "$ROOT/pg_bonect_p2_memory_join_v21_wm32_pair_${TS}" \
  | tee "report/bonect/phase2_memory_wm32_family_pairs_raw_${TS}.json"

python3 - <<PY
import json
from pathlib import Path
from datetime import datetime
TS="$TS"
raw=json.loads(Path(f"report/bonect/phase2_memory_wm32_family_pairs_raw_{TS}.json").read_text())
labels={"sort":"Sort AP1/3/7 v124","agg":"Aggregate AP8 v124","join":"Join AP10 v21"}
rows=[]
for item in raw:
  rid=item.get("run_id","")
  if "sort_" in rid: fam="sort"
  elif "agg_" in rid: fam="agg"
  elif "join_" in rid: fam="join"
  else: fam=rid
  d=item.get("resource_delta_pp") or {}
  rows.append({
    "family": fam,
    "label": labels.get(fam,fam),
    "positive": bool(item.get("family_positive")),
    "qps": item.get("db_qps_delta_pct"),
    "dmem": d.get("memory"),
    "dcpu": d.get("cpu"),
    "dio": d.get("io"),
    "base_mem": (item.get("base_util") or {}).get("memory"),
    "final_mem": (item.get("cand_util") or {}).get("memory"),
    "base_cpu": (item.get("base_util") or {}).get("cpu"),
    "base_io": (item.get("base_util") or {}).get("io"),
    "io_source": item.get("io_source"),
    "io_warn": item.get("io_metric_warning"),
    "status": item.get("status") or item.get("status_row"),
    "candidate": item.get("candidate_id"),
    "run_id": rid,
  })
pos=[r for r in rows if r["positive"]]
md=[]
md.append(f"# Memory  worker-local  ({TS})")
md.append("")
md.append(f"- : `{datetime.now().isoformat(timespec='seconds')}`")
md.append("- Configuration: work_mem=32MB, shared_buffers=1536MB, hash_mem_multiplier=1.0, cap_mem=2.45GiB")
md.append("- : **** Stage A PASS")
md.append("")
md.append("| family | ΔQPS% | ΔMem pp | ΔCPU pp | ΔIO pp | base Mem/CPU/IO | positive | reason |")
md.append("|---|---:|---:|---:|---:|---|---|---|")
for r in rows:
  base=f"{(r['base_mem'] or 0):.1f}/{(r['base_cpu'] or 0):.1f}/{(r['base_io'] or 0):.1f}" if r['base_mem'] is not None else "n/a"
  q=f"{r['qps']:.2f}" if isinstance(r['qps'],(int,float)) else "n/a"
  dm=f"{r['dmem']:.2f}" if isinstance(r['dmem'],(int,float)) else "n/a"
  dc=f"{r['dcpu']:.2f}" if isinstance(r['dcpu'],(int,float)) else "n/a"
  di=f"{r['dio']:.2f}" if isinstance(r['dio'],(int,float)) else "n/a"
  reason = "KEEP" if r["positive"] else "DROP/down-weight"
  md.append(f"| {r['label']} | {q} | {dm} | {dc} | {di} | {base} | {r['positive']} | {reason} |")
md.append("")
md.append("## ")
if pos:
  for r in pos:
    md.append(f"- `{r['family']}`: {r['label']} (run `{r['run_id']}`)")
else:
  md.append("- **** profile/weights  work_mem  Stage A ")
md.append("")
md.append("## ")
if pos:
  md.append("-  AP  workload")
  md.append("-  profile  default Memory 82-90%CPU/IO<80%physical vda ")
  md.append("-  fresh baseline + teacher/ + reconfirm")
else:
  md.append("-  IO  4MB/16MB ")
Path(f"report/bonect/phase2_memory_wm32_family_pairs_result_{TS}.md").write_text("\n".join(md)+"\n", encoding="utf-8")
Path(f"report/bonect/phase2_memory_wm32_family_pairs_result_{TS}.json").write_text(json.dumps({"rows":rows,"positive":pos},indent=2)+"\n", encoding="utf-8")
print("positive_families", [r["family"] for r in pos])
print("wrote result md/json")
PY
