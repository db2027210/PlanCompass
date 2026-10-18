#!/usr/bin/env python3
"""Continuously materialize crash-safe best-so-far artifacts for VM searches."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List, Optional


TARGETS = ("cpu", "memory", "io", "none")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                # An append may be visible between write syscalls. The next scan
                # will pick up the completed line.
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def accepted_key(row: Dict[str, Any], target: str) -> tuple:
    decision = row.get("decision") or {}
    resources = row.get("resources") or {}
    explanation_ok = bool(decision.get("resource_explanation_ok"))
    common = (
        number(row.get("db_qps")),
        1 if explanation_ok else 0,
        number(decision.get("best_other_up_delta_pct")),
    )
    if target == "none":
        return (*common, -number(row.get("p95_ms")), -number(row.get("p99_ms")))
    return (
        *common,
        -number(resources.get(target), float("inf")),
        -number(row.get("p95_ms"), float("inf")),
        -number(row.get("p99_ms"), float("inf")),
    )


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def latest(rows: List[Dict[str, Any]], event: str) -> Optional[Dict[str, Any]]:
    for row in reversed(rows):
        if row.get("event") == event:
            return row
    return None


def build_payload(run_root: Path, target: str) -> Dict[str, Any]:
    target_dir = run_root / target
    log_path = target_dir / f"{target}_binary_search_hybenchxp.jsonl"
    raw_path = target_dir / f"{target}_vm_replay_raw.jsonl"
    rows = load_jsonl(log_path)
    candidates = [row for row in rows if row.get("event") == "candidate_validation"]
    accepted = [
        row
        for row in candidates
        if row.get("accepted") is True and str(row.get("status") or "").lower() == "ok"
    ]
    successful = [row for row in candidates if str(row.get("status") or "").lower() == "ok"]
    best_accepted = max(accepted, key=lambda row: accepted_key(row, target)) if accepted else None
    best_observed_qps = max(successful, key=lambda row: number(row.get("db_qps"))) if successful else None
    baseline = latest(rows, "baseline_validation")
    round_summaries = [row for row in rows if row.get("event") == "round_summary"]
    round_starts = [row for row in rows if row.get("event") == "round_start"]
    stage_start = latest(rows, "stage_start")
    final_path = target_dir / f"final_for_{target}.json"
    final_payload: Optional[Dict[str, Any]] = None
    if final_path.is_file():
        try:
            value = json.loads(final_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                final_payload = value
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "artifact": "binary_search_best_so_far",
        "target": target,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "run_root": str(run_root),
        "source_log": str(log_path),
        "source_raw_log": str(raw_path),
        "configured_max_rounds": (stage_start or {}).get("max_rounds"),
        "required_rounds_for_epsilon": (stage_start or {}).get("required_rounds_for_epsilon"),
        "completed_round_count": len(round_summaries),
        "active_round": max(
            (int(row.get("round") or 0) for row in round_starts),
            default=0,
        ),
        "latest_completed_round": max(
            (int(row.get("round") or 0) for row in round_summaries),
            default=0,
        ),
        "candidate_validation_count": len(candidates),
        "successful_candidate_count": len(successful),
        "failed_candidate_count": len(candidates) - len(successful),
        "accepted_candidate_count": len(accepted),
        "baseline": baseline,
        "latest_candidate": candidates[-1] if candidates else None,
        "best_accepted_point": best_accepted,
        "best_observed_qps_point": best_observed_qps,
        "converged": bool((final_payload or {}).get("converged")),
        "stop_reason": (final_payload or {}).get("stop_reason"),
        "final_artifact": str(final_path) if final_payload is not None else None,
    }


def markdown_summary(run_root: Path, payloads: List[Dict[str, Any]]) -> str:
    updated_at = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    guard_payload: Dict[str, Any] = {}
    try:
        value = json.loads((run_root / "host_disk_isolation_status.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            guard_payload = value
    except (OSError, json.JSONDecodeError):
        pass
    invalidation_markers = sorted(path.name for path in run_root.glob("INVALIDATED*.md"))
    lines = [
        "# Four-VM binary-search live status",
        "",
        f"- Updated: `{updated_at}`",
        f"- Run root: `{run_root}`",
        "- Acceptance: bottleneck resource drop `5%–10%` and DB QPS gain `>=15%`.",
        "- None has no designated bottleneck resource, so only the DB QPS gain gate applies there.",
        "- Terminal condition: all parameter ranges satisfy epsilon; hitting a target point does not stop the run.",
        "- The round-count denominator is a launch-time estimate, not a required or exact stopping round.",
        f"- Host disk guard: `{guard_payload.get('status') or 'missing'}` "
        f"(updated `{guard_payload.get('updated_at') or '—'}`, "
        f"conflicts `{len(guard_payload.get('conflicts') or [])}`).",
        f"- Invalidation markers: `{len(invalidation_markers)}`.",
        "",
        "| Target | Active round | Completed/launch estimate | Candidates | OK | Failed | Accepted | "
        "Best accepted | Best DB QPS | QPS gain | Converged |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for payload in payloads:
        best = payload.get("best_accepted_point") or {}
        decision = best.get("decision") or {}
        required = payload.get("required_rounds_for_epsilon") or "?"
        best_label = str(best.get("label") or "—").replace("|", "\\|")
        best_qps = f"{number(best.get('db_qps')):.2f}" if best else "—"
        qps_gain = (
            f"{number(decision.get('db_qps_delta_pct')):.2f}%"
            if best and decision.get("db_qps_delta_pct") is not None
            else "—"
        )
        lines.append(
            "| {target} | {active_round} | {rounds}/{required} | {candidates} | {ok} | {failed} | "
            "{accepted} | {best_label} | {best_qps} | {qps_gain} | {converged} |".format(
                target=payload.get("target"),
                active_round=payload.get("active_round") or "—",
                rounds=payload.get("completed_round_count"),
                required=required,
                candidates=payload.get("candidate_validation_count"),
                ok=payload.get("successful_candidate_count"),
                failed=payload.get("failed_candidate_count"),
                accepted=payload.get("accepted_candidate_count"),
                best_label=best_label,
                best_qps=best_qps,
                qps_gain=qps_gain,
                converged="yes" if payload.get("converged") else "no",
            )
        )
    lines.extend(
        [
            "",
            "The full candidate history remains authoritative in each target's `*_binary_search_hybenchxp.jsonl` file.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    while True:
        all_converged = True
        payloads: List[Dict[str, Any]] = []
        for target in TARGETS:
            payload = build_payload(run_root, target)
            payloads.append(payload)
            atomic_write_json(run_root / target / f"best_so_far_for_{target}.json", payload)
            all_converged = all_converged and bool(payload.get("converged"))
        atomic_write_text(run_root / "live_search_summary.md", markdown_summary(run_root, payloads))
        if args.once:
            return 0
        if all_converged:
            return 0
        time.sleep(max(1.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
