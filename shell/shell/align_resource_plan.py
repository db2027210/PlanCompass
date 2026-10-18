#!/usr/bin/env python3

import argparse
import csv
import json
import os
from collections import defaultdict, Counter
from datetime import datetime


def parse_args():
    p = argparse.ArgumentParser(description="Align runtime resource metrics with parsed plan events")
    p.add_argument("--runtime-jsonl", required=True, help="runtime_metrics_*.jsonl")
    p.add_argument("--plan-jsonl", required=True, help="Parsed plan_events.jsonl")
    p.add_argument("--output-joined-csv", required=True, help="Output CSV joined by phase+fingerprint")
    p.add_argument("--output-phase-resource-csv", required=True, help="Output phase-level resource summary CSV")
    p.add_argument("--warmup-ratio", type=float, default=0.2, help="Warmup ratio in [0,1)")
    p.add_argument("--cooldown-ratio", type=float, default=0.2, help="Cooldown ratio in [0,1)")
    return p.parse_args()


def ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def parse_ts_maybe_str(v):
    if isinstance(v, (int, float)):
        return int(v)

    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
            try:
                return int(datetime.strptime(v, fmt).timestamp())
            except Exception:
                pass
    return None


def phase_for(ts, start_ts, end_ts, warmup_ratio, cooldown_ratio):
    if end_ts <= start_ts:
        return "steady"
    total = end_ts - start_ts
    warmup_end = start_ts + total * warmup_ratio
    cooldown_start = end_ts - total * cooldown_ratio

    if ts < warmup_end:
        return "warmup"
    if ts >= cooldown_start:
        return "cooldown"
    return "steady"


def load_runtime(runtime_jsonl):
    rows = []
    with open(runtime_jsonl, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = parse_ts_maybe_str(obj.get("timestamp"))
            if ts is None:
                continue
            rows.append(
                {
                    "ts": ts,
                    "cpu_used": to_float(obj.get("cpu_used")),
                    "read_bytes_per_sec": to_float(obj.get("read_bytes_per_sec")),
                    "write_bytes_per_sec": to_float(obj.get("write_bytes_per_sec")),
                    "memory_working_set_bytes": to_float(obj.get("memory_working_set_bytes")),
                }
            )
    rows.sort(key=lambda x: x["ts"])
    return rows


def load_plan(plan_jsonl):
    rows = []
    with open(plan_jsonl, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = parse_ts_maybe_str(obj.get("timestamp"))
            if ts is None:
                continue
            rows.append(
                {
                    "ts": ts,
                    "query_fingerprint": str(obj.get("query_fingerprint") or ""),
                    "plan_hash": str(obj.get("plan_hash") or ""),
                    "execution_time_ms": to_float(obj.get("execution_time_ms")),
                }
            )
    rows.sort(key=lambda x: x["ts"])
    return rows


def avg(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return ""
    return sum(vals) / len(vals)


def summarize_phase_resources(runtime_rows, start_ts, end_ts, warmup_ratio, cooldown_ratio):
    buckets = defaultdict(list)
    for r in runtime_rows:
        ph = phase_for(r["ts"], start_ts, end_ts, warmup_ratio, cooldown_ratio)
        buckets[ph].append(r)

    out = {}
    for ph, rows in buckets.items():
        out[ph] = {
            "cpu_used_avg": avg([x["cpu_used"] for x in rows]),
            "read_bytes_per_sec_avg": avg([x["read_bytes_per_sec"] for x in rows]),
            "write_bytes_per_sec_avg": avg([x["write_bytes_per_sec"] for x in rows]),
            "memory_working_set_bytes_avg": avg([x["memory_working_set_bytes"] for x in rows]),
            "samples": len(rows),
        }
    return out


def summarize_plan(plan_rows, start_ts, end_ts, warmup_ratio, cooldown_ratio):
    by_key = defaultdict(list)
    for r in plan_rows:
        if r["ts"] < start_ts or r["ts"] > end_ts:
            continue
        ph = phase_for(r["ts"], start_ts, end_ts, warmup_ratio, cooldown_ratio)
        fp = r["query_fingerprint"] or "<empty>"
        by_key[(ph, fp)].append(r)

    out = {}
    for key, rows in by_key.items():
        counter = Counter(x["plan_hash"] for x in rows if x["plan_hash"])
        dominant_plan_hash = ""
        dominant_ratio = ""
        if counter:
            dominant_plan_hash, dominant_count = counter.most_common(1)[0]
            dominant_ratio = dominant_count / len(rows)

        out[key] = {
            "plan_event_count": len(rows),
            "unique_plan_hash_count": len(counter),
            "dominant_plan_hash": dominant_plan_hash,
            "dominant_plan_ratio": dominant_ratio,
            "avg_execution_time_ms": avg([x["execution_time_ms"] for x in rows]),
        }
    return out


def write_phase_resource_csv(path, phase_resource, start_ts, end_ts):
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(
            [
                "runtime_start_ts",
                "runtime_end_ts",
                "phase",
                "samples",
                "cpu_used_avg",
                "read_bytes_per_sec_avg",
                "write_bytes_per_sec_avg",
                "memory_working_set_bytes_avg",
            ]
        )
        for ph in ["warmup", "steady", "cooldown"]:
            st = phase_resource.get(ph, {})
            w.writerow(
                [
                    start_ts,
                    end_ts,
                    ph,
                    st.get("samples", 0),
                    st.get("cpu_used_avg", ""),
                    st.get("read_bytes_per_sec_avg", ""),
                    st.get("write_bytes_per_sec_avg", ""),
                    st.get("memory_working_set_bytes_avg", ""),
                ]
            )


def write_joined_csv(path, plan_summary, phase_resource):
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(
            [
                "phase",
                "query_fingerprint",
                "plan_event_count",
                "unique_plan_hash_count",
                "dominant_plan_hash",
                "dominant_plan_ratio",
                "avg_execution_time_ms",
                "phase_cpu_used_avg",
                "phase_read_bytes_per_sec_avg",
                "phase_write_bytes_per_sec_avg",
                "phase_memory_working_set_bytes_avg",
                "phase_runtime_samples",
            ]
        )

        keys = sorted(plan_summary.keys(), key=lambda x: (x[0], x[1]))
        for ph, fp_key in keys:
            ps = plan_summary[(ph, fp_key)]
            rs = phase_resource.get(ph, {})
            w.writerow(
                [
                    ph,
                    fp_key,
                    ps.get("plan_event_count", 0),
                    ps.get("unique_plan_hash_count", 0),
                    ps.get("dominant_plan_hash", ""),
                    ps.get("dominant_plan_ratio", ""),
                    ps.get("avg_execution_time_ms", ""),
                    rs.get("cpu_used_avg", ""),
                    rs.get("read_bytes_per_sec_avg", ""),
                    rs.get("write_bytes_per_sec_avg", ""),
                    rs.get("memory_working_set_bytes_avg", ""),
                    rs.get("samples", 0),
                ]
            )


def main():
    args = parse_args()
    if args.warmup_ratio < 0 or args.cooldown_ratio < 0 or (args.warmup_ratio + args.cooldown_ratio) >= 1:
        raise ValueError("warmup_ratio + cooldown_ratio must be < 1 and each >= 0")

    runtime_rows = load_runtime(args.runtime_jsonl)
    if not runtime_rows:
        raise RuntimeError(f"runtime jsonl is empty or invalid: {args.runtime_jsonl}")

    plan_rows = load_plan(args.plan_jsonl)
    if not plan_rows:
        raise RuntimeError(f"plan jsonl is empty or invalid: {args.plan_jsonl}")

    start_ts = runtime_rows[0]["ts"]
    end_ts = runtime_rows[-1]["ts"]

    phase_resource = summarize_phase_resources(
        runtime_rows,
        start_ts,
        end_ts,
        args.warmup_ratio,
        args.cooldown_ratio,
    )
    plan_summary = summarize_plan(
        plan_rows,
        start_ts,
        end_ts,
        args.warmup_ratio,
        args.cooldown_ratio,
    )

    ensure_parent(args.output_phase_resource_csv)
    ensure_parent(args.output_joined_csv)

    write_phase_resource_csv(args.output_phase_resource_csv, phase_resource, start_ts, end_ts)
    write_joined_csv(args.output_joined_csv, plan_summary, phase_resource)

    print(f"[align_resource_plan] runtime_window={start_ts}..{end_ts}")
    print(f"[align_resource_plan] plan_rows={len(plan_rows)} in_window_keys={len(plan_summary)}")
    print(f"[align_resource_plan] phase_resource_csv={args.output_phase_resource_csv}")
    print(f"[align_resource_plan] joined_csv={args.output_joined_csv}")


if __name__ == "__main__":
    main()
