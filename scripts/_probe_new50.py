import json, random, sys
sys.path.insert(0, r"D:/Users/grok-auto-register")
from pathlib import Path
from cpa_xai.probe import probe_models

new_files = [p for p in Path(r"D:/Users/grok-auto-register/cpa_auths").glob("xai-*@haksummer.de.json")]
print("new haksummer files:", len(new_files))
random.seed(3)
for f in random.sample(new_files, min(3, len(new_files))):
    d = json.load(open(f, encoding="utf-8"))
    try:
        r = probe_models(d["access_token"], base_url=d.get("base_url", "https://cli-chat-proxy.grok.com/v1"))
        print(f.name[:40], "=>", str(r)[:120])
    except Exception as e:
        print(f.name[:40], "=> ERR", str(e)[:120])
