#!/usr/bin/env python3
"""Build two repeat measurements for each selected preflight convergence point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=Path("preflight/full_20260721/summary.json"))
    parser.add_argument("--points", type=Path, default=Path("preflight/experience_points_20260721.json"))
    parser.add_argument("--output", type=Path, default=Path("preflight/convergence_repeats_20260721.json"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--benchmarks", default="", help="optional comma-separated subset")
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    source = json.loads(args.points.read_text(encoding="utf-8"))
    by_id = {item["point_id"]: item for item in source["points"]}
    output = []
    missing = []
    wanted = {item for item in args.benchmarks.split(",") if item}
    for row in summary["benchmarks"]:
        if wanted and row["benchmark"] not in wanted:
            continue
        selected = row.get("selected_convergence_point") or {}
        point_id = selected.get("point_id")
        if not point_id or point_id not in by_id:
            missing.append(row["benchmark"])
            continue
        for repeat in range(1, args.repeats + 1):
            item = dict(by_id[point_id])
            item["point_id"] = f"{row['benchmark']}-conv-r{repeat}"
            item["source_point_id"] = point_id
            item["source"] = "selected_preflight_convergence_repeat"
            output.append(item)
    if missing:
        raise RuntimeError(f"no convergence candidate for: {', '.join(missing)}")
    payload = {
        "version": 1,
        "protocol": source.get("protocol", {}),
        "source_summary": str(args.summary.resolve()),
        "points": output,
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "points": len(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
