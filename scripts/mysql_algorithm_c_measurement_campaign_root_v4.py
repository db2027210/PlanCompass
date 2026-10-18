#!/usr/bin/env python3
"""Resolve incumbent and outer paths without mutable predecessor recursion."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_measurement_campaign_root_v2 as predecessor

RUN_ID = predecessor.RUN_ID
_PREDECESSOR_MEASUREMENT_CAMPAIGN_ROOT = predecessor.measurement_campaign_root


def measurement_campaign_root(output: Path) -> Path:
    output = Path(output).resolve()
    if output.name == "formal_incumbent_baseline":
        run_root = output.parent
        if RUN_ID.fullmatch(run_root.name) is None or run_root.name == "mysql_four_scene_20260809":
            raise RuntimeError("formal baseline run-id boundary differs")
        return run_root
    return _PREDECESSOR_MEASUREMENT_CAMPAIGN_ROOT(output)
