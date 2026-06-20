"""
Stripe Webhook Handler
======================
Single Azure Function HTTP trigger for all Stripe billing events.
Validates webhook signature and delegates to stripe_service.py.

No authentication decorator — Stripe signs requests with STRIPE_WEBHOOK_SECRET.

Register in function_app.py:
    app.route(route="stripe/webhook", methods=["POST"])(handle_stripe_webhook)

File: function_app_pkg/api/stripe_webhooks.py
"""

import logging
import stripe
import azure.functions as func

from function_app_pkg.core.stripe_service import (
    construct_webhook_event,
    process_webhook_event,
)

logger = logging.getLogger(__name__)


def handle_stripe_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/stripe/webhook

    Stripe sends POST requests here for every billing event.

    CRITICAL: Always return 200, even on processing errors.
    Non-200 causes Stripe to retry the event for up to 72 hours,
    which can lead to duplicate activations or charge attempts.
    """

    # --- Signature validation ---
    payload = req.get_body()
    sig = req.headers.get('stripe-signature', '')

    if not sig:
        logger.error("Stripe webhook: missing stripe-signature header")
        return func.HttpResponse(status_code=400)

    try:
        event = construct_webhook_event(payload, sig)
    except stripe.error.SignatureVerificationError:
        logger.error("Stripe webhook: signature verification failed")
        return func.HttpResponse(status_code=400)
    except ValueError:
        logger.error("Stripe webhook: invalid payload")
        return func.HttpResponse(status_code=400)

    # --- Process ---
    try:
        result = process_webhook_event(event)
        logger.info(f"Stripe webhook processed: {event['type']} -> {result}")
    except Exception as e:
        # Log the error but STILL return 200 to prevent retries
        logger.error(
            f"Stripe webhook processing error [{event['type']}]: {e}",
            exc_info=True,
        )

    return func.HttpResponse(status_code=200)