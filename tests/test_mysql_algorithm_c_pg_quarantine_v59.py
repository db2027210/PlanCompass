from scripts import mysql_algorithm_c_interval_control_v3 as interval
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v59 as runtime


class Policy:
    @staticmethod
    def admit_partial_topk(rows, **_kwargs):
        return list(rows)[:5], {"status": "PASS"}


def key(params):
    return str(int(params["p"]))


def rows():
    return [
        {"optimized_params": {"p": value}, "rank": value}
        for value in range(1, 9)
    ]


def test_first_miss_never_selects_quarantined_or_replayed_identity() -> None:
    selected, audit = interval.select_expanded_top5(
        coverage_rankings=rows(),
        local_rankings=[],
        incumbent_params={"p": 0},
        replayed_signatures={"2"},
        permanently_invalid_signatures={"1", "3"},
        accepted_signatures=set(),
        policy=Policy,
        optimizer_params_key=key,
    )
    assert [key(row["optimized_params"]) for row in selected] == ["4", "5", "6", "7", "8"]
    assert audit["excluded_previously_replayed"] == 1
    assert audit["excluded_permanently_invalid"] == 2
    assert audit["permanent_invalid_selected"] is False


def test_boundary_and_numeric_recovery_use_the_same_exclusions() -> None:
    filtered, audit = interval.filter_recovery_rankings(
        rows(),
        replayed_signatures={"4"},
        permanently_invalid_signatures={"1", "2", "3"},
        optimizer_params_key=key,
    )
    assert [key(row["optimized_params"]) for row in filtered] == ["5", "6", "7", "8"]
    assert audit["excluded_permanently_invalid"] == 3
    assert audit["excluded_previously_replayed"] == 1


def test_effective_v59_source_binds_exclusions_to_all_recovery_paths() -> None:
    assert runtime._source.count(
        "permanently_invalid_signatures=permanently_invalid[scene]"
    ) == 3
    assert runtime._source.count("filter_recovery_rankings(") == 2
