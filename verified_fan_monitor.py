import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

logger = logging.getLogger("verified_fan")

STATE_FILE = os.path.join(os.path.dirname(__file__), "viking_state.json")
STATE_LOCK = threading.Lock()

DEFAULT_INTERVAL_SECONDS = int(os.getenv("VERIFIED_FAN_POLL_SECONDS", str(2 * 60 * 60)))  # 2 hours

_VF_THREAD: Optional[threading.Thread] = None
_VF_STOP = threading.Event()


def _load_state() -> Dict[str, Any]:
    with STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            return {}
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}


def _save_state(state: Dict[str, Any]) -> None:
    with STATE_LOCK:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, sort_keys=True)
        except Exception:
            logger.exception("Failed writing state file: %s", STATE_FILE)


def _get_seen_set(state: Dict[str, Any]) -> set:
    seen = state.get("verified_fan_seen")
    if isinstance(seen, list):
        return set(seen)
    if isinstance(seen, set):
        return seen
    return set()


def _set_seen_set(state: Dict[str, Any], seen: set) -> None:
    state["verified_fan_seen"] = sorted(seen)


def _canonicalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()

        if host.endswith("duckduckgo.com"):
            qs = parse_qs(p.query)
            for key in ("u", "u3"):
                if key in qs and qs[key]:
                    return qs[key][0]
        return url
    except Exception:
        return url


def _is_ticketmaster_domain(host: str) -> bool:
    host = (host or "").lower().strip(".")
    return host == "ticketmaster.com" or host.endswith(".ticketmaster.com")


def _vf_signal_in_text(txt: str) -> bool:
    t = (txt or "").lower()
    return ("verified fan" in t) or ("verifiedfan" in t) or ("verified-fan" in t) or ("fan registration" in t)


def _vf_signal_in_url(url: str) -> bool:
    u = (url or "").lower()
    needles = [
        "verifiedfan",
        "verified-fan",
        "verified_fan",
        "fan-registration",
        "registration",
        "verified-fan-tickets",
    ]
    return any(n in u for n in needles)


def _is_allowed_item(item: Dict[str, Any]) -> bool:
    url = _canonicalize_url(str(item.get("url") or "").strip())
    if not url:
        return False

    try:
        p = urlparse(url)
    except Exception:
        return False

    if (p.scheme or "").lower() != "https":
        return False

    host = (p.hostname or "").lower().strip(".")
    if not _is_ticketmaster_domain(host):
        return False

    title = str(item.get("event") or item.get("artist") or item.get("title") or "")
    if _vf_signal_in_text(title):
        return True

    if _vf_signal_in_url(url) or _vf_signal_in_url(p.path or ""):
        return True

    return False


def _post_item(post_func: Optional[Callable[[Dict[str, Any]], Any]], item: Dict[str, Any]) -> None:
    title = item.get("event") or item.get("artist") or item.get("title") or "Verified Fan"
    logger.info("New Verified Fan posted: %s", title)

    if callable(post_func):
        try:
            post_func(item)
        except Exception:
            logger.exception("post_func failed for Verified Fan item")


def poll_verified_fan_once(
    post_func: Optional[Callable[[Dict[str, Any]], Any]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    try:
        from ticketmaster_agent import fetch_verified_fan_programs
    except Exception:
        logger.exception("Could not import ticketmaster_agent.fetch_verified_fan_programs")
        return []

    raw = fetch_verified_fan_programs() or []
    if not isinstance(raw, list):
        return []

    items: List[Dict[str, Any]] = []
    for it in raw[: max(1, int(limit))]:
        if not isinstance(it, dict):
            continue
        it = dict(it)
        it["url"] = _canonicalize_url(str(it.get("url") or "").strip())
        if _is_allowed_item(it):
            items.append(it)

    state = _load_state()
    seen = _get_seen_set(state)

    new_items: List[Dict[str, Any]] = []
    for it in items:
        key = str(it.get("id") or it.get("url") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        new_items.append(it)
        _post_item(post_func, it)

    _set_seen_set(state, seen)
    state["verified_fan_last_run"] = int(time.time())
    _save_state(state)

    return new_items


def _poll_loop(post_func: Optional[Callable[[Dict[str, Any]], Any]], interval_seconds: int) -> None:
    logger.info("Verified Fan polling loop started (%s-second interval).", interval_seconds)

    try:
        poll_verified_fan_once(post_func=post_func)
    except Exception:
        logger.exception("Verified Fan first poll failed")

    while not _VF_STOP.is_set():
        _VF_STOP.wait(interval_seconds)
        if _VF_STOP.is_set():
            break
        try:
            poll_verified_fan_once(post_func=post_func)
        except Exception:
            logger.exception("Verified Fan poll failed")


async def poll_verified_fan_loop(*_args, **kwargs) -> None:
    global _VF_THREAD

    interval = (
        kwargs.get("interval_seconds")
        or kwargs.get("interval")
        or kwargs.get("poll_seconds")
        or DEFAULT_INTERVAL_SECONDS
    )
    try:
        interval_seconds = max(60, int(interval))
    except Exception:
        interval_seconds = DEFAULT_INTERVAL_SECONDS

    post_func = kwargs.get("post_func") or kwargs.get("callback") or kwargs.get("post_callback")

    if _VF_THREAD and _VF_THREAD.is_alive():
        return

    _VF_STOP.clear()
    _VF_THREAD = threading.Thread(
        target=_poll_loop,
        name="verified_fan_poll",
        daemon=True,
        args=(post_func, interval_seconds),
    )
    _VF_THREAD.start()

    await asyncio.sleep(0)


def stop_verified_fan_loop() -> None:
    _VF_STOP.set()

# --- Viking AI integration helpers (safe) ---
def start_polling_loop(discord_client=None, channel_id: int = 0) -> None:
    """
    Compatibility wrapper expected by bot.py.
    If this module already has its own loop/thread starter, call it here.
    If dependencies are missing, disable gracefully.
    """
    if requests is None:
        logging.getLogger("verified_fan_monitor").warning(
            "verified_fan_monitor disabled: requests not installed"
        )
        return

    # Try to call existing entrypoints if present
    for fn_name in ("start_background_thread", "start", "run"):
        fn = globals().get(fn_name)
        if callable(fn):
            try:
                return fn(discord_client, channel_id)
            except TypeError:
                # Some implementations may not accept both args
                return fn()

    logging.getLogger("verified_fan_monitor").info(
        "verified_fan_monitor present but no runnable entrypoint found"
    )
