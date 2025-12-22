import os, json, time, hashlib
from typing import Any, Optional
from analytics import log_cache_hit, log_cache_miss

CACHE_DIR = "tm_cache"
CACHE_TTL = 1800  # 30 min
os.makedirs(CACHE_DIR, exist_ok=True)

def _path(key: str): return os.path.join(CACHE_DIR, hashlib.md5(key.encode()).hexdigest() + ".json")

def get_cache(key: str) -> Optional[Any]:
    p = _path(key)
    if not os.path.exists(p):
        log_cache_miss(); return None
    try:
        d = json.load(open(p, "r", encoding="utf-8"))
        if time.time() - d["timestamp"] > CACHE_TTL:
            os.remove(p)
            log_cache_miss()
            return None
        log_cache_hit()
        return d["response"]
    except Exception:
        log_cache_miss(); return None

def set_cache(key: str, data: Any):
    json.dump({"timestamp": time.time(), "response": data}, open(_path(key), "w", encoding="utf-8"))
