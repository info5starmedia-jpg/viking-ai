"""
VikingAI Auto-Setup & Maintenance Utility

- Checks required environment keys (LLM + integrations)
- Runs diagnostics (Gemini / OpenRouter / Tavily / etc.)
- Updates README / system docs with latest timestamp
- Manages backups + cleanup on a simple schedule
"""

import os
import sys
import time
import shutil
import zipfile
import argparse
import datetime
import schedule
import requests
from dotenv import load_dotenv

# Load .env at startup
load_dotenv()

# Paths
ROOT_DIR = os.getcwd()
BACKUPS_DIR = os.path.join(ROOT_DIR, "backups")
LOG_PATH = os.path.join(ROOT_DIR, "setup_log.txt")

# Prefer SYSTEM_README.md if it exists, otherwise README.txt
SYSTEM_README = os.path.join(ROOT_DIR, "SYSTEM_README.md")
README_TXT = os.path.join(ROOT_DIR, "README.txt")
README_PATH = SYSTEM_README if os.path.exists(SYSTEM_README) else README_TXT

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")


# ---------- Logging / notifications ----------

def log(msg: str):
    """Log to console and to setup_log.txt with timestamp."""
    stamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{stamp} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Don't crash if logging fails
        pass


def send_discord_notice(message: str):
    """Send a simple notice to a Discord webhook if configured."""
    if not DISCORD_WEBHOOK:
        log("ℹ️ Discord webhook not set; skipping Discord notify.")
        return
    try:
        payload = {"content": f"🧩 **VikingAI Auto-Setup:** {message}"}
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        log("✅ Discord notification sent.")
    except Exception as e:
        log(f"❌ Failed to send Discord notification: {e}")


def append_changelog(entry: str):
    """Append a human-readable changelog entry into setup_log.txt too."""
    log(f"🔁 {entry}")


# ---------- Folder setup ----------

def ensure_folders():
    """Ensure core folders exist: agents, utilities, video_pipeline, backups, logs."""
    folders = ["agents", "utilities", "video_pipeline", "backups", "logs"]
    for folder in folders:
        path = os.path.join(ROOT_DIR, folder)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            log(f"📁 Created folder: {folder}")
        else:
            log(f"✅ Folder exists: {folder}")


# ---------- Environment key checks ----------

def check_env_keys(required_keys):
    """
    Check that important env keys are present.
    Does NOT exit on failure; just logs what is missing.
    """
    missing = []
    log("\n🔑 Checking environment keys...")
    for key in required_keys:
        val = os.getenv(key)
        if val:
            log(f"✅ {key}: present")
        else:
            log(f"⚠️ {key}: MISSING")
            missing.append(key)

    if missing:
        log(f"⚠️ Missing {len(missing)} required key(s). VikingAI will still run, "
            f"but some features may be disabled:\n    - " + "\n    - ".join(missing))
        send_discord_notice(
            f"Some required env keys are missing in Auto-Setup:\n" +
            "\n".join([f"- {k}" for k in missing])
        )
    else:
        log("✅ All required env keys are set.")


# ---------- Diagnostics ----------

def run_full_diagnostics():
    """
    Wraps diagnostics.run_diagnostics() and pretty-prints the results.
    """
    try:
        import diagnostics  # your diagnostics.py in the root
    except ImportError as e:
        log(f"❌ diagnostics.py not found or failed to import: {e}")
        return

    log("\n🔍 Running diagnostics...")
    try:
        results = diagnostics.run_diagnostics()
    except Exception as e:
        log(f"❌ Diagnostics threw an error: {e}")
        return

    for name, ok in results.items():
        mark = "✅" if ok else "⚠️"
        log(f"{mark} {name}")

    # If diagnostics has format_llm_status, use it for a nice summary
    if hasattr(diagnostics, "format_llm_status"):
        try:
            llm_line = diagnostics.format_llm_status(results)
            log(f"🧠 LLM stack: {llm_line}")
        except Exception:
            pass

    log("✅ Diagnostics complete.")


# ---------- Backup / cleanup ----------

def _backup_filename():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(BACKUPS_DIR, f"viking_backup_{ts}.zip")


def create_backup():
    """
    Create a ZIP backup of key files:
    - .env
    - viking_ai.db
    - README / SYSTEM_README
    - config.json (if present)
    - tm_cache, logs (lightweight)
    """
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    backup_path = _backup_filename()
    log("🧹 Preparing backup...")
    try:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
            # .env
            env_path = os.path.join(ROOT_DIR, ".env")
            if os.path.exists(env_path):
                z.write(env_path, arcname=".env")

            # DB
            db_path = os.path.join(ROOT_DIR, "viking_ai.db")
            if os.path.exists(db_path):
                z.write(db_path, arcname="viking_ai.db")

            # README files
            if os.path.exists(README_TXT):
                z.write(README_TXT, arcname="README.txt")
            if os.path.exists(SYSTEM_README):
                z.write(SYSTEM_README, arcname="SYSTEM_README.md")

            # config.json
            cfg = os.path.join(ROOT_DIR, "config.json")
            if os.path.exists(cfg):
                z.write(cfg, arcname="config.json")

            # Small folders: tm_cache & logs
            for folder in ("tm_cache", "logs"):
                folder_path = os.path.join(ROOT_DIR, folder)
                if os.path.isdir(folder_path):
                    for root, _, files in os.walk(folder_path):
                        for f in files:
                            full = os.path.join(root, f)
                            rel = os.path.relpath(full, ROOT_DIR)
                            z.write(full, arcname=rel)

        log(f"✅ Backup created: {backup_path}")
        send_discord_notice(f"📦 New VikingAI backup created: `{os.path.basename(backup_path)}`")
    except Exception as e:
        log(f"❌ Backup failed: {e}")
        send_discord_notice(f"❌ Backup failed: {e}")


def cleanup_old_backups(days: int = 21):
    """
    Delete backup ZIP files older than N days (default 21).
    """
    if not os.path.isdir(BACKUPS_DIR):
        log("ℹ️ No backups directory yet; nothing to clean.")
        return

    now = time.time()
    cutoff = now - days * 86400
    removed = 0

    log("🧹 Cleaning old backups...")
    for fname in os.listdir(BACKUPS_DIR):
        path = os.path.join(BACKUPS_DIR, fname)
        if not os.path.isfile(path) or not fname.endswith(".zip"):
            continue
        try:
            mtime = os.path.getmtime(path)
            if mtime < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            continue

    log(f"✅ Cleanup complete. {removed} file(s) removed.")
    send_discord_notice(f"🧹 Backup cleanup complete. Removed {removed} old backup(s).")


# ---------- README update ----------

def update_readme():
    """
    Update README (or SYSTEM_README.md if present) with a 'Last auto-update' line.
    """
    if not os.path.exists(README_PATH):
        log("⚠️ README file not found; skipping README auto-update.")
        return

    try:
        with open(README_PATH, "r+", encoding="utf-8") as f:
            content = f.read()
            version = datetime.datetime.now().strftime("%Y.%m.%d")
            marker = "📅 Last auto-update:"

            if marker in content:
                # Replace existing line
                before = content.split(marker)[0]
                content = before + f"{marker} {version}\n"
            else:
                # Prepend marker at the top
                content = f"{marker} {version}\n" + content

            f.seek(0)
            f.write(content)
            f.truncate()

        append_changelog(f"{os.path.basename(README_PATH)} auto-updated successfully.")
        log("📘 README refreshed.")
    except Exception as e:
        log(f"❌ Failed to update README: {e}")


# ---------- Scheduler ----------

def scheduled_tasks():
    """
    Run backup every 3 weeks and cleanup daily, at configured times.
    """
    backup_time = os.getenv("BACKUP_TIME", "03:00")
    cleanup_time = os.getenv("CLEANUP_TIME", "03:30")

    log(f"\n⏰ Scheduler started: Backup every 3 weeks at {backup_time}, "
        f"Cleanup daily at {cleanup_time}. Ctrl+C to stop.")

    schedule.every(3).weeks.at(backup_time).do(create_backup)
    schedule.every().day.at(cleanup_time).do(cleanup_old_backups)

    send_discord_notice(
        f"⏰ Auto-Setup scheduler running: backup every 3 weeks @ {backup_time}, "
        f"cleanup daily @ {cleanup_time}."
    )

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------- MAIN ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-now", action="store_true", help="Run immediate backup")
    parser.add_argument("--cleanup-now", action="store_true", help="Run cleanup now")
    parser.add_argument("--diagnostics-only", action="store_true", help="Run only diagnostics")
    args = parser.parse_args()

    # New LLM + integrations key set:
    required_keys = [
        # LLM stack
        "GEMINI_API_KEY",       # or GOOGLE_API_KEY / GOOGLE_GEMINI_API_KEY used in diagnostics
        "TAVILY_API_KEY",

        # Media / SEO / ticketing
        "CANVA_CLIENT_ID",
        "CANVA_CLIENT_SECRET",
        "CANVA_ACCESS_TOKEN",
        "TICKETMASTER_API_KEY",
        "ELEVENLABS_API_KEY",
        "KDENLIVE_PATH",  # or path on PATH, depending on your setup
        "GOOGLE_CUSTOM_SEARCH_API_KEY",
        "GOOGLE_CUSTOM_SEARCH_ENGINE_ID",
    ]

    log("🔁 VikingAI Auto-Setup cycle starting...")
    ensure_folders()
    check_env_keys(required_keys)
    run_full_diagnostics()

    if args.diagnostics_only:
        log("🩺 Diagnostics-only mode complete. Exiting.")
        return

    update_readme()

    if args.backup_now:
        create_backup()
        append_changelog("Manual backup executed via auto_setup.py.")

    if args.cleanup_now:
        cleanup_old_backups()
        append_changelog("Manual cleanup executed via auto_setup.py.")

    # If no immediate flags, start scheduler loop
    if not any([args.backup_now, args.cleanup_now, args.diagnostics_only]):
        scheduled_tasks()

    log("✅ Auto-Setup main routine finished.")


if __name__ == "__main__":
    main()
