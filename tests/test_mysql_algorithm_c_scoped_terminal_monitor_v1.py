from pathlib import Path

from scripts import monitor_mysql_algorithm_c_scoped_terminal_repair_v1 as monitor


def test_monitor_is_strictly_terminal_and_does_not_replay() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert "repair_if_scoped_complete" in source
    assert 'status != "RUNNING"' in source
    assert "subprocess" not in source
    assert "docker" not in source
    assert "accepted_state_credit" not in source
