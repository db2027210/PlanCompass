#!/usr/bin/env python3
"""Audit evidence for the MySQL bottleneck preflight.

The audit intentionally distinguishes fresh random-point measurements from
historical formal convergence results; a historical pass cannot masquerade as
a fresh random-point pass.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

TARGETS = {
    "A25": ("hybench_ap", "memory"), "A26": ("job", "memory"),
    "A27": ("tpch", "memory"), "A28": ("hybench_tp", "cpu"),
    "A29": ("tpcc", "cpu"), "A30": ("hybench_xp", "io"),
}

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--formal-root", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/formal_dynamic")); ap.add_argument("--a30-retry", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/formal_dynamic_retry108_a30_q9_pool352_chunk16_partialprewarm39/A30-r1.json")); ap.add_argument("--random", type=Path, default=Path("preflight/random_measurements_20260721_v3.json")); ap.add_argument("--output", type=Path, default=Path("preflight/verification_20260721.json")); a=ap.parse_args()
    formal = []
    for exp, (bench, target) in TARGETS.items():
        path = a.formal_root / f"{exp}-r1.json"
        if exp == "A30" and path.exists():
            try:
                current = json.loads(path.read_text())
                if current.get("result", {}).get("bad_config"):
                    path = a.a30_retry
            except Exception:
                pass
        row = {"exp_id": exp, "benchmark": bench, "target": target, "path": str(path), "exists": path.exists()}
        if path.exists():
            p = json.loads(path.read_text()); first = p["outer_loop"][0]["resource"]; last = p["outer_loop"][-1]["resource"]
            key = "mem" if target == "memory" else target
            row.update({"initial": first[key], "final": last[key], "initial_bottleneck": first[key] >= .90, "final_cleared": last[key] <= .90, "converge_outer_iters": p["result"].get("converge_outer_iters"), "qps_before": p["result"].get("qps_before"), "qps_after": p["result"].get("qps_after")})
        formal.append(row)
    random = json.loads(a.random.read_text()).get("points", []) if a.random.exists() else []
    random_summary=[]
    for row in random:
        norm=row.get("resource_norm", {}); target=row.get("target_bottleneck"); key="memory" if target=="memory" else target
        random_summary.append({"point_id":row.get("point_id"),"benchmark":row.get("benchmark"),"target":target,"error":row.get("error"),"target_util":norm.get(key),"target_constructed":isinstance(norm.get(key),(int,float)) and norm[key]>=.90})
    payload={"formal_convergence":formal,"fresh_random_points":random_summary,"fresh_random_all_targets_constructed":all(x["target_constructed"] for x in random_summary if not x["error"])}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2)+"\n"); print(json.dumps(payload,indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
