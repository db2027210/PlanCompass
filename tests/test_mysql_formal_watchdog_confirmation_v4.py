from __future__ import annotations

from scripts import watch_mysql_formal_external_postgres_interference_v4 as v4


def test_v4_uses_fifteen_second_same_identity_confirmation(monkeypatch) -> None:
    captured = {}

    def fake(run_id, first_result, *, target_mems, confirmation_seconds):
        captured.update({
            "run_id": run_id,
            "first_result": first_result,
            "target_mems": target_mems,
            "confirmation_seconds": confirmation_seconds,
        })
        return None

    monkeypatch.setattr(v4, "ORIGINAL_CONFIRM", fake)
    result = v4.confirmed_workload_isolation_failure(
        "formal_run_20260817", {"passed": False}, target_mems={0, 1},
    )
    assert result is None
    assert captured["confirmation_seconds"] == 15.0
    assert captured["target_mems"] == {0, 1}


def test_v4_install_preserves_fail_closed_evaluator(monkeypatch) -> None:
    installed = []
    monkeypatch.setattr(v4.isolation, "install", lambda: installed.append(True))
    original = v4.watchdog.confirmed_workload_isolation_failure
    try:
        v4.install()
        assert installed == [True]
        assert v4.watchdog.confirmed_workload_isolation_failure is (
            v4.confirmed_workload_isolation_failure
        )
    finally:
        v4.watchdog.confirmed_workload_isolation_failure = original
