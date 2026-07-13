#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate disposable signup identity fields."""

from __future__ import annotations

import random
import secrets
import string
from typing import Dict


_FIRST = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn",
    "Sam", "Jamie", "Cameron", "Drew", "Blake", "Reese", "Skyler",
]
_LAST = [
    "Smith", "Johnson", "Lee", "Brown", "Garcia", "Martin", "Clark", "Lewis",
    "Walker", "Young", "King", "Wright", "Lopez", "Hill", "Green",
]


def random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # ensure complexity
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    chars += [secrets.choice(alphabet) for _ in range(max(0, length - 4))]
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


def make_identity() -> Dict[str, str]:
    first = random.choice(_FIRST)
    last = random.choice(_LAST)
    return {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}",
        "company": f"{last} Labs",
        "title": "Engineer",
        "password": random_password(18),
    }
