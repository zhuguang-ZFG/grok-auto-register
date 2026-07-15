#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step2: click through grok.com gates (cookie consent -> TOS wall -> birthday
modal) with sso cookie, send a message, capture/answer birthday dialog.

Usage: python scripts/_birthday_browser_step2.py <email>
"""
import glob
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = "http://127.0.0.1:7897"


def find_sso(email: str) -> str:
    for f in glob.glob(os.path.join(ROOT, "accounts_*.txt")):
        for line in open(f, encoding="utf-8", errors="ignore"):
            parts = line.strip().split("----")
            if len(parts) >= 3 and parts[0].strip().lower() == email.lower():
                return parts[2].strip()
    return ""


def click_by_text(page, patterns, tag="tag:button", timeout=4):
    for el in page.eles(tag):
        try:
            t = (el.text or "").strip()
        except Exception:
            continue
        if not t:
            continue
        for pat in patterns:
            if re.search(pat, t, re.I):
                el.click()
                return t
    return ""


def main() -> int:
    email = sys.argv[1]
    sso = find_sso(email)
    if not sso:
        print("no sso for", email)
        return 1
    from DrissionPage import Chromium, ChromiumOptions

    opts = ChromiumOptions()
    opts.set_timeouts(base=2, page_load=45)
    opts.auto_port()
    opts.set_argument("--no-first-run")
    opts.set_argument("--no-default-browser-check")
    opts.set_argument("--disable-blink-features=AutomationControlled")
    opts.set_argument("--window-position=-32000,-32000")
    opts.set_proxy(PROXY)
    browser = Chromium(opts)
    page = browser.latest_tab
    stem = email.split("@")[0]
    shot = lambda name: page.get_screenshot(
        path=os.path.join(ROOT, "screenshots"), name=name, full_page=False)
    try:
        page.get("https://grok.com/")
        time.sleep(6)
        page.set.cookies([
            {"name": "sso", "value": sso, "domain": ".grok.com", "path": "/"},
            {"name": "sso-rw", "value": sso, "domain": ".grok.com", "path": "/"},
        ])
        page.refresh()
        time.sleep(10)

        # 1) cookie consent (OneTrust) — accept all
        t = click_by_text(page, [r"accept all", r"allow all", r"同意", r"接受", r"允许全部"])
        print("cookie consent:", t or "none")
        time.sleep(2)
        # 2) TOS wall
        t = click_by_text(page, [r"知道了", r"got it", r"^ok$", r"understand"])
        print("tos wall:", t or "none")
        time.sleep(3)
        shot(f"bday2_{stem}_1walls.png")

        # 3) send a message
        ta = page.ele("tag:textarea", timeout=8)
        if ta:
            ta.input("hi")
            time.sleep(1)
            ta.input("\n")
            print("sent hi")
        else:
            print("no textarea")
        time.sleep(12)
        shot(f"bday2_{stem}_2sent.png")

        # 4) capture dialogs
        html = page.html or ""
        print("birth in html:", "birth" in html.lower())
        for sel in ("css:[role=dialog]", "css:[role=listbox]", "css:[role=combobox]"):
            for el in page.eles(sel)[:4]:
                try:
                    txt = (el.text or "")[:200].replace("\n", " | ")
                    print(f"DIALOG {sel} visible={el.states.is_displayed}: {txt}")
                except Exception:
                    pass
        # dump all buttons with short text
        btns = []
        for el in page.eles("tag:button"):
            try:
                t = (el.text or "").strip()
                if t and len(t) < 40:
                    btns.append(t)
            except Exception:
                pass
        print("buttons:", btns[:50])
        # dump selects
        for el in page.eles("tag:select")[:5]:
            print("SELECT html:", (el.html or "")[:300])
        shot(f"bday2_{stem}_3dialog.png")
    finally:
        try:
            browser.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
