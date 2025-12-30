# Summary
- Add slash command sync diagnostics, tracking, and manual sync tools.
- Implement standalone Discord REST sync diagnostic script without external deps.
- Remove `.env.example` and document validation outputs.

# Changes
- Added sync state tracking, `/diag`, and `/sync_now` commands plus robust sync routing in `bot.py`.
- Added `tools/diag_sync.py` to inspect token app id and list global/guild commands via REST.
- Removed `.env.example` from the repo.

# Validation
```sh
python -m py_compile bot.py usage_db.py
```
_Output:_
```
(no output)
```

```sh
python tools/diag_sync.py
```
_Output:_
```
DISCORD_TOKEN is not set in /opt/viking-ai/.env
```

```sh
python - <<'PY'
import bot
print("registered:", sorted([c.name for c in bot.tree.get_commands()]))
PY
```
_Output:_
```
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/workspace/viking-ai/bot.py", line 14, in <module>
    import discord
ModuleNotFoundError: No module named 'discord'
```

# Service Validation
```sh
sudo systemctl restart viking-ai
```
_Output:_
```
System has not been booted with systemd as init system (PID 1). Can't operate.
Failed to connect to bus: Host is down
```

```sh
sudo journalctl -u viking-ai -n 120 --no-pager -l --since "3 minutes ago"
```
_Output:_
```
No journal files were found.
-- No entries --
```
