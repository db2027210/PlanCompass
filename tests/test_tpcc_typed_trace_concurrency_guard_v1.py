from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "source/TpccTypedTrace.java"
)


def test_tpcc_trace_serializes_same_jvm_before_cross_process_lock():
    text = SOURCE.read_text(encoding="utf-8")
    method = text.index(
        "private static synchronized void appendCommitted("
    )
    file_lock = text.index(
        "FileLock fileLock = lockChannel.lock()", method
    )
    write = text.index("writeFully(output, bytes)", file_lock)

    assert method < file_lock < write
    assert "if (!fileLock.isValid())" in text[file_lock:write]
