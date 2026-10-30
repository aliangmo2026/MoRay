# L4.5 E2E helper: poll a /api/tasks/{id} until terminal, print final view.
import json, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TID = sys.argv[2]
LIMIT = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
t0 = time.time()
last = None
while time.time() - t0 < LIMIT:
    try:
        with urllib.request.urlopen(BASE + "/api/tasks/" + TID, timeout=10) as r:
            d = json.load(r)["data"]
    except Exception as e:
        print("poll-error:", e)
        time.sleep(5)
        continue
    sig = (d["status"], d.get("current_stage"))
    if sig != last:
        print("%.0fs %s stage=%s steps=%s" % (time.time() - t0, d["status"], d.get("current_stage"),
              json.dumps([{k: s.get(k) for k in ("id", "status", "engine")} for s in d["steps"]],
                         ensure_ascii=False)))
        last = sig
    if d["status"] != "running":
        print("FINAL " + json.dumps({k: d.get(k) for k in
              ("status", "tokens_spent", "duration_sec", "human_intervention", "error",
               "report")}, ensure_ascii=False)[:3000])
        sys.exit(0 if d["status"] == "completed" else 2)
    time.sleep(4)
print("TIMEOUT waiting")
sys.exit(3)
