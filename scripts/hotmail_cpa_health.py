#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPA health snapshot for Hotmail/Outlook minted accounts.

Usage:
  python scripts/hotmail_cpa_health.py
  python scripts/hotmail_cpa_health.py --json
  python scripts/hotmail_cpa_health.py --auth-dir cpa_auths

Compares MS-mail CPA files vs own-domain CPA so mix ratio decisions
are data-driven (disabled / quota cool rates).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MS_DOMAINS = ("hotmail.com", "outlook.com", "live.com", "msn.com")


def _load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _own_domains(cfg: dict[str, Any]) -> list[str]:
    raw = str(cfg.get("defaultDomains") or cfg.get("own_domains") or "")
    parts = raw.replace(";", ",").replace(" ", ",").split(",")
    return [p.strip().lower() for p in parts if p.strip()]


def _domain_of_name(name: str) -> str:
    if "@" not in name:
        return ""
    return name.rsplit("@", 1)[-1].removesuffix(".json").lower()


def _is_ms(dom: str) -> bool:
    return any(dom == d or dom.endswith("." + d) for d in MS_DOMAINS)


def _is_own(dom: str, own: list[str]) -> bool:
    if not dom or not own:
        return False
    for o in own:
        if dom == o or dom.endswith("." + o):
            return True
    return False


def _jwt_exp(tok: str) -> float | None:
    try:
        parts = tok.split(".")
        if len(parts) < 2:
            return None
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad.encode()))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def _scan_bucket(files: list[Path], now: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "total": 0,
        "enabled": 0,
        "disabled": 0,
        "prefer_buffer_hold": 0,
        "quota_cool": 0,
        "expired_access": 0,
        "has_rt": 0,
        "hold_reasons": Counter(),
        "quota_reasons": Counter(),
        "source": Counter(),
    }
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        out["total"] += 1
        if data.get("refresh_token"):
            out["has_rt"] += 1
        dis = bool(data.get("disabled"))
        if dis:
            out["disabled"] += 1
        else:
            out["enabled"] += 1
        hr = str(data.get("hold_reason") or "")
        if hr:
            out["hold_reasons"][hr] += 1
        if hr == "prefer_buffer":
            out["prefer_buffer_hold"] += 1
        qs = data.get("quota_state") if isinstance(data.get("quota_state"), dict) else {}
        reason = str(qs.get("reason") or "")
        if reason:
            out["quota_reasons"][reason] += 1
        recover = qs.get("recover_after")
        is_quota = False
        if reason and reason != "prefer_buffer":
            is_quota = True
        if recover is not None and hr != "prefer_buffer":
            is_quota = True
        if is_quota and dis:
            out["quota_cool"] += 1
        exp = _jwt_exp(str(data.get("access_token") or ""))
        if exp is not None and exp < now:
            out["expired_access"] += 1
        src = str(data.get("source") or data.get("pool_tier") or "unknown")
        out["source"][src] += 1
    t = out["total"] or 1
    out["disabled_rate"] = round(out["disabled"] / t, 4) if out["total"] else 0.0
    out["quota_cool_rate"] = round(out["quota_cool"] / t, 4) if out["total"] else 0.0
    out["hold_reasons"] = dict(out["hold_reasons"])
    out["quota_reasons"] = dict(out["quota_reasons"])
    out["source"] = dict(out["source"])
    return out


def analyze(auth_dir: Path, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or _load_cfg()
    own = _own_domains(cfg)
    now = time.time()
    ms_files: list[Path] = []
    own_files: list[Path] = []
    other_files: list[Path] = []
    if auth_dir.is_dir():
        for path in auth_dir.glob("xai-*.json"):
            dom = _domain_of_name(path.name)
            if _is_ms(dom):
                ms_files.append(path)
            elif _is_own(dom, own):
                own_files.append(path)
            else:
                other_files.append(path)
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "auth_dir": str(auth_dir),
        "mix_ratio": cfg.get("email_mix_hotmail_ratio"),
        "mix_enabled": cfg.get("email_mix_hotmail"),
        "hotmail": _scan_bucket(ms_files, now),
        "own": _scan_bucket(own_files, now),
        "other_buffer": _scan_bucket(other_files, now),
        "advice": "",
    }
    hm = report["hotmail"]
    ow = report["own"]
    # simple advice
    if hm["total"] == 0:
        report["advice"] = "no hotmail CPA yet — keep mix low until sample grows"
    elif hm["total"] < 20:
        report["advice"] = (
            f"hotmail sample small (n={hm['total']}); treat rates as noisy; "
            f"mix_ratio={cfg.get('email_mix_hotmail_ratio')}"
        )
    else:
        # compare real cool (not soft-hold): quota_cool_rate
        if hm["quota_cool_rate"] > max(0.15, (ow["quota_cool_rate"] or 0) + 0.1):
            report["advice"] = (
                "hotmail quota_cool_rate elevated vs own — consider ratio 0.2–0.3 "
                "or temporary email_mix_hotmail=false"
            )
        elif hm["disabled_rate"] > 0.4 and hm["prefer_buffer_hold"] < hm["disabled"] * 0.5:
            report["advice"] = "hotmail disabled high (not mostly soft-hold) — reduce mix ratio"
        else:
            report["advice"] = "hotmail rates ok for now — keep mix 0.3–0.4 and recheck later"
    return report


def _print_human(r: dict[str, Any]) -> None:
    print(f"[*] ts={r['ts']}")
    print(f"[*] auth_dir={r['auth_dir']}")
    print(f"[*] mix_enabled={r.get('mix_enabled')} ratio={r.get('mix_ratio')}")
    for key, label in (
        ("hotmail", "Hotmail/Outlook CPA"),
        ("own", "Own-domain CPA"),
        ("other_buffer", "Other buffer CPA"),
    ):
        b = r[key]
        print(f"--- {label} ---")
        print(
            f"  total={b['total']} enabled={b['enabled']} disabled={b['disabled']} "
            f"disabled_rate={b['disabled_rate']}"
        )
        print(
            f"  quota_cool={b['quota_cool']} rate={b['quota_cool_rate']} "
            f"prefer_buffer_hold={b['prefer_buffer_hold']} expired_access={b['expired_access']}"
        )
        if b.get("quota_reasons"):
            print(f"  quota_reasons={b['quota_reasons']}")
        if b.get("hold_reasons"):
            print(f"  hold_reasons={b['hold_reasons']}")
    print(f"[*] advice: {r.get('advice')}")


def main(argv: list[str] | None = None) -> int:
    try:
        import stdio_utf8  # noqa: F401
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Hotmail CPA health vs own domains")
    ap.add_argument("--auth-dir", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = _load_cfg()
    ad = Path(args.auth_dir) if args.auth_dir else Path(str(cfg.get("cpa_auth_dir") or "cpa_auths"))
    if not ad.is_absolute():
        ad = (ROOT / ad).resolve()
    report = analyze(ad, cfg)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
