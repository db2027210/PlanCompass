#!/usr/bin/env python3
"""Package the post-probe2 TPC-C formal batch06 without starting it."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.package_tpcc_formal_batch06 as package


package.BATCH_ROOT = (
    package.ATTEMPT
    / "formal10_tpcc_batch06_post_probe2_v1_20260729T2245"
)
package.PACKAGE_ROOT = Path(
    "/tmp/tpcc_formal_batch06_post_probe2_package_v1"
)
package.RUNNER_ROOT = package.PACKAGE_ROOT / "runner"
package.PACKAGE_SCHEMA = "tpcc_formal_batch06_post_probe2_package_v1"


if __name__ == "__main__":
    raise SystemExit(package.main())
