#!/usr/bin/env python3
"""Run-scoped mixed-SSD reset with a single-worker management mutex."""

from __future__ import annotations

import fcntl
from pathlib import Path
import threading
from typing import Any

from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v4_base as predecessor


for _name, _value in vars(predecessor).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

AUTHORITY_VERSION = "xp_mixed_four_worker_v4_run_scoped_serial_worker_reset"
WORKER_RESET_MUTEX = Path("/tmp/mysql_xp_worker_reset_management_v1.lock")
_PROCESS_MUTEX = threading.Lock()
_base_prepare_fresh_xp = predecessor._prepare_fresh_xp


def _prepare_fresh_xp(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _PROCESS_MUTEX:
        descriptor = WORKER_RESET_MUTEX.open("a+", encoding="utf-8")
        try:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
            row = _base_prepare_fresh_xp(*args, **kwargs)
            row["worker_reset_management_mutex"] = {
                "schema_version": "mysql_xp_worker_reset_management_mutex_v1",
                "path": str(WORKER_RESET_MUTEX),
                "scope": "single_worker_data_plane_replacement_only",
                "scene_wave_or_measurement_serialized": False,
                "candidate_or_outer_policy_changed": False,
            }
            evidence_path = Path(str(row["worker_evidence_path"]))
            evidence = dict(row)
            evidence.pop("worker_evidence_path", None)
            evidence.pop("worker_evidence_sha256", None)
            temporary = evidence_path.with_name(
                f".{evidence_path.name}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, evidence_path)
            row["worker_evidence_sha256"] = _sha(evidence_path)
            return row
        finally:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
            descriptor.close()


_impl["AUTHORITY_VERSION"] = AUTHORITY_VERSION
_impl["_prepare_fresh_xp"] = _prepare_fresh_xp
