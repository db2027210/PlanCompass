#!/usr/bin/env python3
"""Auto-launch Algorithm B with the current v68/v11 formal authority."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v11 as isolation
from scripts import wait_and_launch_mysql_algorithm_b_after_c_v1 as predecessor


PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006_rebind/prepared_binding.json"
ACCOUNTS = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006_accounts/benchmark_accounts_binding.json"
R0 = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006/continuation_r0/r0_binding.json"
OPTIMIZATION = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v59/optimization_contract.json"


def authority_probe() -> tuple[bool, str | None]:
    from scripts import wait_and_launch_mysql_algorithm_c_v42_rank4_v1 as c_waiter

    isolation.validate_release()
    isolation.install()
    c_waiter.OPTIMIZATION = OPTIMIZATION
    c_waiter.RUNTIME_PREFLIGHT = PREFLIGHT
    c_waiter.PREPARED = PREPARED
    c_waiter.ACCOUNTS = ACCOUNTS
    c_waiter.configure_authority()
    return c_waiter.authority_probe()


def main() -> int:
    predecessor.LAUNCHER = (
        ROOT / "scripts/launch_mysql_four_scene_algorithm_b_from_c_v2.py"
    )
    predecessor.PREFLIGHT = PREFLIGHT
    predecessor.PREPARED = PREPARED
    predecessor.ACCOUNTS = ACCOUNTS
    predecessor.R0 = R0
    predecessor.authority_probe = authority_probe
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
