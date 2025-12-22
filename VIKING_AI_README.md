# Viking AI — The Ultimate Tour Intelligence & Growth Automation Bot

Viking AI is a multi-agent Discord bot integrating Ticketmaster, Verified Fan monitoring, SEO engines,
tour news analysis, sell‑out predictions, and a full LLM stack (Gemini + OpenRouter + Tavily).

## 🚀 Key Features

### 🎫 Ticketmaster & Touring Intelligence
- **/events artist:<name>** — Search Ticketmaster events  
- **/eventdetails id:<event_id>** — Full event analysis + sell‑out score  
- **/tm_tomorrow** — Tomorrow’s onsale summary  
- **/tm_tomorrow_csv** — CSV import + analysis  

### 🔥 Verified Fan Intelligence
- Automated 24/7 polling  
- Alerts when new Verified Fan programs open  
- Scores demand + stores in database  

### 🎯 Sell-Out Probability Engine
- Venue size, artist tier, streaming data, history, geo demand  
- Returns 0–100 score + probability tier  

### 📰 Real-Time Tour News
- Fetches fresh news  
- AI filters only tour‑relevant articles  

### 🔍 SEO Engine
- **/seo_audit**
- **/keywords**
- **/backlinks**

### 💬 AI Chat
- Gemini + OpenRouter + Tavily  
- Direct touring insights, no fluff  

### 🎞 Video Creation
- **/video prompt:<idea>** — Canva template generation  

## 🧠 System Internals
- Full LLM stack health checking  
- Automatic folder setup  
- Auto-repair / watchdog  
- Cron scheduling  

## 🗄 Database
SQLite: `viking_ai.db`  
Stores events, demand, verified fan history, logs, and analytics.

## 🔐 Required Environment Variables (.env)
```
DISCORD_BOT_TOKEN=
TICKETMASTER_API_KEY=
OPENROUTER_API_KEY=
GEMINI_API_KEY=
TAVILY_API_KEY=
CANVA_CLIENT_ID=
CANVA_CLIENT_SECRET=
CANVA_ACCESS_TOKEN=
GOOGLE_CUSTOM_SEARCH_API_KEY=
GOOGLE_CUSTOM_SEARCH_ENGINE_ID=
VERIFIED_FAN_ALERT_CHANNEL_ID=
```

## 📦 Usage
Run:
```
python bot.py
```

## 📄 Version
This README reflects the **latest Viking AI upgrades**, including:
- New Tour News agent  
- Verified Fan bug fixes  
- Updated demand model  
- Ticketmaster full integration  
- LLM stack orchestration  
- 15 synced slash commands  
