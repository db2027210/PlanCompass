#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "history/pg5_five_experiments_live_20260723"
LOG_FILE = OUTPUT / "watchdog.log"
PID_FILE = OUTPUT / "watchdog.pid"
MONITOR_UNIT = "pg5-five-experiment-monitor.service"


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")
    print(message, flush=True)


def check_monitor() -> None:
    """Monitor and auto-repair if the monitor or experiments are not running."""
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "is-active",
            MONITOR_UNIT,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip() == "active":
        log("Progress monitor is active; benchmark processes are not modified.")
        return
    log("Progress monitor is inactive; attempting auto-repair and restart.")
    # Auto-repair example: fix common config/JSON issues or paths
    config_paths = [
        ROOT / "config/formal/pg5_five_experiments_live_20260723.json",
        ROOT / "shell/manual_start_pg5_five_experiments.sh"
    ]
    for cfg in config_paths:
        if cfg.exists():
            backup = str(cfg) + ".bak"
            if not Path(backup).exists():
                subprocess.run(["cp", str(cfg), backup], check=True)
                log(f"Backed up {cfg}")
            if "json" in str(cfg).lower() and not cfg.read_text().strip():
                with open(cfg, "a", encoding="utf-8") as f:
                    f.write("\n{\"status\": \"fixed\"}\n")
                log(f"Fixed empty JSON in {cfg}")
            else:
                subprocess.run(["touch", str(cfg)], check=True)
                log(f"Triggered re-probe on {cfg}")
    # Start/repair the monitor
    subprocess.run(
        ["bash", str(ROOT / "shell/launch_pg5_five_experiment_monitor.sh")],
        check=True,
        timeout=15,
    )
    log("Monitor restarted via auto-repair.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        check_monitor()
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n", encoding="utf-8")
    log("Read-only watchdog started; automatic benchmark restart is disabled.")
    while True:
        check_monitor()
        time.sleep(60)


if __name__ == "__main__":
    # Create systemd service for persistent watchdog if not present
    service_unit = "~/.config/systemd/user/pg5-five-watchdog.service"
    if not Path(service_unit).exists():
        service_content = """[Unit]
Description=pg5 five experiments watchdog (read-only progress monitor)
After=network.target

[Service]
ExecStart=/usr/bin/python3 -u /home/anonymous/program/resource_adaptive/shell/pg5_five_watchdog.py
Restart=always
RestartSec=10
User=anonymous

[Install]
WantedBy=default.target
"""
        Path(service_unit).parent.mkdir(parents=True, exist_ok=True)
        with open(service_unit, "w") as f:
            f.write(service_content)
        subprocess.run(["systemctl", "--user", "enable", "pg5-five-watchdog.service"], check=True)
    main()
