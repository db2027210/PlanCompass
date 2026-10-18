#!/usr/bin/env python3
"""Print the frozen JOB OAT plan; this controller intentionally cannot execute it."""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'config/formal/frozen_mysql_job_oat_calibration_20260723_v1'
def main():
 plan=json.loads((D/'job.oat_controller_plan.json').read_text());runtime=json.loads((D/'job.runtime.template.json').read_text())
 print(json.dumps({'status':'DRY_RUN_BLOCKED','execution_authorized':False,'minimum_distinct_formal_points':plan['design']['minimum_distinct_formal_points'],'duration':plan['duration'],'unsatisfied_runtime_gates':sorted(k for k,v in runtime['runtime_gates'].items()if v is False),'database_contacted':False,'oat_started':False},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
