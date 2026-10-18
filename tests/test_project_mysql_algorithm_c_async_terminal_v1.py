from __future__ import annotations

from pathlib import Path

from scripts import project_mysql_algorithm_c_async_terminal_v1 as subject
from scripts import reconcile_mysql_algorithm_c_async_terminal_v1 as reconcile
from tests.test_reconcile_mysql_algorithm_c_async_terminal_v1 import fixture


def test_projection_is_complete_without_changing_source(tmp_path: Path) -> None:
    run = fixture(tmp_path)
    source = run / "run_state.json"
    correction = tmp_path / "reconciliation.json"
    reconcile.atomic(correction, reconcile.reconcile(run))
    before = source.read_bytes()
    projected = subject.project(source, correction)
    assert projected["status"] == "COMPLETE"
    assert projected["completion_reason"] == "all_active_log_domain_widths_reached_1pct"
    assert projected["new_physical_measurement_invoked_for_projection"] is False
    assert projected["accepted_or_rejected_credit_increment_for_projection"] == 0
    assert source.read_bytes() == before
