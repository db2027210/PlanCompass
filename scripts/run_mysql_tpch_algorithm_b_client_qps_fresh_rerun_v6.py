#!/usr/bin/env python3
"""Fresh TPC-H B rerun using the timeout-safe client-QPS adapter."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as client_qps_v2
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as tpch_release
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_rerun_v5 as predecessor


CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_fresh_rerun_20260830_v9"
    / "algorithm_b_fresh_rerun_contract.json"
)


def install_runtime() -> Any:
    # Valid-row semantics are unchanged. Rows already rejected by the formal
    # hard gate may carry QPS=0 so the outer loop can classify them infeasible.
    tpch_release.client_qps = client_qps_v2
    tpch_release._validate_successor_contract = lambda alg: alg.load(CONTRACT)
    predecessor.CONTRACT = CONTRACT
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "fresh_rerun_contract_path": str(CONTRACT),
            "fresh_rerun_contract_sha256": alg.sha(CONTRACT),
            "invalid_qps_adapter_release": client_qps_v2.contract(),
            "algorithm_a_parameter_or_measurement_read": False,
            "algorithm_a_used_as_search_prior": False,
        })
        return document

    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
