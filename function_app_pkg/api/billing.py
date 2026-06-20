"""
Billing & Marketplace API
==========================
Endpoints the React frontend calls for:
    - Landing page token resolution (after Azure Marketplace purchase)
    - Billing overview (current plan, usage, limits)
    - Plan change (trigger from your UI)
    - Usage summary

Routes (register in function_app.py):
    POST /api/marketplace/resolve    → resolve landing page token
    GET  /api/billing                → plan overview, usage
    POST /api/billing/change-plan    → initiate plan change
    GET  /api/billing/usage          → usage summary

File: function_app_pkg/api/billing.py
"""

import logging
import azure.functions as func

from function_app_pkg.core.marketplace_service import (
    handle_landing_page_token,
    change_plan,
    get_subscription,
    TIER_TO_PLAN,
    TIER_LIMITS,
    PLAN_TO_TIER,
)
from function_app_pkg.core.usage_service import get_usage_summary
from function_app_pkg.core.database import get_organization, update_organization
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =========================================================================
# POST /api/marketplace/resolve
# =========================================================================

def handle_marketplace_resolve(req: func.HttpRequest) -> func.HttpResponse:
    """
    Resolve a Marketplace purchase token.

    Called by the React landing page after the customer is redirected
    from Azure Portal with ?token=xxx.

    Body: { "token": "marketplace-purchase-token" }

    Returns the provisioned org details so the frontend can redirect
    to the dashboard.

    No @require_auth — the customer doesn't have an account yet
    at the time of first purchase. The token itself is the auth.
    """
    try:
        body = req.get_json()
    except ValueError:
        return json_response(400, error="Invalid JSON body")

    token = (body.get('token') or '').strip()
    if not token:
        return json_response(400, error="Missing marketplace token")

    result, error = handle_landing_page_token(token)

    if error:
        logger.error(f"Marketplace resolve failed: {error}")
        return json_response(400, error=error)

    return json_response(200, data=result)


# =========================================================================
# GET /api/billing
# =========================================================================

def handle_billing_overview(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    Current plan, live usage, and plan details.

    Enterprise clients get a simple "managed" response.
    Marketplace clients get full usage + plan info.
    Self-service trial clients get trial info.
    """
    org = get_organization(user.organization_id)
    if not org:
        return json_response(404, error="Organisation not found")

    auth_type = org.get('auth_type', 'entra_enterprise')

    # Enterprise clients — offline billing
    if auth_type == 'entra_enterprise':
        return json_response(200, data={
            'billing_type': 'enterprise',
            'tier': 'enterprise',
            'message': 'Enterprise billing is managed by your account manager.',
            'limits': {
                'scans_per_month': -1,
                'max_users': -1,
                'jurisdictions': -1,
                'teams': -1,
                'custom_rules': True,
                'advisory_hours': -1,
            }
        })

    # Self-service trial clients
    if auth_type == 'entra_external':
        tier = org.get('subscription_tier', 'trial')
        usage = get_usage_summary(user.organization_id)
        limits = TIER_LIMITS.get(tier, TIER_LIMITS['trial'])
        
        return json_response(200, data={
            'billing_type': 'self_service',
            'tier': tier,
            'status': org.get('subscription_status', 'trialing'),
            'signup_completed': org.get('signup_completed', False),
            'usage': usage,
            'limits': limits,
            'message': 'Upgrade via Azure Marketplace to unlock more features.',
            'marketplace_offers': {
                'basic': 'https://portal.azure.com/#create/Microsoft.Template/uri...',  # Add your actual links
                'core': 'https://portal.azure.com/#create/Microsoft.Template/uri...',
                'premium': 'https://portal.azure.com/#create/Microsoft.Template/uri...',
            }
        })

    # Marketplace clients
    tier = org.get('subscription_tier', 'basic')
    usage = get_usage_summary(user.organization_id)
    limits = TIER_LIMITS.get(tier, TIER_LIMITS['basic'])

    # Get subscription details from Microsoft if we have one
    sub_id = org.get('marketplace_subscription_id')
    marketplace_status = None
    if sub_id:
        try:
            sub = get_subscription(sub_id)
            if sub:
                marketplace_status = {
                    'subscription_id': sub_id,
                    'subscription_name': sub.get('subscriptionName', ''),
                    'plan_id': sub.get('planId'),
                    'plan_display_name': sub.get('planDisplayName', sub.get('planId', '')),
                    'offer_id': sub.get('offerId'),
                    'offer_name': sub.get('offerName', sub.get('offerId', '')),
                    'status': sub.get('saasSubscriptionStatus'),
                    'is_free_trial': sub.get('isFreeTrial', False),
                    'term': sub.get('term', {}),
                    'auto_renew': sub.get('autoRenew', True),
                    'beneficiary_email': sub.get('beneficiary', {}).get('emailId'),
                    'purchaser_email': sub.get('purchaser', {}).get('emailId'),
                    'created': sub.get('created', ''),
                }
        except Exception as e:
            logger.warning(f"Could not fetch marketplace subscription: {e}")

    return json_response(200, data={
        'billing_type': 'marketplace',
        'tier': tier,
        'status': org.get('subscription_status', 'active'),
        'signup_completed': org.get('signup_completed', True),
        'usage': usage,
        'limits': limits,
        'marketplace': marketplace_status,
        'available_plans': _available_plans(tier),
        'manage_url': (
            'https://portal.azure.com/#view/HubsExtension/'
            'BrowseResourceBlade/resourceType/'
            'Microsoft.SaaS%2Fresources'
        ),
    })


# =========================================================================
# POST /api/billing/change-plan
# =========================================================================

def handle_change_plan(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    Initiate a plan change from the publisher side.

    Body: { "plan": "core" }

    This calls the Marketplace API to request the change. Microsoft
    will then fire a ChangePlan webhook which our webhook handler
    acknowledges and applies.

    Alternatively, the customer can change plans directly in the
    Azure Portal — we handle that via webhook too.
    """
    try:
        body = req.get_json()
    except ValueError:
        return json_response(400, error="Invalid JSON body")

    target_tier = (body.get('plan') or '').lower().strip()
    target_plan = TIER_TO_PLAN.get(target_tier)

    if not target_plan:
        valid = list(TIER_TO_PLAN.keys())
        return json_response(
            400,
            error=f"Invalid plan '{target_tier}'. Choose from: {', '.join(valid)}",
        )

    org = get_organization(user.organization_id)
    if not org:
        return json_response(404, error="Organisation not found")

    sub_id = org.get('marketplace_subscription_id')
    if not sub_id:
        return json_response(
            400,
            error=(
                "No marketplace subscription found. "
                "Please purchase through Azure Marketplace first."
            ),
        )

    current_tier = org.get('subscription_tier', 'trial')
    if current_tier == target_tier:
        return json_response(400, error=f"Already on the {target_tier} plan.")

    # Request plan change via Marketplace API
    try:
        op_location = change_plan(sub_id, target_plan)
    except Exception as e:
        logger.error(f"Marketplace API error: {e}")
        return json_response(
            503,
            error="Unable to contact Microsoft Marketplace. Please try again later."
        )

    if not op_location:
        return json_response(
            500,
            error=(
                "Plan change request failed. "
                "Try changing your plan in the Azure Portal instead: "
                "https://portal.azure.com/#view/HubsExtension/BrowseResourceBlade/resourceType/Microsoft.SaaS%2Fresources"
            ),
        )

    return json_response(200, data={
        'message': (
            f"Plan change to {target_tier} requested. "
            f"This may take a few moments to take effect."
        ),
        'target_plan': target_tier,
        'operation_location': op_location,
        'azure_portal_url': (
            'https://portal.azure.com/#view/HubsExtension/'
            'BrowseResourceBlade/resourceType/'
            'Microsoft.SaaS%2Fresources'
        ),
    })


# =========================================================================
# GET /api/billing/usage
# =========================================================================

def handle_billing_usage(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    Get current usage summary.
    Simple endpoint that just returns usage data.
    """
    usage = get_usage_summary(user.organization_id)
    return json_response(200, data=usage)


# =========================================================================
# Helpers
# =========================================================================

def _available_plans(current_tier: str) -> list:
    """Return all plans with their limits for the pricing page."""
    plans = []
    for tier in ['basic', 'core', 'premium']:
        limits = TIER_LIMITS.get(tier, {})
        plans.append({
            'tier': tier,
            'plan_id': TIER_TO_PLAN.get(tier, ''),
            'display_name': tier.capitalize(),
            'is_current': tier == current_tier,
            'scans_per_month': limits.get('scans_per_month'),
            'max_users': limits.get('users'),
            'jurisdictions': limits.get('jurisdictions'),
            'teams': limits.get('teams'),
            'custom_rules': limits.get('custom_rules', False),
            'advisory_hours': limits.get('advisory_hours', 0),
            'azure_marketplace_url': f"https://azuremarketplace.microsoft.com/en-us/marketplace/apps/your-publisher-id/your-offer?plan={TIER_TO_PLAN.get(tier, '')}"
        })
    return plans


# =========================================================================
# Stripe endpoints removed - using pure Microsoft Marketplace
# =========================================================================

# Note: handle_create_checkout and handle_stripe_webhook have been removed
# as we're using pure Microsoft Marketplace billing.