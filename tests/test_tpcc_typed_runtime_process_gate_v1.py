from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "capture/mysql_tpcc_typed_capture_entry_v1.py"


def test_capture_checks_for_late_overlapping_benchmarks_throughout_measurement() -> None:
    source = ENTRY.read_text(encoding="utf-8")

    assert 'row["category"] not in {"benchmark", "controller"}' in source
    assert "target.intersection(allowed)" in source
    assert "self._start_runtime_interference_monitor()" in source
    assert source.count("self._assert_runtime_interference_clear()") >= 3
    assert "runtime_process_interference_gate_v1.json" in source
    assert '"passed": False' in source
    assert '"passed": True' in source
