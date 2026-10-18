from __future__ import annotations

import json

from scripts import launch_mysql_four_scene_algorithm_c_v2 as launcher
from scripts import run_mysql_four_scene_algorithm_c_v2 as controller


def test_v2_uses_latest_passing_r0_and_post_r0_credentials() -> None:
    r0 = json.loads(controller.R0.read_text(encoding="utf-8"))
    accounts = json.loads(
        controller.POST_R0_BENCHMARK_ACCOUNTS.read_text(encoding="utf-8")
    )

    assert launcher.R0 == controller.R0
    assert r0["status"] == "PASS"
    assert r0["fresh_r0_complete"] is True
    assert r0["formal_outer_launch_authorized"] is True
    assert r0["accepted_rounds"] == {scene: 0 for scene in controller.SCENES}
    assert accounts["status"] == "PASS"
    assert accounts["passwords_emitted"] is False
    assert accounts["prepared_binding_sha256"] == r0["prepared_binding_sha256"]
    assert accounts["freeze_sha256"] == r0["freeze_sha256"]
    assert {row["worker_id"] for row in accounts["workers"]} == set(
        controller.WORKER_IDS
    )
    assert all(
        row["ready"] is True
        and row["account_mutation_performed_in_this_run"] is True
        for row in accounts["workers"]
    )


def test_v2_outer_attempt_limit_remains_ten() -> None:
    assert controller.MAXIMUM_OUTER_ATTEMPTS == 10
