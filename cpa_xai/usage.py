"""Per-account quota tracking for smarter pool rotation.

Each CPA file gets a 'quota_state' section tracking:
- tokens_used: cumulative tokens used (from 429 error context)
- exhausted_at: when 429 was last hit for this account
- recover_after: estimated time when quota resets (24h rolling window)

quota_watch checks this before selecting a pool candidate.
"""
import json
import os
import time
from pathlib import Path

FREE_TIER_LIMIT = 2_000_000  # tokens per rolling 24h window
RESET_WINDOW_SEC = 24 * 3600  # 24 hours


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: write to .tmp then os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def mark_account_exhausted(
    cpa_file: Path,
    *,
    tokens_used: int | None = None,
    log=None,
) -> None:
    """Mark a CPA account as quota-exhausted (called when 429 hits it)."""
    if not cpa_file.is_file():
        return
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.setdefault("quota_state", {})
        now = time.time()
        qs["exhausted_at"] = now
        qs["recover_after"] = now + RESET_WINDOW_SEC
        if tokens_used:
            qs["tokens_used"] = tokens_used
        qs["limit"] = FREE_TIER_LIMIT
        _atomic_write_json(cpa_file, data)
        if log:
            email = data.get("email", cpa_file.name)
            log(f"[quota] marked {email} exhausted (recovers in ~24h)")
    except Exception:
        pass


def is_account_recovered(cpa_file: Path) -> bool:
    """Check if a previously-exhausted account has recovered its quota."""
    if not cpa_file.is_file():
        return True
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.get("quota_state", {})
        recover_after = qs.get("recover_after") or 0
        if not recover_after:
            return True  # never exhausted
        return time.time() >= recover_after
    except Exception:
        return True


def recover_in_sec(cpa_file: Path) -> int:
    """Seconds until this account recovers. 0 if already recovered."""
    if not cpa_file.is_file():
        return 0
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        qs = data.get("quota_state", {})
        recover_after = qs.get("recover_after") or 0
        if not recover_after:
            return 0
        remain = int(recover_after - time.time())
        return max(0, remain)
    except Exception:
        return 0


def clear_exhausted_mark(cpa_file: Path) -> None:
    """Clear quota_state when an account is successfully used again."""
    if not cpa_file.is_file():
        return
    try:
        data = json.loads(cpa_file.read_text(encoding="utf-8"))
        if "quota_state" in data:
            del data["quota_state"]
            _atomic_write_json(cpa_file, data)
    except Exception:
        pass
