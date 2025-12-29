# Summary
- Add usage logging + tier overrides in SQLite with paid-tier gating and admin-only intel refresh.
- Introduce scheduled artist intel auto-refresh with concurrency/TTL safeguards and status visibility.
- Tune sell-out probability scoring with capacity and momentum inputs.

# Changes
- Added `usage_db.py` for `usage_events` logging + `guild_tiers` overrides (migration-safe).
- Wired tier gating, usage logging, and intel refresh loop/command into `bot.py`.
- Updated sell-out probability model and event scoring to use capacity/momentum when available.
- Added `.env.example` entries for tiers and intel refresh settings.

# Tests
```sh
python -m py_compile bot.py tour_scan_monitor.py verified_fan_monitor.py price_monitor.py
```
_Output:_
```
(no output)
```

```sh
python - <<'PY'
import bot
print("loaded bot ok")
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

# Rollout Notes
- Ensure `discord.py` is installed in the runtime virtualenv before running `python - <<'PY'` import checks.
- Populate `DEFAULT_TIER`, `ADMIN_USER_IDS`, `PRO_GUILD_IDS`, and intel refresh envs in `/opt/viking-ai/.env`.
- Intel refresh loop can be disabled by setting `INTEL_REFRESH_SECONDS=0`.

# How to Test (server)
```sh
source /opt/viking-ai/.venv/bin/activate
python -m py_compile /opt/viking-ai/bot.py /opt/viking-ai/tour_scan_monitor.py /opt/viking-ai/verified_fan_monitor.py /opt/viking-ai/price_monitor.py
python - <<'PY'
import bot
print("loaded bot ok")
PY
```
