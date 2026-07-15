import json, glob
f = sorted(glob.glob(r"D:/Users/grok-auto-register/cpa_auths/xai-*.json"))[0]
ref = json.load(open(f, encoding="utf-8"))
for k, v in ref.items():
    if k in ("access_token", "refresh_token", "id_token"):
        print(k, "=", str(v)[:18] + "...")
    else:
        print(k, "=", v)
