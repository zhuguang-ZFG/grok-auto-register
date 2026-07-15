import json, sys, time, urllib.request

port = int(sys.argv[1])
out_file = sys.argv[2]
prompt_file = sys.argv[3]
token = open(r"C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/data/a2a_token", encoding="utf-8").read().strip()
prompt = open(prompt_file, encoding="utf-8").read()

body = {"jsonrpc": "2.0", "id": "send", "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": prompt}]}}}
req = urllib.request.Request(f"http://127.0.0.1:{port}/",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
d = json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8"))
task = d.get("result", {})
tid = task.get("id")
print("task_id:", tid, "| initial state:", task.get("status", {}).get("state"))
sys.stdout.flush()

for i in range(240):
    time.sleep(15)
    body = {"jsonrpc": "2.0", "id": "poll", "method": "tasks/get", "params": {"id": tid}}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    except Exception:
        continue
    task = d.get("result", {})
    state = task.get("status", {}).get("state")
    if state in ("completed", "failed", "canceled"):
        msg = task.get("status", {}).get("message", {})
        out = "\n".join(p.get("text", "") for p in msg.get("parts", []) if p.get("text"))
        open(out_file, "w", encoding="utf-8").write("state: " + state + "\n\n" + out)
        print("FINAL:", state, "| chars:", len(out))
        break
else:
    print("TIMEOUT")
