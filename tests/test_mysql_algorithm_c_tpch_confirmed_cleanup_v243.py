from pathlib import Path
import subprocess

import pytest

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v134 as controller


WORKER = {"worker_id": "tpch_w1"}


def timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(["mysql"], 15)


def test_cleanup_timeout_is_success_when_server_readback_says_view_absent() -> None:
    calls: list[str] = []

    def mysql(worker: object, sql: str) -> str:
        calls.append(sql)
        if sql.startswith("SET SESSION"):
            raise timeout()
        assert sql == controller.READ_REVENUE0_COUNT
        return "0"

    assert controller.cleanup_with_server_confirmation(
        mysql, WORKER, controller.DROP_REVENUE0, sleep=lambda _: None
    ) == ""
    assert calls == [
        "SET SESSION lock_wait_timeout=5; " + controller.DROP_REVENUE0,
        controller.READ_REVENUE0_COUNT,
    ]


def test_cleanup_retries_only_while_readback_says_view_present() -> None:
    calls: list[str] = []
    attempts = 0

    def mysql(worker: object, sql: str) -> str:
        nonlocal attempts
        calls.append(sql)
        if sql.startswith("SET SESSION"):
            attempts += 1
            if attempts == 1:
                raise subprocess.CalledProcessError(1, ["mysql"], stderr="lock wait")
            return ""
        assert sql == controller.READ_REVENUE0_COUNT
        return "1"

    assert controller.cleanup_with_server_confirmation(
        mysql, WORKER, controller.DROP_REVENUE0, sleep=lambda _: None
    ) == ""
    assert attempts == 2


def test_cleanup_fails_closed_after_three_bounded_attempts() -> None:
    attempts = 0

    def mysql(worker: object, sql: str) -> str:
        nonlocal attempts
        if sql.startswith("SET SESSION"):
            attempts += 1
            raise timeout()
        assert sql == controller.READ_REVENUE0_COUNT
        return "1"

    with pytest.raises(subprocess.TimeoutExpired):
        controller.cleanup_with_server_confirmation(
            mysql, WORKER, controller.DROP_REVENUE0, sleep=lambda _: None
        )
    assert attempts == 3


def test_non_cleanup_sql_is_unchanged() -> None:
    def mysql(worker: object, sql: str) -> str:
        return "unchanged:" + sql

    assert controller.cleanup_with_server_confirmation(
        mysql, WORKER, "SELECT 1", sleep=lambda _: None
    ) == "unchanged:SELECT 1"
