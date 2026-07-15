#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recon: open grok.com with sso cookie, send a message, capture birthday modal.

Usage: python scripts/_birthday_browser_probe.py <email>
Reads sso from accounts_*.txt. Screenshots -> screenshots/bday_*.png
"""
import glob
import json
import os
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
        shot(f"bday_{email.split('@')[0]}_1loaded.png")
        print("title:", page.title)
        html = page.html or ""
        print("html len:", len(html),
              "| has birthday:", "birthday" in html.lower() or "birth" in html.lower(),
              "| has 出生:", "出生" in html)

        # try to send a message to trigger the modal
        ta = page.ele("tag:textarea", timeout=8)
        if ta:
            ta.input("hi")
            time.sleep(1)
            ta.input("\n")
            print("sent 'hi'")
        else:
            print("no textarea found")
        time.sleep(12)
        shot(f"bday_{email.split('@')[0]}_2after.png")
        html2 = page.html or ""
        low = html2.lower()
        print("after: birthday" in low, "birth" in low, "出生" in html2)
        # dump dialog-ish elements
        for sel in ("tag:dialog", "css:[role=dialog]", "css:[role=listbox]",
                    "css:[data-radix-popper-content-wrapper]"):
            try:
                els = page.eles(sel)
            except Exception:
                els = []
            for el in els[:3]:
                try:
                    txt = (el.text or "")[:300].replace("\n", " | ")
                    print(f"ELEM {sel}: visible={el.states.is_displayed} text={txt}")
                    print("HTML:", (el.html or "")[:1500])
                except Exception as e:
                    print("elem err:", e)
        # also dump any select elements
        for el in page.eles("tag:select")[:5]:
            print("SELECT:", (el.html or "")[:400])
        for el in page.eles("tag:button")[:40]:
            t = (el.text or "").strip()
            if t and any(k in t.lower() for k in ("year", "month", "day", "年", "月", "日", "confirm", "save", "ok")):
                print("BUTTON:", t[:80])
        shot(f"bday_{email.split('@')[0]}_3final.png")
    finally:
        try:
            browser.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
