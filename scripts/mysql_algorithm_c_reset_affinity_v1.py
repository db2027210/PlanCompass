#!/usr/bin/env python3
"""Pin XP reset filesystem helpers to the frozen worker CPU envelope."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from scripts import reset_mysql_xp_data_planes_v6 as implementation


RELEASE = "v208-xp-reset-management-affinity"
_source = inspect.getsource(implementation._prepare_fresh_xp)
_worker_anchor = '    worker_id = str(worker["worker_id"])\n'
if _source.count(_worker_anchor) != 1:
    raise RuntimeError("XP reset worker boundary differs")
_source = _source.replace(
    _worker_anchor,
    _worker_anchor
    + '    management_cpuset = str(worker["database_cpuset"])\n',
)
if _source.count('["sudo", "-n"') != 8:
    raise RuntimeError("XP reset sudo helper boundary differs")
_source = _source.replace(
    '["sudo", "-n"',
    '["taskset", "-c", management_cpuset, "sudo", "-n"',
)

_namespace: dict[str, Any] = dict(implementation.__dict__)
_namespace["__name__"] = __name__
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _namespace)
prepare_fresh_xp = _namespace["_prepare_fresh_xp"]

