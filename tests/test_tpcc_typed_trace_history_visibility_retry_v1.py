from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "source/TpccTypedTrace.java"
)


def test_statement_metrics_waits_for_exact_history_row_and_fails_closed() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "for (int attempt = 0; attempt < 2000 && eventId == null; attempt++)" in source
    assert "WHERE THREAD_ID=? AND EVENT_ID>?" in source
    assert "Thread.sleep(1L)" in source
    assert (
        'throw new SQLException("executed statement is absent from history_long after 2000ms")'
        in source
    )
    assert "fabricate zero metrics" in source
