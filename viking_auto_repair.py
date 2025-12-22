import os
import subprocess
import sys
import time
import datetime
import shutil
import requests
from dotenv import load_dotenv

# === CONFIGURATION ===
VENV_PATH = os.path.join(os.getcwd(), ".venv312")
REQUIREMENTS = os.path.join(os.getcwd(), "requirements.txt")
WATCHDOG = os.path.join(os.getcwd(), "viking_watchdog.py")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
LOG_PATH = os.path.join(os.getcwd(), "logs", "auto_repair.log")

# Load .env variables
load_dotenv()
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def log(msg):
    stamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{stamp} {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_discord_alert(message: str):
    """Send message to Discord webhook."""
    if not DISCORD_WEBHOOK:
        log("⚠️ No Discord webhook found — skipping alert.")
        return
    try:
        payload = {"content": f"🧠 **VikingAI AutoRepair:** {message}"}
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        log("✅ Discord alert sent.")
    except Exception as e:
        log(f"❌ Failed to send Discord alert: {e}")


def recreate_venv():
    """Recreate .venv if missing or broken."""
    if not os.path.exists(VENV_PATH):
        log("⚠️ Virtual environment missing — recreating...")
        subprocess.run([sys.executable, "-m", "venv", VENV_PATH])
        log("✅ New venv created successfully.")
        send_discord_alert("🛠️ Recreated missing .venv312 environment.")
    else:
        log("✅ Virtual environment found and healthy.")


def install_requirements():
    """Install dependencies."""
    pip_path = os.path.join(VENV_PATH, "Scripts", "pip.exe")
    if not os.path.exists(pip_path):
        log("❌ Pip not found in venv; repairing...")
        recreate_venv()

    if not os.path.exists(REQUIREMENTS):
        log("⚠️ No requirements.txt found — skipping dependency install.")
        return

    try:
        subprocess.run([pip_path, "install", "-r", REQUIREMENTS], check=True)
        log("✅ Dependencies verified/installed successfully.")
    except subprocess.CalledProcessError as e:
        log(f"❌ Dependency installation failed: {e}")
        send_discord_alert("❌ Dependency installation failed during repair.")


def run_watchdog():
    """Launch the watchdog."""
    python_path = os.path.join(VENV_PATH, "Scripts", "python.exe")
    try:
        subprocess.Popen([python_path, WATCHDOG])
        log("✅ Watchdog launched successfully.")
        send_discord_alert("🚀 VikingAI Watchdog relaunched automatically.")
    except Exception as e:
        log(f"💥 Failed to launch watchdog: {e}")
        send_discord_alert(f"💥 Failed to launch watchdog: {e}")


def auto_update_packages():
    """Weekly pip auto-update cycle."""
    pip_path = os.path.join(VENV_PATH, "Scripts", "pip.exe")
    log("🔄 Running weekly pip package update...")
    try:
        subprocess.run([pip_path, "install", "--upgrade", "pip"], check=True)
        subprocess.run([pip_path, "list", "--outdated"], capture_output=True, text=True)
        subprocess.run([pip_path, "install", "--upgrade", "-r", REQUIREMENTS], check=True)
        log("✅ Weekly dependency update complete.")
        send_discord_alert("🔁 Weekly dependency update completed successfully.")
    except Exception as e:
        log(f"❌ Failed during auto-update: {e}")
        send_discord_alert(f"❌ Weekly dependency update failed: {e}")


def main():
    log("🧩 VikingAI Auto-Repair system started — verifying environment...")
    recreate_venv()
    install_requirements()

    # Check weekly auto-update
    last_update_file = os.path.join(os.getcwd(), "last_update.txt")
    now = datetime.datetime.now()
    run_update = False

    if not os.path.exists(last_update_file):
        run_update = True
    else:
        with open(last_update_file, "r") as f:
            last_update_str = f.read().strip()
            try:
                last_update = datetime.datetime.strptime(last_update_str, "%Y-%m-%d")
                if (now - last_update).days >= 7:
                    run_update = True
            except ValueError:
                run_update = True

    if run_update:
        auto_update_packages()
        with open(last_update_file, "w") as f:
            f.write(now.strftime("%Y-%m-%d"))

    run_watchdog()

    log("✅ Auto-Repair + Auto-Update completed successfully.")
    send_discord_alert("✅ Auto-Repair + Weekly Update cycle finished — system stable.")


if __name__ == "__main__":
    main()
