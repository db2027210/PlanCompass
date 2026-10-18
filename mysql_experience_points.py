#!/usr/bin/env python3
"""Build ten deterministic, experience-guided points per MySQL workload.

Each coordinate is one of ten equally spaced levels in its configured absolute
range from the existing ``xp_io_pool_cost_topk`` experiment profile.
The vectors are deliberately structured around plan-cost directions observed in
the previous MySQL runs; this is not an unstructured random design.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

PARAMS = ["io_block_read_cost","memory_block_read_cost","row_evaluate_cost","key_compare_cost","disk_temptable_create_cost","disk_temptable_row_cost","memory_temptable_create_cost","memory_temptable_row_cost"]
DEFAULTS = {"io_block_read_cost":1.0,"memory_block_read_cost":.25,"row_evaluate_cost":.1,"key_compare_cost":.05,"disk_temptable_create_cost":20.0,"disk_temptable_row_cost":.5,"memory_temptable_create_cost":1.0,"memory_temptable_row_cost":.1}
BOUNDS = {
 "io_block_read_cost":[.01,100.0], "memory_block_read_cost":[.01,100.0],
 "row_evaluate_cost":[.01,10.0], "key_compare_cost":[.01,10.0],
 "disk_temptable_create_cost":[.01,100.0], "disk_temptable_row_cost":[.01,100.0],
 "memory_temptable_create_cost":[.01,100.0], "memory_temptable_row_cost":[.01,100.0],
}
TARGETS = {"hybench_ap":"memory","job":"memory","tpch":"memory","hybench_tp":"cpu","tpcc":"cpu","hybench_xp":"io"}
VARIANTS = {
 "hybench_ap":"AP query 10, 2 clients",
 "job":"JOB 9b.sql on IMDB, concurrency 1",
 "tpch":"TPC-H Q6, 1 terminal",
 "hybench_tp":"TP transaction 17, 8 clients; 1.5 CPU cap",
 "tpcc":"TPC-C StockLevel-only weights 0,0,0,0,100; 8 terminals",
 "hybench_xp":"Native XP IQ1-IQ7 uniform random, XAP 8 + XTP 8 clients; 8 MiB/s IO cap",
}

# A deterministic Latin-hypercube layout: every parameter uses each of the ten
# equal levels exactly once.  The first two rows retain the lower/upper anchors;
# the remaining crossed rows preserve the low-IO/high-memory, row/key and
# temporary-table contrasts that caused plan switches in the previous runs.
BASE = [
 [0,0,0,0,0,0,0,0], [9,9,9,9,9,9,9,9],
 [1,3,4,6,5,5,4,6], [8,6,5,3,4,4,6,4],
 [2,2,1,1,8,8,1,1], [7,7,8,8,1,1,8,8],
 [3,4,3,5,6,3,5,5], [6,5,6,4,3,6,3,3],
 [4,1,2,2,7,7,7,7], [5,8,7,7,2,2,2,2],
]

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",type=Path,default=Path("preflight/experience_points_20260721.json")); a=ap.parse_args()
    points=[]
    for benchmark,target in TARGETS.items():
        for i,levels in enumerate(BASE):
            lv=list(levels)
            values={
                p:BOUNDS[p][0]+(BOUNDS[p][1]-BOUNDS[p][0])*lv[j]/9.0
                for j,p in enumerate(PARAMS)
            }
            multipliers={p:values[p]/DEFAULTS[p] for p in PARAMS}
            points.append({"point_id":f"{benchmark}-p{i}","benchmark":benchmark,"workload_variant":VARIANTS[benchmark],"target_bottleneck":target,"levels":lv,"multipliers":multipliers,"values":values,"source":"experience_anchor_or_interpolation"})
    for column,param in enumerate(PARAMS):
        levels=sorted(row[column] for row in BASE)
        if levels != list(range(10)):
            raise RuntimeError(f"{param} does not cover each equal level exactly once: {levels}")
    payload={"version":3,"scheme":"10-level parameter-specific absolute-bound Latin hypercube with experience-guided contrasts","parameters":PARAMS,"defaults":DEFAULTS,"absolute_bounds":BOUNDS,"level_values":{p:[BOUNDS[p][0]+(BOUNDS[p][1]-BOUNDS[p][0])*level/9.0 for level in range(10)] for p in PARAMS},"protocol":{"warmup_sec":60,"min_measure_sec":180,"max_measure_sec":600,"early_stop_on_stability":True,"bottleneck_thresholds":{"memory":.70,"cpu":.80,"io":.75}},"workload_variants":VARIANTS,"job_dataset":"IMDB","points":points}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2)+"\n"); print(json.dumps({"output":str(a.output),"points":len(points),"per_benchmark":10},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
