from __future__ import annotations

import json
from pathlib import Path

from scripts import execute_mysql_algorithm_a_xp_slot_transition_v1 as execute


def test_source_freeze_binding_is_current_v18():
    assert execute.SOURCE_FREEZE.is_file()
    assert execute.sha(execute.SOURCE_FREEZE) == execute.SOURCE_FREEZE_SHA256
    value = json.loads(execute.SOURCE_FREEZE.read_text(encoding="utf-8"))
    assert value["schema_version"] == (
        "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
    )


def test_transition_outputs_are_campaign_scoped():
    root = Path("/home/anonymous/program/resource_adaptive_mysql")
    assert execute.TRANSITION_ROOT.is_relative_to(
        root / "history/mysql_four_scene_20260809"
    )
    assert execute.FREEZE_PATH.is_relative_to(root / "config/formal")
    assert execute.CONTROLLER == (
        root / "scripts/run_mysql_algorithm_a_dynamic_resume_v2.py"
    )

