from __future__ import annotations

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v33 as controller
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


def test_baseline_only_state_keeps_measured_incumbents():
    assert runtime._source.count('state["incumbents"] = incumbents') >= 1
    assert runtime._source.index('state["incumbents"] = incumbents') < runtime._source.index(
        'state["stage"] = "BASELINE_ONLY_COMPLETE"'
    )


def test_v123_contract_binds_audit_fix():
    contract = controller.validate_contract()
    assert contract["baseline_only_persists_measured_incumbents"] is True
