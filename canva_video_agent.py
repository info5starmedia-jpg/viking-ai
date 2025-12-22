# canva_video_agent.py

"""
Viking AI – Text-to-video helper (Runway-based engine).

Reality (based on current docs):
- Runway provides a documented REST API for video generation via /image_to_video.
- It expects BOTH:
    - promptImage  (URL or data URI of a seed image)
    - promptText   (your text prompt)
- Canva's AI video feature is not exposed as a simple backend text-to-video API.
  We can still later upload the generated video into Canva via Connect APIs.

Design of this module:
- Use Runway's /image_to_video endpoint to generate the video.
- Optionally, notify a Canva-related webhook if you configure it (future step).
- Expose a single function used by bot.py:

    create_video_with_fallback(prompt: str) -> dict

Return on success:
  {
    "ok": True,
    "provider": "runway",          # or "runway+canva" in the future
    "url": "https://video.mp4"
  }

Return on failure:
  {
    "ok": False,
    "provider": "runway" or None,
    "error": "explanation"
  }
"""

import os
import time
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# Runway API config
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY")
RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"  # from official docs

# Optional Canva import webhook (NOT required for basic video to work)
CANVA_ASSET_UPLOAD_URL = os.getenv("CANVA_ASSET_UPLOAD_URL")  # optional
CANVA_ASSET_UPLOAD_TOKEN = os.getenv("CANVA_ASSET_UPLOAD_TOKEN")  # optional

# A neutral seed image for image_to_video (you can change this later)
# This is the same kind of public example they use in the docs.
RUNWAY_SEED_IMAGE = (
    "https://upload.wikimedia.org/wikipedia/commons/8/85/"
    "Tour_Eiffel_Wikimedia_Commons_(cropped).jpg"
)


# -------------------------------------------------------------------
# Generic helpers
# -------------------------------------------------------------------

def _runway_headers() -> Dict[str, str]:
    if not RUNWAY_API_KEY:
        return {
            "Content-Type": "application/json",
            "X-Runway-Version": RUNWAY_VERSION,
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RUNWAY_API_KEY}",
        "X-Runway-Version": RUNWAY_VERSION,
    }


def _runway_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST JSON to Runway API and return the JSON response as dict.
    Raises HTTPError on bad status codes.
    """
    url = RUNWAY_API_BASE + path
    resp = requests.post(url, headers=_runway_headers(), json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return {}
    return data


def _runway_get(path: str) -> Dict[str, Any]:
    """
    GET JSON from Runway API and return the JSON response as dict.
    """
    url = RUNWAY_API_BASE + path
    resp = requests.get(url, headers=_runway_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return {}
    return data


def _find_video_url_anywhere(obj: Any) -> Optional[str]:
    """
    Scan nested dict/list/str for a URL-looking string.
    """
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http"):
            lower = s.lower()
            if any(lower.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".mkv")):
                return s
            # If it's a CDN URL without extension, still accept it.
            return s

    if isinstance(obj, dict):
        for v in obj.values():
            url = _find_video_url_anywhere(v)
            if url:
                return url

    if isinstance(obj, (list, tuple)):
        for item in obj:
            url = _find_video_url_anywhere(item)
            if url:
                return url

    return None


def _post_json(url: str, payload: Dict[str, Any], token: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
    """
    Generic POST JSON helper for the optional Canva import webhook.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "VikingAI-Video/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


# -------------------------------------------------------------------
# Runway image_to_video (correct endpoint)
# -------------------------------------------------------------------

def create_video_runway(
    prompt: str,
    duration: int = 5,
    ratio: str = "1280:720",
    poll_interval: int = 5,
    max_wait_seconds: int = 180,
) -> Dict[str, Any]:
    """
    Use Runway's /image_to_video endpoint with a seed image + text prompt.

    POST /image_to_video payload (from docs):
      {
        "promptImage": "https://...jpg",
        "promptText": "your prompt",
        "model": "gen4_turbo",
        "ratio": "1280:720",
        "duration": 5
      }

    Then poll /tasks/{id} until SUCCEEDED/FAILED.

    Returns:
      { "ok": True/False, "provider": "runway", "url": "...", "error": "..." }
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {
            "ok": False,
            "provider": "runway",
            "error": "Empty prompt.",
        }

    if not RUNWAY_API_KEY:
        return {
            "ok": False,
            "provider": "runway",
            "error": "RUNWAY_API_KEY is not configured in .env.",
        }

    # 1) Start the image_to_video task
    try:
        payload = {
            "model": "gen4_turbo",
            "promptImage": RUNWAY_SEED_IMAGE,
            "promptText": prompt,
            "ratio": ratio,
            "duration": duration,
        }
        start_resp = _runway_post("/image_to_video", payload)
    except requests.HTTPError as e:
        # Include some body text to help debug 400s, 401s, etc.
        body = ""
        if e.response is not None:
            try:
                body = e.response.text
            except Exception:
                body = ""
        return {
            "ok": False,
            "provider": "runway",
            "error": f"Error starting Runway task (HTTP {e.response.status_code if e.response else ''}): {body}",
        }
    except Exception as e:
        return {
            "ok": False,
            "provider": "runway",
            "error": f"Error starting Runway task: {e}",
        }

    task_id = start_resp.get("id") or start_resp.get("taskId") or start_resp.get("task_id")
    if not task_id:
        return {
            "ok": False,
            "provider": "runway",
            "error": f"Could not find task ID in Runway response: {start_resp}",
        }

    # 2) Poll until done
    start_time = time.time()
    last_status = None

    while True:
        if time.time() - start_time > max_wait_seconds:
            return {
                "ok": False,
                "provider": "runway",
                "error": f"Runway task {task_id} timed out after {max_wait_seconds} seconds. Last status: {last_status}",
            }

        try:
            task = _runway_get(f"/tasks/{task_id}")
        except requests.HTTPError as e:
            body = ""
            if e.response is not None:
                try:
                    body = e.response.text
                except Exception:
                    body = ""
            return {
                "ok": False,
                "provider": "runway",
                "error": f"Error fetching Runway task (HTTP {e.response.status_code if e.response else ''}): {body}",
            }
        except Exception as e:
            return {
                "ok": False,
                "provider": "runway",
                "error": f"Error fetching Runway task {task_id}: {e}",
            }

        status = (task.get("status") or "").upper()
        last_status = status

        if status in ("SUCCEEDED", "COMPLETED", "FINISHED"):
            url = _find_video_url_anywhere(task)
            if not url:
                return {
                    "ok": False,
                    "provider": "runway",
                    "error": f"Runway task {task_id} completed but no video URL was found.",
                }
            return {
                "ok": True,
                "provider": "runway",
                "url": url,
            }

        if status in ("FAILED", "CANCELLED", "ERROR"):
            err = (
                task.get("error")
                or task.get("failure_reason")
                or f"Runway task {task_id} failed with status {status}"
            )
            return {
                "ok": False,
                "provider": "runway",
                "error": str(err),
            }

        # Still running
        time.sleep(poll_interval)


# -------------------------------------------------------------------
# Optional: notify / import into Canva (future)
# -------------------------------------------------------------------

def _notify_canva(video_url: str, prompt: str) -> Optional[str]:
    """
    Optional hook: if CANVA_ASSET_UPLOAD_URL is set, POST the video URL
    so an external service can import it into Canva via Connect APIs.

    Returns an optional 'canva_url' if your webhook sends one back.
    """
    if not CANVA_ASSET_UPLOAD_URL:
        return None

    payload = {
        "video_url": video_url,
        "prompt": prompt,
    }
    data = _post_json(CANVA_ASSET_UPLOAD_URL, payload, token=CANVA_ASSET_UPLOAD_TOKEN)
    if not data:
        return None

    canva_url = data.get("canva_url") or data.get("url")
    if isinstance(canva_url, str) and canva_url.startswith("http"):
        return canva_url
    return None


# -------------------------------------------------------------------
# Main entry for bot.py
# -------------------------------------------------------------------

def create_video_with_fallback(prompt: str) -> Dict[str, Any]:
    """
    Entry point used by /video in bot.py.

    For now:
      - Generate video via Runway (image_to_video).
      - Optionally notify Canva import webhook.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "provider": None, "error": "Empty prompt."}

    # 1) Runway engine
    runway_res = create_video_runway(prompt)
    if not runway_res.get("ok"):
        return runway_res

    video_url = runway_res.get("url")
    provider = runway_res.get("provider", "runway")

    # 2) Optional Canva import hook (won't break success if it fails)
    canva_url = None
    try:
        if video_url:
            canva_url = _notify_canva(video_url, prompt)
    except Exception:
        canva_url = None

    result: Dict[str, Any] = {
        "ok": True,
        "provider": "runway" if not canva_url else "runway+canva",
        "url": video_url,
    }
    if canva_url:
        result["canva_url"] = canva_url

    return result


if __name__ == "__main__":
    test_prompt = "A 5 second hype shot of a sold-out arena concert with lights and crowd cheering."
    print(create_video_with_fallback(test_prompt))
