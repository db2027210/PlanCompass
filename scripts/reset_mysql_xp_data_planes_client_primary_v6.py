#!/usr/bin/env python3
"""Stale-task recovery with legacy-freeze runtime-root derivation."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "reset_mysql_xp_data_planes_client_primary_v5.py"
)
_source = SOURCE.read_text(encoding="utf-8")
old = '        runtime_root = Path(str(worker.get("runtime_root") or ""))\n'
new = '''        storage_by_worker = freeze.get("storage_by_worker") or {}
        storage = dict(
            storage_by_worker.get(worker_id) or freeze.get("storage") or {}
        )
        runtime_root = Path(str(
            worker.get("runtime_root")
            or (Path(str(storage.get("runtime_root") or "")) / worker_id)
        ))
'''
if _source.count(old) != 1:
    raise RuntimeError("XP reset v5 runtime-root boundary differs")
_source = _source.replace(old, new)
_source = _source.replace(
    'implementation.AUTHORITY_VERSION = "client_primary_stale_task_recovery_v5"',
    'implementation.AUTHORITY_VERSION = "client_primary_stale_task_recovery_v6"',
)
_source = _source.replace(
    'implementation.OUTPUT_SCHEMA = "mysql_client_primary_xp_reset_v5"',
    'implementation.OUTPUT_SCHEMA = "mysql_client_primary_xp_reset_v6"',
)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
