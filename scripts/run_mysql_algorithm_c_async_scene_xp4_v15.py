#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts import run_mysql_algorithm_c_async_scene_xp4_v1 as p
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v9 as runtime
C=ROOT/"config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v22"
def main():
 p.runtime=runtime; p.CONTRACT_ROOT=C; p.ASYNC_CONTRACT=C/"algorithm_c_xp4_async_contract.json"; p.OPTIMIZATION_CONTRACT=C/"optimization_contract.json"; p.COORDINATION_CONTRACT=C/"coordination_contract.json"; return p.main()
if __name__=="__main__": raise SystemExit(main())
