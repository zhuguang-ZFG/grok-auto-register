"""Durable, cross-process-safe persistence for registered accounts."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable, Iterable

from filelock import FileLock

_DELIMITER = "----"
_PENDING_SUFFIX = ".pending.jsonl"


def _absolute_path(path: os.PathLike[str] | str) -> str:
    return os.path.abspath(os.fspath(path))


def _same_path(left: os.PathLike[str] | str, right: os.PathLike[str] | str) -> bool:
    return os.path.normcase(_absolute_path(left)) == os.path.normcase(_absolute_path(right))


def _lock_path(path: os.PathLike[str] | str) -> str:
    return f"{_absolute_path(path)}.lock"


def _ensure_parent(path: os.PathLike[str] | str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _read_text(path: os.PathLike[str] | str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _account_key(line: str) -> tuple[str, str] | None:
    parts = line.rstrip("\r\n").split(_DELIMITER, 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[2]


def _append_account_line_unlocked(
    path: os.PathLike[str] | str,
    email: str,
    password: str,
    sso: str,
) -> bool:
    wanted = (email, sso)
    for line in _read_text(path).splitlines():
        if _account_key(line) == wanted:
            return False

    _ensure_parent(path)
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(f"{email}{_DELIMITER}{password}{_DELIMITER}{sso}\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def append_account_line(
    path: os.PathLike[str] | str,
    email: str,
    password: str,
    sso: str,
) -> bool:
    """Append one account unless the same ``(email, sso)`` already exists."""

    _ensure_parent(path)
    with FileLock(_lock_path(path), timeout=30):
        return _append_account_line_unlocked(path, email, password, sso)


def queue_unsaved_account(
    path: os.PathLike[str] | str,
    payload: dict[str, Any],
    error: object,
) -> str:
    """Append a failed account save to ``<path>.pending.jsonl`` durably."""

    output_path = os.fspath(path)
    pending_path = f"{output_path}{_PENDING_SUFFIX}"
    record = dict(payload)
    record["output_path"] = output_path
    record["error"] = str(error)
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    _ensure_parent(pending_path)
    with FileLock(_lock_path(pending_path), timeout=30):
        with open(pending_path, "a", encoding="utf-8", newline="") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return pending_path


def _valid_pending_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(value.get(key), str) for key in ("email", "password", "sso"))


def _parse_pending_line(raw_line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw_line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if _valid_pending_record(value) else None


def _targets_from_pending(
    pending_path: os.PathLike[str] | str,
) -> set[str]:
    """Read target paths while holding the pending lock for discovery."""

    with FileLock(_lock_path(pending_path), timeout=30):
        lines = _read_text(pending_path).splitlines(keepends=True)
    targets: set[str] = set()
    for raw_line in lines:
        record = _parse_pending_line(raw_line)
        if record and isinstance(record.get("output_path"), str):
            targets.add(_absolute_path(record["output_path"]))
    return targets


def _acquire_sorted_locks(paths: Iterable[os.PathLike[str] | str]) -> ExitStack:
    stack = ExitStack()
    lock_paths = sorted({_lock_path(path) for path in paths}, key=os.path.normcase)
    try:
        for path in lock_paths:
            stack.enter_context(FileLock(path, timeout=30))
    except BaseException:
        stack.close()
        raise
    return stack


def _rewrite_pending_atomic(path: os.PathLike[str] | str, lines: list[str]) -> None:
    _ensure_parent(path)
    parent = os.path.dirname(_absolute_path(path))
    fd, temporary_path = tempfile.mkstemp(prefix=f".{Path(path).name}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _log(log_callback: Callable[[str], Any] | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def retry_pending_file(
    pending_path: os.PathLike[str] | str,
    output_path: os.PathLike[str] | str | None = None,
    log_callback: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Restore valid pending records and atomically retain unrecoverable lines."""

    if output_path is not None and _same_path(pending_path, output_path):
        raise ValueError("pending_path and output_path must be different files")

    pending_absolute = _absolute_path(pending_path)
    if output_path is not None:
        target_paths = {_absolute_path(output_path)}
    else:
        target_paths = _targets_from_pending(pending_absolute)

    restored = 0
    retained: list[str] = []
    with _acquire_sorted_locks([pending_absolute, *target_paths]):
        raw_lines = _read_text(pending_absolute).splitlines(keepends=True)
        for raw_line in raw_lines:
            record = _parse_pending_line(raw_line)
            if record is None:
                retained.append(raw_line)
                _log(log_callback, "Retained malformed pending line")
                continue

            target = _absolute_path(output_path) if output_path is not None else record.get("output_path")
            if not isinstance(target, str) or _absolute_path(target) not in target_paths:
                retained.append(raw_line)
                _log(log_callback, "Retained pending record without a locked output path")
                continue

            try:
                _append_account_line_unlocked(
                    target,
                    record["email"],
                    record["password"],
                    record["sso"],
                )
            except Exception as exc:
                retained.append(raw_line)
                _log(log_callback, f"Retained pending record after save failure: {exc}")
            else:
                restored += 1

        _rewrite_pending_atomic(pending_absolute, retained)

    return {
        "restored": restored,
        "remaining": len(retained),
        "output": os.fspath(output_path) if output_path is not None else None,
    }
