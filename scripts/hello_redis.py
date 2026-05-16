"""Round-trip XADD → XREADGROUP to prove Redis Stack + streams work."""
import redis
from palimpsest.config import REDIS_URL, STREAM, GROUP, CONSUMER

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
print("PING:", r.ping())

try:
    r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    print(f"created group {GROUP} on {STREAM}")
except redis.ResponseError as e:
    print(f"group already exists ({e})")

msg_id = r.xadd(STREAM, {"hello": "world"})
print("XADD ->", msg_id)

resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1, block=500)
print("XREADGROUP ->", resp)
for _, msgs in resp or []:
    for mid, _fields in msgs:
        r.xack(STREAM, GROUP, mid)
        print("XACK", mid)

# JSON sanity
r.json().set("wiki:concept:test", "$", {"current": "hello", "history": []})
print("JSON.GET ->", r.json().get("wiki:concept:test", "$"))
r.delete("wiki:concept:test")

# Cleanup so smoke test is repeatable
r.xtrim(STREAM, maxlen=0)
