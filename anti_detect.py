"""Anti-detection fingerprint randomization.

Borrows from community practice (lxf746/any-auto-register using Camoufox + UA
pools + proxy rotation). Since we use DrissionPage/Chromium (not Camoufox),
we randomize the Chromium-leveraged signals instead:
  - User-Agent + matching sec-ch-ua platform
  - viewport (screen resolution — Canvas/WebGL fingerprint input)
  - timezone + Accept-Language locale
  --lang flag ( navigator.language )

Each call to pick_fingerprint() returns a self-consistent bundle so UA,
platform, sec-ch-ua and Accept-Language all agree.
"""
from __future__ import annotations

import random
from dataclasses import dataclass


# Real-world UA strings grouped by platform. Keep versions current but not
# bleeding-edge (Cloudflare flags brand-new versions more).
_UA_POOL = [
    # Windows / Chrome 137-138
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
     "Windows", '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
     "Windows", '"Google Chrome";v="138", "Chromium";v="138", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
    # macOS / Chrome 137-138
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
     "macOS", '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
     "macOS", '"Google Chrome";v="138", "Chromium";v="138", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
    # Linux / Chrome 137-138
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
     "Linux", '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
     "Linux", '"Google Chrome";v="138", "Chromium";v="138", "Not/A)Brand";v="24"', "en-US,en;q=0.9"),
]

# Common real-world viewport sizes (width, height). Cloudflare uses these as
# inputs to Canvas/WebGL fingerprinting.
_VIEWPORTS = [
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1680, 1050),
    (2560, 1440),
    (1280, 800),
]

# Timezone + locale pairs. Keep them geographically consistent with the
# Accept-Language so Cloudflare's heuristics don't flag a mismatch.
_TZ_LOCALE = [
    ("America/New_York", "en-US", "en-US,en;q=0.9"),
    ("America/Los_Angeles", "en-US", "en-US,en;q=0.9"),
    ("America/Chicago", "en-US", "en-US,en;q=0.9"),
    ("Europe/London", "en-GB", "en-GB,en;q=0.9"),
    ("Europe/Berlin", "en-US", "en-US,en,de;q=0.8"),
    ("Asia/Singapore", "en-US", "en-US,en;q=0.9"),
    ("Asia/Tokyo", "en-US", "en-US,en,ja;q=0.8"),
]


@dataclass
class Fingerprint:
    user_agent: str
    platform: str
    sec_ch_ua: str
    accept_language: str
    viewport_w: int
    viewport_h: int
    timezone: str
    lang_code: str  # for Chromium --lang flag

    @property
    def window_size(self) -> str:
        return f"{self.viewport_w},{self.viewport_h}"


def pick_fingerprint() -> Fingerprint:
    """Pick a self-consistent random fingerprint bundle."""
    ua, platform, sec_ch_ua, _acc = random.choice(_UA_POOL)
    w, h = random.choice(_VIEWPORTS)
    tz, lang_code, accept_lang = random.choice(_TZ_LOCALE)
    # 70% chance the Accept-Language matches the picked locale pair (real users
    # often have en-US browsers in any timezone). Keep it natural.
    if random.random() < 0.7:
        accept_lang = _acc
    return Fingerprint(
        user_agent=ua,
        platform=platform,
        sec_ch_ua=sec_ch_ua,
        accept_language=accept_lang,
        viewport_w=w,
        viewport_h=h,
        timezone=tz,
        lang_code=lang_code,
    )


# Chromium flag mapping for common timezones (TZ env var or --timezone flag)
TZ_OFFSETS = {
    "America/New_York": "-05:00",
    "America/Los_Angeles": "-08:00",
    "America/Chicago": "-06:00",
    "Europe/London": "+00:00",
    "Europe/Berlin": "+01:00",
    "Asia/Singapore": "+08:00",
    "Asia/Tokyo": "+09:00",
}
