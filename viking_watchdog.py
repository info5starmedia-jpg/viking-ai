import os
import subprocess
import time
import datetime
import requests
from dotenv import load_dotenv

# === Load environment variables ===
load_dotenv()

# === CONFIGURATION ===
MANAGER_FILE = "viking_bot_manager.py"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
CHECK_INTERVAL = 60  # seconds between checks
LOG_PATH = os.path.join(os.getcwd(), "logs", "watchdog.log")

# Ensure logs directory exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def log_message(msg: str):
    """Log to console and file."""
    stamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{stamp} {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(line + "\n")


def send_discord_alert(message: str):
    """Send alert to Discord."""
    if not DISCORD_WEBHOOK:
        log_message("⚠️ No Discord webhook found — skipping alert.")
        return
    try:
        payload = {"content": f"⚙️ **Watchdog Notice:** {message}"}
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        log_message("✅ Discord alert sent.")
    except Exception as e:
        log_message(f"❌ Failed to send Discord alert: {e}")


def is_process_running(keyword: str) -> bool:
    """Check if the process is currently running (Windows version)."""
    try:
        result = subprocess.run(["tasklist"], capture_output=True, text=True)
        return keyword.lower() in result.stdout.lower()
    except Exception as e:
        log_message(f"⚠️ Error checking process: {e}")
        return False


def restart_manager():
    """Restart the bot manager process."""
    try:
        subprocess.Popen(["python", MANAGER_FILE])
        log_message("✅ Manager restarted successfully.")
        send_discord_alert("🧠 Manager process crashed — restarted successfully.")
    except Exception as e:
        log_message(f"💥 Failed to restart manager: {e}")
        send_discord_alert(f"❌ Watchdog failed to restart manager: {e}")


def main():
    log_message("🧩 VikingAI Watchdog started — monitoring bot manager.")
    send_discord_alert("🟢 VikingAI Watchdog started — monitoring active.")

    while True:
        if not is_process_running(MANAGER_FILE):
            log_message("⚠️ Manager not running — initiating restart...")
            restart_manager()
        else:
            log_message("✅ Manager process healthy.")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
