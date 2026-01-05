# Viking AI — Developer Spec (Canonical)

This document is the **single source of truth** for Viking AI’s event → scoring → output → alerts pipeline.

---

## Source of truth mapping (current code)
- Artist resolver: `agents/artist_resolver.py`
- Artist rating (1–5 stars): `agents/artist_rating_engine.py`
- Market heat: `agents/market_heat_agent.py` (used by sellout engine)
- Sellout probability (0–100): `agents/sellout_probability_engine.py`
- Demand heatmap / top cities: `agents/demand_heatmap.py`
- Final sellout scoring logic + tier label: `demand_model.py` (`score_event()` output)
- Discord UX / chunking: `bot.py`

---

# 1) Canonical Event Flow (FINAL)

### Input
User provides:
- `artist_query` (string)
- optional region filters (country_code, etc.)

### Pipeline
1. **Resolve Artist Identity**
   - Use `agents/artist_resolver.resolve_artist()`
   - Output includes best-effort: Spotify + YouTube + Ticketmaster attraction info

2. **Fetch Events**
   - Ticketmaster Discovery events (via `ticketmaster_agent.search_events_for_artist()` or internal TM search)
   - Must support legacy calling patterns: `search_events_for_artist("bts", 10)` and `search_events_for_artist("bts", "US", 25)`

3. **Compute Best Cities (Heatmap)**
   - Use `agents/demand_heatmap.top_cities_for_artist()`
   - Model: Ticketmaster upcoming event density by city
   - Fallback: MAJOR_MARKETS_US prior if no TM data

4. **Score Each Event**
   - Sellout scoring uses `agents/sellout_probability_engine.score_sellout_probability()`
   - That engine:
     - Extracts city/venue (raw TM dict or flattened dict)
     - Calls `agents.market_heat_agent.compute_market_heat()` -> (heat, reason)
     - Builds `DemandSignals` and calls `demand_model.score_event(event, signals)`
     - Returns dict containing:
       - `sellout_probability` (0–100)
       - `reasons` (list)
       - `market_heat` (0–100)
       - `market_heat_reason` (string)
       - plus whatever `score_event()` returns (label/tier/etc.)

5. **Compute Artist Rating (1–5 stars)**
   - Use `agents/artist_rating_engine.rate_artist()`
   - Weighting (LOCKED):
     - Spotify: 50%
       - 70% popularity + 30% follower-norm
     - YouTube: 25%
       - 65% subs-norm + 35% momentum
     - TikTok: 25%
       - 75% views-norm + 25% weekly-growth-norm
   - Star mapping (LOCKED):
     - <25 => 1 “Emerging”
     - <45 => 2 “Growing”
     - <65 => 3 “Hot”
     - <85 => 4 “Headliner”
     - else => 5 “Rockstar”

6. **Render Output**
   - Output must satisfy the Intel Output Contract (below).
   - Discord formatting in `bot.py` must not exceed message limits; chunk outputs safely.

---

# 2) Intel Output Contract (FINAL & LOCKED)

### Response schema (internal dict, then rendered to Discord)
Top-level keys:
- `artist_query` (string)
- `artist_name` (string)
- `generated_at` (ISO string)
- `artist_rating` (dict from `rate_artist`)
- `best_cities` (list of `{city, weight}`)
- `events` (list of Event objects)
- `notes` (list of strings)
- `warnings` (list of strings)

Event object:
- `event_id` (string)
- `name` (string)
- `date_local` (string or empty)
- `city` (string)
- `venue` (string)
- `url` (string)
- `sellout_probability` (int 0–100)
- `demand_tier` (string: LOW|MED|HIGH|EXTREME)  **(SOURCE: demand_model.score_event output)**
- `market_heat` (int 0–100)
- `drivers` (list of strings)  # reasons / signals
- `confidence` (int 0–100)     # placeholder until locked (see section 4)

---

# 3) Demand Tier Definitions (FINAL)
Demand tier thresholds are defined by `demand_model.score_event()` output.

**LOCKED RULE:** Viking AI must use the tier label produced by `score_event()` (do not invent tier mappings elsewhere).
If tier is missing, default to `"MED"`.

---

# 4) Confidence Scoring Framework (FINAL)
Not yet centralized. Placeholder:

**LOCKED RULE:** If confidence is unavailable, set `confidence = 60`.
Once implemented, confidence scoring must be deterministic and test-covered.

---

# 5) Demand Heatmap (FINAL & LOCKED)
Implemented in `agents/demand_heatmap.py`.

- Uses Ticketmaster event density per city.
- Weight = `min(10, count) * 10`
- Cache TTL: 30 minutes
- Fallback: MAJOR_MARKETS_US with decreasing weights.

---

# 6) Verified Fan (FINAL)
Current implementation may return empty list:
- `ticketmaster_agent.fetch_verified_fan_programs()` returns `[]` for stability unless implemented.

**LOCKED RULE:** VF pipeline must never crash the bot; errors must degrade gracefully.

---

# 7) Evaluation Test Suite (FINAL)
At minimum:
- Contract schema validation (events contain required keys)
- Deterministic scoring (same inputs → same outputs)
- Legacy TM signature compatibility tests

