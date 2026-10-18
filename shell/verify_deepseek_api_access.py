#!/usr/bin/env python3
"""Fail-fast, zero-generation-token DeepSeek API credential preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


OFFICIAL_BASE_URLS = {
    "https://api.deepseek.com",
    "https://api.deepseek.com/v1",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    return parser.parse_args()


def safe_error_message(payload: bytes) -> str:
    try:
        value = json.loads(payload.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return "non-JSON API error"
    if not isinstance(value, dict):
        return "unexpected API error"
    error = value.get("error")
    if not isinstance(error, dict):
        return "unexpected API error"
    return str(error.get("message") or "API request failed")[:200]


def model_endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized not in OFFICIAL_BASE_URLS:
        raise ValueError("DeepSeek preflight requires the official API endpoint")
    return f"{normalized}/models"


def verify_access(
    api_key: str,
    base_url: str,
    model: str,
    *,
    opener: Any = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        model_endpoint(base_url),
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    direct_opener = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({})
    )
    with direct_opener.open(request, timeout=20) as response:
        payload = json.load(response)
        model_ids = sorted(
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        )
        if model not in model_ids:
            raise RuntimeError(f"required model is unavailable: {model}")
        return {
            "contract": "deepseek_api_access_preflight_v1",
            "http_status": int(response.status),
            "key_length": len(api_key),
            "key_sha256_12": hashlib.sha256(api_key.encode()).hexdigest()[:12],
            "model": model,
            "model_available": True,
            "status": "PASS",
        }


def main() -> int:
    args = parse_args()
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"missing API credential in {args.api_key_env}")
    try:
        result = verify_access(api_key, args.base_url, args.model)
    except urllib.error.HTTPError as exc:
        result = {
            "contract": "deepseek_api_access_preflight_v1",
            "error": safe_error_message(exc.read()),
            "http_status": exc.code,
            "key_length": len(api_key),
            "key_sha256_12": hashlib.sha256(api_key.encode()).hexdigest()[:12],
            "model": args.model,
            "model_available": False,
            "status": "FAIL",
        }
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr, flush=True)
        return 3
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
