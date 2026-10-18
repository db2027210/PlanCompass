#!/usr/bin/env python3
"""Offline tests for the DeepSeek credential/model preflight."""

from __future__ import annotations

import io
import json

from verify_deepseek_api_access import model_endpoint, verify_access


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class FakeOpener:
    def __init__(self) -> None:
        self.authorization = ""

    def open(self, request: object, timeout: int) -> FakeResponse:
        assert timeout == 20
        self.authorization = request.get_header("Authorization")
        payload = {
            "object": "list",
            "data": [
                {"id": "deepseek-v4-flash"},
                {"id": "deepseek-v4-pro"},
            ],
        }
        return FakeResponse(json.dumps(payload).encode())


def test_official_endpoint_and_model_gate() -> None:
    assert model_endpoint("https://api.deepseek.com/") == (
        "https://api.deepseek.com/models"
    )
    opener = FakeOpener()
    result = verify_access(
        "test-secret-not-a-real-key",
        "https://api.deepseek.com",
        "deepseek-v4-pro",
        opener=opener,
    )
    assert result["status"] == "PASS"
    assert result["model_available"] is True
    assert result["key_sha256_12"]
    assert opener.authorization == "Bearer test-secret-not-a-real-key"


def test_non_official_endpoint_is_rejected() -> None:
    try:
        model_endpoint("https://example.invalid/v1")
    except ValueError as exc:
        assert "official API endpoint" in str(exc)
    else:
        raise AssertionError("non-official DeepSeek endpoint was accepted")


if __name__ == "__main__":
    test_official_endpoint_and_model_gate()
    test_non_official_endpoint_is_rejected()
    print("PASS: DeepSeek API access preflight", flush=True)
