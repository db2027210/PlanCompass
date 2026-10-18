#!/usr/bin/env python3
"""v120 search with a paper-baseline resource gate before candidate replay."""

from __future__ import annotations

from scripts import mysql_algorithm_c_paper_baseline_gate_v1 as paper_baseline_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v46 as v46


scene_checkpoint = v46.scene_checkpoint
_source = v46._source
_parser_old = '    parser.add_argument("--fresh-search", action="store_true")'
_parser_new = _parser_old + '\n    parser.add_argument("--baseline-only", action="store_true")'
if _source.count(_parser_old) != 1:
    raise RuntimeError("Algorithm C v47 baseline-only CLI boundary differs")
_source = _source.replace(_parser_old, _parser_new)
_old = '''        state["stage"] = "FORMAL_INCUMBENT_BASELINE_COMPLETE"
        alg.atomic(state_path, state)

        while not all(bool(state["converged"][scene]) for scene in SCENES):'''
_new = '''        state["stage"] = "FORMAL_INCUMBENT_BASELINE_COMPLETE"
        alg.atomic(state_path, state)
        paper_gate = paper_baseline_gate.validate_and_write(
            incumbents=incumbents,
            baseline_rows=baseline_rows,
            output=output / "paper_baseline_resource_gate.json",
        )
        state["paper_baseline_resource_gate"] = {
            "status": paper_gate["status"],
            "path": str(output / "paper_baseline_resource_gate.json"),
            "sha256": alg.sha(output / "paper_baseline_resource_gate.json"),
        }
        alg.atomic(state_path, state)
        if args.baseline_only:
            state["stage"] = "BASELINE_ONLY_COMPLETE"
            state["status"] = "COMPLETE"
            state["baseline_only"] = True
            alg.atomic(state_path, state)
            return 0

        while not all(bool(state["converged"][scene]) for scene in SCENES):'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v47 baseline-to-search boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(__file__), "exec"), globals())
