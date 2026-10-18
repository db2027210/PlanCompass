import json
from pathlib import Path

from scripts import repair_mysql_algorithm_c_scoped_terminal_status_v1 as repair


def test_source_uses_active_scene_not_sibling_flags() -> None:
    source = Path(repair.__file__).read_text(encoding="utf-8")
    assert 'active != [scene]' in source
    assert 'inactive_sibling_flags_excluded_from_terminal_aggregation' in source
    assert 'accepted_state_credit_increment": 0' in source


def test_running_state_is_never_repaired(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "history"
    state = root / "run" / "run_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "status": "RUNNING", "stage": "FORMAL_OUTER_RUNNING",
        "algorithm": "C", "async_scene": "hybench_xp_cpu",
        "active_scenes": ["hybench_xp_cpu"],
        "converged": {"hybench_xp_cpu": True},
    }))
    monkeypatch.setattr(repair, "HISTORY", root)
    assert repair.repair_if_scoped_complete(state, "hybench_xp_cpu") is False
    assert json.loads(state.read_text())["status"] == "RUNNING"
