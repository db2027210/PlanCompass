from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch running IO binary-search logs and trigger handoff/final postprocess.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--final-report", required=True)
    parser.add_argument("--cpu-replay-report", required=True)
    parser.add_argument("--none-replay-report", required=True)
    parser.add_argument("--io-replay-report", required=True)
    parser.add_argument("--status-md", required=True)
    parser.add_argument("--status-json", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    return parser.parse_args()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def find_latest(rows: List[Dict[str, Any]], event: str) -> Optional[Dict[str, Any]]:
    for row in reversed(rows):
        if row.get("event") == event:
            return row
    return None


def run_cmd(cmd: List[str], *, cwd: Path) -> None:
    print(f"[watcher] run: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def write_status(
    *,
    status_md: Path,
    status_json: Path,
    out_dir: Path,
    rows: List[Dict[str, Any]],
    latest_round_summary: Optional[Dict[str, Any]],
    stage_summary: Optional[Dict[str, Any]],
) -> None:
    last_row = rows[-1] if rows else {}
    round_counts: Dict[int, int] = {}
    for row in rows:
        if row.get("event") == "candidate_validation":
            round_index = int(row.get("round") or 0)
            round_counts[round_index] = round_counts.get(round_index, 0) + 1

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "out_dir": str(out_dir),
        "last_event": last_row.get("event"),
        "last_round": last_row.get("round"),
        "last_timestamp": last_row.get("timestamp"),
        "round_candidate_counts": round_counts,
        "latest_round_summary": latest_round_summary,
        "stage_summary": stage_summary,
    }
    status_json.parent.mkdir(parents=True, exist_ok=True)
    status_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: List[str] = []
    lines.append("# IO Watcher Status")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- Output dir: `{out_dir}`")
    lines.append(f"- Last event: `{payload['last_event']}`")
    lines.append(f"- Last round: `{payload['last_round']}`")
    lines.append(f"- Last timestamp: `{payload['last_timestamp']}`")
    if round_counts:
        lines.append("- Round candidate counts: " + ", ".join(
            f"`r{key}`={value}" for key, value in sorted(round_counts.items())
        ))
    if latest_round_summary:
        lines.append("")
        lines.append("## Latest Round Summary")
        lines.append("")
        lines.append(f"- Round: `{latest_round_summary.get('round')}`")
        lines.append(f"- Timestamp: `{latest_round_summary.get('timestamp')}`")
        lines.append(f"- Accepted count: `{latest_round_summary.get('accepted_count')}`")
        rejects = latest_round_summary.get("reject_reasons") or {}
        if rejects:
            lines.append(
                "- Reject reasons: " + ", ".join(
                    f"`{key}`={value}" for key, value in sorted(rejects.items())
                )
            )
    if stage_summary:
        lines.append("")
        lines.append("## Stage Summary")
        lines.append("")
        lines.append(f"- Stop reason: `{stage_summary.get('stop_reason')}`")
        lines.append(f"- Completed rounds: `{stage_summary.get('completed_round_count')}`")
    status_md.parent.mkdir(parents=True, exist_ok=True)
    status_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_live_snapshot(
    *,
    report_dir: Path,
    out_dir: Path,
    rows: List[Dict[str, Any]],
    latest_round_summary: Optional[Dict[str, Any]],
    stage_summary: Optional[Dict[str, Any]],
) -> None:
    last_row = rows[-1] if rows else {}
    latest_candidate = find_latest(rows, "candidate_validation")
    round_counts: Dict[int, int] = {}
    for row in rows:
        if row.get("event") == "candidate_validation":
            round_index = int(row.get("round") or 0)
            round_counts[round_index] = round_counts.get(round_index, 0) + 1

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = f"binary_plan_resource_tuning_hybenchxp_io_live_status_{timestamp}"
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "out_dir": str(out_dir),
        "last_event": last_row.get("event"),
        "last_round": last_row.get("round"),
        "last_timestamp": last_row.get("timestamp"),
        "round_candidate_counts": round_counts,
        "latest_candidate": latest_candidate,
        "latest_round_summary": latest_round_summary,
        "stage_summary": stage_summary,
    }
    lines: List[str] = []
    lines.append("# IO Live Status")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- Output dir: `{out_dir}`")
    lines.append(f"- Last event: `{payload['last_event']}`")
    lines.append(f"- Last round: `{payload['last_round']}`")
    lines.append(f"- Last timestamp: `{payload['last_timestamp']}`")
    if round_counts:
        lines.append("- Round candidate counts: " + ", ".join(
            f"`r{key}`={value}" for key, value in sorted(round_counts.items())
        ))
    if latest_candidate:
        lines.append("")
        lines.append("## Latest Candidate")
        lines.append("")
        lines.append(f"- Label: `{latest_candidate.get('label')}`")
        lines.append(f"- Accepted: `{latest_candidate.get('accepted')}`")
        lines.append(f"- Reject reason: `{latest_candidate.get('accept_reject_reason') or latest_candidate.get('reject_reason')}`")
        lines.append(f"- DB-QPS: `{latest_candidate.get('db_qps')}`")
        resources = latest_candidate.get("resources") or {}
        if resources:
            lines.append(
                "- Resources: " + ", ".join(
                    f"`{key}`={value}" for key, value in sorted(resources.items())
                )
            )
    if latest_round_summary:
        lines.append("")
        lines.append("## Latest Round Summary")
        lines.append("")
        lines.append(f"- Round: `{latest_round_summary.get('round')}`")
        lines.append(f"- Accepted count: `{latest_round_summary.get('accepted_count')}`")
        lines.append(f"- Validation count: `{latest_round_summary.get('validation_count')}`")
    if stage_summary:
        lines.append("")
        lines.append("## Stage Summary")
        lines.append("")
        lines.append(f"- Stop reason: `{stage_summary.get('stop_reason')}`")
        lines.append(f"- Completed rounds: `{stage_summary.get('completed_round_count')}`")

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{prefix}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / f"{prefix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_round_complete_report(
    *,
    report_dir: Path,
    out_dir: Path,
    rows: List[Dict[str, Any]],
    round_summary: Dict[str, Any],
) -> None:
    round_index = int(round_summary.get("round") or 0)
    if round_index <= 0:
        return

    ts_raw = str(round_summary.get("timestamp") or "").strip()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if ts_raw:
        try:
            timestamp = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
        except ValueError:
            pass
    prefix = f"binary_plan_resource_tuning_hybenchxp_io_round{round_index}_complete_{timestamp}"

    candidate_rows = [
        row
        for row in rows
        if row.get("event") == "candidate_validation" and int(row.get("round") or 0) == round_index
    ]
    tail_candidates = candidate_rows[-6:]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "out_dir": str(out_dir),
        "round_summary": round_summary,
        "tail_candidates": [
            {
                "label": row.get("label"),
                "timestamp": row.get("timestamp"),
                "reject_reason": row.get("accept_reject_reason") or row.get("reject_reason"),
                "db_qps": row.get("db_qps"),
                "io": (row.get("resources") or {}).get("io"),
            }
            for row in tail_candidates
        ],
    }

    lines: List[str] = []
    lines.append(f"## HyBench XP IO Round {round_index} Snapshot")
    lines.append("")
    lines.append(f"{payload['generated_at']}")
    lines.append("")
    lines.append("Run dir")
    lines.append(f"`{out_dir}`")
    lines.append("")
    lines.append("Workload`hybenchxp`")
    lines.append("")
    lines.append(f"## Round {round_index} ")
    lines.append("")
    lines.append(f"- round {round_index} `round_summary` `{round_summary.get('timestamp')}`")
    lines.append(f"- accepted_count`{round_summary.get('accepted_count')}`")
    reject_reasons = round_summary.get("reject_reasons") or {}
    if reject_reasons:
        lines.append("- reject_reasons")
        for key, value in sorted(reject_reasons.items()):
            lines.append(f"  - `{key}={value}`")
    lines.append(f"- validation_count`{round_summary.get('validation_count')}`")
    lines.append(f"- validation_parallelism`{round_summary.get('validation_parallelism')}`")
    lines.append(f"- validation_batch_count`{round_summary.get('validation_batch_count')}`")
    lines.append(f"- round_elapsed_s`{round_summary.get('round_elapsed_s')}`")
    lines.append(f"- validation_elapsed_s`{round_summary.get('validation_elapsed_s')}`")
    lines.append(f"- plan_probe_parallelism`{round_summary.get('plan_probe_parallelism')}`")
    lines.append(f"- full_round_search_points`{round_summary.get('full_round_search_points')}`")
    lines.append(f"- replay topk limit`{round_summary.get('topk_limit')}`")
    lines.append("")
    lines.append(f"## Round {round_index} ")
    lines.append("")
    lines.append("| label | timestamp | reject_reason | IO MB/s | DB-QPS |")
    lines.append("| --- | --- | --- | ---: | ---: |")
    for row in tail_candidates:
        lines.append(
            "| `{label}` | `{timestamp}` | `{reason}` | `{io}` | `{db_qps}` |".format(
                label=row.get("label"),
                timestamp=row.get("timestamp"),
                reason=row.get("accept_reject_reason") or row.get("reject_reason"),
                io=(row.get("resources") or {}).get("io"),
                db_qps=row.get("db_qps"),
            )
        )
    next_round = round_index + 1
    lines.append("")
    lines.append(f"## Round {next_round} ")
    lines.append("")
    latest_row = rows[-1] if rows else {}
    lines.append(f"- `{latest_row.get('event')}`")
    lines.append(f"-  round`{latest_row.get('round')}`")
    lines.append(f"- `{latest_row.get('timestamp')}`")

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{prefix}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / f"{prefix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.output_dir).resolve()
    report_dir = Path(args.report_dir).resolve()
    status_md = Path(args.status_md).resolve()
    status_json = Path(args.status_json).resolve()
    final_report = Path(args.final_report).resolve()
    cpu_replay_report = Path(args.cpu_replay_report).resolve()
    none_replay_report = Path(args.none_replay_report).resolve()
    io_replay_report = Path(args.io_replay_report).resolve()

    log_path = out_dir / "io_binary_search_hybenchxp.jsonl"
    if not log_path.exists():
        raise FileNotFoundError(str(log_path))

    last_round_summary_seen = -1
    last_progress_signature: Optional[str] = None
    final_postprocess_done = False

    run_cmd(
        [
            args.python_bin,
            "binary_plan_resource_tuner.py",
            "--config",
            args.config,
            "--output-dir",
            str(out_dir),
            "handoff-snapshots",
            "--target",
            "io",
            "--force",
        ],
        cwd=repo_root,
    )
    rows = load_jsonl(log_path)
    write_live_snapshot(
        report_dir=report_dir,
        out_dir=out_dir,
        rows=rows,
        latest_round_summary=find_latest(rows, "round_summary"),
        stage_summary=find_latest(rows, "stage_summary"),
    )

    while True:
        rows = load_jsonl(log_path)
        latest_round_summary = find_latest(rows, "round_summary")
        latest_candidate = find_latest(rows, "candidate_validation")
        stage_summary = find_latest(rows, "stage_summary")
        write_status(
            status_md=status_md,
            status_json=status_json,
            out_dir=out_dir,
            rows=rows,
            latest_round_summary=latest_round_summary,
            stage_summary=stage_summary,
        )

        if latest_candidate:
            current_round = int(latest_candidate.get("round") or 0)
            current_count = sum(
                1
                for row in rows
                if row.get("event") == "candidate_validation" and int(row.get("round") or 0) == current_round
            )
            progress_signature = f"r{current_round}:c{current_count}:ts={latest_candidate.get('timestamp')}"
            if progress_signature != last_progress_signature:
                write_live_snapshot(
                    report_dir=report_dir,
                    out_dir=out_dir,
                    rows=rows,
                    latest_round_summary=latest_round_summary,
                    stage_summary=stage_summary,
                )
                last_progress_signature = progress_signature

        current_round_summary = int(latest_round_summary.get("round") or -1) if latest_round_summary else -1
        if current_round_summary > last_round_summary_seen:
            write_round_complete_report(
                report_dir=report_dir,
                out_dir=out_dir,
                rows=rows,
                round_summary=latest_round_summary,
            )
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "handoff-snapshots",
                    "--target",
                    "io",
                    "--force",
                ],
                cwd=repo_root,
            )
            last_round_summary_seen = current_round_summary

        if stage_summary and not final_postprocess_done:
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "finalize-stage",
                    "--target",
                    "io",
                    "--reason",
                    "auto_finalize_after_stage_summary",
                    "--force-append",
                ],
                cwd=repo_root,
            )
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "replay-best",
                    "--source-stage",
                    "cpu",
                    "--target",
                    "cpu",
                    "--repo-report",
                    str(cpu_replay_report),
                ],
                cwd=repo_root,
            )
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "replay-best",
                    "--source-stage",
                    "cpu",
                    "--target",
                    "none",
                    "--repo-report",
                    str(none_replay_report),
                ],
                cwd=repo_root,
            )
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "replay-best",
                    "--source-stage",
                    "io",
                    "--target",
                    "io",
                    "--repo-report",
                    str(io_replay_report),
                ],
                cwd=repo_root,
            )
            run_cmd(
                [
                    args.python_bin,
                    "binary_plan_resource_tuner.py",
                    "--config",
                    args.config,
                    "--output-dir",
                    str(out_dir),
                    "report",
                    "--repo-report",
                    str(final_report),
                ],
                cwd=repo_root,
            )
            rows = load_jsonl(log_path)
            latest_round_summary = find_latest(rows, "round_summary")
            stage_summary = find_latest(rows, "stage_summary")
            write_status(
                status_md=status_md,
                status_json=status_json,
                out_dir=out_dir,
                rows=rows,
                latest_round_summary=latest_round_summary,
                stage_summary=stage_summary,
            )
            final_postprocess_done = True
            print("[watcher] final postprocess completed", flush=True)
            return 0

        time.sleep(max(5, int(args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
