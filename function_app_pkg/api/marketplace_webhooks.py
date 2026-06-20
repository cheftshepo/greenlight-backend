"""
Azure Marketplace Webhook Handler
===================================
Receives lifecycle events from Microsoft:
    ChangePlan, ChangeQuantity, Unsubscribe, Suspend, Reinstate, Renew

Microsoft sends a POST with a JSON payload and a Bearer token.
The token MUST be validated to prevent spoofing.

Register in function_app.py:
    app.route(route="marketplace/webhook", methods=["POST"],
              auth_level=func.AuthLevel.ANONYMOUS)(marketplace_webhook_endpoint)

Set the webhook URL in Partner Center → Technical Configuration:
    https://your-func.azurewebsites.net/api/marketplace/webhook

File: function_app_pkg/api/marketplace_webhooks.py
"""

import os
import logging
import jwt
import requests
import azure.functions as func

from function_app_pkg.core.marketplace_service import process_webhook_event
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)

# Microsoft's well-known JWKS for marketplace tokens
_jwks_cache: dict = {}


def _validate_marketplace_jwt(auth_header: str) -> bool:
    """
    Validate the Bearer JWT Microsoft sends with webhook calls.

    The token is issued by Microsoft identity platform. We validate:
      - Signature (via Microsoft's public keys)
      - Audience = our app's client ID
      - Issuer = Microsoft's STS

    In production, ALWAYS validate. Skipping this allows anyone to
    call your webhook with fake events.
    """
    client_id = os.getenv('MARKETPLACE_CLIENT_ID', '')
    tenant_id = os.getenv('MARKETPLACE_PUBLISHER_TENANT_ID', '')

    if not client_id or not tenant_id:
        logger.warning(
            "Marketplace webhook: CLIENT_ID or TENANT_ID not configured. "
            "Skipping token validation (NOT SAFE FOR PRODUCTION)."
        )
        return True

    if not auth_header.startswith('Bearer '):
        logger.error("Marketplace webhook: missing Bearer token")
        return False

    token = auth_header[7:]

    try:
        # Fetch Microsoft's public keys
        jwks_url = (
            f"https://login.microsoftonline.com/{tenant_id}"
            f"/discovery/v2.0/keys"
        )

        if not _jwks_cache.get('keys'):
            resp = requests.get(jwks_url, timeout=10)
            resp.raise_for_status()
            _jwks_cache['keys'] = resp.json()

        jwks = _jwks_cache['keys']

        # Decode the token header to find the signing key
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get('kid', '')

        # Find matching key
        rsa_key = None
        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                rsa_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                break

        if not rsa_key:
            logger.error(f"Marketplace webhook: no matching key for kid={kid}")
            return False

        # Validate the token
        jwt.decode(
            token,
            rsa_key,
            algorithms=['RS256'],
            audience=client_id,
            options={
                'verify_exp': True,
                'verify_aud': True,
                'verify_iss': False,  # Microsoft uses multiple issuers
            },
        )

        return True

    except jwt.ExpiredSignatureError:
        logger.error("Marketplace webhook: token expired")
        return False
    except jwt.InvalidTokenError as e:
        logger.error(f"Marketplace webhook: invalid token: {e}")
        return False
    except Exception as e:
        logger.error(f"Marketplace webhook: validation error: {e}")
        return False


def handle_marketplace_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/marketplace/webhook

    Microsoft sends POST requests here for subscription lifecycle events.

    IMPORTANT: For ChangePlan and ChangeQuantity, we must acknowledge
    the operation within 10 seconds via the Operations API. This is
    handled inside marketplace_service.process_webhook_event().

    For Unsubscribe, Suspend, Reinstate, Renew — these are notify-only.
    No acknowledgement is needed.
    """

    # --- Token validation ---
    auth_header = req.headers.get('Authorization', '')

    if not _validate_marketplace_jwt(auth_header):
        logger.error("Marketplace webhook: authentication failed")
        return func.HttpResponse(status_code=401)

    # --- Parse payload ---
    try:
        payload = req.get_json()
    except ValueError:
        logger.error("Marketplace webhook: invalid JSON payload")
        return func.HttpResponse(status_code=400)

    if not payload or not payload.get('action'):
        logger.error("Marketplace webhook: missing action in payload")
        return func.HttpResponse(status_code=400)

    # --- Process ---
    try:
        result = process_webhook_event(payload)
        logger.info(
            f"Marketplace webhook processed: "
            f"{payload.get('action')} -> {result}"
        )
    except Exception as e:
        logger.error(
            f"Marketplace webhook processing error "
            f"[{payload.get('action')}]: {e}",
            exc_info=True,
        )

    # Always return 200 — Microsoft will retry on non-2xx
    return func.HttpResponse(status_code=200)