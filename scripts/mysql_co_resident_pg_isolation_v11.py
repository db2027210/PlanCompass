#!/usr/bin/env python3
"""Install v62 isolation with complete bounded Docker readback retries.

The predecessor retried only when *zero* inspect documents were returned.  A
busy Docker daemon can return a strict subset, which made one transient partial
read look like a changed/missing sealed container.  This successor retries only
the missing names, remains read-only, and still fails closed after three total
attempts.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v10 as predecessor
from scripts import mysql_co_resident_pg_isolation_v4 as implementation


CONTRACT_PATH = predecessor.CONTRACT_PATH
MAXIMUM_READBACK_ATTEMPTS = 3
RETRY_SECONDS = 0.5
ORIGINAL_DOCUMENTS_ONCE = implementation._docker_documents_once


def docker_documents_complete_retry(
    names: list[str],
) -> tuple[dict[str, dict[str, Any]], str]:
    requested = list(dict.fromkeys(names))
    documents: dict[str, dict[str, Any]] = {}
    last_errors: dict[str, str] = {}
    missing = list(requested)
    for attempt in range(1, MAXIMUM_READBACK_ATTEMPTS + 1):
        observed, error = ORIGINAL_DOCUMENTS_ONCE(missing)
        documents.update(observed)
        if error:
            for line in error.splitlines():
                if line:
                    last_errors[line.rsplit(":", 1)[-1]] = line
        missing = [name for name in requested if name not in documents]
        if not missing:
            return documents, ""
        if attempt < MAXIMUM_READBACK_ATTEMPTS:
            time.sleep(RETRY_SECONDS)
    return documents, "\n".join(
        last_errors.get(name, f"docker_api_read_incomplete:{name}")
        for name in missing
    )


def install() -> None:
    predecessor.install()
    implementation.CONTRACT_PATH = CONTRACT_PATH
    implementation._docker_documents = docker_documents_complete_retry


def validate_release() -> None:
    global ORIGINAL_DOCUMENTS_ONCE, RETRY_SECONDS
    original = ORIGINAL_DOCUMENTS_ONCE
    original_retry = RETRY_SECONDS
    calls: list[list[str]] = []

    def partial(names: list[str]):
        calls.append(list(names))
        if len(calls) == 1:
            return {"a": {"Id": "a"}}, "docker_api_read_failed:b"
        return {"b": {"Id": "b"}}, ""

    try:
        ORIGINAL_DOCUMENTS_ONCE = partial
        RETRY_SECONDS = 0.0
        documents, error = docker_documents_complete_retry(["a", "b"])
        if set(documents) != {"a", "b"} or error or calls != [["a", "b"], ["b"]]:
            raise RuntimeError("partial Docker readback retry test failed")

        calls.clear()

        def absent(names: list[str]):
            calls.append(list(names))
            return {}, "docker_api_read_failed:b"

        ORIGINAL_DOCUMENTS_ONCE = absent
        documents, error = docker_documents_complete_retry(["b"])
        if documents or "docker_api_read_failed:b" not in error or len(calls) != 3:
            raise RuntimeError("bounded Docker readback fail-closed test failed")
    finally:
        ORIGINAL_DOCUMENTS_ONCE = original
        RETRY_SECONDS = original_retry


if __name__ == "__main__":
    validate_release()
