"""Subprocess helper: refresh one xAI OAuth token and print JSON result.

Called by purge_dead_pool() when in-process import of oauth_device fails
(e.g. _socket unavailable). Runs in a clean Python process that has full
stdlib access.

Usage: python _refresh_token.py <refresh_token> [proxy_url]
Output (stdout): JSON {"ok":true,"access_token":...} or {"ok":false,"error":...}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

def main() -> None:
    if len(sys.argv) < 2:
        json.dump({"ok": False, "error": "usage: _refresh_token.py <rt> [proxy]"}, sys.stdout)
        return

    rt = sys.argv[1]
    proxy = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        from cpa_xai.oauth_device import refresh_access_token, OAuthDeviceError
    except Exception as e:
        json.dump({"ok": False, "error": f"import: {e}"}, sys.stdout)
        return

    try:
        result = refresh_access_token(rt, proxy=proxy, timeout=15.0, retries=1)
        json.dump({
            "ok": True,
            "access_token": result.access_token,
            "refresh_token": result.refresh_token,
            "expires_in": result.expires_in,
        }, sys.stdout)
    except OAuthDeviceError as e:
        json.dump({"ok": False, "error": str(e), "dead": True}, sys.stdout)
    except Exception as e:
        json.dump({"ok": False, "error": f"{type(e).__name__}: {e}"}, sys.stdout)


if __name__ == "__main__":
    main()
