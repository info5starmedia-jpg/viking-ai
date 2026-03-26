"""
embed_builder.py
Viking AI Drop Catcher — Discord embed factory.

Builds rich discord.Embed + discord.ui.View for all 11 alert types.
Every embed follows the rules:
  - Color  = alert type signal (the left-border bar users scan in <2s)
  - Title  = links directly to the buy/listing page
  - Ping   = role mention lives in message content, NOT inside the embed
  - Timestamp on every alert (Discord auto-localizes to each viewer's timezone)
  - Author = "Viking AI" + bot avatar
  - Footer = source attribution
  - Inline fields: Price | Section/Location | Qty/Status
  - Buttons: Buy Now (Link), Dismiss (Secondary)
  - Webhook path: Link buttons only (no interaction needed)
  - Channel path: full View with Dismiss interaction

Usage:
    from embed_builder import build_alert, AlertType

    embed, view = build_alert({
        "type": AlertType.SALE_START,
        "name": "Taylor Swift | Eras Tour",
        "artist": "Taylor Swift",
        "url": "https://www.ticketmaster.com/event/xyz",
        "city": "New York, NY",
        "event_local_date": "2025-08-15",
        "price_min": 89.0,
        "price_max": 149.0,
        "source": "Ticketmaster",
    })
    await channel.send(content="<@&ROLE_ID>", embed=embed, view=view)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import discord

# ---------------------------------------------------------------------------
# Alert type constants
# ---------------------------------------------------------------------------

class AlertType:
    SALE_START       = "sale_start"
    RESTOCK          = "restock"
    NEW_EVENT        = "new_event"
    PRICE_DROP       = "price_drop"
    PRICE_SPIKE      = "price_spike"
    QUEUE_OPEN       = "queue_open"
    LOW_INVENTORY    = "low_inventory"
    VENUE_CAPACITY   = "venue_capacity"
    CHECKOUT_SUCCESS = "checkout_success"
    CHECKOUT_FAILED  = "checkout_failed"
    ON_SALE_REMINDER = "on_sale_reminder"


# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

_COLORS: Dict[str, int] = {
    AlertType.SALE_START:       0x57F287,  # Green   — tickets available NOW
    AlertType.RESTOCK:          0x57F287,  # Green   — back in stock
    AlertType.NEW_EVENT:        0x5865F2,  # Blurple — new event discovered
    AlertType.PRICE_DROP:       0xF1C40F,  # Gold    — price dropped
    AlertType.PRICE_SPIKE:      0xED4245,  # Red     — price above threshold
    AlertType.QUEUE_OPEN:       0x1ABC9C,  # Teal    — queue/presale open
    AlertType.LOW_INVENTORY:    0xFEE75C,  # Yellow  — few tickets left
    AlertType.VENUE_CAPACITY:   0x34495E,  # Navy    — seating/venue update
    AlertType.CHECKOUT_SUCCESS: 0x57F287,  # Green   — purchase confirmed
    AlertType.CHECKOUT_FAILED:  0xED4245,  # Red     — purchase failed
    AlertType.ON_SALE_REMINDER: 0x5865F2,  # Blurple — going on sale soon
}

_HEADERS: Dict[str, str] = {
    AlertType.SALE_START:       "🟢  ON SALE NOW",
    AlertType.RESTOCK:          "🎟  TICKET RESTOCK",
    AlertType.NEW_EVENT:        "🆕  NEW EVENT ON SALE",
    AlertType.PRICE_DROP:       "📉  PRICE DROP",
    AlertType.PRICE_SPIKE:      "📈  PRICE SPIKE",
    AlertType.QUEUE_OPEN:       "⚡  QUEUE OPEN",
    AlertType.LOW_INVENTORY:    "⚠️  LOW INVENTORY",
    AlertType.VENUE_CAPACITY:   "🏟  VENUE UPDATE",
    AlertType.CHECKOUT_SUCCESS: "✅  CHECKOUT SUCCESS",
    AlertType.CHECKOUT_FAILED:  "❌  CHECKOUT FAILED",
    AlertType.ON_SALE_REMINDER: "⏰  ON-SALE REMINDER",
}

# Fallback color for unknown types
_DEFAULT_COLOR = 0x979C9F  # Dark grey

# ---------------------------------------------------------------------------
# Button view
# ---------------------------------------------------------------------------

class _DismissButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="✕  Dismiss",
            style=discord.ButtonStyle.secondary,
            custom_id="viking_alert_dismiss",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(
                content="*(alert dismissed)*",
                embed=None,
                view=None,
            )
        except discord.NotFound:
            pass
        except Exception:
            try:
                await interaction.response.send_message(
                    "Dismissed.", ephemeral=True
                )
            except Exception:
                pass


class AlertView(discord.ui.View):
    """
    Reusable View for alert messages.

    If buy_url is provided, a "Buy Now" Link button is prepended.
    A "Dismiss" secondary button is always appended.

    Use link_only=True when sending via webhook (no interaction callbacks).
    """

    def __init__(
        self,
        buy_url: str = "",
        link_only: bool = False,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(timeout=timeout)

        if buy_url:
            self.add_item(
                discord.ui.Button(
                    label="🎟  Buy Now",
                    url=buy_url,
                    style=discord.ButtonStyle.link,
                )
            )

        if not link_only:
            self.add_item(_DismissButton())

    async def on_timeout(self) -> None:
        """Disable non-link buttons when the view times out."""
        for child in self.children:
            if isinstance(child, discord.ui.Button) and not child.url:
                child.disabled = True


def _link_only_view(buy_url: str) -> Optional[AlertView]:
    """Return a link-only view for webhook delivery, or None if no URL."""
    if not buy_url:
        return None
    return AlertView(buy_url=buy_url, link_only=True)


# ---------------------------------------------------------------------------
# Core embed builder
# ---------------------------------------------------------------------------

def build_alert(
    data: Dict[str, Any],
    *,
    bot_name: str = "Viking AI",
    bot_avatar_url: str = "",
    link_only: bool = False,
) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    """
    Build a (discord.Embed, discord.ui.View) pair from an alert data dict.

    Required key:  "type"  — one of AlertType constants
    Optional keys: name, artist, url, city, country, event_local_date,
                   price_min, price_max, source, seatmap_txt, quantity,
                   old_price, new_price, section, event_id, detail

    Returns (embed, view). view is None for types with no buy URL and link_only=True.
    """
    alert_type  = data.get("type", AlertType.SALE_START)
    color       = _COLORS.get(alert_type, _DEFAULT_COLOR)
    header      = _HEADERS.get(alert_type, "🔔  ALERT")

    name        = data.get("name") or "Event"
    artist      = data.get("artist") or ""
    url         = data.get("url") or ""
    city        = data.get("city") or data.get("country") or ""
    date_str    = data.get("event_local_date") or data.get("date") or ""
    price_min   = data.get("price_min")
    price_max   = data.get("price_max")
    old_price   = data.get("old_price")
    new_price   = data.get("new_price")
    source      = data.get("source") or "Ticketmaster"
    seatmap_txt = data.get("seatmap_txt") or ""
    quantity    = data.get("quantity") or data.get("qty")
    section     = data.get("section") or ""
    thumbnail   = data.get("thumbnail_url") or data.get("image_url") or ""
    event_id    = data.get("event_id") or ""
    detail      = data.get("detail") or ""

    # ----- Title & description -----
    title = f"{header}  —  {name}"
    if len(title) > 256:
        title = title[:253] + "…"

    embed = discord.Embed(
        title=title,
        url=url or None,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    # ----- Author (bot branding) -----
    author_kwargs: Dict[str, Any] = {"name": bot_name}
    if bot_avatar_url:
        author_kwargs["icon_url"] = bot_avatar_url
    embed.set_author(**author_kwargs)

    # ----- Thumbnail (artist/event image) -----
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    # ----- Description -----
    desc_parts = []
    if artist and artist.lower() not in name.lower():
        desc_parts.append(f"**{artist}**")
    if city or date_str:
        loc_date = "  ·  ".join(p for p in [city, _fmt_date(date_str)] if p)
        if loc_date:
            desc_parts.append(loc_date)

    # Type-specific description additions
    if alert_type == AlertType.CHECKOUT_SUCCESS:
        desc_parts.append("✅ Your order was placed successfully.")
    elif alert_type == AlertType.CHECKOUT_FAILED:
        reason = detail or "Unknown error"
        desc_parts.append(f"❌ Checkout failed: {reason}")
    elif alert_type == AlertType.ON_SALE_REMINDER:
        desc_parts.append("🔔 This event goes on sale soon. Be ready!")
    elif alert_type == AlertType.QUEUE_OPEN:
        desc_parts.append("⚡ Queue is open — enter now for the best position.")
    elif alert_type == AlertType.LOW_INVENTORY:
        desc_parts.append("⚠️ Very few tickets remain. Act fast.")

    if desc_parts:
        embed.description = "\n".join(desc_parts)

    # ----- Inline fields -----
    # Price field
    if alert_type in (AlertType.PRICE_DROP, AlertType.PRICE_SPIKE) and old_price and new_price:
        arrow = "↓" if alert_type == AlertType.PRICE_DROP else "↑"
        embed.add_field(
            name="💰 Price Change",
            value=f"~~${old_price:.0f}~~ → **${new_price:.0f}** {arrow}",
            inline=True,
        )
    elif price_min is not None and price_max is not None:
        if abs(price_min - price_max) < 1:
            price_val = f"**${price_min:.0f}**"
        else:
            price_val = f"**${price_min:.0f}** – ${price_max:.0f}"
        embed.add_field(name="💰 Price", value=price_val, inline=True)
    elif price_min is not None:
        embed.add_field(name="💰 From", value=f"**${price_min:.0f}**", inline=True)

    # Section/Location field
    if section:
        embed.add_field(name="📍 Section", value=section, inline=True)
    elif city:
        embed.add_field(name="📍 Location", value=city, inline=True)

    # Quantity / inventory field
    if quantity is not None:
        embed.add_field(name="🎫 Available", value=str(quantity), inline=True)
    elif seatmap_txt:
        # Pull the first line of seatmap text for a compact summary
        first_line = seatmap_txt.split("\n")[0] if seatmap_txt else ""
        if first_line:
            embed.add_field(name="🪑 Inventory", value=first_line, inline=True)

    # Source field (always last inline)
    embed.add_field(name="🔗 Source", value=source, inline=True)

    # Full seatmap detail (non-inline, only if meaningful)
    if seatmap_txt and "\n" in seatmap_txt:
        remaining = "\n".join(seatmap_txt.split("\n")[1:]).strip()
        if remaining:
            embed.add_field(name="📊 Intel", value=remaining[:1024], inline=False)

    # Event ID for reference (non-inline, only if set)
    if event_id:
        embed.add_field(name="🆔 Event ID", value=f"`{event_id}`", inline=False)

    # Checkout-specific detail field
    if alert_type in (AlertType.CHECKOUT_SUCCESS, AlertType.CHECKOUT_FAILED) and detail:
        embed.add_field(name="ℹ️ Detail", value=detail[:1024], inline=False)

    # ----- Footer -----
    footer_text = f"via {source}  ·  Viking AI"
    embed.set_footer(text=footer_text)

    # ----- View (buttons) -----
    if link_only:
        view = _link_only_view(url)
    else:
        view = AlertView(buy_url=url, link_only=False) if url else AlertView(link_only=False)

    return embed, view


# ---------------------------------------------------------------------------
# Convenience constructors for each alert type
# ---------------------------------------------------------------------------

def sale_start(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.SALE_START}, **kwargs)

def restock(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.RESTOCK}, **kwargs)

def new_event(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.NEW_EVENT}, **kwargs)

def price_drop(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.PRICE_DROP}, **kwargs)

def price_spike(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.PRICE_SPIKE}, **kwargs)

def queue_open(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.QUEUE_OPEN}, **kwargs)

def low_inventory(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.LOW_INVENTORY}, **kwargs)

def venue_capacity(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.VENUE_CAPACITY}, **kwargs)

def checkout_success(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.CHECKOUT_SUCCESS}, **kwargs)

def checkout_failed(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.CHECKOUT_FAILED}, **kwargs)

def on_sale_reminder(data: Dict[str, Any], **kwargs) -> Tuple[discord.Embed, Optional[discord.ui.View]]:
    return build_alert({**data, "type": AlertType.ON_SALE_REMINDER}, **kwargs)


# ---------------------------------------------------------------------------
# Sold-out edit helper
# ---------------------------------------------------------------------------

def sold_out_embed(original_embed: discord.Embed) -> discord.Embed:
    """
    Return a greyed-out version of an existing alert embed to signal sold out.
    Call this to edit a previous message when the window closes.
    """
    e = original_embed.copy()
    e.color = discord.Color.from_rgb(151, 156, 159)  # Dark grey
    if e.title and "SOLD OUT" not in e.title:
        e.title = "🔴  SOLD OUT  —  " + e.title.split("  —  ", 1)[-1]
    e.set_footer(text=(e.footer.text or "") + "  ·  Sold Out")
    return e


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to a readable format like 'Aug 15, 2025'."""
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %-d, %Y")
    except Exception:
        return date_str
