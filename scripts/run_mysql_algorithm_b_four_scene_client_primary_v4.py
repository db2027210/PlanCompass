#!/usr/bin/env python3
"""Run client-primary B with active-path run-scoped XP reset tokens."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_xp_reset_run_scoped_token_v2 as token_policy
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v2 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v70"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    predecessor.predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.predecessor.CONTRACT = CONTRACT
    predecessor.predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    original_configure = impl["configure_runtime"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        token_policy.install()
        return contract

    impl["configure_runtime"] = configure_runtime
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
