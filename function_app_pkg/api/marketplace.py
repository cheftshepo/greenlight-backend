"""
Azure Marketplace Integration - Complete Implementation
=======================================================

Handles:
1. Landing page (customer activation after subscribe)
2. Webhook (subscription lifecycle events)
3. Microsoft API calls (resolve token, activate subscription)
4. Database storage of subscriptions

File: function_app_pkg/api/marketplace.py
"""

import azure.functions as func
import logging
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional
import uuid
import jwt
import json

from ..core.database import get_db
from ..shared.http_utils import json_response

logger = logging.getLogger(__name__)

# Microsoft Marketplace API configuration
MARKETPLACE_API_VERSION = "2018-08-31"
MARKETPLACE_API_BASE = "https://marketplaceapi.microsoft.com/api"

# Your Azure AD credentials (from App Registration)
TENANT_ID = os.getenv("AZURE_MARKETPLACE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_MARKETPLACE_CLIENT_ID")  
CLIENT_SECRET = os.getenv("AZURE_MARKETPLACE_CLIENT_SECRET")

# Subscription status constants
STATUS_PENDING = "PendingFulfillmentStart"
STATUS_SUBSCRIBED = "Subscribed"
STATUS_SUSPENDED = "Suspended"
STATUS_UNSUBSCRIBED = "Unsubscribed"


# ============================================================================
# HELPER: Get Microsoft Access Token
# ============================================================================

def _get_marketplace_access_token() -> Optional[str]:
    """
    Get access token for Microsoft Marketplace API
    Uses client credentials flow
    """
    try:
        token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
        
        data = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "20e940b3-4c77-4b0b-9a53-9e16a1b010a7/.default"  # Marketplace API scope
        }
        
        response = requests.post(token_url, data=data, timeout=10)
        response.raise_for_status()
        
        token_data = response.json()
        return token_data.get("access_token")
        
    except Exception as e:
        logger.error(f"❌ Failed to get marketplace access token: {e}")
        return None


# ============================================================================
# HELPER: Call Microsoft Marketplace API
# ============================================================================

def _call_marketplace_api(
    method: str,
    endpoint: str,
    token: str,
    json_data: Optional[Dict] = None,
    params: Optional[Dict] = None
) -> Optional[Dict]:
    """
    Make authenticated call to Microsoft Marketplace API
    """
    try:
        url = f"{MARKETPLACE_API_BASE}/{endpoint}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-ms-marketplace-api-version": MARKETPLACE_API_VERSION
        }
        
        if method == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=json_data, timeout=30)
        elif method == "PATCH":
            response = requests.patch(url, headers=headers, json=json_data, timeout=30)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        response.raise_for_status()
        
        # Some endpoints return 204 No Content
        if response.status_code == 204:
            return {}
        
        return response.json()
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ Marketplace API HTTP error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"❌ Marketplace API call failed: {e}")
        return None


# ============================================================================
# HELPER: Resolve Marketplace Token
# ============================================================================

def _resolve_marketplace_token(token: str) -> Optional[Dict]:
    """
    Exchange marketplace token for subscription details
    
    Returns subscription info:
    {
        "id": "subscription-id-uuid",
        "subscriptionName": "Customer Subscription Name",
        "offerId": "dla-compliance-platform",
        "planId": "professional",
        "quantity": 1,
        "beneficiary": {
            "emailId": "customer@company.com",
            "objectId": "aad-object-id",
            "tenantId": "customer-tenant-id"
        },
        "purchaser": {
            "emailId": "purchaser@company.com",
            "objectId": "aad-object-id",
            "tenantId": "customer-tenant-id"
        },
        "term": {
            "startDate": "2024-01-01T00:00:00Z",
            "endDate": "2024-02-01T00:00:00Z"
        }
    }
    """
    access_token = _get_marketplace_access_token()
    if not access_token:
        return None
    
    return _call_marketplace_api(
        method="POST",
        endpoint="saas/subscriptions/resolve",
        token=access_token,
        json_data=None,
        params={"api-version": MARKETPLACE_API_VERSION, "x-ms-marketplace-token": token}
    )


# ============================================================================
# HELPER: Activate Subscription
# ============================================================================

def _activate_subscription(subscription_id: str, plan_id: str, quantity: int = 1) -> bool:
    """
    Tell Microsoft the subscription has been activated
    MUST be called within 10 days of customer clicking Subscribe
    """
    access_token = _get_marketplace_access_token()
    if not access_token:
        return False
    
    result = _call_marketplace_api(
        method="POST",
        endpoint=f"saas/subscriptions/{subscription_id}/activate",
        token=access_token,
        json_data={
            "planId": plan_id,
            "quantity": quantity
        }
    )
    
    return result is not None


# ============================================================================
# DATABASE: Store/Update Marketplace Subscription
# ============================================================================

def _save_marketplace_subscription(subscription_data: Dict, organization_id: str) -> bool:
    """
    Store marketplace subscription in Cosmos DB
    Container: marketplace_subscriptions (or reuse organizations container)
    """
    try:
        db = get_db()
        container = db.get_container("marketplace_subscriptions")
        
        subscription_record = {
            "id": subscription_data["id"],
            "type": "marketplace_subscription",
            "partition_key": organization_id,  # Link to organization
            "organization_id": organization_id,
            "subscription_id": subscription_data["id"],
            "subscription_name": subscription_data.get("subscriptionName", ""),
            "offer_id": subscription_data.get("offerId", ""),
            "plan_id": subscription_data.get("planId", ""),
            "quantity": subscription_data.get("quantity", 1),
            "status": STATUS_SUBSCRIBED,
            "beneficiary_email": subscription_data.get("beneficiary", {}).get("emailId"),
            "beneficiary_tenant_id": subscription_data.get("beneficiary", {}).get("tenantId"),
            "purchaser_email": subscription_data.get("purchaser", {}).get("emailId"),
            "purchaser_tenant_id": subscription_data.get("purchaser", {}).get("tenantId"),
            "term_start_date": subscription_data.get("term", {}).get("startDate"),
            "term_end_date": subscription_data.get("term", {}).get("endDate"),
            "activated_at": datetime.utcnow().isoformat() + "Z",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        
        container.upsert_item(subscription_record)
        logger.info(f"✅ Saved marketplace subscription: {subscription_data['id']}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to save marketplace subscription: {e}")
        return False


def _update_subscription_status(subscription_id: str, new_status: str, reason: str = "") -> bool:
    """Update subscription status in database"""
    try:
        db = get_db()
        container = db.get_container("marketplace_subscriptions")
        
        # Find subscription (cross-partition query since we don't know org_id)
        query = "SELECT * FROM c WHERE c.subscription_id = @sub_id"
        items = list(container.query_items(
            query=query,
            parameters=[{"name": "@sub_id", "value": subscription_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            logger.error(f"❌ Subscription not found: {subscription_id}")
            return False
        
        subscription = items[0]
        subscription["status"] = new_status
        subscription["status_reason"] = reason
        subscription["updated_at"] = datetime.utcnow().isoformat() + "Z"
        
        container.upsert_item(subscription)
        logger.info(f"✅ Updated subscription {subscription_id} → {new_status}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to update subscription status: {e}")
        return False


# ============================================================================
# ROUTE 1: Landing Page (Activation)
# ============================================================================

def handle_marketplace_landing(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /marketplace/activate?token=<marketplace-token>
    
    Customer clicked Subscribe → Microsoft redirects here
    
    Flow:
    1. Extract token from query params
    2. Call Microsoft API to resolve token → get subscription details
    3. Create/update organization in database
    4. Activate subscription with Microsoft
    5. Redirect customer to app dashboard
    """
    try:
        logger.info("🛍️ Marketplace landing page accessed")
        
        # Get token from query params
        marketplace_token = req.params.get("token")
        
        if not marketplace_token:
            logger.error("❌ No marketplace token provided")
            return func.HttpResponse(
                body="<html><body><h1>Error</h1><p>No marketplace token provided. Please subscribe through Azure Marketplace.</p></body></html>",
                status_code=400,
                mimetype="text/html"
            )
        
        # Resolve token with Microsoft
        logger.info("🔑 Resolving marketplace token...")
        subscription_data = _resolve_marketplace_token(marketplace_token)
        
        if not subscription_data:
            logger.error("❌ Failed to resolve marketplace token")
            return func.HttpResponse(
                body="<html><body><h1>Error</h1><p>Failed to validate subscription with Microsoft. Please try again or contact support.</p></body></html>",
                status_code=500,
                mimetype="text/html"
            )
        
        subscription_id = subscription_data["id"]
        plan_id = subscription_data["planId"]
        beneficiary_email = subscription_data.get("beneficiary", {}).get("emailId")
        
        logger.info(f"✅ Resolved subscription: {subscription_id} | Plan: {plan_id} | Customer: {beneficiary_email}")
        
        # Create or get organization
        # Use beneficiary tenant ID as organization ID (1 Azure tenant = 1 org)
        org_id = subscription_data.get("beneficiary", {}).get("tenantId")
        
        if not org_id:
            # Fallback: use subscription ID as org ID
            org_id = subscription_id
        
        # TODO: Create organization record if doesn't exist
        # TODO: Create user account for beneficiary if doesn't exist
        
        # Save subscription to database
        if not _save_marketplace_subscription(subscription_data, org_id):
            logger.error("❌ Failed to save subscription to database")
            # Continue anyway - can fix database later
        
        # Activate subscription with Microsoft
        logger.info(f"🚀 Activating subscription with Microsoft...")
        if not _activate_subscription(subscription_id, plan_id):
            logger.error("❌ Failed to activate subscription with Microsoft")
            return func.HttpResponse(
                body="<html><body><h1>Error</h1><p>Failed to activate subscription. Please contact support with subscription ID: " + subscription_id + "</p></body></html>",
                status_code=500,
                mimetype="text/html"
            )
        
        logger.info(f"✅ Subscription activated: {subscription_id}")
        
        # Redirect to app with success message
        # In production, include a JWT token or session cookie for auto-login
        app_url = os.getenv("APP_URL", "https://yourapp.azurewebsites.net")
        redirect_url = f"{app_url}/marketplace/success?subscription_id={subscription_id}&plan={plan_id}"
        
        return func.HttpResponse(
            status_code=302,
            headers={"Location": redirect_url}
        )
        
    except Exception as e:
        logger.error(f"❌ Marketplace landing page error: {e}", exc_info=True)
        return func.HttpResponse(
            body=f"<html><body><h1>Error</h1><p>Unexpected error: {str(e)}</p></body></html>",
            status_code=500,
            mimetype="text/html"
        )


# ============================================================================
# ROUTE 2: Webhook (Subscription Events)
# ============================================================================

def handle_marketplace_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /marketplace/webhook
    
    Microsoft sends subscription lifecycle events:
    - Unsubscribe: Customer cancelled
    - ChangePlan: Customer upgraded/downgraded
    - ChangeQuantity: Customer changed seats
    - Suspend: Payment failed
    - Reinstate: Payment issue resolved
    
    Webhook payload:
    {
        "id": "event-id",
        "activityId": "activity-id",
        "subscriptionId": "subscription-id",
        "offerId": "offer-id",
        "publisherId": "publisher-id",
        "planId": "plan-id",
        "quantity": 1,
        "status": "Subscribed",
        "timeStamp": "2024-01-15T10:30:00Z",
        "action": "Unsubscribe"
    }
    """
    try:
        logger.info("📬 Marketplace webhook received")
        
        # Parse webhook payload
        try:
            payload = req.get_json()
        except Exception:
            logger.error("❌ Invalid JSON payload")
            return json_response(400, error="Invalid JSON")
        
        action = payload.get("action")
        subscription_id = payload.get("subscriptionId")
        plan_id = payload.get("planId")
        quantity = payload.get("quantity", 1)
        status = payload.get("status")
        
        logger.info(f"📋 Webhook event: {action} | Subscription: {subscription_id} | Status: {status}")
        
        # Handle different actions
        if action == "Unsubscribe":
            # Customer cancelled subscription
            logger.warning(f"🚫 Customer unsubscribed: {subscription_id}")
            _update_subscription_status(subscription_id, STATUS_UNSUBSCRIBED, "Customer cancelled")
            
            # TODO: Disable organization access
            # TODO: Export data for customer download (30-day grace period)
            # TODO: Schedule data deletion after grace period
            
        elif action == "ChangePlan":
            # Customer upgraded or downgraded
            logger.info(f"🔄 Customer changed plan: {subscription_id} → {plan_id}")
            _update_subscription_status(subscription_id, STATUS_SUBSCRIBED, f"Changed to plan: {plan_id}")
            
            # TODO: Update organization plan limits
            # TODO: Send confirmation email
            
        elif action == "ChangeQuantity":
            # Customer changed number of seats (if you support per-seat pricing)
            logger.info(f"🔢 Customer changed quantity: {subscription_id} → {quantity} seats")
            _update_subscription_status(subscription_id, STATUS_SUBSCRIBED, f"Quantity changed to: {quantity}")
            
            # TODO: Update organization user limits
            
        elif action == "Suspend":
            # Payment failed - Microsoft suspends subscription
            logger.warning(f"⏸️ Subscription suspended (payment failed): {subscription_id}")
            _update_subscription_status(subscription_id, STATUS_SUSPENDED, "Payment failed")
            
            # TODO: Disable organization access (read-only mode)
            # TODO: Send payment failure email to customer
            
        elif action == "Reinstate":
            # Payment issue resolved - Microsoft reinstates subscription
            logger.info(f"▶️ Subscription reinstated: {subscription_id}")
            _update_subscription_status(subscription_id, STATUS_SUBSCRIBED, "Payment resolved")
            
            # TODO: Re-enable full organization access
            # TODO: Send confirmation email
            
        else:
            logger.warning(f"⚠️ Unknown webhook action: {action}")
        
        # Acknowledge receipt (Microsoft expects 200 OK)
        return json_response(200, data={"received": True, "action": action})
        
    except Exception as e:
        logger.error(f"❌ Marketplace webhook error: {e}", exc_info=True)
        # Still return 200 to avoid Microsoft retrying
        return json_response(200, data={"received": True, "error": str(e)})


# ============================================================================
# ROUTE 3: Get Subscription Details (for admin dashboard)
# ============================================================================

def handle_get_subscription(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /marketplace/subscription/{subscription_id}
    
    Admin endpoint to view subscription details
    """
    try:
        subscription_id = req.route_params.get("subscriptionId")
        
        if not subscription_id:
            return json_response(400, error="Subscription ID required")
        
        # Get from database
        db = get_db()
        container = db.get_container("marketplace_subscriptions")
        
        query = "SELECT * FROM c WHERE c.subscription_id = @sub_id"
        items = list(container.query_items(
            query=query,
            parameters=[{"name": "@sub_id", "value": subscription_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return json_response(404, error="Subscription not found")
        
        subscription = items[0]
        
        # Optionally: Call Microsoft API to get latest status
        access_token = _get_marketplace_access_token()
        if access_token:
            live_data = _call_marketplace_api(
                method="GET",
                endpoint=f"saas/subscriptions/{subscription_id}",
                token=access_token
            )
            
            if live_data:
                subscription["live_status"] = live_data.get("saasSubscriptionStatus")
        
        return json_response(200, data=subscription)
        
    except Exception as e:
        logger.error(f"❌ Get subscription error: {e}", exc_info=True)
        return json_response(500, error=str(e))