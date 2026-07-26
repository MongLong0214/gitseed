import json, sys, time
def log(level, msg, **fields):
    rec = {"t": time.time(), "level": level, "msg": msg, **fields}
    sys.stdout.write(json.dumps(rec) + "\n")
