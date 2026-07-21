# 🛡️ Viking AI — Industry Report & Project Overview

> **A production-grade Discord bot for live-event / touring intelligence and ticket-drop detection**, with a monetized web dashboard layered on top.

*Report generated: 2026-07-21 · Branch: `claude/industry-report-overview-ucuq0w` · Version: v1.0 ("Frozen", ~92% complete)*

---

## 1. Executive Summary

Viking AI answers one question extremely well: **"Will this artist / show / tour sell out — and where should they play next?"** — and catches ticket drops the moment they go on sale.

It combines real-time signals (Ticketmaster, Spotify, YouTube, TikTok), a weighted demand model, and multi-LLM reasoning (OpenAI + Gemini + Tavily), all delivered through a Discord bot with **26 live slash commands** and several always-on background jobs. A bundled Flask/Stripe web dashboard (**DropCatcher**) turns it into a subscription product.

**Target users:** ticket flippers, promoters, artist managers, fan clubs, touring analysts, and SEO/growth teams.

---

## 2. What It Does — Feature Layout

### 🎟️ Tour Intelligence
`/intel` · `/intel_refresh` · `/events` · `/eventdetails`
- Sell-out probability scoring (0–100) + demand tier (LOW / MED / HIGH / EXTREME)
- Artist star rating (1–5: Emerging → Growing → Hot → Headliner → Rockstar)
- Best-cities demand heatmap (Ticketmaster event density per city)
- Streaming momentum (Spotify + YouTube) and LLM-generated intel dossiers

### 🎫 Drop Catcher (two-track ticket-drop detection)
`/drop_add` · `/drop_list` · `/drop_remove` · `/drop_search` · `/drop_changes` · `/drop_tomorrow` · `/drop_aggressive`
- **Track 1 — Watched events:** poll specific Ticketmaster event IDs; fire when status flips to `onsale`
- **Track 2 — Global scan:** sweep all US/CA music onsales in a rolling window, hash-deduped
- Aggressive burst mode (poll interval drops to ~3s inside an onsale window)

### 📈 Surge & Price Tracking
`/surge_add` · `/surge_list` · `/surge_remove` · `/price_history`
- Ticketmaster surge watching and price-change history

### 📰 News & Scan
`/news_now` · `/tour_scan_now`
- AI-filtered tour news via Tavily (strips gossip, keeps presale/venue/festival signal)
- RSS-based tour-announcement scanning

### 💳 Subscription & Ops
`/tier_check` · `/tier_set` · `/status` · `/health` · `/debug` · `/diag` · `/sync_now` · `/city_debug` · `/help`
- Tier gating, slash-command sync diagnostics, health/status reporting

### ⚙️ Background jobs (no command; run automatically)
Verified Fan poller (2h) · Drop-catcher poller (300s + 3s aggressive) · Global scan · Price monitor · Platform monitor · Expiry warnings · Daily digest · Watchdog + auto-repair

---

## 3. Architecture

```
Discord ──▶ bot.py ──▶ orchestrator_v2 / tour_brain_v4 ──▶ agents/* ──▶ LLM ──▶ Final analysis
                          │
                          └──▶ background loops (drop catcher, VF poller, price/surge, tour scan)

Flask dashboard (dashboard/app.py) ──▶ Stripe billing ──▶ SQLite
```

**Multi-agent layer (`agents/`):** artist resolver, artist rating engine, market heat, sellout probability engine, demand heatmap, Spotify / YouTube / TikTok / Ticketmaster / Tavily agents, SEO, tour planner.

**Scoring model (LOCKED per `DEVELOPER.md`):**
- Artist rating weights: **Spotify 50% · YouTube 25% · TikTok 25%**
- Star thresholds: `<25` Emerging · `<45` Growing · `<65` Hot · `<85` Headliner · else Rockstar
- Demand tier comes only from `demand_model.score_event()` (single source of truth)

**LLM stack:** OpenAI (primary) → Gemini (fallback), Tavily for web lookups.
**Data:** SQLite (`viking_ai.db` / `viking_ai.sqlite`).
**Scraping resilience:** Apify as multi-source fallback; `curl_cffi` for TLS/JA3 browser impersonation.

---

## 4. Web Dashboard & Monetization (DropCatcher)

Flask + Gunicorn + Stripe (`dashboard/`): login, invite-code redemption, admin panel, per-client monitors.

| Tier | Price / mo | Monitor limit |
|------|-----------:|--------------:|
| tester | $0 | unlimited (checkout blocked in app) |
| starter | $80 | 7 |
| pro | $100 | 15 |
| unlimited | $300 | unlimited |

Templates: `dashboard`, `admin`, `admin_client`, `login`, `redeem`, `expired`.

---

## 5. Deployment

`render.yaml` defines **two Render services**:
- **`viking-ai-bot`** — `worker`, `python bot.py`
- **`viking-ai-dashboard`** — `web`, `gunicorn dashboard.app:app`

Secrets (`sync: false`) include Discord, Ticketmaster, Apify, OpenAI, Google CSE, Tavily, Spotify, YouTube, TikTok, and Drop-Catcher webhook/channel config.

---

## 6. Project Status & Known Gaps

**Status:** v1.0, self-described "Frozen," **~92% complete**, marked production-ready. PR #6 (drop-catcher) merged. Latest work added Apify integration, price history, RSS defaults, tier-upgrade DMs, and streaming-metrics hardening.

**Known gaps / caveats (from the code itself):**
- ⚠️ **Verified Fan detection is a stub** — `fetch_verified_fan_programs()` returns `[]` by design "for stability." A headline feature is not truly live.
- ⚠️ **Confidence scoring not implemented** — hardcoded to `60` as a placeholder per the spec.
- ⚠️ **Documentation drift** — READMEs disagree on the LLM stack (GPT-4.1-mini vs. Gemini as primary); three overlapping READMEs exist.
- ⚠️ **Repo hygiene** — stray/duplicate files (e.g. `streaming_metrics (2).py`) and several one-line agent shims.
- ⚠️ **Validation not fully exercised** — `PR_BODY.md` shows checks run where `discord` was not installed and systemd was unavailable.

---

## 7. Roadmap (from project docs)

- 🗺️ Dynamic seatmap tracking
- 💵 Revenue forecasting
- 🔥 Arbitrage alerts (resale > face value)
- 📊 Dynamic pricing-curve prediction
- 🎧 Viral score (TikTok + Spotify trend fusion)
- ✅ Centralize & test-cover confidence scoring
- ✅ Implement real Verified Fan detection

---

*This report is a factual snapshot of the codebase as of the date above. Feature and status claims are drawn from source (`bot.py`, `drop_catcher.py`, `agents/`, `dashboard/`, `render.yaml`, `DEVELOPER.md`) rather than marketing copy.*
