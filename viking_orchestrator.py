import os
import importlib.util
import traceback
import requests
from datetime import datetime
from dotenv import load_dotenv

# === SETTINGS ===
UTILITIES_PATH = os.path.join(os.getcwd(), "utilities")
LOG_PATH = os.path.join(os.getcwd(), "logs", "orchestrator_log.txt")

# Load .env
load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Ensure folders
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


# === LOGGING ===
def log_message(msg: str):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    entry = f"{timestamp} {msg}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
    print(msg)


# === DISCORD NOTIFY ===
def send_discord_message(content: str, alert: bool = False, attach_log: bool = False):
    """Send message to Discord webhook with optional log attachment."""
    if not DISCORD_WEBHOOK_URL:
        log_message("ℹ️ Discord webhook not set; skipping notification.")
        return

    color = "🔴" if alert else "🟢"
    message = f"{color} {content}"

    try:
        if attach_log and os.path.exists(LOG_PATH):
            with open(LOG_PATH, "rb") as f:
                files = {"file": (os.path.basename(LOG_PATH), f)}
                data = {"content": message}
                r = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files)
        else:
            r = requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

        if r.status_code in (200, 204):
            log_message("📨 Discord notification sent successfully.")
        else:
            log_message(f"⚠️ Discord notify failed ({r.status_code}): {r.text}")

    except Exception as e:
        log_message(f"❌ Discord notification error: {e}")


# === RUNNING SCRIPTS ===
def load_and_run(script_path: str):
    """Safely load and execute .main() from a Python script."""
    try:
        spec = importlib.util.spec_from_file_location("module", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "main"):
            log_message(f"▶️ Running {os.path.basename(script_path)} ...")
            module.main()
            log_message(f"✅ {os.path.basename(script_path)} completed successfully.\n")
            return True
        else:
            log_message(f"⚠️ No main() found in {os.path.basename(script_path)}. Skipping.\n")
            return False
    except Exception as e:
        log_message(f"❌ Error in {os.path.basename(script_path)} → {e}")
        traceback.print_exc()
        return False


# === FIND SCRIPTS ===
def find_scripts():
    """Discover utility test scripts in utilities folder."""
    return sorted([
        os.path.join(UTILITIES_PATH, f)
        for f in os.listdir(UTILITIES_PATH)
        if f.startswith("test_") and f.endswith(".py")
    ])


# === MAIN ORCHESTRATION ===
def orchestrate_utilities():
    """Main orchestration routine for all utility tests."""
    print("\n🧠 VikingAI Utilities Orchestrator Starting...\n")
    log_message("🔄 Orchestrator run started.")

    scripts = find_scripts()
    if not scripts:
        msg = "⚠️ No utility scripts found in /utilities folder."
        log_message(msg)
        send_discord_message(msg, alert=True)
        return

    ordered = sorted(scripts, key=lambda s: (
        "canva" not in s,
        "google" not in s,
        "kdenlive" not in s
    ))

    failed = []
    for script in ordered:
        ok = load_and_run(script)
        if not ok:
            failed.append(os.path.basename(script))

    if failed:
        summary = (
            f"⚠️ **VikingAI Utility Check Report:** {len(failed)} diagnostic(s) failed.\n"
            f"❌ Failed scripts:\n" + "\n".join([f"- {f}" for f in failed]) +
            "\n📎 Full log attached below."
        )
        send_discord_message(summary, alert=True, attach_log=True)
        log_message("⚠️ Some utility checks failed; log attached to Discord.")
    else:
        send_discord_message(
            "✅ **VikingAI Utility Checks Complete:** All diagnostics passed successfully! ⚙️",
            alert=False
        )
        log_message("✅ All utilities processed successfully.")

    print("\n✅ Utility orchestration finished.\n")


if __name__ == "__main__":
    orchestrate_utilities()
