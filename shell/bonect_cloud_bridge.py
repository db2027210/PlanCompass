#!/usr/bin/env python3
"""File-based cloud bridge for Bonect main-agent supervision.

The bridge does not execute arbitrary remote commands. It exports compact
snapshots for a cloud agent to inspect, then ingests allowlisted command
requests as local files that the main agent/supervisor can read and act on.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix"
REPORT_ROOT = PROJECT_ROOT / "report"
CONFIG_ROOT = PROJECT_ROOT / "config" / "generated" / "pg_benchmark_bottleneck_matrix"
BRIDGE_ROOT = PROJECT_ROOT / "cloud_bridge" / "bonect"
SUPERVISOR_STATE = HISTORY_ROOT / "bonect_supervisor_state.json"

DEFAULT_TAIL_LINES = 120
DEFAULT_TAIL_BYTES = 250_000

ALLOWED_ACTIONS = {
    "analyze_only",
    "request_status",
    "request_stop",
    "request_restart",
    "request_config_change",
    "request_workload_change",
    "request_code_change",
    "request_subagent_analysis",
    "mark_unreachable",
}

REQUIRED_COMMAND_FIELDS = {
    "command_id",
    "run_id",
    "action",
    "summary",
    "rationale",
}


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def run_capture(args: Sequence[str], *, timeout: float = 8.0) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "args": list(args)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "args": list(args),
    }


def latest_by_mtime(paths: Iterable[Path], limit: int) -> List[Path]:
    existing = [path for path in paths if path.exists()]
    return sorted(existing, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def read_tail(path: Path, max_lines: int, max_bytes: int = DEFAULT_TAIL_BYTES) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            text = "".join(deque(fp, maxlen=max_lines))
    except OSError:
        return ""
    if max_bytes > 0 and len(text.encode("utf-8", errors="replace")) > max_bytes:
        encoded = text.encode("utf-8", errors="replace")[-max_bytes:]
        text = encoded.decode("utf-8", errors="replace")
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        text = "[tail truncated by bonect_cloud_bridge max_bytes]\n" + text
    return text


def copy_file(src: Path, dst: Path) -> Optional[str]:
    if not src.exists() or not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst.relative_to(PROJECT_ROOT))


def write_tail_copy(src: Path, dst: Path, max_lines: int) -> Optional[str]:
    if not src.exists() or not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(read_tail(src, max_lines), encoding="utf-8")
    return str(dst.relative_to(PROJECT_ROOT))


def write_text_copy(text: str, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return str(dst.relative_to(PROJECT_ROOT))


def detect_run_id(explicit: str = "") -> str:
    if explicit:
        return explicit
    state = load_json(SUPERVISOR_STATE)
    if state and state.get("run_id"):
        return str(state["run_id"])
    candidates: List[Path] = []
    if HISTORY_ROOT.is_dir():
        candidates.extend(path for path in HISTORY_ROOT.iterdir() if path.is_dir())
    if REPORT_ROOT.is_dir():
        candidates.extend(path for path in REPORT_ROOT.iterdir() if path.is_dir())
    latest = latest_by_mtime(candidates, 1)
    if latest:
        return latest[0].name
    raise SystemExit("run_id not provided and no active Bonect run was found")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def command_template(run_id: str) -> Dict[str, Any]:
    return {
        "command_id": f"cloud_{timestamp()}",
        "run_id": run_id,
        "created_at": iso_now(),
        "author": "cloud-agent",
        "action": "request_status",
        "priority": "normal",
        "summary": "Description agent  agent ",
        "rationale": "Description",
        "requested_changes": [
            " shell ",
        ],
        "evidence_paths": [],
        "requires_user_confirmation": False,
    }


def create_snapshot(run_id: str, *, tail_lines: int = DEFAULT_TAIL_LINES) -> Path:
    run_id = detect_run_id(run_id)
    stamp = timestamp()
    bridge_run_dir = BRIDGE_ROOT / run_id
    snapshot_dir = bridge_run_dir / "snapshots" / stamp
    current_dir = bridge_run_dir / "current"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    current_dir.mkdir(parents=True, exist_ok=True)

    history_dir = HISTORY_ROOT / run_id
    report_dir = REPORT_ROOT / run_id
    config_dir = CONFIG_ROOT / run_id

    copied: List[str] = []
    tails: List[str] = []

    for src, name in [
        (SUPERVISOR_STATE, "supervisor_state.json"),
        (history_dir / "bonect_reference_summary.md", "history/bonect_reference_summary.md"),
        (history_dir / "bonect_reference_summary.json", "history/bonect_reference_summary.json"),
        (report_dir / "agent_current_summary.md", "report/agent_current_summary.md"),
        (report_dir / "agent_current_summary.json", "report/agent_current_summary.json"),
        (report_dir / "README.md", "report/README.md"),
    ]:
        copied_path = copy_file(src, snapshot_dir / name)
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(src, current_dir / name)
        if current_path:
            copied.append(current_path)

    for path in latest_by_mtime(report_dir.glob("stage_summary_*.*") if report_dir.is_dir() else [], 8):
        copied_path = copy_file(path, snapshot_dir / "report" / path.name)
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(path, current_dir / "report" / path.name)
        if current_path:
            copied.append(current_path)

    for path in latest_by_mtime(history_dir.glob("control_decision_*.*") if history_dir.is_dir() else [], 6):
        copied_path = copy_file(path, snapshot_dir / "history" / path.name)
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(path, current_dir / "history" / path.name)
        if current_path:
            copied.append(current_path)

    for path in latest_by_mtime(history_dir.glob("*/*_resource_reports/round_*_resource_report.*") if history_dir.is_dir() else [], 8):
        dst_rel = Path("history") / path.parent.parent.name / path.parent.name / path.name
        copied_path = copy_file(path, snapshot_dir / dst_rel)
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(path, current_dir / dst_rel)
        if current_path:
            copied.append(current_path)

    for path in latest_by_mtime(history_dir.glob("*/*_optimizer_history*.jsonl") if history_dir.is_dir() else [], 4):
        dst_rel = Path("history_tails") / path.parent.name / f"{path.name}.tail"
        tail_path = write_tail_copy(path, snapshot_dir / dst_rel, tail_lines)
        if tail_path:
            tails.append(tail_path)
        current_tail = write_tail_copy(path, current_dir / dst_rel, tail_lines)
        if current_tail:
            tails.append(current_tail)

    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*/*.json")):
            copied_path = copy_file(path, snapshot_dir / "config" / path.parent.name / path.name)
            if copied_path:
                copied.append(copied_path)
            current_path = copy_file(path, current_dir / "config" / path.parent.name / path.name)
            if current_path:
                copied.append(current_path)

    for path in [
        PROJECT_ROOT / ".agents" / "index.md",
        PROJECT_ROOT / ".agents" / "bonect-main-goal.md",
        PROJECT_ROOT / ".agents" / "bonect-agent-orchestration.md",
        PROJECT_ROOT / ".agents" / "bonect-bruteforce-tuning-agent-prompt.md",
        PROJECT_ROOT / ".agents" / "bonect-result-reference.md",
        PROJECT_ROOT / ".agents" / "20260620T000821-large-loop-iteration-change-direction.md",
        PROJECT_ROOT / ".agents" / "bonect-cloud-control.md",
        PROJECT_ROOT / ".agents" / "bonect-cloud-agent-prompt.md",
    ]:
        copied_path = copy_file(path, snapshot_dir / ".agents" / path.name)
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(path, current_dir / ".agents" / path.name)
        if current_path:
            copied.append(current_path)

    diff_targets = [
        "config/optimizer_config.json",
        "parameter_optimizer.py",
        "plan_resource_estimator.py",
        "resource_feature_predictor.py",
        "shell/generate_chinese_bottleneck_reports.py",
        "shell/run_pg_benchmark_bottleneck_matrix.py",
    ]
    local_diff = run_capture(["git", "diff", "--", *diff_targets], timeout=20.0)
    if local_diff.get("stdout"):
        diff_text = str(local_diff["stdout"]) + "\n"
        copied.append(write_text_copy(diff_text, snapshot_dir / "local_code" / "git_uncommitted_diff.patch"))
        copied.append(write_text_copy(diff_text, current_dir / "local_code" / "git_uncommitted_diff.patch"))

    for path in [
        PROJECT_ROOT / "resource_model_retrainer.py",
        PROJECT_ROOT / "shell" / "prepare_bonect_reference.py",
    ]:
        copied_path = copy_file(path, snapshot_dir / "local_code" / path.relative_to(PROJECT_ROOT))
        if copied_path:
            copied.append(copied_path)
        current_path = copy_file(path, current_dir / "local_code" / path.relative_to(PROJECT_ROOT))
        if current_path:
            copied.append(current_path)

    diagnostics = {
        "git_rev_parse_head": run_capture(["git", "rev-parse", "HEAD"]),
        "git_status_short": run_capture(["git", "status", "--short"]),
        "git_diff_stat": run_capture(["git", "diff", "--stat"]),
        "tmux_ls": run_capture(["tmux", "ls"]),
        "pgrep_run_id": run_capture(["pgrep", "-af", run_id]),
    }

    template = command_template(run_id)
    write_json(snapshot_dir / "cloud_command_template.json", template)
    write_json(current_dir / "cloud_command_template.json", template)

    manifest = {
        "created_at": iso_now(),
        "run_id": run_id,
        "snapshot_dir": rel(snapshot_dir),
        "current_dir": rel(current_dir),
        "history_dir": rel(history_dir),
        "report_dir": rel(report_dir),
        "config_dir": rel(config_dir),
        "bridge_inbox": rel(bridge_run_dir / "inbox"),
        "bridge_processed": rel(bridge_run_dir / "processed"),
        "local_command_output": rel(report_dir / "cloud_agent_commands"),
        "tail_lines": tail_lines,
        "copied_files": sorted(set(copied)),
        "tail_files": sorted(set(tails)),
        "diagnostics": diagnostics,
        "cloud_agent_contract": {
            "cloud_reads": [
                rel(current_dir),
                rel(snapshot_dir),
            ],
            "cloud_writes_commands_to": rel(bridge_run_dir / "inbox"),
            "allowed_actions": sorted(ALLOWED_ACTIONS),
            "important": " agent ",
        },
    }
    write_json(snapshot_dir / "manifest.json", manifest)
    write_json(current_dir / "manifest.json", manifest)
    write_json(bridge_run_dir / "current_snapshot.json", manifest)
    write_json(BRIDGE_ROOT / "current_run.json", {"run_id": run_id, "snapshot": manifest})
    return snapshot_dir


def validate_command(command: Dict[str, Any], run_id: str) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    missing = sorted(field for field in REQUIRED_COMMAND_FIELDS if not command.get(field))
    if missing:
        errors.append(f"missing required fields: {missing}")
    if command.get("run_id") != run_id:
        errors.append(f"run_id mismatch: expected {run_id}, got {command.get('run_id')}")
    action = str(command.get("action") or "")
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {sorted(ALLOWED_ACTIONS)}, got {action!r}")
    if "shell" in command or "command" in command or "cmd" in command:
        errors.append("raw shell commands are not allowed in cloud bridge commands")
    changes = command.get("requested_changes", [])
    if changes is not None and not isinstance(changes, list):
        errors.append("requested_changes must be a list")
    return not errors, errors


def command_to_markdown(command: Dict[str, Any], source_path: Path) -> str:
    lines = [
        "# Cloud Agent Command",
        "",
        f"- command_id: `{command.get('command_id')}`",
        f"- run_id: `{command.get('run_id')}`",
        f"- action: `{command.get('action')}`",
        f"- priority: `{command.get('priority', 'normal')}`",
        f"- created_at: `{command.get('created_at', '')}`",
        f"- source_path: `{rel(source_path)}`",
        "",
        "## Summary",
        "",
        str(command.get("summary") or ""),
        "",
        "## Rationale",
        "",
        str(command.get("rationale") or ""),
        "",
        "## Requested Changes",
        "",
    ]
    changes = command.get("requested_changes") or []
    if isinstance(changes, list) and changes:
        lines.extend(f"- {item}" for item in changes)
    else:
        lines.append("- ")
    evidence = command.get("evidence_paths") or []
    lines.extend(["", "## Evidence Paths", ""])
    if isinstance(evidence, list) and evidence:
        lines.extend(f"- `{item}`" for item in evidence)
    else:
        lines.append("- ")
    lines.extend(
        [
            "",
            "## Local Execution Rule",
            "",
            " agent  `.agents/bonect-cloud-control.md` Configuration",
            "",
        ]
    )
    return "\n".join(lines)


def ingest_command(run_id: str, command_path: Path, *, move_processed: bool = False) -> Path:
    run_id = detect_run_id(run_id)
    command = load_json(command_path)
    if command is None:
        raise SystemExit(f"invalid JSON command file: {command_path}")
    ok, errors = validate_command(command, run_id)
    bridge_run_dir = BRIDGE_ROOT / run_id
    if not ok:
        reject = {
            "created_at": iso_now(),
            "source_path": rel(command_path),
            "status": "rejected",
            "errors": errors,
        }
        append_jsonl(bridge_run_dir / "command_log.jsonl", reject)
        raise SystemExit("; ".join(errors))

    command_id = str(command["command_id"])
    safe_command_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in command_id)
    report_dir = REPORT_ROOT / run_id / "cloud_agent_commands"
    json_dst = report_dir / f"{safe_command_id}.json"
    md_dst = report_dir / f"{safe_command_id}.md"
    write_json(json_dst, command)
    md_dst.write_text(command_to_markdown(command, command_path), encoding="utf-8")
    write_json(REPORT_ROOT / run_id / "latest_cloud_agent_command.json", command)
    append_jsonl(
        bridge_run_dir / "command_log.jsonl",
        {
            "created_at": iso_now(),
            "command_id": command_id,
            "action": command.get("action"),
            "source_path": rel(command_path),
            "status": "accepted",
            "local_json": rel(json_dst),
            "local_md": rel(md_dst),
        },
    )
    if move_processed:
        processed = bridge_run_dir / "processed" / command_path.name
        processed.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(command_path), str(processed))
    return md_dst


def poll(run_id: str, *, poll_sec: float, tail_lines: int, once: bool) -> int:
    run_id = detect_run_id(run_id)
    inbox = BRIDGE_ROOT / run_id / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    print(f"[bonect-cloud-bridge] polling run_id={run_id} inbox={rel(inbox)}", flush=True)
    while True:
        snapshot_path = create_snapshot(run_id, tail_lines=tail_lines)
        print(f"[bonect-cloud-bridge] snapshot {rel(snapshot_path)}", flush=True)
        for path in sorted(inbox.glob("*.json")):
            try:
                md_path = ingest_command(run_id, path, move_processed=True)
                print(f"[bonect-cloud-bridge] accepted command {rel(md_path)}", flush=True)
            except SystemExit as exc:
                print(f"[bonect-cloud-bridge] rejected {rel(path)}: {exc}", flush=True)
        if once:
            return 0
        time.sleep(max(5.0, poll_sec))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Bonect snapshots and ingest cloud-agent command requests.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    snapshot_parser = sub.add_parser("snapshot", help="Export a compact current-run snapshot.")
    snapshot_parser.add_argument("--run-id", default="")
    snapshot_parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES)

    apply_parser = sub.add_parser("apply-command", help="Validate and ingest one cloud-agent command JSON file.")
    apply_parser.add_argument("--run-id", default="")
    apply_parser.add_argument("--command-file", required=True)
    apply_parser.add_argument("--move-processed", action="store_true")

    poll_parser = sub.add_parser("poll", help="Continuously export snapshots and ingest inbox commands.")
    poll_parser.add_argument("--run-id", default="")
    poll_parser.add_argument("--poll-sec", type=float, default=60.0)
    poll_parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES)
    poll_parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.cmd == "snapshot":
        path = create_snapshot(args.run_id, tail_lines=args.tail_lines)
        print(rel(path), flush=True)
        return 0
    if args.cmd == "apply-command":
        path = ingest_command(args.run_id, Path(args.command_file), move_processed=args.move_processed)
        print(rel(path), flush=True)
        return 0
    if args.cmd == "poll":
        return poll(args.run_id, poll_sec=args.poll_sec, tail_lines=args.tail_lines, once=args.once)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
