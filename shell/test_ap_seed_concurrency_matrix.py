#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence


DEFAULT_BEFORE_CMD = (
    "ssh anonymous@192.0.2.8 "
    "\"bash -lc 'sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
    "-D /home/anonymous/pg_master/pg_data -m fast restart </dev/null >/dev/null 2>&1 "
    "&& sleep 5 && sudo -n /home/anonymous/bin/drop_vm_cache.sh && sleep 10'\""
)


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def find_latest_summary(result_dir: Path) -> Path:
    candidates = list(result_dir.glob("*/seed_summary.csv"))
    if not candidates:
        raise FileNotFoundError(f"no seed_summary.csv found under {result_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_top_seeds(summary_csv: Path, top_n: int, rank_key: str) -> List[int]:
    rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    valid = []
    for row in rows:
        score = parse_float(row.get(rank_key))
        seed = row.get("seed")
        if score is None or seed is None:
            continue
        valid.append((score, int(seed), row))
    if not valid:
        raise RuntimeError(f"no valid rows with rank key {rank_key} in {summary_csv}")
    valid.sort(key=lambda item: item[0], reverse=True)
    seeds: List[int] = []
    for _, seed, _ in valid:
        if seed not in seeds:
            seeds.append(seed)
        if len(seeds) >= top_n:
            break
    return seeds


def run_seed_selector(
    selector_script: Path,
    seeds: Sequence[int],
    apclient: int,
    run_mins: int,
    repeats: int,
    output_dir: Path,
    before_cmd: str,
) -> int:
    seed_args: List[str] = []
    for _ in range(max(1, repeats)):
        seed_args.extend(str(seed) for seed in seeds)
    cmd = [
        sys.executable,
        str(selector_script),
        "--seeds",
        *seed_args,
        "--apclient",
        str(apclient),
        "--run-mins",
        str(run_mins),
        "--output-dir",
        str(output_dir),
    ]
    if before_cmd:
        cmd.extend(["--before-cmd", before_cmd])
    print(f"[apclient={apclient}] running {len(seed_args)} tests -> {output_dir}", flush=True)
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def aggregate_rows(summary_csv: Path, apclient: int) -> List[Dict[str, object]]:
    rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    by_seed: Dict[int, List[Dict[str, str]]] = {}
    for row in rows:
        seed_text = row.get("seed")
        if not seed_text:
            continue
        by_seed.setdefault(int(seed_text), []).append(row)

    aggregated: List[Dict[str, object]] = []
    for seed, seed_rows in by_seed.items():
        output: Dict[str, object] = {"apclient": apclient, "seed": seed, "runs": len(seed_rows)}
        for src_key, dst_key in [
            ("final_qps", "mean_qps"),
            ("avg_ap_p95_ms", "mean_p95_ms"),
            ("max_ap_p99_ms", "mean_max_p99_ms"),
            ("current_qps_cv", "mean_qps_cv"),
            ("balanced_score", "mean_balanced_score"),
        ]:
            values = [parse_float(row.get(src_key)) for row in seed_rows]
            numeric = [value for value in values if value is not None]
            output[dst_key] = statistics.mean(numeric) if numeric else None
        qps_values = [parse_float(row.get("final_qps")) for row in seed_rows]
        qps_numeric = [value for value in qps_values if value is not None]
        output["repeat_qps_cv"] = (
            statistics.stdev(qps_numeric) / statistics.mean(qps_numeric)
            if len(qps_numeric) > 1 and statistics.mean(qps_numeric) > 0
            else None
        )
        output["first_run_list"] = seed_rows[0].get("first_run_list", "")
        output["source_csv"] = str(summary_csv)
        aggregated.append(output)

    aggregated.sort(
        key=lambda row: (
            parse_float(row.get("mean_balanced_score")) is not None,
            parse_float(row.get("mean_balanced_score")) or -1.0,
        ),
        reverse=True,
    )
    for idx, row in enumerate(aggregated, start=1):
        row["rank_in_apclient"] = idx
    return aggregated


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test top AP random seeds across a range of apclient concurrency values."
    )
    parser.add_argument("--source-summary", default="", help="Existing seed_summary.csv. Defaults to latest under result/.")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--rank-key", default="balanced_score")
    parser.add_argument("--apclient-min", type=int, default=1)
    parser.add_argument("--apclient-max", type=int, default=8)
    parser.add_argument("--run-mins", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--before-cmd", default=DEFAULT_BEFORE_CMD)
    parser.add_argument("--selector-script", default="shell/select_hybench_ap_seed.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source_summary = Path(args.source_summary).resolve() if args.source_summary else find_latest_summary(Path("result"))
    seeds = load_top_seeds(source_summary, args.top_n, args.rank_key)
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else Path("result") / f"ap_seed_concurrency_matrix_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    apclients = list(range(args.apclient_min, args.apclient_max + 1))
    print(f"Source summary: {source_summary}", flush=True)
    print(f"Top {len(seeds)} seeds by {args.rank_key}: {' '.join(str(seed) for seed in seeds)}", flush=True)
    print(f"AP clients: {' '.join(str(ap) for ap in apclients)}", flush=True)
    print(f"Output dir: {output_dir}", flush=True)

    if args.dry_run:
        return 0

    selector_script = Path(args.selector_script).resolve()
    all_rows: List[Dict[str, object]] = []
    returncodes: Dict[int, int] = {}

    for apclient in apclients:
        ap_dir = output_dir / f"apclient_{apclient}"
        rc = run_seed_selector(
            selector_script=selector_script,
            seeds=seeds,
            apclient=apclient,
            run_mins=args.run_mins,
            repeats=args.repeats,
            output_dir=ap_dir,
            before_cmd=args.before_cmd,
        )
        returncodes[apclient] = rc
        summary_csv = ap_dir / "seed_summary.csv"
        if summary_csv.exists():
            all_rows.extend(aggregate_rows(summary_csv, apclient))
        else:
            print(f"[WARN] missing summary for apclient={apclient}: {summary_csv}", flush=True)

    matrix_csv = output_dir / "concurrency_seed_matrix.csv"
    matrix_fields = [
        "apclient",
        "rank_in_apclient",
        "seed",
        "runs",
        "mean_qps",
        "mean_p95_ms",
        "mean_max_p99_ms",
        "mean_qps_cv",
        "repeat_qps_cv",
        "mean_balanced_score",
        "first_run_list",
        "source_csv",
    ]
    write_csv(matrix_csv, all_rows, matrix_fields)

    best_rows = []
    for apclient in apclients:
        rows = [row for row in all_rows if row.get("apclient") == apclient]
        if rows:
            best_rows.append(min(rows, key=lambda row: int(row.get("rank_in_apclient") or 999999)))
    best_csv = output_dir / "best_seed_by_apclient.csv"
    write_csv(best_csv, best_rows, matrix_fields)

    seed_rank_rows = []
    for seed in seeds:
        rows = [row for row in all_rows if row.get("seed") == seed]
        ranks = [int(row["rank_in_apclient"]) for row in rows if row.get("rank_in_apclient")]
        qps_values = [parse_float(row.get("mean_qps")) for row in rows]
        qps_numeric = [value for value in qps_values if value is not None]
        score_values = [parse_float(row.get("mean_balanced_score")) for row in rows]
        score_numeric = [value for value in score_values if value is not None]
        seed_rank_rows.append(
            {
                "seed": seed,
                "covered_apclients": len(rows),
                "avg_rank": statistics.mean(ranks) if ranks else None,
                "best_rank": min(ranks) if ranks else None,
                "worst_rank": max(ranks) if ranks else None,
                "avg_qps": statistics.mean(qps_numeric) if qps_numeric else None,
                "avg_balanced_score": statistics.mean(score_numeric) if score_numeric else None,
            }
        )
    seed_rank_rows.sort(
        key=lambda row: (
            parse_float(row.get("avg_rank")) is not None,
            -(parse_float(row.get("avg_rank")) or 999999.0),
            parse_float(row.get("avg_balanced_score")) or -1.0,
        ),
        reverse=True,
    )
    overall_csv = output_dir / "overall_seed_ranking.csv"
    write_csv(
        overall_csv,
        seed_rank_rows,
        ["seed", "covered_apclients", "avg_rank", "best_rank", "worst_rank", "avg_qps", "avg_balanced_score"],
    )

    summary = {
        "source_summary": str(source_summary),
        "top_seeds": seeds,
        "apclients": apclients,
        "run_mins": args.run_mins,
        "repeats": args.repeats,
        "returncodes": returncodes,
        "matrix_csv": str(matrix_csv),
        "best_seed_by_apclient_csv": str(best_csv),
        "overall_seed_ranking_csv": str(overall_csv),
        "best_seed_by_apclient": best_rows,
        "overall_seed_ranking": seed_rank_rows,
    }
    summary_json = output_dir / "summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSummary files:", flush=True)
    print(f"  {matrix_csv}", flush=True)
    print(f"  {best_csv}", flush=True)
    print(f"  {overall_csv}", flush=True)
    print(f"  {summary_json}", flush=True)
    return 0 if all(rc == 0 for rc in returncodes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
