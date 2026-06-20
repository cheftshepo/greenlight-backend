"""
Stripe Billing Service
======================
Core Stripe integration for self-service SaaS billing.
Handles customer creation, checkout sessions, webhooks, and tier management.

File: function_app_pkg/core/stripe_service.py

Environment variables required:
    STRIPE_SECRET_KEY           - Stripe API secret key
    STRIPE_WEBHOOK_SECRET       - Stripe webhook signing secret
    STRIPE_PRICE_BASIC_MONTHLY  - price_xxx for Basic tier
    STRIPE_PRICE_CORE_MONTHLY   - price_xxx for Core tier
    STRIPE_PRICE_PREMIUM_MONTHLY - price_xxx for Premium tier
    FRONTEND_URL                - Frontend base URL for redirects
"""

import os
import logging
import stripe
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://app.yourplatform.com')

PRICE_IDS: Dict[str, Optional[str]] = {
    'basic_monthly':   os.getenv('STRIPE_PRICE_BASIC_MONTHLY'),
    'core_monthly':    os.getenv('STRIPE_PRICE_CORE_MONTHLY'),
    'premium_monthly': os.getenv('STRIPE_PRICE_PREMIUM_MONTHLY'),
}

# Reverse lookup: price_xxx → tier name
TIER_FROM_PRICE: Dict[str, str] = {
    v: k.replace('_monthly', '')
    for k, v in PRICE_IDS.items() if v
}

# ---------------------------------------------------------------------------
# Tier limits — single source of truth
# Must stay in sync with database.py SubscriptionTier / TIER_LIMITS
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
# Customer management
# =========================================================================

def create_stripe_customer(email: str, org_name: str, org_id: str) -> str:
    """Create a Stripe Customer and return cus_xxx id."""
    customer = stripe.Customer.create(
        email=email,
        name=org_name,
        metadata={
            'organization_id': org_id,
            'platform': 'compliance-saas',
        },
    )
    logger.info(f"Stripe customer created: {customer.id} for org {org_id}")
    return customer.id


def create_checkout_session(
    customer_id: str,
    price_id: str,
    org_id: str,
    user_email: str,
    trial_days: int = 14,
) -> str:
    """Create a Stripe Checkout session. Returns the hosted checkout URL."""
    params: Dict = {
        'customer': customer_id,
        'payment_method_types': ['card'],
        'line_items': [{'price': price_id, 'quantity': 1}],
        'mode': 'subscription',
        'allow_promotion_codes': True,
        'subscription_data': {
            'metadata': {
                'organization_id': org_id,
                'user_email': user_email,
            },
        },
        'metadata': {
            'organization_id': org_id,
            'user_email': user_email,
        },
        'success_url': (
            f"{FRONTEND_URL}/onboarding/success"
            f"?session_id={{CHECKOUT_SESSION_ID}}&org_id={org_id}"
        ),
        'cancel_url': f"{FRONTEND_URL}/pricing?cancelled=true",
    }

    if trial_days and trial_days > 0:
        params['subscription_data']['trial_period_days'] = trial_days

    session = stripe.checkout.Session.create(**params)
    logger.info(
        f"Checkout session created for org {org_id}, "
        f"tier {TIER_FROM_PRICE.get(price_id, '?')}"
    )
    return session.url


def create_billing_portal_session(
    customer_id: str,
    return_path: str = '/settings/billing',
) -> str:
    """Create a Stripe Billing Portal session. Returns the portal URL."""
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{FRONTEND_URL}{return_path}",
    )
    return session.url


def get_upcoming_invoice(customer_id: str) -> Optional[Dict]:
    """Return next invoice preview, or None if unavailable."""
    try:
        inv = stripe.Invoice.upcoming(customer=customer_id)
        return {
            'amount_due': inv.get('amount_due', 0) / 100,
            'currency': inv.get('currency', 'usd').upper(),
            'next_payment_attempt': inv.get('next_payment_attempt'),
            'period_end': inv.get('period_end'),
        }
    except stripe.error.StripeError as e:
        logger.debug(f"No upcoming invoice for {customer_id}: {e}")
        return None


# =========================================================================
# Webhook processing
# =========================================================================

def construct_webhook_event(payload: bytes, signature: str) -> Dict:
    """Verify Stripe webhook signature and parse the event."""
    return stripe.Webhook.construct_event(
        payload, signature, STRIPE_WEBHOOK_SECRET,
    )


def process_webhook_event(event: Dict) -> Dict:
    """Route a verified Stripe event to the correct handler."""
    handlers = {
        'checkout.session.completed':    _on_checkout_completed,
        'customer.subscription.created': _on_subscription_changed,
        'customer.subscription.updated': _on_subscription_changed,
        'customer.subscription.deleted': _on_subscription_deleted,
        'invoice.payment_failed':        _on_payment_failed,
        'invoice.payment_succeeded':     _on_payment_succeeded,
    }
    handler = handlers.get(event['type'])
    if not handler:
        logger.debug(f"Unhandled Stripe event type: {event['type']}")
        return {'handled': False, 'event_type': event['type']}

    result = handler(event['data']['object'])
    return {'handled': True, 'event_type': event['type'], **result}


# ---------------------------------------------------------------------------
# Internal webhook handlers
# ---------------------------------------------------------------------------

def _on_checkout_completed(session: Dict) -> Dict:
    """Activate org after successful checkout."""
    from function_app_pkg.core.database import update_organization

    org_id = (session.get('metadata') or {}).get('organization_id')
    if not org_id:
        logger.error("checkout.session.completed missing organization_id in metadata")
        return {'error': 'missing_org_id'}

    # Determine tier from the subscription's price
    tier = 'basic'
    sub_id = session.get('subscription')
    if sub_id:
        try:
            sub = stripe.Subscription.retrieve(sub_id, expand=['items.data.price'])
            price_id = sub['items']['data'][0]['price']['id']
            tier = TIER_FROM_PRICE.get(price_id, 'basic')
        except Exception as e:
            logger.warning(f"Could not resolve tier from subscription: {e}")

    limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

    update_organization(org_id, {
        'signup_completed':        True,
        'subscription_tier':       tier,
        'subscription_status':     sub.get('status', 'trialing') if sub_id else 'active',
        'stripe_customer_id':      session.get('customer'),
        'stripe_subscription_id':  sub_id,
        'scans_per_month':         limits['scans_per_month'],
        'max_users':               limits['users'],
        'custom_rules_enabled':    limits['custom_rules'],
        'advisory_hours_remaining': limits['advisory_hours'],
        'activated_at':            datetime.utcnow().isoformat() + 'Z',
    })

    _send_welcome_email(org_id, tier)
    logger.info(f"Org {org_id} activated on {tier} tier")
    return {'org_id': org_id, 'tier': tier}


def _on_subscription_changed(subscription: Dict) -> Dict:
    """Handle subscription create or update (plan change, trial→active, etc)."""
    from function_app_pkg.core.database import update_organization

    org_id = _resolve_org_id(subscription)
    if not org_id:
        return {'error': 'org_not_found'}

    price_id = subscription['items']['data'][0]['price']['id']
    tier = TIER_FROM_PRICE.get(price_id, 'basic')
    limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

    update_organization(org_id, {
        'subscription_tier':       tier,
        'subscription_status':     subscription.get('status', 'active'),
        'stripe_subscription_id':  subscription.get('id'),
        'scans_per_month':         limits['scans_per_month'],
        'max_users':               limits['users'],
        'custom_rules_enabled':    limits['custom_rules'],
        'advisory_hours_remaining': limits['advisory_hours'],
        'subscription_expires':    datetime.utcfromtimestamp(
            subscription.get('current_period_end', 0)
        ).isoformat() + 'Z',
    })

    logger.info(f"Org {org_id} subscription updated to {tier}")
    return {'org_id': org_id, 'tier': tier}


def _on_subscription_deleted(subscription: Dict) -> Dict:
    """Downgrade org to trial when subscription is cancelled."""
    from function_app_pkg.core.database import update_organization

    org_id = _resolve_org_id(subscription)
    if not org_id:
        return {'error': 'org_not_found'}

    trial = TIER_LIMITS['trial']
    update_organization(org_id, {
        'subscription_tier':       'trial',
        'subscription_status':     'canceled',
        'stripe_subscription_id':  None,
        'scans_per_month':         trial['scans_per_month'],
        'max_users':               trial['users'],
        'custom_rules_enabled':    False,
        'advisory_hours_remaining': 0,
    })

    logger.warning(f"Org {org_id} subscription cancelled — downgraded to trial")
    return {'org_id': org_id, 'tier': 'trial'}


def _on_payment_failed(invoice: Dict) -> Dict:
    """Mark org as past_due on failed payment."""
    from function_app_pkg.core.database import update_organization

    org_id = _org_id_from_customer(invoice.get('customer'))
    if org_id:
        update_organization(org_id, {
            'subscription_status':   'past_due',
            'payment_failed_at':     datetime.utcnow().isoformat() + 'Z',
            'payment_failure_count': invoice.get('attempt_count', 1),
        })
        logger.warning(f"Org {org_id} payment failed (attempt {invoice.get('attempt_count', 1)})")
    return {'org_id': org_id, 'status': 'past_due'}


def _on_payment_succeeded(invoice: Dict) -> Dict:
    """Clear past_due status and reset monthly usage on successful payment."""
    from function_app_pkg.core.database import update_organization

    org_id = _org_id_from_customer(invoice.get('customer'))
    if org_id:
        update_organization(org_id, {
            'subscription_status':   'active',
            'payment_failed_at':     None,
            'payment_failure_count': 0,
            'last_payment_at':       datetime.utcnow().isoformat() + 'Z',
        })
        # Reset monthly scan counter on new billing period
        from function_app_pkg.core.usage_service import reset_monthly_usage
        reset_monthly_usage(org_id)
        logger.info(f"Org {org_id} payment succeeded — usage reset")
    return {'org_id': org_id, 'status': 'active'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_org_id(subscription: Dict) -> Optional[str]:
    """Get org_id from subscription metadata or by customer lookup."""
    org_id = (subscription.get('metadata') or {}).get('organization_id')
    if not org_id:
        org_id = _org_id_from_customer(subscription.get('customer'))
    return org_id


def _org_id_from_customer(customer_id: str) -> Optional[str]:
    """Query Cosmos for the org linked to a Stripe customer ID."""
    if not customer_id:
        return None
    try:
        from function_app_pkg.core.database import get_container
        items = list(get_container('organizations').query_items(
            query="SELECT c.id FROM c WHERE c.stripe_customer_id = @cid",
            parameters=[{"name": "@cid", "value": customer_id}],
            enable_cross_partition_query=True,
        ))
        return items[0]['id'] if items else None
    except Exception as e:
        logger.error(f"_org_id_from_customer lookup failed: {e}")
        return None


def _send_welcome_email(org_id: str, tier: str):
    """Send welcome email to org admin. Gracefully no-ops if email not configured."""
    try:
        from function_app_pkg.core.database import get_organization, get_users_by_org

        org = get_organization(org_id)
        if not org:
            return

        users = get_users_by_org(org_id, limit=1)
        if not users:
            return

        admin = users[0]
        limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

        # TODO: Replace with your email_service.send_email() when ready
        logger.info(
            f"WELCOME EMAIL (not sent — email service not configured): "
            f"to={admin.get('email')}, org={org.get('name')}, tier={tier}, "
            f"scans={limits['scans_per_month']}, users={limits['users']}"
        )

    except Exception as e:
        # Never fail the webhook because of an email error
        logger.error(f"Welcome email failed for org {org_id}: {e}")