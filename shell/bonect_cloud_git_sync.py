#!/usr/bin/env python3
"""Periodic Git sync loop for the Bonect Codex Cloud bridge.

This script automates the file-mailbox part of the cloud workflow:

1. Pull cloud-agent inbox JSON files from a Git branch.
2. Ingest allowlisted inbox commands into local report files.
3. Export a fresh compact snapshot.
4. Commit and push only the cloud bridge whitelist.

It intentionally does not execute experiment control actions. The local main
agent remains responsible for deciding whether to act on ingested commands.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = PROJECT_ROOT / "cloud_bridge" / "bonect"
BRIDGE_SCRIPT = PROJECT_ROOT / "shell" / "bonect_cloud_bridge.py"


class CommandError(RuntimeError):
    """Raised when a subprocess command fails."""


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def run(
    args: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    dry_run: bool = False,
    mutates: bool = False,
) -> subprocess.CompletedProcess[str]:
    if dry_run and mutates:
        print(f"[bonect-cloud-git-sync] dry-run skip: {' '.join(args)}", flush=True)
        return subprocess.CompletedProcess(list(args), 0, "", "")
    proc = subprocess.run(
        list(args),
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and proc.returncode != 0:
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        detail = "\n".join(part for part in [stdout, stderr] if part)
        raise CommandError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
    return proc


def git_output(args: Sequence[str]) -> str:
    proc = run(["git", *args])
    return (proc.stdout or "").strip()


def current_branch() -> str:
    return git_output(["rev-parse", "--abbrev-ref", "HEAD"])


def detect_run_id(explicit: str = "") -> str:
    if explicit:
        return explicit
    current = BRIDGE_ROOT / "current_run.json"
    if current.exists():
        try:
            data = json.loads(current.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        run_id = data.get("run_id")
        if run_id:
            return str(run_id)
    raise SystemExit("run_id is required because cloud_bridge/bonect/current_run.json is missing or invalid")


def staged_paths() -> List[str]:
    proc = run(["git", "diff", "--cached", "--name-only"])
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def allowed_stage_path(path: str, run_id: str) -> bool:
    allowed_exact = {
        "cloud_bridge/bonect/current_run.json",
    }
    allowed_prefixes = [
        f"cloud_bridge/bonect/{run_id}/current/",
        f"cloud_bridge/bonect/{run_id}/inbox/",
    ]
    return path in allowed_exact or any(path.startswith(prefix) for prefix in allowed_prefixes)


def assert_no_foreign_staged_changes(run_id: str) -> None:
    foreign = [path for path in staged_paths() if not allowed_stage_path(path, run_id)]
    if foreign:
        raise SystemExit(
            "refusing to continue because unrelated files are already staged:\n"
            + "\n".join(f"  - {path}" for path in foreign)
        )


def pull_remote(remote: str, branch: str, *, autostash: bool, dry_run: bool) -> None:
    run(["git", "fetch", remote, branch], dry_run=dry_run, mutates=True)
    cmd = ["git", "pull", "--rebase"]
    if autostash:
        cmd.append("--autostash")
    cmd.extend([remote, branch])
    run(cmd, dry_run=dry_run, mutates=True)


def apply_inbox(run_id: str, *, dry_run: bool) -> int:
    inbox = BRIDGE_ROOT / run_id / "inbox"
    if not inbox.is_dir():
        return 0
    count = 0
    for path in sorted(inbox.glob("*.json")):
        count += 1
        run(
            [
                sys.executable,
                str(BRIDGE_SCRIPT),
                "apply-command",
                "--run-id",
                run_id,
                "--command-file",
                str(path),
                "--move-processed",
            ],
            dry_run=dry_run,
            mutates=True,
        )
    return count


def create_snapshot(run_id: str, tail_lines: int, *, dry_run: bool) -> None:
    run(
        [
            sys.executable,
            str(BRIDGE_SCRIPT),
            "snapshot",
            "--run-id",
            run_id,
            "--tail-lines",
            str(tail_lines),
        ],
        dry_run=dry_run,
        mutates=True,
    )


def stage_whitelist(run_id: str, *, dry_run: bool) -> None:
    paths = [
        "cloud_bridge/bonect/current_run.json",
        f"cloud_bridge/bonect/{run_id}/current",
    ]
    run(["git", "add", *paths], dry_run=dry_run, mutates=True)
    inbox = f"cloud_bridge/bonect/{run_id}/inbox"
    tracked_inbox = git_output(["ls-files", "--", inbox])
    if tracked_inbox:
        run(["git", "add", "-u", inbox], dry_run=dry_run, mutates=True)


def has_staged_changes(run_id: str) -> bool:
    proc = run(
        [
            "git",
            "diff",
            "--cached",
            "--quiet",
            "--",
            "cloud_bridge/bonect/current_run.json",
            f"cloud_bridge/bonect/{run_id}/current",
            f"cloud_bridge/bonect/{run_id}/inbox",
        ],
        check=False,
    )
    return proc.returncode == 1


def commit_and_push(
    run_id: str,
    *,
    remote: str,
    branch: str,
    push: bool,
    dry_run: bool,
) -> bool:
    if not has_staged_changes(run_id):
        print("[bonect-cloud-git-sync] no staged bridge changes", flush=True)
        return False
    subject = f"Sync Bonect cloud snapshot {run_id} {stamp()}"
    run(["git", "commit", "-m", subject], dry_run=dry_run, mutates=True)
    if push:
        run(["git", "push", remote, f"HEAD:{branch}"], dry_run=dry_run, mutates=True)
    return True


def sync_once(args: argparse.Namespace) -> None:
    branch = args.branch or current_branch()
    run_id = detect_run_id(args.run_id)
    print(
        f"[bonect-cloud-git-sync] {iso_now()} run_id={run_id} branch={branch}",
        flush=True,
    )

    assert_no_foreign_staged_changes(run_id)
    if not args.no_pull:
        pull_remote(args.remote, branch, autostash=not args.no_autostash, dry_run=args.dry_run)

    run_id = detect_run_id(args.run_id)
    assert_no_foreign_staged_changes(run_id)
    ingested = 0 if args.no_apply_inbox else apply_inbox(run_id, dry_run=args.dry_run)
    if ingested:
        print(f"[bonect-cloud-git-sync] ingested {ingested} cloud command(s)", flush=True)

    create_snapshot(run_id, args.tail_lines, dry_run=args.dry_run)
    stage_whitelist(run_id, dry_run=args.dry_run)
    assert_no_foreign_staged_changes(run_id)
    committed = commit_and_push(
        run_id,
        remote=args.remote,
        branch=branch,
        push=not args.no_push,
        dry_run=args.dry_run,
    )
    if committed:
        print("[bonect-cloud-git-sync] committed bridge sync", flush=True)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Periodically sync Bonect cloud bridge snapshots through Git.")
    parser.add_argument("--run-id", default="", help="Bonect run id. Defaults to cloud_bridge/bonect/current_run.json.")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="", help="Git branch to sync. Defaults to current branch.")
    parser.add_argument("--interval-sec", type=float, default=300.0)
    parser.add_argument("--tail-lines", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-autostash", action="store_true")
    parser.add_argument("--no-apply-inbox", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    while True:
        try:
            sync_once(args)
        except CommandError as exc:
            print(f"[bonect-cloud-git-sync] ERROR: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(30.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
