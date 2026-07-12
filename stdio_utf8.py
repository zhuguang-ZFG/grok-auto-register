#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Force UTF-8 stdio on Windows so Chinese logs are not mojibake in file redirects."""
from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to utf-8 with backslashreplace when possible."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


# Apply on import for scripts that only import this module.
ensure_utf8_stdio()
