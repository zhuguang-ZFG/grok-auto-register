"""Guard against refresh-token rotation races among concurrent refreshers.

Background (why this module exists — do NOT remove):
  xAI rotates the refresh_token on EVERY successful refresh: the old RT is
  consumed the instant a refresh succeeds. This pool has several independent
  refreshers running as separate scheduled tasks — cpa_keepalive, quota_watch,
  pool_health, hard_purge, refresh_pool, local_grok_auth. When two of them hit
  the SAME account at the same time, only one wins; the loser reads a stale RT,
  the server answers `invalid_grant`, and — if the loser does not re-check — it
  falsely marks the account dead / moves it to cpa_auths_dead. This once wiped
  thousands of healthy accounts.

Rule for every refresher: on `invalid_grant`, RE-READ the file. If the RT on
disk now differs from the one you just failed with, another process already
rotated it -> the account is ALIVE; do NOT disable or move it. Only treat the
account as dead when the on-disk RT still equals what you tried (truly
revoked), or when there is genuinely no RT on disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def rt_rotated_by_other(path: PathLike, tried_rt: str) -> bool:
    """Return True when the account should NOT be killed (rotated/alive).

    Re-reads the auth file and compares its current refresh_token against the
    one that just produced invalid_grant.

      - file missing            -> another process already moved/handled it -> True
      - file unreadable         -> be conservative, do not kill            -> True
      - on-disk RT != tried RT  -> someone rotated it (account is alive)   -> True
      - on-disk RT == tried RT  -> genuinely revoked, safe to disable      -> False
    """
    tried = (tried_rt or "").strip()
    try:
        cur = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001  unreadable -> conservative
        return True
    cur_rt = str(cur.get("refresh_token") or "").strip()
    return cur_rt != tried
