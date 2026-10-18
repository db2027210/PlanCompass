import json
from pathlib import Path

import pytest

from formal_mysql_cost_protocol import (
    CostProtocolError,
    cost_apply_sql,
    drain_apply_flush_readback,
)
from formal_recost_core import MYSQL_COST_NAMES


ROOT = Path(__file__).parents[1]
SEARCH = ROOT / "preflight/frozen_formal_config_20260721_v3/search_spaces.json"


def defaults():
    document = json.loads(SEARCH.read_text())
    return {name: float(document["mysql"]["default"][name]) for name in MYSQL_COST_NAMES}


class FakeClient:
    def __init__(self, factory, generation):
        self.factory = factory
        self.generation = generation

    def execute(self, sql):
        self.factory.calls.append((self.generation, sql))
        if "information_schema.PROCESSLIST" in sql:
            self.factory.drain_queries += 1
            return "11\n12\n" if self.factory.drain_queries == 1 else ""
        if sql.startswith("KILL CONNECTION"):
            return ""
        if sql.startswith("START TRANSACTION"):
            self.factory.applied = True
            return ""
        if "FROM mysql.server_cost" in sql and "UNION ALL" in sql:
            assert self.factory.applied
            return "\n".join(f"{name}\t{value}" for name, value in sorted(defaults().items()))
        raise AssertionError(sql)


class FakeFactory:
    def __init__(self):
        self.generation = 0
        self.calls = []
        self.drain_queries = 0
        self.applied = False

    def __call__(self):
        self.generation += 1
        return FakeClient(self, self.generation)


def test_exact_apply_sql_has_transaction_commit_and_flush():
    sql = cost_apply_sql(defaults())
    assert sql.startswith("START TRANSACTION;")
    assert sql.count("UPDATE mysql.server_cost") == 6
    assert sql.count("UPDATE mysql.engine_cost") == 2
    assert ";COMMIT;FLUSH OPTIMIZER_COSTS;" in sql


def test_drain_apply_and_new_connection_readback_order():
    factory = FakeFactory()
    evidence = drain_apply_flush_readback(factory, SEARCH, defaults())
    assert evidence.drained_connection_ids == (11, 12)
    assert evidence.new_connection_after_flush
    assert evidence.readback_connection_generation > evidence.apply_connection_generation
    assert [sql for _, sql in factory.calls if sql.startswith("KILL CONNECTION")] == [
        "KILL CONNECTION 11;", "KILL CONNECTION 12;"
    ]


def test_missing_parameter_fails_before_any_client_is_created():
    factory = FakeFactory()
    values = defaults()
    values.pop("key_compare_cost")
    with pytest.raises(CostProtocolError, match="exactly"):
        drain_apply_flush_readback(factory, SEARCH, values)
    assert factory.generation == 0
