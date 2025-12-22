# update_manager.py
import os, subprocess, zipfile, datetime, shutil

def create_backup(repo_path="."):
    """Create a zip backup of current VikingAI before updating."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = os.path.join(repo_path, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = os.path.join(backup_dir, f"vikingAI_backup_{timestamp}.zip")

    print(f"🗄️ Creating backup: {backup_file} ...")

    with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(repo_path):
            # skip .git and backups folders
            if ".git" in root or "backups" in root:
                continue
            for file in files:
                zipf.write(os.path.join(root, file),
                           os.path.relpath(os.path.join(root, file), repo_path))
    print("✅ Backup created successfully.")
    return backup_file

def git_pull_update(repo_path="."):
    """Sync local VikingAI folder with remote GitHub repo."""
    print("🔁 Checking for updates from GitHub...")
    os.chdir(repo_path)

    try:
        # Create a backup before pulling
        create_backup(repo_path)

        if not os.path.exists(os.path.join(repo_path, ".git")):
            print("⚙️ No git repo found — initializing...")
            remote_url = input("Enter your GitHub repository URL (https://github.com/YourUser/VikingAI.git): ")
            subprocess.run(["git", "init"], check=True)
            subprocess.run(["git", "remote", "add", "origin", remote_url], check=True)
            subprocess.run(["git", "fetch", "origin"], check=True)
            subprocess.run(["git", "checkout", "-t", "origin/main"], check=True)

        subprocess.run(["git", "pull"], check=True)
        print("✅ VikingAI code is up to date!")

    except subprocess.CalledProcessError as e:
        print(f"❌ Git update failed: {e}")
        print("🧩 Restoring from latest backup...")
        restore_latest_backup(repo_path)
    except Exception as ex:
        print(f"⚠️ Unexpected error: {ex}")

def restore_latest_backup(repo_path="."):
    """Restore the most recent backup if update fails."""
    backup_dir = os.path.join(repo_path, "backups")
    if not os.path.exists(backup_dir):
        print("❌ No backups found to restore.")
        return

    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith(".zip")])
    if not backups:
        print("❌ No backups available.")
        return

    latest = backups[-1]
    latest_path = os.path.join(backup_dir, latest)
    print(f"♻️ Restoring from backup: {latest_path}")

    shutil.unpack_archive(latest_path, repo_path)
    print("✅ Backup restored successfully.")

if __name__ == "__main__":
    git_pull_update()
