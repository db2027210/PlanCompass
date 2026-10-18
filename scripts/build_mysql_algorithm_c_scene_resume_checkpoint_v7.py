#!/usr/bin/env python3
"""Chain v3 scene checkpoints through the quarantine-collision recovery."""

from pathlib import Path

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v6 as predecessor


core = predecessor.predecessor.predecessor.prior


def _verify_inherited(path: Path):
    value = core.checkpoint_v2._load(path)
    schema = value.get("schema_version")
    if schema == core.SCHEMA:
        return core.verify_checkpoint(path)
    if schema == core.checkpoint_v2.SCHEMA:
        return core.checkpoint_v2.verify_checkpoint(path)
    if schema == core.checkpoint_v1.SCHEMA:
        return core.checkpoint_v1.verify_checkpoint(path)
    return core.checkpoint_v2._STANDARD_VERIFY(path)


def _with_chain_verifier(call):
    old = core._verify_inherited
    core._verify_inherited = _verify_inherited
    try:
        return call()
    finally:
        core._verify_inherited = old


def verify_checkpoint(path: Path):
    return _with_chain_verifier(lambda: predecessor.verify_checkpoint(path))


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path):
        return _with_chain_verifier(
            lambda: predecessor.scene_checkpoint.verify_or_standard(path)
        )


scene_checkpoint = _CheckpointVerifier()


def main() -> int:
    return int(_with_chain_verifier(predecessor.main))


if __name__ == "__main__":
    raise SystemExit(main())
