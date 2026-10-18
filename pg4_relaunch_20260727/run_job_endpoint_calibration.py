#!/usr/bin/env python3
"""Extend JOB/Memory calibration with full-range single-coordinate endpoints."""

from __future__ import annotations

from typing import Any, Dict, List

import run_job_direction_calibration as base


base.ROOT = base.sealed.OUTPUT.parent / "direction_calibration_endpoints"

BOUNDS = {
    "seq_page_cost": (0.1, 10.0),
    "random_page_cost": (0.1, 20.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (10.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}


def endpoint_design(_scope: int) -> List[Dict[str, Any]]:
    defaults = base.sealed.runner.default_cost_parameters()
    rows: List[Dict[str, Any]] = []
    for parameter in base.PARAMETERS:
        for direction, value in zip(("decrease", "increase"), BOUNDS[parameter]):
            params = dict(defaults)
            params[parameter] = value
            rows.append(
                {
                    "parameter": parameter,
                    "direction": direction,
                    "params": params,
                }
            )
    return rows


base.design = endpoint_design


if __name__ == "__main__":
    raise SystemExit(base.main())
