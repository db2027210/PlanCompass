#!/usr/bin/env python3
"""Resume partial TPC-H A with cache preconditioning and NVMe exclusion.

The asynchronous authority installer restores the generic single-scene
measurement adapter.  Rebind the TPC-H cache adapter after authority
validation so every new physical wave carries its frozen cache evidence.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_shared_nvme_memory_wave_lease_v2 as lease
from scripts import mysql_tpch_cache_preconditioned_measurement_v1 as measurement
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v1 as resume


_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority


def validate_fresh_authority(**kwargs):
    result = _ORIGINAL_VALIDATE(**kwargs)
    measurement._validate_contract()
    resume.base.runtime.alg.measure_batch = measurement.measure_batch
    if resume.base.runtime.alg.measure_batch is not measurement.measure_batch:
        raise RuntimeError("TPC-H cache measurement adapter binding did not persist")
    return result


def main() -> int:
    if (
        "--scene" not in sys.argv
        or sys.argv[sys.argv.index("--scene") + 1] != "tpch_sf4_memory"
    ):
        raise RuntimeError("storage-serialized TPC-H v2 continuation is TPC-H-only")
    measurement._validate_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    lease.install(resume.base)
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
