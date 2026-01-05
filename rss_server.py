#!/usr/bin/env python3
import os
import json
import time
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import formatdate

logger = logging.getLogger("rss_server")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

HOST = os.getenv("VIKING_RSS_HOST", "127.0.0.1")
PORT = int(os.getenv("VIKING_RSS_PORT", "8099"))
DATA_PATH = os.getenv("VIKING_RSS_DATA_PATH", "/opt/viking-ai/data/test_rss_items.json")

TITLE = os.getenv("VIKING_RSS_TITLE", "Viking AI Test Feed")
LINK = os.getenv("VIKING_RSS_LINK", "http://localhost/rss.xml")
DESC = os.getenv("VIKING_RSS_DESC", "Controlled RSS feed for testing.")

_started = time.time()

def _load_items():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning("Failed to load %s: %s", DATA_PATH, e)
    return []

def _save_items(items):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_PATH)

_ITEMS = _load_items()

def _xml_escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _rss_xml():
    now_rfc = formatdate(timeval=None, localtime=False, usegmt=True)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        f'  <title>{_xml_escape(TITLE)}</title>',
        f'  <link>{_xml_escape(LINK)}</link>',
        f'  <description>{_xml_escape(DESC)}</description>',
        f'  <lastBuildDate>{now_rfc}</lastBuildDate>',
    ]
    for it in sorted(_ITEMS, key=lambda x: x.get("pubDate",""), reverse=True):
        guid = _xml_escape(it.get("guid",""))
        title = _xml_escape(it.get("title",""))
        link = _xml_escape(it.get("link",""))
        pub = _xml_escape(it.get("pubDate",""))
        summ = _xml_escape(it.get("summary",""))
        parts.extend([
            "  <item>",
            f"    <guid isPermaLink=\"false\">{guid}</guid>",
            f"    <title>{title}</title>",
            f"    <link>{link}</link>",
            f"    <pubDate>{pub}</pubDate>" if pub else "",
            f"    <description>{summ}</description>" if summ else "",
            "  </item>",
        ])
    parts.extend(["</channel>", "</rss>"])
    return "\n".join([p for p in parts if p != ""])

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            payload = {
                "ok": True,
                "items_count": len(_ITEMS),
                "uptime_seconds": int(time.time() - _started),
            }
            self._send(200, json.dumps(payload).encode("utf-8"))
            return

        if path == "/rss.xml":
            xml = _rss_xml().encode("utf-8")
            self._send(200, xml, ctype="application/xml; charset=utf-8")
            return

        self._send(404, json.dumps({"ok": False, "error": "not found"}).encode("utf-8"))

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/add":
            self._send(404, json.dumps({"ok": False, "error": "not found"}).encode("utf-8"))
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "invalid json"}).encode("utf-8"))
            return

        guid = (data.get("guid") or "").strip() or f"guid-{int(time.time())}"
        title = (data.get("title") or "").strip()
        link = (data.get("link") or "").strip()
        pubDate = (data.get("pubDate") or "").strip()
        summary = (data.get("summary") or "").strip()

        if not title and not link:
            self._send(400, json.dumps({"ok": False, "error": "title or link required"}).encode("utf-8"))
            return

        global _ITEMS
        _ITEMS = [it for it in _ITEMS if (it.get("guid") or "").strip() != guid]
        _ITEMS.append({
            "guid": guid,
            "title": title,
            "link": link,
            "pubDate": pubDate,
            "summary": summary,
            "added_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        _save_items(_ITEMS)

        self._send(200, json.dumps({"ok": True, "items_count": len(_ITEMS)}).encode("utf-8"))

def main():
    httpd = HTTPServer((HOST, PORT), Handler)
    logger.info("RSS test server listening on http://%s:%s", HOST, PORT)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
