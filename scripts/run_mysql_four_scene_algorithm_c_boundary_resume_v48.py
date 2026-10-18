#!/usr/bin/env python3
"""Persist the measured incumbent before baseline-only lineage auditing."""

from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v47 as v47


scene_checkpoint = v47.scene_checkpoint
_source = v47._source
_old = '''        if args.baseline_only:
            state["stage"] = "BASELINE_ONLY_COMPLETE"
            state["status"] = "COMPLETE"
            state["baseline_only"] = True
            alg.atomic(state_path, state)
            return 0'''
_new = '''        if args.baseline_only:
            state["incumbents"] = incumbents
            state["stage"] = "BASELINE_ONLY_COMPLETE"
            state["status"] = "COMPLETE"
            state["baseline_only"] = True
            alg.atomic(state_path, state)
            return 0'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v48 baseline-only audit boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(__file__), "exec"), globals())
