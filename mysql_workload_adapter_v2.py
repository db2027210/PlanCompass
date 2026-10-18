#!/usr/bin/env python3
"""Successor adapter allowing the frozen five-minute native-XP drain."""

from __future__ import annotations

from pathlib import Path


PREDECESSOR = Path(__file__).with_name("mysql_workload_adapter.py")
_source = PREDECESSOR.read_text(encoding="utf-8")
_old = 'or not 30 <= int(control["graceful_quiescence_seconds"]) <= 120'
_new = 'or not 30 <= int(control["graceful_quiescence_seconds"]) <= 300'
if _source.count(_old) != 1:
    raise RuntimeError("MySQL workload-adapter predecessor validation differs")
exec(compile(_source.replace(_old, _new), str(Path(__file__)), "exec"), globals())
