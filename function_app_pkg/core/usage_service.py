"""
Usage Service — Subscription Limit Enforcement & Metering
==========================================================
Called before scans, user invites, and jurisdiction access to enforce
tier limits. Works identically for enterprise, marketplace, and
any future billing provider.

Enterprise and premium (-1) tiers always pass.

File: function_app_pkg/core/usage_service.py
"""

import logging
from datetime import datetime
from typing import Dict, Tuple

from function_app_pkg.core.database import (
    get_container,
    get_organization,
    update_organization,
)
from function_app_pkg.core.marketplace_service import get_tier_limits

logger = logging.getLogger(__name__)


# =========================================================================
# Limit checks — return (allowed: bool, reason: str)
# =========================================================================

def check_scan_limit(org_id: str) -> Tuple[bool, str]:
    """
    Check if an org can perform another scan this billing period.

    Call at the TOP of scan.py handle() before any processing.
    Returns (True, "ok") or (False, "human-readable reason").
    """
    org = get_organization(org_id)
    if not org:
        return False, "Organisation not found."

    tier = org.get('subscription_tier', 'trial')

    # Enterprise / premium always pass
    if tier in ('enterprise', 'premium'):
        return True, "ok"

    # Hard block on cancelled subscriptions
    status = org.get('subscription_status', 'active')
    if status == 'canceled':
        return False, (
            "Your subscription has been cancelled. "
            "Please renew to continue scanning."
        )

    # Suspended gets a warning but we allow (Microsoft gives 30 days)
    if status == 'suspended':
        logger.warning(f"Org {org_id} is suspended — allowing scan (grace period)")

    # Monthly scan cap
    limits = get_tier_limits(tier)
    monthly_limit = limits.get('scans_per_month', 10)

    if monthly_limit == -1:
        return True, "ok"

    used = org.get('scans_this_month', 0)
    if used >= monthly_limit:
        return False, (
            f"Monthly scan limit reached ({used}/{monthly_limit}). "
            f"Upgrade your plan to continue scanning."
        )

    return True, "ok"


def check_user_limit(org_id: str) -> Tuple[bool, str]:
    """
    Check if an org can add another user.

    Call before creating / inviting a user.
    """
    org = get_organization(org_id)
    if not org:
        return False, "Organisation not found."

    tier = org.get('subscription_tier', 'trial')
    if tier in ('enterprise', 'premium'):
        return True, "ok"

    limits = get_tier_limits(tier)
    max_users = limits.get('users', 3)
    if max_users == -1:
        return True, "ok"

    # Count active users in org
    container = get_container('users')
    result = list(container.query_items(
        query=(
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.organization_id = @oid "
            "AND c.type = 'user' "
            "AND c.is_active = true"
        ),
        parameters=[{"name": "@oid", "value": org_id}],
        partition_key=org_id,
    ))
    current = result[0] if result else 0

    if current >= max_users:
        return False, (
            f"User limit reached ({current}/{max_users}). "
            f"Upgrade your plan to add more team members."
        )

    return True, "ok"


def check_jurisdiction_access(org_id: str, jurisdiction: str) -> Tuple[bool, str]:
    """
    Check if an org's plan includes the requested jurisdiction.

    Enterprise / unlimited tiers bypass the list check entirely.
    """
    org = get_organization(org_id)
    if not org:
        return False, "Organisation not found."

    tier = org.get('subscription_tier', 'trial')
    limits = get_tier_limits(tier)

    # Unlimited jurisdictions
    if limits.get('jurisdictions') == -1:
        return True, "ok"

    allowed = org.get('jurisdictions', [])
    if jurisdiction not in allowed:
        return False, (
            f"Jurisdiction '{jurisdiction}' is not included in your {tier} plan. "
            f"Your plan covers: {', '.join(allowed) if allowed else 'none'}. "
            f"Upgrade to access more jurisdictions."
        )

    return True, "ok"


# =========================================================================
# Metering
# =========================================================================

def increment_scan_count(org_id: str) -> int:
    """
    Increment the monthly scan counter. Call AFTER a successful scan.

    Returns the new count.
    """
    org = get_organization(org_id)
    if not org:
        return 0

    new_count = org.get('scans_this_month', 0) + 1
    update_organization(org_id, {'scans_this_month': new_count})
    return new_count


def reset_monthly_usage(org_id: str):
    """
    Reset monthly scan counter. Called by marketplace_service on
    Renew webhook (start of new billing period).
    """
    update_organization(org_id, {
        'scans_this_month': 0,
        'usage_reset_at': datetime.utcnow().isoformat() + 'Z',
    })
    logger.info(f"Monthly usage reset for org {org_id}")


# =========================================================================
# Usage dashboard
# =========================================================================

def get_usage_summary(org_id: str) -> Dict:
    """
    Full usage snapshot for the billing settings page.

    Returns current usage vs tier limits with percentages.
    """
    org = get_organization(org_id)
    if not org:
        return {}

    tier = org.get('subscription_tier', 'trial')
    limits = get_tier_limits(tier)

    # Live user count
    container = get_container('users')
    user_result = list(container.query_items(
        query=(
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.organization_id = @oid "
            "AND c.type = 'user' "
            "AND c.is_active = true"
        ),
        parameters=[{"name": "@oid", "value": org_id}],
        partition_key=org_id,
    ))
    active_users = user_result[0] if user_result else 0

    scans_used = org.get('scans_this_month', 0)
    scans_limit = limits['scans_per_month']
    users_limit = limits['users']

    def _pct(used: int, limit: int) -> float:
        if limit <= 0:
            return 0.0
        return round(used / limit * 100, 1)

    return {
        'tier': tier,
        'status': org.get('subscription_status', 'active'),
        'billing_type': org.get('auth_type', 'enterprise'),
        'scans': {
            'used': scans_used,
            'limit': scans_limit,
            'unlimited': scans_limit == -1,
            'percent': _pct(scans_used, scans_limit),
        },
        'users': {
            'used': active_users,
            'limit': users_limit,
            'unlimited': users_limit == -1,
            'percent': _pct(active_users, users_limit),
        },
        'features': {
            'custom_rules': limits['custom_rules'],
            'jurisdictions': limits['jurisdictions'],
            'teams': limits['teams'],
            'advisory_hours': limits['advisory_hours'],
            'advisory_hours_used': max(
                0,
                limits['advisory_hours'] - org.get('advisory_hours_remaining', 0),
            ) if limits['advisory_hours'] > 0 else 0,
        },
        'marketplace_subscription_id': org.get('marketplace_subscription_id'),
        'next_reset': org.get('usage_reset_at'),
    }