"""Smoke test for the Grok Smart Router on public port 8317."""
import sys
from pathlib import Path

import httpx
import yaml

# Windows console defaults to GBK; force UTF-8 for emoji-bearing responses.
if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _grok_api_key() -> str:
    data = yaml.safe_load(Path("D:/cli-proxy-api/config.yaml").read_text(encoding="utf-8"))
    keys = data.get("api-keys") or []
    if not keys:
        raise RuntimeError("no api-keys in D:/cli-proxy-api/config.yaml")
    return str(keys[0])


def main() -> int:
    base = "http://127.0.0.1:8317"
    headers = {"Authorization": f"Bearer {_grok_api_key()}"}

    # 1. Probe /v1/models
    r1 = httpx.get(f"{base}/v1/models", headers=headers, timeout=10)
    print(f"models status={r1.status_code}")
    print(r1.text[:200])
    if r1.status_code != 200:
        return 1

    # 2. Tiny chat completion
    r2 = httpx.post(
        f"{base}/v1/chat/completions",
        headers=headers,
        json={
            "model": "grok-4.5",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        },
        timeout=60,
    )
    print(f"chat status={r2.status_code}")
    print(r2.text[:200])
    if r2.status_code not in (200, 429):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
