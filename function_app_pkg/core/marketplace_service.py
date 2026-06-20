"""
Azure Marketplace SaaS Fulfillment Service
============================================
Core integration with the Microsoft Commercial Marketplace
SaaS Fulfillment APIs v2.

Handles:
    - Service-to-service OAuth2 authentication
    - Token resolution (landing page flow)
    - Subscription activation
    - Plan changes
    - Subscription queries
    - Webhook event processing

All API calls go through the publisher's backend (service-to-service).
Direct browser calls to the Fulfillment API are NOT supported by Microsoft.

File: function_app_pkg/core/marketplace_service.py

Environment variables required:
    MARKETPLACE_PUBLISHER_TENANT_ID  - AAD tenant that owns the app registration
    MARKETPLACE_CLIENT_ID            - App registration client ID
    MARKETPLACE_CLIENT_SECRET        - App registration client secret
    FRONTEND_URL                     - Frontend base URL (landing page lives here)
"""

import os
import time
import logging
import requests
from datetime import datetime
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MARKETPLACE_API_BASE = "https://marketplaceapi.microsoft.com/api"
MARKETPLACE_API_VERSION = "2018-08-31"

# The fixed resource ID Microsoft uses for Marketplace fulfillment auth
MARKETPLACE_RESOURCE_ID = "20e940b3-4c77-4b0b-9a53-9e16a1b010a7"

PUBLISHER_TENANT_ID = os.getenv('MARKETPLACE_PUBLISHER_TENANT_ID', '')
CLIENT_ID = os.getenv('MARKETPLACE_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('MARKETPLACE_CLIENT_SECRET', '')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://app.yourplatform.com')

# ---------------------------------------------------------------------------
# Plan ID → tier mapping
#
# When you create plans in Partner Center, use these EXACT plan IDs:
#   basic-monthly
#   core-monthly
#   premium-monthly
#
# This keeps the mapping clean between Marketplace and your internal tiers.
# ---------------------------------------------------------------------------

PLAN_TO_TIER: Dict[str, str] = {
    'basic-monthly':   'basic',
    'core-monthly':    'core',
    'premium-monthly': 'premium',
    # Annual variants (add when ready)
    'basic-annual':    'basic',
    'core-annual':     'core',
    'premium-annual':  'premium',
}

TIER_TO_PLAN: Dict[str, str] = {
    'basic':   'basic-monthly',
    'core':    'core-monthly',
    'premium': 'premium-monthly',
}

# ---------------------------------------------------------------------------
# Tier limits — single source of truth (shared with usage_service.py)
# ---------------------------------------------------------------------------

TIER_LIMITS: Dict[str, Dict] = {
    'trial': {
        'scans_per_month': 10,
        'users': 3,
        'jurisdictions': 1,
        'teams': 1,
        'custom_rules': False,
        'advisory_hours': 0,
    },
    'basic': {
        'scans_per_month': 100,
        'users': 10,
        'jurisdictions': 3,
        'teams': 3,
        'custom_rules': False,
        'advisory_hours': 2,
    },
    'core': {
        'scans_per_month': 500,
        'users': 50,
        'jurisdictions': 10,
        'teams': 10,
        'custom_rules': True,
        'advisory_hours': 10,
    },
    'premium': {
        'scans_per_month': -1,
        'users': -1,
        'jurisdictions': -1,
        'teams': -1,
        'custom_rules': True,
        'advisory_hours': 50,
    },
    'enterprise': {
        'scans_per_month': -1,
        'users': -1,
        'jurisdictions': -1,
        'teams': -1,
        'custom_rules': True,
        'advisory_hours': -1,
    },
}


def get_tier_limits(tier: str) -> Dict:
    """Return limit dict for a tier. Falls back to trial."""
    return TIER_LIMITS.get(tier, TIER_LIMITS['trial'])


# =========================================================================
# OAuth2 — Service-to-service token for Marketplace API
# =========================================================================

_token_cache: Dict = {'access_token': '', 'expires_at': 0}


def _get_marketplace_token() -> str:
    """
    Acquire an OAuth2 access token for the Marketplace Fulfillment API.

    Uses client_credentials flow with the fixed Marketplace resource ID.
    Tokens are cached until 5 minutes before expiry.
    """
    now = time.time()
    if _token_cache['access_token'] and _token_cache['expires_at'] > now + 300:
        return _token_cache['access_token']

    token_url = (
        f"https://login.microsoftonline.com/{PUBLISHER_TENANT_ID}"
        f"/oauth2/token"
    )

    resp = requests.post(token_url, data={
        'grant_type':    'client_credentials',
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'resource':      MARKETPLACE_RESOURCE_ID,
    }, timeout=30)

    resp.raise_for_status()
    data = resp.json()

    _token_cache['access_token'] = data['access_token']
    _token_cache['expires_at'] = now + int(data.get('expires_in', 3600))

    logger.info("Marketplace API token acquired")
    return _token_cache['access_token']


def _marketplace_headers() -> Dict[str, str]:
    """Standard headers for Marketplace API calls."""
    return {
        'Authorization': f"Bearer {_get_marketplace_token()}",
        'Content-Type':  'application/json',
    }


def _marketplace_url(path: str) -> str:
    """Build a full Marketplace API URL with version querystring."""
    separator = '&' if '?' in path else '?'
    return f"{MARKETPLACE_API_BASE}{path}{separator}api-version={MARKETPLACE_API_VERSION}"


# =========================================================================
# SaaS Fulfillment API — Subscription operations
# =========================================================================

def resolve_subscription(marketplace_token: str) -> Dict:
    """
    Exchange the purchase identification token for subscription details.

    Called from the landing page endpoint after the customer clicks
    "Configure Account" in the Azure Portal.

    The token is valid for 24 hours.

    Returns:
        {
            'id': 'subscription-guid',
            'subscriptionName': 'My Subscription',
            'offerId': 'your-offer-id',
            'planId': 'basic-monthly',
            'quantity': 1,
            'subscription': { ... full details ... },
            'purchaser': { 'emailId': '...', 'tenantId': '...' },
            'beneficiary': { 'emailId': '...', 'tenantId': '...' },
            'isFreeTrial': True/False,
            'saasSubscriptionStatus': 'PendingFulfillmentStart',
        }
    """
    resp = requests.post(
        _marketplace_url('/saas/subscriptions/resolve'),
        headers={
            **_marketplace_headers(),
            'x-ms-marketplace-token': marketplace_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    logger.info(
        f"Subscription resolved: {data.get('id')} "
        f"plan={data.get('planId')} offer={data.get('offerId')}"
    )
    return data


def activate_subscription(subscription_id: str, plan_id: str) -> bool:
    """
    Activate a subscription to start billing.

    Must be called AFTER the publisher has provisioned resources for
    the customer. Microsoft starts billing from this point.

    Returns True on success (HTTP 200).
    """
    resp = requests.post(
        _marketplace_url(f'/saas/subscriptions/{subscription_id}/activate'),
        headers=_marketplace_headers(),
        json={'planId': plan_id},
        timeout=30,
    )

    if resp.status_code == 200:
        logger.info(f"Subscription {subscription_id} activated on plan {plan_id}")
        return True

    logger.error(
        f"Subscription activation failed: {resp.status_code} {resp.text}"
    )
    return False


def get_subscription(subscription_id: str) -> Optional[Dict]:
    """Get current subscription details."""
    resp = requests.get(
        _marketplace_url(f'/saas/subscriptions/{subscription_id}'),
        headers=_marketplace_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    logger.warning(f"Get subscription {subscription_id} failed: {resp.status_code}")
    return None


def get_all_subscriptions() -> list:
    """Get all subscriptions for this publisher. Useful for admin dashboards."""
    resp = requests.get(
        _marketplace_url('/saas/subscriptions'),
        headers=_marketplace_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json().get('subscriptions', [])
    logger.warning(f"Get all subscriptions failed: {resp.status_code}")
    return []


def change_plan(subscription_id: str, new_plan_id: str) -> Optional[str]:
    """
    Request a plan change. Returns the operation-location URL for polling.

    The actual plan change is async — Microsoft will fire a webhook
    when it's ready for us to complete.
    """
    resp = requests.patch(
        _marketplace_url(f'/saas/subscriptions/{subscription_id}'),
        headers=_marketplace_headers(),
        json={'planId': new_plan_id},
        timeout=30,
    )

    if resp.status_code == 202:
        op_location = resp.headers.get('Operation-Location', '')
        logger.info(f"Plan change requested for {subscription_id} → {new_plan_id}")
        return op_location

    logger.error(f"Plan change failed: {resp.status_code} {resp.text}")
    return None


def delete_subscription(subscription_id: str) -> Optional[str]:
    """
    Cancel a subscription from the publisher side.

    Returns the operation-location URL for polling.
    """
    resp = requests.delete(
        _marketplace_url(f'/saas/subscriptions/{subscription_id}'),
        headers=_marketplace_headers(),
        timeout=30,
    )

    if resp.status_code == 202:
        op_location = resp.headers.get('Operation-Location', '')
        logger.info(f"Subscription {subscription_id} deletion requested")
        return op_location

    logger.error(f"Delete subscription failed: {resp.status_code} {resp.text}")
    return None


def update_operation_status(
    subscription_id: str,
    operation_id: str,
    status: str,
    plan_id: str = '',
    quantity: int = 0,
) -> bool:
    """
    Acknowledge a webhook operation with Success or Failure.

    Microsoft requires a PATCH within 10 seconds of webhook delivery.

    status: 'Success' or 'Failure'
    """
    body: Dict = {'status': status}
    if plan_id:
        body['planId'] = plan_id
    if quantity:
        body['quantity'] = quantity

    resp = requests.patch(
        _marketplace_url(
            f'/saas/subscriptions/{subscription_id}'
            f'/operations/{operation_id}'
        ),
        headers=_marketplace_headers(),
        json=body,
        timeout=10,
    )

    if resp.status_code == 200:
        logger.info(
            f"Operation {operation_id} acknowledged: {status}"
        )
        return True

    logger.error(
        f"Operation update failed: {resp.status_code} {resp.text}"
    )
    return False


# =========================================================================
# Landing page flow — resolve + provision + activate
# =========================================================================

def handle_landing_page_token(marketplace_token: str) -> Tuple[Dict, Optional[str]]:
    """
    Full landing page flow: resolve token → provision org → activate.

    Called from the API endpoint that the React landing page calls.

    Returns:
        (result_dict, error_string_or_None)
    """
    from function_app_pkg.core.database import (
        get_container,
        get_organization,
        update_organization,
    )

    # Step 1: Resolve the token
    try:
        resolved = resolve_subscription(marketplace_token)
    except requests.HTTPError as e:
        logger.error(f"Token resolution failed: {e}")
        return {}, f"Failed to resolve marketplace token: {e}"

    subscription_id = resolved.get('id', '')
    plan_id = resolved.get('planId', '')
    offer_id = resolved.get('offerId', '')
    status = resolved.get('saasSubscriptionStatus', '')
    is_free_trial = resolved.get('isFreeTrial', False)

    purchaser = resolved.get('purchaser', {})
    beneficiary = resolved.get('beneficiary', {})
    customer_email = (
        beneficiary.get('emailId')
        or purchaser.get('emailId', '')
    )
    customer_tenant = (
        beneficiary.get('tenantId')
        or purchaser.get('tenantId', '')
    )

    tier = PLAN_TO_TIER.get(plan_id, 'basic')
    limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

    # Step 2: Check if we already have this subscription
    container = get_container('organizations')
    existing = list(container.query_items(
        query=(
            "SELECT * FROM c "
            "WHERE c.type = 'organization' "
            "AND c.marketplace_subscription_id = @sub_id"
        ),
        parameters=[{"name": "@sub_id", "value": subscription_id}],
        enable_cross_partition_query=True,
    ))

    if existing:
        org = existing[0]
        org_id = org['id']

        # Returning customer managing their subscription
        if status == 'Subscribed':
            logger.info(
                f"Returning customer: org {org_id}, "
                f"subscription {subscription_id}"
            )
            return {
                'action': 'manage',
                'org_id': org_id,
                'subscription_id': subscription_id,
                'tier': org.get('subscription_tier', tier),
                'status': 'active',
                'customer_email': customer_email,
                'message': 'Subscription already active.',
            }, None

        # Re-activation after suspend or plan change
        update_organization(org_id, {
            'subscription_tier':        tier,
            'subscription_status':      'active',
            'scans_per_month':          limits['scans_per_month'],
            'max_users':                limits['users'],
            'custom_rules_enabled':     limits['custom_rules'],
            'advisory_hours_remaining': limits['advisory_hours'],
            'marketplace_plan_id':      plan_id,
        })

        # Activate with Microsoft if pending
        if status == 'PendingFulfillmentStart':
            activate_subscription(subscription_id, plan_id)

        return {
            'action': 'reactivated',
            'org_id': org_id,
            'subscription_id': subscription_id,
            'tier': tier,
            'status': 'active',
            'customer_email': customer_email,
        }, None

    # Step 3: New customer — provision org
    import uuid
    now = datetime.utcnow().isoformat() + 'Z'

    new_org = {
        'id': str(uuid.uuid4()),
        'type': 'organization',
        'auth_type': 'marketplace',
        'owner_oid': None,
        'name': resolved.get('subscriptionName', f"Org-{subscription_id[:8]}"),
        'azure_tenant_id': customer_tenant,
        # Marketplace fields
        'marketplace_subscription_id': subscription_id,
        'marketplace_offer_id':        offer_id,
        'marketplace_plan_id':         plan_id,
        'marketplace_purchaser':       purchaser,
        'marketplace_beneficiary':     beneficiary,
        'marketplace_is_free_trial':   is_free_trial,
        # Subscription tier & limits
        'subscription_tier':       tier,
        'subscription_status':     'active',
        'signup_completed':        True,
        'scans_per_month':         limits['scans_per_month'],
        'max_users':               limits['users'],
        'custom_rules_enabled':    limits['custom_rules'],
        'advisory_hours_remaining': limits['advisory_hours'],
        'scans_this_month':        0,
        # Not used for marketplace but schema consistency
        'stripe_customer_id':      None,
        'stripe_subscription_id':  None,
        'payment_failed_at':       None,
        'payment_failure_count':   0,
        # Jurisdictions (default for tier)
        'jurisdictions': (
            ['UK'] if tier == 'basic'
            else ['UK', 'ZA', 'US'][:limits.get('jurisdictions', 3)]
            if limits.get('jurisdictions', -1) != -1
            else ['UK', 'ZA', 'US']
        ),
        # Timestamps
        'activated_at': now,
        'created_at':   now,
        'updated_at':   now,
        'settings':     {},
    }

    container.create_item(body=new_org)
    org_id = new_org['id']
    logger.info(
        f"Marketplace org provisioned: {org_id}, "
        f"tier={tier}, subscription={subscription_id}"
    )

    # Step 4: Create initial admin user
    try:
        from function_app_pkg.core.database import create_user

        create_user({
            'email': customer_email.lower() if customer_email else '',
            'name': customer_email.split('@')[0] if customer_email else 'Admin',
            'organization_id': org_id,
            'roles': ['Organization.Admin', 'Marketing.User'],
            'auth_type': 'marketplace',
            'is_active': True,
            'created_at': now,
        })
        logger.info(f"Admin user created: {customer_email}")
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}")

    # Step 5: Activate subscription with Microsoft (start billing)
    if status == 'PendingFulfillmentStart':
        activated = activate_subscription(subscription_id, plan_id)
        if not activated:
            logger.error(
                f"Subscription activation failed for {subscription_id}. "
                f"Customer will need to retry from Azure Portal."
            )
            return {
                'action': 'provisioned_activation_pending',
                'org_id': org_id,
                'subscription_id': subscription_id,
                'tier': tier,
                'customer_email': customer_email,
                'warning': (
                    'Account created but billing activation pending. '
                    'If this persists, re-open the subscription in Azure Portal '
                    'and click "Configure Account" again.'
                ),
            }, None

    return {
        'action': 'provisioned',
        'org_id': org_id,
        'subscription_id': subscription_id,
        'tier': tier,
        'status': 'active',
        'is_free_trial': is_free_trial,
        'customer_email': customer_email,
        'message': f"Welcome! Your {tier} plan is now active.",
    }, None


# =========================================================================
# Webhook processing
# =========================================================================

def process_webhook_event(payload: Dict) -> Dict:
    """
    Route a Marketplace webhook event to the correct handler.

    Webhook payload shape:
    {
        "id": "operation-guid",
        "activityId": "guid",
        "subscriptionId": "subscription-guid",
        "publisherId": "your-publisher-id",
        "offerId": "your-offer-id",
        "planId": "basic-monthly",
        "quantity": 10,
        "action": "ChangePlan" | "ChangeQuantity" | "Unsubscribe" |
                  "Suspend" | "Reinstate" | "Renew",
        "status": "InProgress",
        "timeStamp": "2026-02-18T00:00:00Z",
        "subscription": { ... full subscription details ... }
    }
    """
    action = payload.get('action', '')
    subscription_id = payload.get('subscriptionId', '')
    operation_id = payload.get('id', '')

    logger.info(
        f"Marketplace webhook: action={action}, "
        f"subscription={subscription_id}, operation={operation_id}"
    )

    handlers = {
        'ChangePlan':      _on_change_plan,
        'ChangeQuantity':  _on_change_quantity,
        'Unsubscribe':     _on_unsubscribe,
        'Suspend':         _on_suspend,
        'Reinstate':       _on_reinstate,
        'Renew':           _on_renew,
    }

    handler = handlers.get(action)
    if not handler:
        logger.warning(f"Unhandled marketplace webhook action: {action}")
        return {'handled': False, 'action': action}

    result = handler(payload)
    return {'handled': True, 'action': action, **result}


def _find_org_by_subscription(subscription_id: str) -> Optional[Dict]:
    """Look up the org document by marketplace_subscription_id."""
    from function_app_pkg.core.database import get_container

    items = list(get_container('organizations').query_items(
        query=(
            "SELECT * FROM c "
            "WHERE c.type = 'organization' "
            "AND c.marketplace_subscription_id = @sub_id"
        ),
        parameters=[{"name": "@sub_id", "value": subscription_id}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def _on_change_plan(payload: Dict) -> Dict:
    """Customer changed plan in Azure Portal — acknowledge and update."""
    from function_app_pkg.core.database import update_organization

    subscription_id = payload['subscriptionId']
    operation_id = payload['id']
    new_plan_id = payload.get('planId', '')

    org = _find_org_by_subscription(subscription_id)
    if not org:
        logger.error(f"ChangePlan: org not found for subscription {subscription_id}")
        update_operation_status(subscription_id, operation_id, 'Failure')
        return {'error': 'org_not_found'}

    tier = PLAN_TO_TIER.get(new_plan_id, 'basic')
    limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

    update_organization(org['id'], {
        'subscription_tier':        tier,
        'marketplace_plan_id':      new_plan_id,
        'scans_per_month':          limits['scans_per_month'],
        'max_users':                limits['users'],
        'custom_rules_enabled':     limits['custom_rules'],
        'advisory_hours_remaining': limits['advisory_hours'],
        'updated_at':               datetime.utcnow().isoformat() + 'Z',
    })

    # Acknowledge success to Microsoft (must be within 10 seconds)
    update_operation_status(
        subscription_id, operation_id, 'Success', plan_id=new_plan_id,
    )

    logger.info(f"Org {org['id']} plan changed to {tier} ({new_plan_id})")
    return {'org_id': org['id'], 'tier': tier}


def _on_change_quantity(payload: Dict) -> Dict:
    """Customer changed seat count. Acknowledge and update."""
    from function_app_pkg.core.database import update_organization

    subscription_id = payload['subscriptionId']
    operation_id = payload['id']
    new_quantity = payload.get('quantity', 0)

    org = _find_org_by_subscription(subscription_id)
    if not org:
        update_operation_status(subscription_id, operation_id, 'Failure')
        return {'error': 'org_not_found'}

    update_organization(org['id'], {
        'marketplace_quantity': new_quantity,
        'max_users': new_quantity if new_quantity > 0 else org.get('max_users', -1),
        'updated_at': datetime.utcnow().isoformat() + 'Z',
    })

    update_operation_status(
        subscription_id, operation_id, 'Success', quantity=new_quantity,
    )

    logger.info(f"Org {org['id']} quantity changed to {new_quantity}")
    return {'org_id': org['id'], 'quantity': new_quantity}


def _on_unsubscribe(payload: Dict) -> Dict:
    """Customer cancelled. Downgrade to trial. No ACK needed (notify-only)."""
    from function_app_pkg.core.database import update_organization

    subscription_id = payload['subscriptionId']
    org = _find_org_by_subscription(subscription_id)
    if not org:
        return {'error': 'org_not_found'}

    trial = TIER_LIMITS['trial']
    update_organization(org['id'], {
        'subscription_tier':        'trial',
        'subscription_status':      'canceled',
        'marketplace_plan_id':      None,
        'scans_per_month':          trial['scans_per_month'],
        'max_users':                trial['users'],
        'custom_rules_enabled':     False,
        'advisory_hours_remaining': 0,
        'canceled_at':              datetime.utcnow().isoformat() + 'Z',
        'updated_at':               datetime.utcnow().isoformat() + 'Z',
    })

    logger.warning(f"Org {org['id']} subscription cancelled — downgraded to trial")
    return {'org_id': org['id'], 'tier': 'trial'}


def _on_suspend(payload: Dict) -> Dict:
    """Payment issue — suspend the org. No ACK needed (notify-only)."""
    from function_app_pkg.core.database import update_organization

    subscription_id = payload['subscriptionId']
    org = _find_org_by_subscription(subscription_id)
    if not org:
        return {'error': 'org_not_found'}

    update_organization(org['id'], {
        'subscription_status': 'suspended',
        'suspended_at':        datetime.utcnow().isoformat() + 'Z',
        'updated_at':          datetime.utcnow().isoformat() + 'Z',
    })

    logger.warning(f"Org {org['id']} suspended (payment issue)")
    return {'org_id': org['id'], 'status': 'suspended'}


def _on_reinstate(payload: Dict) -> Dict:
    """Payment resolved — reinstate the org. No ACK needed (notify-only)."""
    from function_app_pkg.core.database import update_organization

    subscription_id = payload['subscriptionId']
    org = _find_org_by_subscription(subscription_id)
    if not org:
        return {'error': 'org_not_found'}

    update_organization(org['id'], {
        'subscription_status': 'active',
        'suspended_at':        None,
        'updated_at':          datetime.utcnow().isoformat() + 'Z',
    })

    logger.info(f"Org {org['id']} reinstated")
    return {'org_id': org['id'], 'status': 'active'}


def _on_renew(payload: Dict) -> Dict:
    """
    Subscription auto-renewed. No ACK needed (notify-only).

    Reset monthly usage counter on renewal.
    """
    from function_app_pkg.core.usage_service import reset_monthly_usage

    subscription_id = payload['subscriptionId']
    org = _find_org_by_subscription(subscription_id)
    if not org:
        return {'error': 'org_not_found'}

    reset_monthly_usage(org['id'])
    logger.info(f"Org {org['id']} renewed — usage reset")
    return {'org_id': org['id'], 'status': 'renewed'}