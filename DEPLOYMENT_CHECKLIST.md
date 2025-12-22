# 🚀 Viking AI Deployment Checklist (v1.0)

## 1. Environment Setup
- [ ] Python 3.11+ installed
- [ ] Virtual environment created and activated
- [ ] requirements.txt installed

## 2. Environment Variables
- [ ] DISCORD_TOKEN set (bot token, not client secret)
- [ ] OPENAI_API_KEY set
- [ ] GEMINI_API_KEY set
- [ ] SPOTIFY_CLIENT_ID / SECRET set
- [ ] YOUTUBE_API_KEY set
- [ ] TICKETMASTER_API_KEY set
- [ ] VERIFIED_FAN_ALERT_CHANNEL_ID set

## 3. Discord Configuration
- [ ] Bot invited with correct scopes
- [ ] Slash commands synced successfully
- [ ] Verified Fan channel exists

## 4. Feature Toggles
- [ ] Verified Fan polling = 2 hours
- [ ] YouTube heavy mode OFF (default)
- [ ] Spotify light mode ON
- [ ] LLM routing enabled (OpenAI primary, Gemini fallback)

## 5. Runtime Checks
- [ ] bot.py starts without errors
- [ ] /status command works
- [ ] /intel command returns data
- [ ] No repeated Verified Fan alerts

## 6. Hardening (Recommended)
- [ ] Run inside tmux / screen / systemd
- [ ] Enable log rotation
- [ ] Daily restart cron (optional)

## 7. Release
- [ ] Tag release: v1.0
- [ ] README.md finalized
- [ ] Backup config + database

✅ Viking AI is now production-ready.
