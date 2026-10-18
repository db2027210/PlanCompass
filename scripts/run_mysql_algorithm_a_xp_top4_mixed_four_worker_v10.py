#!/usr/bin/env python3
"""Run XP Top4-A on the mixed-SSD v4 four-worker cohorts."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v16 as gate_v16
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset_v2
from scripts import run_mysql_algorithm_a_xp_top4_async_v1 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v10"


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
    predecessor.COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
    predecessor.no_lease.coordination = gate_v16
    original_configure = predecessor.resume.configure_runtime

    def configure_runtime(*, freeze_path, workers, scene):
        original_configure(freeze_path=freeze_path, workers=workers, scene=scene)
        prepared = Path(str(predecessor._active_authority_artifacts["prepared"]))
        reset_v2.configure(freeze=freeze_path, prepared=prepared)
        worker_ids = tuple(sorted(workers))
        reset_v2.XP_IDS = set(worker_ids)
        reset_v2.XP_CONTAINER_ALLOWLIST = {
            worker_id: str(workers[worker_id]["container"])
            for worker_id in worker_ids
        }
        reset_v2.install()
        predecessor.base.alg.reset = reset_v2

    predecessor.resume.configure_runtime = configure_runtime


def main() -> int:
    original_validate = predecessor.validate_launch_authority

    def validate_launch_authority(path, scene):
        authority = predecessor.load(path)
        predecessor._active_authority_artifacts = dict(authority.get("artifacts") or {})
        return original_validate(path, scene)

    predecessor.validate_launch_authority = validate_launch_authority
    install()
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
