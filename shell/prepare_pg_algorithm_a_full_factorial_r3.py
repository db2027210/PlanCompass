#!/usr/bin/env python3
"""Create the clean r3 campaign with a shared immutable PG baseline."""

from pathlib import Path

from shell import prepare_pg_algorithm_a_full_factorial_r1 as preparation


preparation.DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
    "20260816_algorithm_a_full_factorial_sharedbaseline_clean_r3"
)


if __name__ == "__main__":
    raise SystemExit(preparation.main())
