from scripts import mysql_algorithm_b_benchmark_session_drain_v1 as drain


def test_drain_worker_kills_only_reported_benchmark_sessions_and_cleans_q15(monkeypatch):
    calls = []
    processlists = iter([
        "11\tExecute\t901\n12\tSleep\t4",
        "",
    ])

    def fake_mysql(_worker, sql):
        calls.append(sql)
        if sql == drain.READBACK_SQL:
            return next(processlists)
        if sql.startswith("KILL CONNECTION "):
            return ""
        if sql.startswith("SELECT COUNT(*)"):
            return "1" if calls.count(sql) == 1 else "0"
        if sql == "DROP VIEW IF EXISTS tpch.revenue0":
            return ""
        raise AssertionError(sql)

    monkeypatch.setattr(drain, "_mysql", fake_mysql)
    result = drain.drain_worker({
        "worker_id": "tpch_w1",
        "scene": "tpch_sf4_memory",
        "container": "formal_tpch_w1",
        "container_id": "abc",
        "credential_defaults": "/unused",
        "host_port": 1,
    })
    assert result["killed_connection_ids"] == [11, 12]
    assert result["sessions_after"] == []
    assert result["revenue0_view_count_before"] == 1
    assert result["revenue0_view_count_after"] == 0
    assert "DROP VIEW IF EXISTS tpch.revenue0" in calls


def test_readback_sql_is_scoped_to_frozen_benchmark_user():
    assert "USER='ra_benchmark'" in drain.READBACK_SQL
    assert "PROCESSLIST" in drain.READBACK_SQL

