#!/usr/bin/env python3
"""Manual sustained-window audit for HyBenchXP Memory floor A/B.

Does not trust driver pass alone. Aligns sidecar working-set series to the
JSONL validation window (or HyBench start/end) and enforces:

- aligned sample count >= 45 on both sides
- aligned mean >= 75% and p50 >= 75% on both sides
- |default-candidate| aligned-mean delta <= 3 pp
- status ok, physical blkio, CPU<80%, IO<75 MiB/s
- identical intended floor plan structure (from summary or EXPLAIN)

Writes a separate audit JSON next to the floor A/B root.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


CAP_GIB = 6.4
CPU_CORES = 10.0


def percentiles(vals: Sequence[float], ps: Sequence[float]) -> Dict[str, float]:
    if not vals:
        return {f"p{int(p)}": float("nan") for p in ps}
    s = sorted(vals)
    n = len(s)
    out: Dict[str, float] = {}
    for p in ps:
        if n == 1:
            out[f"p{int(p)}"] = s[0]
            continue
        k = (p / 100.0) * (n - 1)
        f = int(k)
        c = min(f + 1, n - 1)
        frac = k - f
        out[f"p{int(p)}"] = s[f] * (1 - frac) + s[c] * frac
    return out


def load_ws_series(csv_path: Path) -> List[Tuple[float, float, float]]:
    rows: List[Tuple[float, float, float]] = []
    if not csv_path.exists():
        return rows
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = float(row["ts"])
                gib = float(row.get("ws_gib") or 0.0)
                pct = float(row.get("ws_pct_cap") or (100.0 * gib / CAP_GIB))
            except (KeyError, ValueError):
                continue
            rows.append((ts, pct, gib))
    return rows


def last_event(jsonl: Path, names: Sequence[str]) -> Optional[dict]:
    if not jsonl.exists():
        return None
    last = None
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o.get("event") in names:
            last = o
    return last


def extract_window(ev: dict) -> Optional[Tuple[float, float]]:
    w = ev.get("window")
    if isinstance(w, (list, tuple)) and len(w) == 2:
        return float(w[0]), float(w[1])
    stats = ev.get("resource_stats") or {}
    for key in ("window", "sample_window", "stable_window"):
        w2 = stats.get(key)
        if isinstance(w2, (list, tuple)) and len(w2) == 2:
            return float(w2[0]), float(w2[1])
    # hybench timestamps as fallback (epoch may be absent)
    return None


def align_series(
    series: List[Tuple[float, float, float]],
    window: Optional[Tuple[float, float]],
) -> Dict[str, Any]:
    full_pct = [p for _, p, _ in series]
    full_stats = {
        "n": len(full_pct),
        "avg": (sum(full_pct) / len(full_pct)) if full_pct else float("nan"),
        **percentiles(full_pct, [50, 90, 95, 99]),
        "min": min(full_pct) if full_pct else float("nan"),
        "max": max(full_pct) if full_pct else float("nan"),
    }
    if not window:
        return {
            "window": None,
            "aligned": None,
            "full_series": full_stats,
            "note": "no_window_in_jsonl",
        }
    a, b = window
    aligned = [(ts, p, g) for ts, p, g in series if a <= ts <= b]
    pct = [p for _, p, _ in aligned]
    stats = {
        "n": len(pct),
        "avg": (sum(pct) / len(pct)) if pct else float("nan"),
        **percentiles(pct, [50, 90, 95, 99]),
        "min": min(pct) if pct else float("nan"),
        "max": max(pct) if pct else float("nan"),
    }
    # secondary diagnostics only (NOT pass gates by themselves)
    late = [p for ts, p, _ in aligned if ts >= b - 60.0]
    late_stats = {
        "n": len(late),
        "avg": (sum(late) / len(late)) if late else float("nan"),
        "p50": statistics.median(late) if late else float("nan"),
    }
    # drop in-window cold ramp: first sample with pct>=50 through end
    steady_pts = []
    seen_warm = False
    for ts, p, g in aligned:
        if not seen_warm and p >= 50.0:
            seen_warm = True
        if seen_warm:
            steady_pts.append(p)
    steady_stats = {
        "n": len(steady_pts),
        "avg": (sum(steady_pts) / len(steady_pts)) if steady_pts else float("nan"),
        **percentiles(steady_pts, [50, 90]),
        "note": "diagnostic: samples after first aligned point with ws>=50%; not a substitute for hard_pass",
    }
    return {
        "window": [a, b],
        "window_duration_s": b - a,
        "aligned": stats,
        "last60s_of_window": late_stats,
        "steady_after_ws50": steady_stats,
        "full_series": full_stats,
    }


def metrics_from_event(ev: dict) -> Dict[str, Any]:
    res = ev.get("resources") or {}
    stats = ev.get("resource_stats") or {}
    samples = ev.get("resource_samples") or {}
    mem_gib = float(res.get("memory") or 0.0)
    cpu_cores = float(res.get("cpu") or 0.0)
    io_mibs = float(res.get("io") or 0.0)
    return {
        "status": ev.get("status"),
        "db_qps": ev.get("db_qps"),
        "p95_ms": ev.get("p95_ms"),
        "p99_ms": ev.get("p99_ms"),
        "memory_gib": mem_gib,
        "memory_pct_official": 100.0 * mem_gib / CAP_GIB,
        "cpu_pct": 100.0 * cpu_cores / CPU_CORES,
        "io_mib_s": io_mibs,
        "memory_source": stats.get("memory_source"),
        "io_source": stats.get("io_source"),
        "samples_memory": samples.get("memory_bytes"),
        "samples_cpu": samples.get("cpu_cores"),
        "samples_io": samples.get("io_bps"),
        "memory_bytes_stats": stats.get("memory_bytes"),
        "label": ev.get("label"),
        "params": ev.get("params") or ev.get("effective_params"),
    }


def side_audit(
    name: str,
    side_dir: Path,
    event_names: Sequence[str],
    jsonl_name_candidates: Sequence[str],
) -> Dict[str, Any]:
    jsonl = None
    for n in jsonl_name_candidates:
        p = side_dir / n
        if p.exists():
            jsonl = p
            break
    ev = last_event(jsonl, event_names) if jsonl else None
    series = load_ws_series(side_dir / "ws_series.csv")
    if not ev:
        return {
            "side": name,
            "error": "missing_event",
            "jsonl": str(jsonl) if jsonl else None,
            "series_n": len(series),
        }
    window = extract_window(ev)
    metrics = metrics_from_event(ev)
    aligned = align_series(series, window)
    astats = aligned.get("aligned") or {}
    checks = {
        "status_ok": metrics.get("status") == "ok",
        "samples_ge_45": int(metrics.get("samples_memory") or 0) >= 45,
        "samples_cpu_ge_45": int(metrics.get("samples_cpu") or 0) >= 45,
        "samples_io_ge_45": int(metrics.get("samples_io") or 0) >= 45,
        "physical_blkio": str(metrics.get("io_source") or "") == "docker_cgroup_v1_blkio",
        "memory_working_set": "working_set" in str(metrics.get("memory_source") or ""),
        "cpu_lt_80": float(metrics.get("cpu_pct") or 0.0) < 80.0,
        "io_lt_75": float(metrics.get("io_mib_s") or 0.0) < 75.0,
        "aligned_n_ge_45": int(astats.get("n") or 0) >= 45,
        "aligned_mean_ge_75": float(astats.get("avg") or 0.0) >= 75.0,
        "aligned_p50_ge_75": float(astats.get("p50") or 0.0) >= 75.0,
        # official field is informational; NOT sufficient alone
        "official_mem_ge_75": float(metrics.get("memory_pct_official") or 0.0) >= 75.0,
    }
    side_ok = all(
        [
            checks["status_ok"],
            checks["samples_ge_45"],
            checks["samples_cpu_ge_45"],
            checks["samples_io_ge_45"],
            checks["physical_blkio"],
            checks["memory_working_set"],
            checks["cpu_lt_80"],
            checks["io_lt_75"],
            checks["aligned_n_ge_45"],
            checks["aligned_mean_ge_75"],
            checks["aligned_p50_ge_75"],
        ]
    )
    return {
        "side": name,
        "ok": side_ok,
        "jsonl": str(jsonl),
        "metrics": metrics,
        "series_audit": aligned,
        "checks": checks,
    }


def audit_floor_ab(
    root: Path,
    *,
    default_subdir: str = "default_baseline",
    cand_subdir: str = "spc2_rpc4",
    structure_same: Optional[bool] = None,
    explain: Optional[dict] = None,
) -> Dict[str, Any]:
    default = side_audit(
        "default",
        root / default_subdir,
        ("baseline_validation",),
        ("memory_binary_search_hybenchxp.jsonl",),
    )
    cand = side_audit(
        "candidate",
        root / cand_subdir,
        ("single_candidate_validation", "baseline_validation"),
        (
            "memory_single_validation_hybenchxp.jsonl",
            "memory_binary_search_hybenchxp.jsonl",
        ),
    )

    d_avg = float(((default.get("series_audit") or {}).get("aligned") or {}).get("avg") or float("nan"))
    c_avg = float(((cand.get("series_audit") or {}).get("aligned") or {}).get("avg") or float("nan"))
    delta = abs(d_avg - c_avg) if (d_avg == d_avg and c_avg == c_avg) else float("nan")

    if structure_same is None and explain is not None:
        structure_same = bool(explain.get("structure_same"))
    if structure_same is None:
        # try floor_ab_summary.json
        summary_path = root / "floor_ab_summary.json"
        if summary_path.exists():
            s = json.loads(summary_path.read_text(encoding="utf-8"))
            structure_same = bool((s.get("explain") or {}).get("structure_same", s.get("structure_same")))
            explain = s.get("explain") or explain

    joint = {
        "structure_same": structure_same,
        "both_sides_ok": bool(default.get("ok") and cand.get("ok")),
        "aligned_mean_delta_pp": delta,
        "aligned_mean_delta_le_3pp": (delta == delta and delta <= 3.0),
    }
    hard_pass = (
        structure_same is True
        and joint["both_sides_ok"]
        and joint["aligned_mean_delta_le_3pp"]
    )
    # Explicit: driver pass without this audit is not evidence
    driver_summary = None
    sp = root / "floor_ab_summary.json"
    if sp.exists():
        try:
            driver_summary = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            driver_summary = {"error": "unreadable_floor_ab_summary"}

    result = {
        "audit_type": "manual_sustained_window_floor_ab",
        "root": str(root),
        "cap_gib": CAP_GIB,
        "hard_pass": hard_pass,
        "classification": (
            "pass / sustained-floor-window-aligned"
            if hard_pass
            else "reject / sustained-floor-window-aligned-failed"
        ),
        "joint": joint,
        "default": default,
        "candidate": cand,
        "explain": explain,
        "driver_summary_pass": (driver_summary or {}).get("pass") if isinstance(driver_summary, dict) else None,
        "driver_summary_note": (
            "Do not trust driver pass alone; sampler starts before restore/warmup and "
            "hard_pass may omit series_avg_ge_75 / window alignment / p50."
        ),
        "failing_checks": [],
    }
    for side_name, side in (("default", default), ("candidate", cand)):
        for k, v in (side.get("checks") or {}).items():
            if v is False:
                result["failing_checks"].append(f"{side_name}.{k}")
    if structure_same is not True:
        result["failing_checks"].append("structure_same")
    if not joint["aligned_mean_delta_le_3pp"]:
        result["failing_checks"].append("aligned_mean_delta_le_3pp")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="Floor A/B run root directory")
    ap.add_argument("--default-subdir", default="default_baseline")
    ap.add_argument("--cand-subdir", default="spc2_rpc4")
    ap.add_argument(
        "--structure-same",
        default=None,
        choices=["true", "false"],
        help="Override plan structure_same if EXPLAIN not in summary",
    )
    ap.add_argument("--out", default=None, help="Audit JSON output path")
    args = ap.parse_args()
    root = Path(args.root)
    structure_same = None
    if args.structure_same == "true":
        structure_same = True
    elif args.structure_same == "false":
        structure_same = False
    result = audit_floor_ab(
        root,
        default_subdir=args.default_subdir,
        cand_subdir=args.cand_subdir,
        structure_same=structure_same,
    )
    out = Path(args.out) if args.out else root / "manual_sustained_window_audit.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "hard_pass": result["hard_pass"],
        "classification": result["classification"],
        "failing_checks": result["failing_checks"],
        "default_aligned_avg": ((result.get("default") or {}).get("series_audit") or {}).get("aligned"),
        "cand_aligned_avg": ((result.get("candidate") or {}).get("series_audit") or {}).get("aligned"),
        "out": str(out),
    }, indent=2), flush=True)
    return 0 if result["hard_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
