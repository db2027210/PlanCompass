#!/usr/bin/env python3
"""Run fresh v15 R0 after the formal Docker-remove timeout recovery."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_r0_v5 as implementation


implementation.PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v29.json"
)
implementation.RUNTIME_PREFLIGHT = implementation.PREFLIGHT
implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v27_20260811T170000p0800"
    / "prepared_binding.json"
)
implementation.BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v29_20260811T170100p0800"
    / "benchmark_accounts_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())
