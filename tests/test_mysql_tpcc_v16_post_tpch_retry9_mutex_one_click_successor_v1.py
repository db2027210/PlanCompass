from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
if not SOURCE.is_file():
    SOURCE = ROOT / "controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
SPEC = importlib.util.spec_from_file_location("tpcc_mutex_successor", SOURCE)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_exact_lineage_and_mutex_are_pinned():
    assert M.TPH_ID == "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3"
    assert M.TPCC_ID == "025b4b9595755761b4c10bdb3324dc8f7dc2f7edea76bb906e166de4dcbdb6d8"
    assert str(M.MUTEX) == "/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock"


def test_no_bootstrap_only_concurrency_escape_exists():
    text = SOURCE.read_text()
    assert "PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT" in text
    assert 'tpcc_fourflow_mutex_held_for_full_window' in text
    assert "bootstrap-only" not in text


def test_absence_to_launch_race_is_closed_by_ownership():
    text = SOURCE.read_text()
    assert "mutex_identity = acquire_mutex()" in text
    assert "verify_retry9_completion(args.tpch_retry9_completion" in text
    assert "finally:\n        release_mutex(mutex_identity)" in text


def test_retry9_plan_binds_same_three_inputs():
    text = SOURCE.read_text()
    for term in ("bootstrap_spec", "bootstrap_evidence", "mysql_defaults",
                 "controller/retry9.plan.json", "inner_completion_sha256"):
        assert term in text


def test_only_mysql_scope_and_no_pg_or_ssh_action():
    text = SOURCE.read_text().lower()
    assert 'formal_scope") != "mysql_only"' in text
    assert "postgresql_mutation_prohibited" in text
    assert "ssh" not in text
    assert "systemctl" not in text


def test_predecessor_is_run_under_pinned_python():
    text = SOURCE.read_text()
    assert 'command = [str(PINNED_PYTHON), "-B", str(controller)' in text
    assert "subprocess.run(command, check=False)" in text


def test_busy_mutex_fails_closed(tmp_path, monkeypatch):
    lock = tmp_path / "lock"
    lock.mkdir()
    monkeypatch.setattr(M, "MUTEX", lock)
    with pytest.raises(M.GateError, match="occupied"):
        M.acquire_mutex()


def test_acquire_and_release_exact_mutex_identity(tmp_path, monkeypatch):
    lock = tmp_path / "lock"
    monkeypatch.setattr(M, "MUTEX", lock)
    identity = M.acquire_mutex()
    assert lock.is_dir()
    M.release_mutex(identity)
    assert not lock.exists()
