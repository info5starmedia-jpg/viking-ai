
# 🎟️⚔️ **Viking AI**  
### _The Ultimate Touring, Ticketing & Intelligence Automation Bot_

![Viking Banner](https://img.shields.io/badge/VikingAI-Touring%20Intelligence-blueviolet?style=for-the-badge)
![Discord](https://img.shields.io/badge/Discord%20Bot-Active-7289DA?style=for-the-badge&logo=discord&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow?style=for-the-badge&logo=python)

Viking AI is a **full-stack AI powerhouse** designed for:  
✔ Ticket flippers  
✔ Promoters  
✔ Managers  
✔ Fan clubs  
✔ Touring analysts  
✔ Growth/SEO teams  

It combines **Ticketmaster intelligence**, **Verified Fan monitoring**, **AI news scanning**, **SEO engines**, **sell-out probability modeling**, and a **multi-LLM brain**, all running inside a **Discord bot**.

---

# ⚡️ **Core Capabilities**

## 🎫 **Ticketmaster Intelligence Suite**
📌 _Powered by ticketmaster_agent.py_

- **/events artist:<name>** → Full event search w/ real-time TM API  
- **/eventdetails id:<event_id>** → Deep breakdown + sell-out probability  
- **/tm_tomorrow** → Lists music onsales happening tomorrow  
- **/tm_tomorrow_csv** → Upload TM CSV & auto-analyze  

🧠 Includes:  
- Venue capacity  
- Price tiers  
- Verified Fan indicators  
- Demand scoring  
- Inventory signals (when available)

---

## 🔥 **Verified Fan Monitoring System (24/7 Background Job)**
📡 Runs automatically — no commands required.

Features:  
- Detects new Verified Fan programs  
- Sends alerts to your Discord channel  
- Tracks changes + flags surprise openings  
- Scores demand based on artist tier, history, geo, and more  

Requires:  
```
VERIFIED_FAN_ALERT_CHANNEL_ID=
```

---

## 🎯 **Sell-Out Probability Engine**
🧮 _Powered by demand_model.py_

The model uses weighted features:

| Factor | Weight |
|--------|--------|
| Venue size | ⭐⭐⭐⭐⭐ |
| Artist Tier | ⭐⭐⭐⭐ |
| Spotify listeners | ⭐⭐⭐ |
| Tour history | ⭐⭐⭐⭐ |
| Geo demand | ⭐⭐⭐⭐ |
| Ticket velocity | ⭐⭐⭐⭐⭐ |
| Verified Fan activity | ⭐⭐⭐⭐ |

Outputs:
- **0–100 Sell-Out Score**
- **Demand Tier (LOW / MED / HIGH / EXTREME)**

Used in:  
- `/eventdetails`  
- `/intel`  

---

## 📰 **Tour News Intelligence Engine**
🔎 _Powered by tour_news_agent.py_

- Searches breaking news  
- AI filters ONLY **tour-related** articles  
- Removes gossip, irrelevant content, unrelated topics  
- Prioritizes:  
  ✔ Presale rumors  
  ✔ Venue confirmations  
  ✔ Ticketmaster leaks  
  ✔ Festival hints  

Slash Command:
```
/news_now artist:<name>
```

---

## 🧠 **Tour Intel Report Generator**
🔮 _Powered by tour_intel_agent.py_

Creates a **full intelligence dossier** using:  
- Ticketmaster data  
- Verified Fan signals  
- Sell-out model  
- Social + streaming signals  
- AI reasoning  
- News context  
- Historical tour performance  

Slash Command:
```
/intel artist:<name> region:<NA/EU/...>
```

---

## 🔍 **SEO & Growth Automation Suite**
📈 _Powered by seo_agent.py_

### Commands:
- **/seo_audit url:<site>** → On-page SEO health  
- **/keywords topic:<topic>** → Keyword clustering & suggestions  
- **/backlinks topic:<topic>** → Competitor backlink targets  

---

## 🤖 **LLM Brain (Orchestrator Engine)**

### Models Used:
| Task | Model |
|------|--------|
| Long reasoning | **Gemini** |
| Creativity, fallback | **Gemini (multi-model)** |
| Web lookups | **Tavily Search Engine** |

The orchestrator chooses the best LLM depending on:
- Complexity  
- Length  
- Speed requirement  
- Need for web browsing  
- Creativity vs accuracy  

---

## 💬 **Chat & Media Tools**

### 🗨️ Chat
```
/chat message:<text>
```
- Quick answers  
- Market analysis  
- Event predictions  
- SEO advice  
- Touring business logic  

### 🎞 Video Concepts
```
/video prompt:<idea>
```
- Auto-generates Canva/Runway-style promo templates  
- Useful for TikTok, Reels, and tour announcements  

---

# ⚙️ **System Internal Architecture**

### 🧩 Services Running in the Background
| Service | Description |
|---------|-------------|
| ⚡ Verified Fan Poller | Constant TM/AXS monitoring |
| 🔧 Auto-Repair | Fixes broken configs + API issues |
| 👁 Watchdog | Restarts bot if it crashes |
| 📅 Scheduler | Daily TM scanning + CSV handling |
| 🧠 LLM Checker | Ensures Gemini/Tavily are online |

Files that support this:
- `viking_watchdog.py`  
- `viking_auto_repair.py`  
- `auto_setup.py`  
- `update_manager.py`  

---

# 🗄 Database Architecture

SQLite database:  
```
viking_ai.db
```

Stores:
- Events  
- Verified Fan programs  
- Historical sale behavior  
- Streaming & social data  
- Sell-out predictions  
- Logs & analytics  

Migrations:  
```
db_migrations_demand.py
```

---

# 🔐 Environment Configuration

Add to `.env`:

```
DISCORD_BOT_TOKEN=
TICKETMASTER_API_KEY=
GEMINI_API_KEY=
TAVILY_API_KEY=
CANVA_CLIENT_ID=
CANVA_CLIENT_SECRET=
CANVA_ACCESS_TOKEN=
GOOGLE_CUSTOM_SEARCH_API_KEY=
GOOGLE_CUSTOM_SEARCH_ENGINE_ID=
VERIFIED_FAN_ALERT_CHANNEL_ID=
```

---

# 🚀 Running Viking AI

Start the bot:
```
python bot.py
```

Auto-run:
```
viking_autorun.bat
```

---

# 🧭 Roadmap

### Future Upgrades (Optional):
- 🗺 Dynamic seatmap tracking  
- 💵 Revenue forecasting  
- 🔥 Arbitrage alerts (resale > face value)  
- 📊 Dynamic pricing curve predictions  
- 🎧 Viral score (TikTok + Spotify trends integration)  

---

# ⚔️ Final Notes  
Viking AI is now one of the **most advanced tour-intelligence Discord systems ever created**, combining:

- Enterprise-level data pipelines  
- LLM-powered reasoning  
- Ticketing automation  
- SEO research  
- Media generation  
- Real-time alerting  

---

