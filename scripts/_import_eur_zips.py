import zipfile, json
from pathlib import Path

dst = Path(r"D:/Users/grok-auto-register/chatgpt_auths")
existing = {p.stem.lower() for p in dst.glob("*.json")}
before = len(existing)
imported, dup, bad = 0, 0, []
for z in ["21", "22", "23", "24"]:
    zp = Path(f"D:/Downloads/{z}.zip")
    with zipfile.ZipFile(zp) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            try:
                d = json.loads(zf.read(name).decode("utf-8"))
                email = (d.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    bad.append((z, name, "no-email")); continue
                if not d.get("refresh_token"):
                    bad.append((z, name, "no-rt")); continue
                if email in existing:
                    dup += 1; continue
                d["source_zip"] = f"{z}.zip"
                (dst / f"{email}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                existing.add(email)
                imported += 1
            except Exception as e:
                bad.append((z, name, str(e)[:60]))
print(f"imported={imported} dup={dup} bad={len(bad)} total={before}+{imported}={len(existing)}")
for b in bad[:10]:
    print(" BAD:", b)
