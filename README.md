# 🛡️ Viking AI
### Touring Intelligence & Verified Fan Detection Engine

> Viking AI is a production-grade Discord intelligence agent designed for tour demand analysis, sell-out probability forecasting, and Verified Fan detection — powered by Spotify, YouTube, Ticketmaster, and LLM reasoning.

---

## 🚀 What Viking AI Does

Viking AI answers one question extremely well:

**“Will this artist / show / tour sell out — and where should they play next?”**

It combines real-world signals, market demand modeling, and LLM reasoning to deliver actionable touring intelligence.

---

## 🧠 Core Capabilities

### 🎟 Touring Intelligence (`/intel`)
- Sell-out probability scoring
- Market heat analysis
- Spotify + YouTube momentum
- Venue + seatmap context (optional)
- LLM-generated touring insights (short, focused)

### 🔔 Verified Fan Monitor (24/7)
- Polls Ticketmaster every 2 hours
- Silent by default
- Posts only when a NEW Verified Fan program appears
- No spam, no duplicates, state tracked in DB

### 📊 Streaming Momentum
- Spotify (followers, popularity, cached)
- YouTube (momentum, optional heavy mode)

### 📰 Tour News + SEO
- Live tour news via Tavily
- SEO audits & keyword insights
- Optional via config

---

## 🧱 Architecture Overview

Discord → bot.py → Touring Brain v4 → LLM Orchestrator → Final Analysis

Primary LLM: GPT-4.1-mini  
Fallback LLM: Gemini Pro

---

## ⚙️ Configuration Philosophy

- Heavy features OFF by default
- LLM output short & controlled
- All external APIs cached
- Failures degrade gracefully

---

## 🔐 Required Environment Variables

DISCORD_TOKEN  
OPENAI_API_KEY  
GEMINI_API_KEY  
SPOTIFY_CLIENT_ID  
SPOTIFY_CLIENT_SECRET  
YOUTUBE_API_KEY  
TICKETMASTER_API_KEY  
VERIFIED_FAN_ALERT_CHANNEL_ID

---

## 🧪 Stability & Safety

- Async-safe
- Rate-limit aware
- Auto reconnect
- Disk-safe logging
- DB-backed state

---

## 🏁 Status

Version: **v1.0 (Frozen)**  
Completion: **~92%**  
Ready for production deployment.
