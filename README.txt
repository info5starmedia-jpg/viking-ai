Viking AI – Discord Touring, SEO & Media Agent
============================================================

Last updated: 2025-12-11 07:29 UTC

Overview
--------
Viking AI is a Discord bot that helps monitor Ticketmaster tours, scan tour news, compute demand ratings, run SEO audits, and create short AI videos.

Core features
-------------
- Ticketmaster Discovery integration (/events, /eventdetails, /artistinfo, /venuesearch)
- Auto-watch artists and post new tour legs into a chosen channel
- Tour news scanning via Tavily (/news_now)
- Time-aware demand rating (/intel) combining:
    * Ticketmaster events & tour coverage history
    * Spotify followers & popularity
    * YouTube subscribers
- SEO tools: on-page audit, keyword ideas, backlink prospects, scheduled audits
- Short AI video generation for prompts (/video)

Configuration
-------------
- Announcement channel ID: not set (use /setchannel in Discord)

Ticketmaster watchlist
----------------------
- Total watched artists: 1
  • Justin  beiber

Verified Fan / presale watchlist
--------------------------------
- No Verified Fan URLs stored. Use /vf_watch to add a page you care about.

SEO scan targets
----------------
- Total SEO targets: 1
  • https://5starmediaprod.com/

Deployment & environment
------------------------
The bot expects the following environment variables in .env:
- DISCORD_TOKEN
- TICKETMASTER_API_KEY
- OPENAI_API_KEY (for GPT-4o-mini or similar)
- GOOGLE_API_KEY / GOOGLE_CSE_ID (if used for any future news features)
- SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
- YOUTUBE_API_KEY
- TAVILY_API_KEY
- CANVA_API_KEY (and optional video providers like Pika / Runway keys)

To update this README automatically, the bot calls update_readme()
whenever you change configuration via slash commands.