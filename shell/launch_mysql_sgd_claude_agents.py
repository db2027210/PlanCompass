#!/usr/bin/env python3
"""Health-gated, duplicate-safe launcher for the five MySQL Claude agents."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "shell" / "claude_no_socks_proxy.sh"
PROMPT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v3_draft/claude_prompts"
)
LOCK_PATH = ROOT / "history/mysql_sgd_env_agents_20260723/claude_agent_launcher.lock"
MODEL = "deepseek-v4-pro[1m]"
HEALTH_PROMPT = " HEALTH_OK"
PRE_TUNING_GATE = ROOT / "config/mysql_sgd_pre_tuning_gate_v1.json"
SCENARIOS = {
    "job": "mysql_sgd_job_20260723",
    "xp": "mysql_sgd_xp_20260723",
    "tpch": "mysql_sgd_tpch_20260723",
    "tpcc": "mysql_sgd_tpcc_20260723",
}


class LaunchError(RuntimeError):
    pass


_POTENTIAL_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|password|passwd|token|secret)"
    r"\s*(?::|=|\s)\s*\S+|\bsk-(?:ant-)?[A-Za-z0-9_-]{8,}"
)


def _safe_failure_detail(stdout: str, stderr: str) -> str:
    """Return a bounded diagnostic only when it is safe for terminal output."""
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if not combined:
        return "unexpected health response"
    if any(
        unicodedata.category(character) == "Cc" and character != "\n"
        for character in combined
    ):
        return "health response withheld: control characters detected"
    if _POTENTIAL_SECRET.search(combined):
        return "health response withheld: potential secret detected"
    return combined[-500:]


def _pid_is_live(raw_pid: Any) -> bool:
    if isinstance(raw_pid, bool):
        return False
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def active_named_agents(payload: Any) -> set[str]:
    """Trust a session only when it is non-terminal and its OS PID is live."""
    active: set[str] = set()
    name_fields = ("name", "displayName", "display_name", "sessionName", "session_name")
    pid_fields = ("pid", "processId", "process_id", "processPid", "process_pid")
    for item in _objects(payload):
        name = next((item.get(key) for key in name_fields if isinstance(item.get(key), str)), None)
        if name not in SCENARIOS.values():
            continue
        state = str(item.get("state") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if state in {"failed", "completed", "stopped", "cancelled", "canceled"}:
            continue
        if status in {
            "failed", "completed", "stopped", "cancelled", "canceled", "idle"
        }:
            continue
        candidates = [item.get(key) for key in pid_fields]
        process = item.get("process")
        if isinstance(process, Mapping):
            candidates.extend(process.get(key) for key in pid_fields)
        if any(_pid_is_live(pid) for pid in candidates):
            active.add(str(name))
    return active


def _run(command: list[str], *, wrapper: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"
    ):
        environment.pop(key, None)
    return subprocess.run(
        command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )


def _prompt(name: str) -> tuple[Path, str, str]:
    path = PROMPT_ROOT / f"{name}.txt"
    if not path.is_file() or path.is_symlink():
        raise LaunchError(f"prompt is absent or non-regular: {path}")
    text = path.read_text(encoding="utf-8")
    required = (
        name,
        "frozen_mysql_deployment_schedule_20260723_v3_draft",
        "mysql_experience_sgd_eight_algorithm_contract_20260723.sha256",
        "mysql_five_benchmark_resource_envelope_and_search_algorithms_20260722.md",
        " 5 ",
        " host09" if name != "mysql_sgd_ap_20260723" else " host09",
        "readiness",
        "PG",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise LaunchError(f"prompt {path.name} lacks required clauses: {missing}")
    if not PRE_TUNING_GATE.is_file() or PRE_TUNING_GATE.is_symlink():
        raise LaunchError(f"pre-tuning hard gate is absent or non-regular: {PRE_TUNING_GATE}")
    gate = PRE_TUNING_GATE.read_text(encoding="utf-8")
    text += (
        "\n\n "
        "config/mysql_sgd_pre_tuning_gate_v1.jsonR0 and SGD require a passed bottleneck check"
        "Machine resource envelope config JSON "
        " SHA256  configured/effective "
        "CPUcpusetNUMAmemoryswapI/Opids/benchmark "
        " 180  controller hard_gate  readiness "
        " sealed launch request controller\n"
        f"\n{gate}"
    )
    return path, text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _selected(values: list[str] | None) -> list[tuple[str, str]]:
    keys = list(SCENARIOS) if not values else values
    if len(keys) != len(set(keys)):
        raise LaunchError("duplicate --only scenario")
    return [(key, SCENARIOS[key]) for key in keys]


def launch(*, wrapper: Path, selected: list[tuple[str, str]], dry_run: bool) -> int:
    prompts = {name: _prompt(name) for _, name in selected}
    if dry_run:
        print("DRY_RUN: no Claude command was executed")
        print("WOULD_REQUIRE_HEALTH=HEALTH_OK")
        print("WOULD_QUERY=agents --json")
        for _, name in selected:
            path, _, digest = prompts[name]
            print(f"WOULD_LAUNCH name={name} prompt={path.relative_to(ROOT)} sha256={digest}")
        return 0

    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise LaunchError(f"Claude wrapper is absent or not executable: {wrapper}")
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LaunchError("another MySQL Claude launcher holds the local lock") from error

        # Health is deliberately the first Claude invocation. Any failure means
        # agents/list and all background launches remain untouched.
        health = _run(
            [str(wrapper), "-p", "--model", MODEL, "--effort", "low",
             "--no-session-persistence", HEALTH_PROMPT],
            wrapper=wrapper,
        )
        if health.returncode != 0 or health.stdout.strip() != "HEALTH_OK":
            detail = _safe_failure_detail(health.stdout, health.stderr)
            raise LaunchError(f"Claude HEALTH_OK failed; zero agents launched: {detail}")
        print("HEALTH_OK")

        listing = _run([str(wrapper), "agents", "--json"], wrapper=wrapper)
        if listing.returncode != 0:
            raise LaunchError("Claude agents --json failed; zero agents launched")
        try:
            payload = json.loads(listing.stdout)
        except json.JSONDecodeError as error:
            raise LaunchError("Claude agents --json returned invalid JSON; zero agents launched") from error
        active = active_named_agents(payload)

        failed = False
        launched_names: list[str] = []
        for _, name in selected:
            if name in active:
                print(f"ACTIVE_SKIP name={name} reason=live_pid")
                continue
            path, prompt, digest = prompts[name]
            command = [
                str(wrapper), "--bg", "--dangerously-skip-permissions",
                "--model", MODEL, "--effort", "max", "--name", name, prompt,
            ]
            result = _run(command, wrapper=wrapper)
            if result.returncode != 0:
                failed = True
                print(f"LAUNCH_FAILED name={name} rc={result.returncode}", file=sys.stderr)
                continue
            launched_names.append(name)
            print(f"LAUNCH_ACCEPTED name={name} awaiting_live_pid_verification")

        if launched_names:
            verification = _run([str(wrapper), "agents", "--json"], wrapper=wrapper)
            if verification.returncode != 0:
                raise LaunchError("post-launch agents --json failed; launches are unverified")
            try:
                verification_payload = json.loads(verification.stdout)
            except json.JSONDecodeError as error:
                raise LaunchError(
                    "post-launch agents --json returned invalid JSON; launches are unverified"
                ) from error
            verified_active = active_named_agents(verification_payload)
            missing_live = [name for name in launched_names if name not in verified_active]
            if missing_live:
                print(
                    "LAUNCH_VERIFICATION_FAILED missing_live_pid=" + ",".join(missing_live),
                    file=sys.stderr,
                )
                failed = True
            for name in launched_names:
                if name not in verified_active:
                    continue
                path, _, digest = prompts[name]
                print(
                    f"LAUNCHED name={name} prompt={path.relative_to(ROOT)} "
                    f"sha256={digest} verified=live_pid"
                )
        return 1 if failed else 0
    finally:
        os.close(lock_descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate and print; invoke no Claude command")
    parser.add_argument("--only", action="append", choices=tuple(SCENARIOS), help="launch only one scenario; repeatable")
    parser.add_argument("--wrapper", type=Path, default=WRAPPER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        return launch(wrapper=args.wrapper.resolve(), selected=_selected(args.only), dry_run=args.dry_run)
    except LaunchError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
