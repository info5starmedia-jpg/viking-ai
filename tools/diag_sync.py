import json
import os
import sys
from typing import Any, Dict, List


def _load_dotenv_file(path: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        return


_load_dotenv_file("/opt/viking-ai/.env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()

API_BASE = "https://discord.com/api/v10"


def _exit(msg: str, code: int = 1) -> None:
    print(msg)
    sys.exit(code)


def _api_get(path: str) -> Any:
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        _exit(f"Request failed {e.code} for {path}: {body}")


def _format_commands(commands: List[Dict[str, Any]]) -> str:
    names = [c.get("name") for c in commands if isinstance(c, dict)]
    names = [n for n in names if n]
    return ", ".join(sorted(names)) or "(none)"


def main() -> None:
    if not DISCORD_TOKEN:
        _exit("DISCORD_TOKEN is not set in /opt/viking-ai/.env")

    app_data = _api_get("/oauth2/applications/@me")
    token_app_id = app_data.get("id")
    print(f"Application ID (token): {token_app_id}")

    if DISCORD_APPLICATION_ID:
        if token_app_id and DISCORD_APPLICATION_ID != str(token_app_id):
            print(
                "WARNING: DISCORD_APPLICATION_ID does not match token app id "
                f"({DISCORD_APPLICATION_ID} != {token_app_id})"
            )
        else:
            print("DISCORD_APPLICATION_ID matches token app id.")
    else:
        print("DISCORD_APPLICATION_ID not set.")

    global_commands = _api_get(f"/applications/{token_app_id}/commands")
    print(f"Global commands ({len(global_commands)}): {_format_commands(global_commands)}")

    if GUILD_ID and GUILD_ID.isdigit():
        guild_commands = _api_get(f"/applications/{token_app_id}/guilds/{GUILD_ID}/commands")
        print(f"Guild commands ({len(guild_commands)}) for {GUILD_ID}: {_format_commands(guild_commands)}")
    elif GUILD_ID:
        print(f"WARNING: GUILD_ID is set but not numeric: {GUILD_ID}")
    else:
        print("GUILD_ID not set; skipping guild command listing.")

    print("Application info:")
    print(json.dumps({"id": app_data.get("id"), "name": app_data.get("name")}, indent=2))


if __name__ == "__main__":
    main()
