#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import os
import re
from collections import defaultdict

TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\s*[+-]\d{2}(?::?\d{2})?)?)"
)


def parse_args():
    p = argparse.ArgumentParser(description="Parse auto_explain JSON logs to structured files")
    p.add_argument("--log-dir", required=True, help="Directory that contains *.log files")
    p.add_argument("--output-jsonl", required=True, help="Output JSONL for parsed plan events")
    p.add_argument("--output-summary-csv", required=True, help="Output CSV aggregated by fingerprint+plan_hash")
    p.add_argument("--output-plan-hash-csv", default="", help="Optional CSV aggregated by plan_hash only")
    p.add_argument("--pattern", default=".log", help="Filename suffix filter (default: .log)")
    return p.parse_args()


def parse_timestamp_from_line(line):
    m = TS_RE.search(line)
    if not m:
        return ""
    return re.sub(r"\s+([+-]\d{2}(?::?\d{2})?)$", r"\1", m.group(1).replace("T", " ").strip())


def normalize_sql(sql):
    s = sql.lower()
    s = re.sub(r"'([^'\\]|\\.)*'", "?", s)
    s = re.sub(r'"([^"\\]|\\.)*"', "?", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def plan_hash(plan_obj):
    canon = json.dumps(plan_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def find_json_payload(line):
    idx = line.find("{")
    if idx < 0:
        return None

    candidate = line[idx:]
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(candidate)
        return obj
    except Exception:
        return None


def extract_event(obj):
    if not isinstance(obj, dict):
        return None

    plan = obj.get("Plan")
    if not isinstance(plan, dict):
        if "plan" in obj and isinstance(obj["plan"], dict):
            plan = obj["plan"]
        else:
            return None

    query_text = obj.get("Query Text", "") or obj.get("query", "") or obj.get("statement", "")
    query_text = str(query_text)

    fp = normalize_sql(query_text) if query_text else ""
    p_hash = plan_hash(plan)

    planning_time = obj.get("Planning Time")
    execution_time = obj.get("Execution Time")

    node_type = plan.get("Node Type", "") if isinstance(plan, dict) else ""

    ev = {
        "query_text": query_text,
        "query_fingerprint": fp,
        "plan_hash": p_hash,
        "plan_node_type": node_type,
        "planning_time_ms": planning_time if isinstance(planning_time, (int, float)) else None,
        "execution_time_ms": execution_time if isinstance(execution_time, (int, float)) else None,
        "plan_tree": plan,
    }
    return ev


def iter_log_files(log_dir, suffix):
    for root, _, files in os.walk(log_dir):
        for name in sorted(files):
            if suffix and not name.endswith(suffix):
                continue
            yield os.path.join(root, name)


def ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def main():
    args = parse_args()

    ensure_parent(args.output_jsonl)
    ensure_parent(args.output_summary_csv)
    if args.output_plan_hash_csv:
        ensure_parent(args.output_plan_hash_csv)

    stats = defaultdict(lambda: {"count": 0, "sum_exec": 0.0, "exec_n": 0, "first_ts": "", "last_ts": ""})
    hash_stats = defaultdict(int)

    total_lines = 0
    parsed_events = 0

    with open(args.output_jsonl, "w", encoding="utf-8") as out_fp:
        for log_file in iter_log_files(args.log_dir, args.pattern):
            with open(log_file, "r", encoding="utf-8", errors="ignore") as in_fp:
                line_no = 0
                pending_ts = ""
                pending_line_no = 0
                pending_buffer = []
                pending_balance = 0

                def flush_pending():
                    nonlocal parsed_events, pending_ts, pending_line_no, pending_buffer, pending_balance
                    if not pending_buffer:
                        return
                    try:
                        obj = json.loads("".join(pending_buffer))
                    except Exception:
                        pending_ts = ""
                        pending_line_no = 0
                        pending_buffer = []
                        pending_balance = 0
                        return

                    event = extract_event(obj)
                    if event is None:
                        pending_ts = ""
                        pending_line_no = 0
                        pending_buffer = []
                        pending_balance = 0
                        return

                    row = {
                        "timestamp": pending_ts,
                        "log_file": os.path.basename(log_file),
                        "line_no": pending_line_no,
                        **event,
                    }
                    out_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    parsed_events += 1

                    key = (row["query_fingerprint"], row["plan_hash"])
                    st = stats[key]
                    st["count"] += 1
                    if isinstance(row["execution_time_ms"], (int, float)):
                        st["sum_exec"] += float(row["execution_time_ms"])
                        st["exec_n"] += 1

                    if pending_ts:
                        if not st["first_ts"] or pending_ts < st["first_ts"]:
                            st["first_ts"] = pending_ts
                        if not st["last_ts"] or pending_ts > st["last_ts"]:
                            st["last_ts"] = pending_ts

                    hash_stats[row["plan_hash"]] += 1

                    pending_ts = ""
                    pending_line_no = 0
                    pending_buffer = []
                    pending_balance = 0

                for raw in in_fp:
                    line_no += 1
                    total_lines += 1
                    if pending_buffer:
                        piece = raw
                        if not pending_buffer and "{" in raw:
                            piece = raw[raw.find("{"):]
                        pending_buffer.append(piece)
                        pending_balance += piece.count("{") - piece.count("}")
                        if pending_balance <= 0:
                            flush_pending()
                        continue

                    obj = find_json_payload(raw)
                    if obj is not None:
                        event = extract_event(obj)
                        if event is None:
                            continue

                        ts = parse_timestamp_from_line(raw)
                        row = {
                            "timestamp": ts,
                            "log_file": os.path.basename(log_file),
                            "line_no": line_no,
                            **event,
                        }

                        out_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                        parsed_events += 1

                        key = (row["query_fingerprint"], row["plan_hash"])
                        st = stats[key]
                        st["count"] += 1
                        if isinstance(row["execution_time_ms"], (int, float)):
                            st["sum_exec"] += float(row["execution_time_ms"])
                            st["exec_n"] += 1

                        if ts:
                            if not st["first_ts"] or ts < st["first_ts"]:
                                st["first_ts"] = ts
                            if not st["last_ts"] or ts > st["last_ts"]:
                                st["last_ts"] = ts

                        hash_stats[row["plan_hash"]] += 1
                        continue

                    if "plan:" in raw.lower():
                        pending_ts = parse_timestamp_from_line(raw)
                        pending_line_no = line_no
                        if "{" in raw:
                            piece = raw[raw.find("{"):]
                            pending_buffer = [piece]
                            pending_balance = piece.count("{") - piece.count("}")
                            if pending_balance <= 0:
                                flush_pending()
                        else:
                            pending_buffer = []
                            pending_balance = 0
                        continue

                    if pending_ts and "{" in raw:
                        piece = raw[raw.find("{"):]
                        pending_buffer = [piece]
                        pending_balance = piece.count("{") - piece.count("}")
                        if pending_balance <= 0:
                            flush_pending()

                flush_pending()

    with open(args.output_summary_csv, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow([
            "query_fingerprint",
            "plan_hash",
            "count",
            "avg_execution_time_ms",
            "first_ts",
            "last_ts",
        ])
        for (fingerprint, p_hash), st in sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True):
            avg = st["sum_exec"] / st["exec_n"] if st["exec_n"] > 0 else ""
            w.writerow([fingerprint, p_hash, st["count"], avg, st["first_ts"], st["last_ts"]])

    if args.output_plan_hash_csv:
        with open(args.output_plan_hash_csv, "w", newline="", encoding="utf-8") as fp:
            w = csv.writer(fp)
            w.writerow(["plan_hash", "count"])
            for p_hash, count in sorted(hash_stats.items(), key=lambda x: x[1], reverse=True):
                w.writerow([p_hash, count])

    print(f"[parse_auto_explain_logs] lines={total_lines} events={parsed_events}")
    print(f"[parse_auto_explain_logs] event_jsonl={args.output_jsonl}")
    print(f"[parse_auto_explain_logs] summary_csv={args.output_summary_csv}")
    if args.output_plan_hash_csv:
        print(f"[parse_auto_explain_logs] plan_hash_csv={args.output_plan_hash_csv}")


if __name__ == "__main__":
    main()
