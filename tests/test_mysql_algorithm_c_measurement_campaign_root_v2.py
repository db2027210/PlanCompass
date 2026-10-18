from pathlib import Path

import pytest

from scripts.mysql_algorithm_c_measurement_campaign_root_v2 import (
    measurement_campaign_root,
)


def test_top5_and_nested_expansion_share_exact_run_root(tmp_path: Path) -> None:
    run = tmp_path / "v1267_formal_mysql_c_xp_cpu"
    top5 = run / "outer_008" / "top5_batch_1"
    nested = run / "outer_008" / "current_interval_plan_coverage_001" / "wave_1"
    assert measurement_campaign_root(top5) == run.resolve()
    assert measurement_campaign_root(nested) == run.resolve()


def test_missing_outer_boundary_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="exactly one outer"):
        measurement_campaign_root(tmp_path / "v1267_formal_mysql_c_xp_cpu" / "wave_1")

