import os, subprocess, time, requests, datetime
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
BOT_SCRIPT = "bot.py"
MAX_RETRIES = 3
FAIL_DELAY = 30  # seconds between restart attempts

def send_discord_alert(msg):
    """Post alerts to a Discord webhook."""
    if not DISCORD_WEBHOOK:
        print("⚠️ No Discord webhook found — skipping alert.")
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": f"🧠 {msg}"}, timeout=10)
        print("✅ Alert sent to Discord")
    except Exception as e:
        print(f"❌ Failed to send Discord alert: {e}")

def run_bot():
    """Start and monitor the Viking AI bot process."""
    print(f"[{datetime.datetime.now()}] 🚀 Starting Viking AI Bot Manager...")
    fail_count = 0

    while True:
        process = subprocess.Popen(
            ["python", BOT_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()
        output = (stderr or stdout).decode(errors="ignore")

        # If process exited cleanly
        if process.returncode == 0:
            send_discord_alert("✅ Bot exited normally.")
            break

        # If crashed
        fail_count += 1
        send_discord_alert(
            f"❌ Bot crash {fail_count}/{MAX_RETRIES}\n```{output[:250]}```"
        )
        print(f"⚠️ Crash detected ({fail_count}/{MAX_RETRIES})")

        if fail_count >= MAX_RETRIES:
            send_discord_alert("🧩 Self-healing triggered; restarting supervisor.")
            fail_count = 0  # reset counter

        time.sleep(FAIL_DELAY)

if __name__ == "__main__":
    send_discord_alert("🟢 Viking AI Bot Manager started — monitoring active.")
    run_bot()
