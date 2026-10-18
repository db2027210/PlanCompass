#!/usr/bin/env python3
"""Resolve every Algorithm C measurement to its owning formal run root.

Expansion measurements are nested below ``outer_NNN/<phase>/wave_N``.  The
legacy resolver only inspected the immediate parent and consequently used the
phase directory as the quarantine namespace.  Identical phase/token names in
different launches then collided on the shared data-plane filesystem.
"""

from __future__ import annotations

from pathlib import Path
import re


OUTER = re.compile(r"outer_[0-9]{3}")
RUN_ID = re.compile(r"[A-Za-z0-9_.-]{8,180}")


def measurement_campaign_root(output: Path) -> Path:
    output = Path(output).resolve()
    outer_roots = [parent for parent in output.parents if OUTER.fullmatch(parent.name)]
    if len(outer_roots) != 1:
        raise RuntimeError("measurement output must have exactly one outer ancestor")
    run_root = outer_roots[0].parent
    if RUN_ID.fullmatch(run_root.name) is None or run_root.name == "mysql_four_scene_20260809":
        raise RuntimeError("formal measurement run-id boundary differs")
    return run_root

