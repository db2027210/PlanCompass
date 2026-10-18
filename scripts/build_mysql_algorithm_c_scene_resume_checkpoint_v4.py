#!/usr/bin/env python3
"""Build chained scene checkpoints after v110 lifecycle failures.

The v3 builder knows how to verify inherited scene checkpoints, but its main
``build_checkpoint`` path still dispatched the supplied base through the
legacy multi-scene verifier.  Bind that path to the schema-aware verifier and
whitelist only the two lifecycle failure classes observed in v110.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v3 as prior


_ORIGINAL_STANDARD_VERIFY = prior._STANDARD_VERIFY
RECOVERABLE_V110_ERRORS = {
    "RuntimeError: dynamic non-interference gate failed closed",
    (
        "TimeoutExpired: Command '['docker', 'inspect', "
        "'ra_mysql_disk2_algc_job_w1_20260809']' timed out after 30 seconds"
    ),
    (
        "TimeoutExpired: Command '['docker', 'inspect', "
        "'ra_mysql_disk2_algc_tpch_w1_20260809']' timed out after 30 seconds"
    ),
}


def verify_any_checkpoint(path: Path):
    value = prior.checkpoint_v2._load(path)
    schema = value.get("schema_version")
    if schema == prior.checkpoint_v2.SCHEMA:
        return prior.checkpoint_v2.verify_checkpoint(path)
    if schema == prior.checkpoint_v1.SCHEMA:
        return prior.checkpoint_v1.verify_checkpoint(path)
    if schema == prior.SCHEMA:
        return prior.verify_checkpoint(path)
    return _ORIGINAL_STANDARD_VERIFY(path)


def _with_bindings(call):
    old_verify = prior._STANDARD_VERIFY
    old_errors = set(prior.RECOVERABLE_ERRORS)
    prior._STANDARD_VERIFY = verify_any_checkpoint
    prior.RECOVERABLE_ERRORS = old_errors | RECOVERABLE_V110_ERRORS
    try:
        return call()
    finally:
        prior.RECOVERABLE_ERRORS = old_errors
        prior._STANDARD_VERIFY = old_verify


def verify_checkpoint(path: Path):
    return _with_bindings(lambda: prior.verify_checkpoint(path))


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path):
        value = prior.checkpoint_v2._load(path)
        if value.get("schema_version") == prior.SCHEMA:
            return verify_checkpoint(path)
        return verify_any_checkpoint(path)


scene_checkpoint = _CheckpointVerifier()


def main() -> int:
    return int(_with_bindings(prior.main))


if __name__ == "__main__":
    raise SystemExit(main())
