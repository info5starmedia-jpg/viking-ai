"""
dashboard/stripe_handler.py
Stripe webhook handler for Viking AI Drop Catcher.

Handles:
  customer.subscription.created
  customer.subscription.updated
  customer.subscription.deleted
  invoice.payment_failed
  invoice.payment_succeeded

Env vars:
  STRIPE_WEBHOOK_SECRET   Stripe webhook signing secret (whsec_...)
  STRIPE_STARTER_PRICE_ID Stripe price ID for Starter ($80/mo)
  STRIPE_PRO_PRICE_ID     Stripe price ID for Pro ($100/mo)
  STRIPE_UNLIMITED_PRICE_ID Stripe price ID for Unlimited ($300/mo)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db as dashboard_db

logger = logging.getLogger("stripe_handler")

try:
    import stripe as _stripe
    _STRIPE_OK = True
except ImportError:
    _stripe = None  # type: ignore
    _STRIPE_OK = False

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_ALERT_WEBHOOK_URL = os.getenv("STRIPE_ALERT_WEBHOOK_URL", "").strip()

# Map Stripe price IDs → internal tier names
_PRICE_TO_TIER: dict[str, str] = {}


def _load_price_map() -> None:
    global _PRICE_TO_TIER
    mapping = [
        ("STRIPE_STARTER_PRICE_ID",   "starter"),
        ("STRIPE_PRO_PRICE_ID",        "pro"),
        ("STRIPE_UNLIMITED_PRICE_ID", "unlimited"),
    ]
    _PRICE_TO_TIER = {}
    for env_var, tier in mapping:
        val = os.getenv(env_var, "")
        if val:
            _PRICE_TO_TIER[val] = tier
        else:
            logger.warning(
                "stripe_handler: %s is not set — webhook events for %s tier will "
                "default to 'starter'. Set this env var to enable correct tier assignment.",
                env_var, tier,
            )


def _tier_from_price(price_id: str) -> str:
    _load_price_map()
    return _PRICE_TO_TIER.get(price_id, "starter")


def _tier_from_subscription(sub: dict) -> str:
    """Extract tier from the first item's price in a Stripe subscription object."""
    try:
        items = sub.get("items", {}).get("data", [])
        if items:
            price_id = items[0]["price"]["id"]
            return _tier_from_price(price_id)
    except Exception:
        pass
    return "starter"


def handle_webhook(payload: bytes, sig_header: str) -> tuple[bool, str]:
    """
    Verify Stripe webhook signature and dispatch the event.
    Returns (success, message).
    """
    if not _STRIPE_OK:
        return False, "stripe library not installed"
    if not STRIPE_WEBHOOK_SECRET:
        return False, "STRIPE_WEBHOOK_SECRET not configured"

    try:
        event = _stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except _stripe.error.SignatureVerificationError:
        logger.warning("stripe_handler: invalid signature")
        return False, "invalid signature"
    except Exception as exc:
        logger.warning("stripe_handler: parse error: %s", exc)
        return False, str(exc)

    event_type = event["type"]
    data_obj = event["data"]["object"]

    try:
        if event_type in (
            "customer.subscription.created",
            "customer.subscription.updated",
        ):
            _handle_subscription_upsert(data_obj)

        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data_obj)

        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(data_obj)

        elif event_type == "invoice.payment_succeeded":
            _handle_payment_succeeded(data_obj)

        else:
            logger.debug("stripe_handler: unhandled event type %s", event_type)

    except Exception as exc:
        logger.error("stripe_handler: dispatch error for %s: %s", event_type, exc)
        return False, str(exc)

    return True, "ok"


_TIER_ORDER = {"tester": 0, "starter": 1, "pro": 2, "unlimited": 3}


def _post_upgrade_notification(customer_id: str, old_tier: str, new_tier: str) -> None:
    """Post a Discord webhook notification when a client upgrades their tier."""
    if not STRIPE_ALERT_WEBHOOK_URL:
        return
    try:
        import requests as _req
        msg = (
            f"🎉 **Tier Upgrade** — customer `{customer_id}` upgraded "
            f"from **{old_tier}** → **{new_tier}**"
        )
        r = _req.post(
            STRIPE_ALERT_WEBHOOK_URL,
            json={"content": msg},
            timeout=10,
        )
        if r.status_code >= 300:
            logger.warning("stripe_handler: upgrade notification failed status=%s", r.status_code)
        else:
            logger.info("stripe_handler: upgrade notification posted cust=%s %s→%s", customer_id, old_tier, new_tier)
    except Exception as exc:
        logger.warning("stripe_handler: upgrade notification error: %s", exc)


def _handle_subscription_upsert(sub: dict) -> None:
    customer_id = sub.get("customer", "")
    subscription_id = sub.get("id", "")
    status = sub.get("status", "active")
    period_end = float(sub.get("current_period_end", 0))
    tier = _tier_from_subscription(sub)

    # Check existing tier before upserting so we can detect upgrades
    old_tier = None
    try:
        with dashboard_db.connect() as conn:
            row = conn.execute(
                "SELECT tier FROM dc_subscriptions WHERE stripe_customer_id=?",
                (customer_id,),
            ).fetchone()
            if row:
                old_tier = row["tier"]
    except Exception:
        pass

    dashboard_db.upsert_subscription_from_stripe(
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        tier=tier,
        status=status,
        current_period_end=period_end,
    )
    logger.info(
        "stripe_handler: subscription upserted cust=%s sub=%s tier=%s status=%s",
        customer_id, subscription_id, tier, status,
    )

    # Notify on upgrade (higher tier rank)
    if (
        old_tier
        and old_tier != tier
        and _TIER_ORDER.get(tier, 0) > _TIER_ORDER.get(old_tier, 0)
    ):
        _post_upgrade_notification(customer_id, old_tier, tier)


def _handle_subscription_deleted(sub: dict) -> None:
    customer_id = sub.get("customer", "")
    subscription_id = sub.get("id", "")
    period_end = float(sub.get("current_period_end", 0))
    tier = _tier_from_subscription(sub)

    dashboard_db.upsert_subscription_from_stripe(
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        tier=tier,
        status="canceled",
        current_period_end=period_end,
    )
    logger.info("stripe_handler: subscription canceled cust=%s sub=%s", customer_id, subscription_id)


def _handle_payment_failed(invoice: dict) -> None:
    """Mark subscription as past_due on failed payment."""
    subscription_id = invoice.get("subscription", "")
    if not subscription_id:
        return
    try:
        with dashboard_db.connect() as conn:
            row = conn.execute(
                "SELECT client_id FROM dc_subscriptions WHERE stripe_subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if row:
                dashboard_db.set_subscription_status(row["client_id"], "past_due")
                logger.info(
                    "stripe_handler: payment_failed → past_due client_id=%s", row["client_id"]
                )
    except Exception as exc:
        logger.error("stripe_handler: payment_failed db error sub=%s: %s", subscription_id, exc)


def _handle_payment_succeeded(invoice: dict) -> None:
    """Restore active status after successful payment."""
    subscription_id = invoice.get("subscription", "")
    if not subscription_id:
        return
    try:
        with dashboard_db.connect() as conn:
            row = conn.execute(
                "SELECT client_id FROM dc_subscriptions WHERE stripe_subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if row:
                dashboard_db.set_subscription_status(row["client_id"], "active")
                logger.info(
                    "stripe_handler: payment_succeeded → active client_id=%s", row["client_id"]
                )
    except Exception as exc:
        logger.error("stripe_handler: payment_succeeded db error sub=%s: %s", subscription_id, exc)
