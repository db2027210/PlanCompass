#!/usr/bin/env python3
"""Unit tests for external-baseline exception cleanup without a database."""

from __future__ import annotations

from typing import Any, Dict

from shell.run_pg_none_external_baseline import fail_closed_after_exception


class FakeHarness:
    def __init__(self, cleanup_error: bool = False) -> None:
        self.state: Dict[str, Any] = {"phase": "search"}
        self.cleanup_error = cleanup_error
        self.restore_reason = ""
        self.save_count = 0

    def restore_clean_baseline(self, *, reason: str) -> None:
        self.restore_reason = reason
        if self.cleanup_error:
            raise RuntimeError("simulated cleanup failure")
        self.state["clean_restore_receipt"] = {"passed": True}

    def save(self) -> None:
        self.save_count += 1


def main() -> None:
    clean = FakeHarness()
    fail_closed_after_exception(clean, ValueError("simulated search failure"))
    assert clean.state["phase"] == "failed_closed_clean"
    assert clean.state["last_error"]["type"] == "ValueError"
    assert clean.state["last_error"]["message"] == "simulated search failure"
    assert clean.restore_reason == "external_baseline_exception"
    assert clean.save_count == 1

    cleanup_failed = FakeHarness(cleanup_error=True)
    fail_closed_after_exception(cleanup_failed, ValueError("original failure"))
    assert cleanup_failed.state["phase"] == "failed_closed_cleanup_error"
    assert cleanup_failed.state["last_error"]["message"] == "original failure"
    assert cleanup_failed.state["cleanup_error"]["type"] == "RuntimeError"
    assert cleanup_failed.state["cleanup_error"]["message"] == "simulated cleanup failure"
    assert cleanup_failed.save_count == 1
    print("external-baseline fail-closed cleanup tests passed", flush=True)


if __name__ == "__main__":
    main()
