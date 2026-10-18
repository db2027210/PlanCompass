#!/usr/bin/env python3
import argparse
import glob
import json
import os
import re
from typing import Dict, List, Tuple


IGNORE_ERROR_PATTERNS = [
    r"deadlock detected",
    r"sql state \[40P01\]",
    r'relation "pg_stat_archiver" does not exist',
]


def build_cases(series: str, bench: str) -> List[str]:
    if series == "M":
        tpcc = [f"c{i}" for i in range(11, 21)]
        tpch = [f"h{i}" for i in range(11, 21)]
        ch = [f"ch{i}" for i in range(21, 41)]
    elif series == "L":
        tpcc = [f"c{i}" for i in range(1, 11)]
        tpch = [f"h{i}" for i in range(1, 11)]
        ch = [f"ch{i}" for i in range(1, 21)]
    else:
        tpcc = [f"c{i}" for i in range(21, 31)]
        tpch = [f"h{i}" for i in range(21, 31)]
        ch = [f"ch{i}" for i in range(41, 61)]

    out: List[str] = []
    if bench in ("all", "tpcc"):
        out.extend(tpcc)
    if bench in ("all", "tpch"):
        out.extend(tpch)
    if bench in ("all", "ch"):
        out.extend(ch)
    return out


def has_non_ignored_error(path: str) -> Tuple[bool, str]:
    if not os.path.isfile(path):
        return False, ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            ignored = any(re.search(p, s, re.IGNORECASE) for p in IGNORE_ERROR_PATTERNS)
            if not ignored:
                return True, s[:200]
    return False, ""


def check_jsonl(path: str, sample_lines: int = 30) -> Tuple[bool, str, int]:
    if not os.path.isfile(path):
        return False, "missing", 0
    lines = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            lines += 1
            if lines <= sample_lines:
                try:
                    json.loads(s)
                except json.JSONDecodeError as e:
                    return False, f"invalid json at line {lines}: {e}", lines
    return True, "", lines


def find_latest(pattern: str) -> str:
    files = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    if not files:
        return ""
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def check_case(case_dir: str, min_jsonl_lines: int) -> Dict[str, object]:
    result: Dict[str, object] = {"status": "OK", "messages": []}
    add = result["messages"].append

    if not os.path.isdir(case_dir):
        result["status"] = "FAIL"
        add("case directory missing")
        return result

    bench_log = find_latest(os.path.join(case_dir, "benchbase_execution.log"))
    if not bench_log:
        result["status"] = "FAIL"
        add("benchbase_execution.log missing")
    elif os.path.getsize(bench_log) == 0:
        result["status"] = "FAIL"
        add("benchbase_execution.log empty")

    metrics_dir = os.path.join(case_dir, "metrics")
    runtime_logs = sorted(glob.glob(os.path.join(metrics_dir, "runtime_metrics_*.jsonl")))
    if not runtime_logs:
        result["status"] = "FAIL"
        add("runtime_metrics_*.jsonl missing")
    else:
        total_lines = 0
        for p in runtime_logs:
            ok, reason, lines = check_jsonl(p)
            total_lines += lines
            if not ok:
                result["status"] = "FAIL"
                add(f"{os.path.basename(p)} {reason}")
        if total_lines < min_jsonl_lines:
            if result["status"] != "FAIL":
                result["status"] = "WARN"
            add(f"runtime jsonl too few lines: {total_lines} < {min_jsonl_lines}")

    metric_csvs = glob.glob(os.path.join(metrics_dir, "*.csv"))
    if len(metric_csvs) < 5:
        if result["status"] != "FAIL":
            result["status"] = "WARN"
        add(f"metrics csv count too low: {len(metric_csvs)}")

    errors_dir = os.path.join(case_dir, "errors")
    for name in ("shell_cmd_error.log", "java_stacktrace.log"):
        p = os.path.join(errors_dir, name)
        bad, sample = has_non_ignored_error(p)
        if bad:
            result["status"] = "FAIL"
            add(f"{name} has non-ignored error: {sample}")

    sql_logs_dir = os.path.join(case_dir, "sql_logs")
    sql_logs = glob.glob(os.path.join(sql_logs_dir, "*.csv"))
    if not sql_logs:
        if result["status"] != "FAIL":
            result["status"] = "WARN"
        add("sql_logs csv missing")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check experiment result health.")
    parser.add_argument("--roots", required=True, help="Comma-separated result roots.")
    parser.add_argument("--mode", default="low", help="Result mode directory, e.g. low/shadow")
    parser.add_argument("--series", default="M", choices=["L", "M", "H"], help="Case series")
    parser.add_argument("--bench", default="all", choices=["all", "tpcc", "tpch", "ch"], help="Bench type")
    parser.add_argument("--cases", default="", help="Optional case list, comma-separated")
    parser.add_argument("--min-jsonl-lines", type=int, default=10, help="Minimum runtime jsonl lines")
    parser.add_argument(
        "--distributed",
        action="store_true",
        help="Treat multiple roots as distributed shards: each case only needs to exist in one root.",
    )
    args = parser.parse_args()

    if args.cases.strip():
        cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    else:
        cases = build_cases(args.series, args.bench)

    roots = [r.strip() for r in args.roots.split(",") if r.strip()]
    if not roots:
        print("No roots given.")
        return 2

    total_ok = 0
    total_warn = 0
    total_fail = 0

    if not args.distributed:
        for root in roots:
            print(f"\n=== Root: {root} | mode={args.mode} ===")
            mode_dir = os.path.join(root, args.mode)
            for case_id in cases:
                case_dir = os.path.join(mode_dir, case_id)
                r = check_case(case_dir, args.min_jsonl_lines)
                status = r["status"]
                msgs = r["messages"]
                print(f"[{status}] {case_id}: {case_dir}")
                for m in msgs:
                    print(f"  - {m}")
                if status == "OK":
                    total_ok += 1
                elif status == "WARN":
                    total_warn += 1
                else:
                    total_fail += 1
    else:
        print(f"\n=== Distributed Mode | roots={','.join(roots)} | mode={args.mode} ===")
        for case_id in cases:
            candidates = [os.path.join(root, args.mode, case_id) for root in roots]
            existing = [p for p in candidates if os.path.isdir(p)]
            if not existing:
                print(f"[FAIL] {case_id}: missing in all roots")
                total_fail += 1
                continue
            chosen = max(existing, key=lambda p: os.path.getmtime(p))
            r = check_case(chosen, args.min_jsonl_lines)
            status = r["status"]
            msgs = r["messages"]
            print(f"[{status}] {case_id}: {chosen}")
            for m in msgs:
                print(f"  - {m}")
            if status == "OK":
                total_ok += 1
            elif status == "WARN":
                total_warn += 1
            else:
                total_fail += 1

    print("\n=== Summary ===")
    print(f"OK:   {total_ok}")
    print(f"WARN: {total_warn}")
    print(f"FAIL: {total_fail}")

    return 1 if total_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
