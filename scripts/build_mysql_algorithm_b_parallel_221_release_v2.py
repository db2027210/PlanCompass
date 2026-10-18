#!/usr/bin/env python3
"""Build the v10 state-initialized Algorithm B parallel scheduler release."""

import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_parallel_221_release_v1 as predecessor


predecessor.OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v10"
    / "algorithm_b_parallel_221_contract.json"
)
predecessor.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v10.py"
)
predecessor.LAUNCHER = (
    ROOT
    / "scripts/launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v10.py"
)
predecessor.TEST = ROOT / "tests/test_mysql_algorithm_b_parallel_221_resume_v10.py"
original_atomic = predecessor.atomic


def atomic_v10(path, value):
    document = copy.deepcopy(dict(value))
    document["release_version"] = "v10"
    document["continuation"]["v9_prestart_failure_reused"] = False
    document["continuation"]["v9_prestart_failure_started_workload"] = False
    original_atomic(path, document)


predecessor.atomic = atomic_v10


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
