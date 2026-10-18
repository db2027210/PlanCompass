#!/usr/bin/env python3
"""Build a scene checkpoint after a bounded TPC-H cleanup timeout.

This successor keeps every completed outer decision and grants zero credit to
the incomplete outer.  It admits only the exact MySQL ``revenue0`` cleanup
timeout observed after the controller had finished outer 10.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v10 as v10


_verify_inherited = v10._verify_inherited
_source = v10._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v10"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v11"',
)

_old = '        or str(state.get("error") or "") not in RECOVERABLE_ERRORS\n'
_new = '''        or not (
            str(state.get("error") or "") in RECOVERABLE_ERRORS
            or (
                scene == "tpch_sf4_memory"
                and str(state.get("error") or "").startswith(
                    "TimeoutExpired: Command "
                )
                and str(state.get("error") or "").endswith(
                    "'DROP VIEW IF EXISTS tpch.revenue0']' timed out after 15 seconds"
                )
            )
        )
'''
if _source.count(_old) != 1:
    raise RuntimeError("scene checkpoint v11 recoverable-error boundary differs")
_source = _source.replace(_old, _new)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
