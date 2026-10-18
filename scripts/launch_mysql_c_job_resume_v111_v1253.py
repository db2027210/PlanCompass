#!/usr/bin/env python3
"""Reinsert only JOB beside the three already-running v1252 C scenes."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v111_v1252 as prior


launch = prior.launch
launch.CAMPAIGN = launch.HISTORY / "v1253_mysql_c_job_resume_v111_20260906"
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1253_mysql_c_job_resume_v111"
)
job = deepcopy(launch.SPECS["job_memory"])
job.update({
    "run_id": "v1253_to_c_full_docker_path_resilience_v111_20260906_job",
    "quarantine": "v1250_c_job_resume_quarantine_v111_20260906",
})
launch.SPECS = {"job_memory": job}


if __name__ == "__main__":
    raise SystemExit(launch.main())
