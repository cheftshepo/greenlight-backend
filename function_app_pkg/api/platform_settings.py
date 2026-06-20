"""Platform Settings Management (SuperAdmin Only)"""
import logging
from datetime import datetime
from typing import Dict, Optional

from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_container

logger = logging.getLogger(__name__)

# Default platform settings
DEFAULT_PLATFORM_SETTINGS = {
    'platform_name': 'Compliance Platform',
    'version': '1.0.0',
    'maintenance_mode': False,
    'new_registrations_enabled': True,
    'default_subscription_tier': 'enterprise',
    'azure_openai_enabled': True,
    'email_notifications_enabled': True,
    'max_file_size_mb': 50,
    'supported_jurisdictions': ['UK', 'US', 'ZA', 'EU', 'GLOBAL', 'AU'],
    'ai_model': 'gpt-4o',
    'support_email': 'support@complianceplatform.com',
    'terms_url': 'https://complianceplatform.com/terms',
    'privacy_url': 'https://complianceplatform.com/privacy',
    'sla_defaults': {
        'initial_review_hours': 24,
        'escalation_review_hours': 48,
        'legal_review_hours': 72
    },
    'pricing_tiers': {
        'trial': {'scans_per_month': 10, 'price_usd': 0},
        'basic': {'scans_per_month': 100, 'price_usd': 1000},
        'core': {'scans_per_month': 500, 'price_usd': 5000},
        'premium': {'scans_per_month': -1, 'price_usd': 15000},
        'enterprise': {'scans_per_month': -1, 'price_usd': 50000}
    },
    'integrations': {
        'azure_ad_enabled': True,
        'teams_integration_enabled': False,
        'slack_integration_enabled': False,
        'service_now_integration_enabled': False
    },
    'advanced_features': {
        'ai_analysis_enabled': True,
        'custom_rules_enabled': True,
        'workflow_automation_enabled': True,
        'legal_escalation_enabled': True,
        'certificate_generation_enabled': True
    }
}


def handle_platform_settings_get(req, user) -> Dict:
    """
    GET /platform/settings
    Get platform-wide settings (SuperAdmin only)
    """
    try:
        # Verify SuperAdmin
        user_roles = user.roles if hasattr(user, 'roles') else user.get('roles', [])
        if 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="SuperAdmin access required")
        
        container = get_container('organizations')
        
        # Try to get platform settings from database
        try:
            settings_doc = container.read_item(
                item='platform_settings',
                partition_key='platform_settings'
            )
            settings = settings_doc.get('settings', DEFAULT_PLATFORM_SETTINGS)
            last_updated = settings_doc.get('updated_at')
            updated_by = settings_doc.get('updated_by')
        except Exception:
            # Settings not found in DB, return defaults
            settings = DEFAULT_PLATFORM_SETTINGS
            last_updated = None
            updated_by = None
        
        return json_response(200, data={
            'settings': settings,
            'metadata': {
                'last_updated': last_updated,
                'updated_by': updated_by,
                'is_default': last_updated is None,
                'supports_update': True
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Platform settings get failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get platform settings: {str(e)}")


def handle_platform_settings_update(req, user) -> Dict:
    """
    PUT /platform/settings
    Update platform-wide settings (SuperAdmin only)
    """
    try:
        # Verify SuperAdmin
        user_roles = user.roles if hasattr(user, 'roles') else user.get('roles', [])
        if 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="SuperAdmin access required")
        
        body = req.get_json()
        if not body:
            return json_response(400, error="Request body required")
        
        settings = body.get('settings', {})
        if not settings:
            return json_response(400, error="Settings object required")
        
        # Validate required fields
        required_fields = ['platform_name', 'version', 'support_email']
        missing = [f for f in required_fields if f not in settings]
        if missing:
            return json_response(400, error=f"Missing required fields: {missing}")
        
        # Validate file size
        max_file_size = settings.get('max_file_size_mb', 50)
        if not isinstance(max_file_size, int) or max_file_size < 1 or max_file_size > 500:
            return json_response(400, error="max_file_size_mb must be between 1 and 500")
        
        container = get_container('organizations')
        now = datetime.utcnow()
        
        try:
            # Try to update existing settings
            settings_doc = container.read_item(
                item='platform_settings',
                partition_key='platform_settings'
            )
            settings_doc['settings'] = settings
            settings_doc['updated_at'] = now.isoformat() + 'Z'
            settings_doc['updated_by'] = user.email if hasattr(user, 'email') else user.get('email')
            
            container.upsert_item(settings_doc)
            
        except Exception:
            # Create new settings document
            settings_doc = {
                'id': 'platform_settings',
                'type': 'platform_settings',
                'settings': settings,
                'created_at': now.isoformat() + 'Z',
                'updated_at': now.isoformat() + 'Z',
                'updated_by': user.email if hasattr(user, 'email') else user.get('email')
            }
            container.create_item(body=settings_doc)
        
        # Log the change
        from function_app_pkg.core.database import log_action
        log_action(
            org_id='platform',
            user_id=user.id if hasattr(user, 'id') else user.get('id', ''),
            user_email=user.email if hasattr(user, 'email') else user.get('email'),
            user_roles=user_roles,
            action='update_platform_settings',
            resource_type='platform_settings',
            resource_id='platform_settings',
            details={'settings_updated': list(settings.keys())},
            success=True
        )
        
        return json_response(200, data={
            'success': True,
            'message': 'Platform settings updated successfully',
            'updated_at': settings_doc['updated_at'],
            'updated_by': settings_doc['updated_by']
        })
        
    except Exception as e:
        logger.error(f"❌ Platform settings update failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to update platform settings: {str(e)}")


def handle_platform_organizations_list(req, user) -> Dict:
    """
    GET /platform/organizations
    List all organizations with details (SuperAdmin only)
    """
    try:
        # Verify SuperAdmin
        user_roles = user.roles if hasattr(user, 'roles') else user.get('roles', [])
        if 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="SuperAdmin access required")
        
        # Get query parameters
        page = int(req.params.get('page', '1'))
        page_size = min(int(req.params.get('page_size', '50')), 100)
        status_filter = req.params.get('status')
        tier_filter = req.params.get('tier')
        
        container = get_container('organizations')
        
        # Build query conditions
        conditions = ["c.type = 'organization'"]
        params = []
        
        if status_filter:
            if status_filter == 'active':
                conditions.append("c.is_active = true")
            elif status_filter == 'inactive':
                conditions.append("c.is_active = false")
            elif status_filter == 'trial':
                conditions.append("c.subscription_tier = 'trial'")
        
        if tier_filter and tier_filter != 'all':
            conditions.append("c.subscription_tier = @tier")
            params.append({"name": "@tier", "value": tier_filter})
        
        # Count query
        count_query = f"""
        SELECT VALUE COUNT(1) FROM c 
        WHERE {' AND '.join(conditions)}
        """
        
        count_items = list(container.query_items(
            query=count_query,
            parameters=params,
            enable_cross_partition_query=True,
            max_item_count=1
        ))
        total_count = count_items[0] if count_items else 0
        
        # Data query
        offset = (page - 1) * page_size
        data_query = f"""
        SELECT * FROM c 
        WHERE {' AND '.join(conditions)}
        ORDER BY c.created_at DESC
        OFFSET {offset} LIMIT {page_size}
        """
        
        orgs = list(container.query_items(
            query=data_query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        # Get document counts for each org
        doc_container = get_container('documents')
        orgs_with_counts = []
        
        for org in orgs:
            org_id = org.get('id')
            
            # Get document count
            doc_count_query = """
            SELECT VALUE COUNT(1) FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'document'
            """
            
            doc_count_items = list(doc_container.query_items(
                query=doc_count_query,
                parameters=[{"name": "@org_id", "value": org_id}],
                partition_key=org_id,
                max_item_count=1
            ))
            doc_count = doc_count_items[0] if doc_count_items else 0
            
            # Get user count
            user_container = get_container('users')
            user_count_query = """
            SELECT VALUE COUNT(1) FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'user'
            AND c.is_active = true
            """
            
            user_count_items = list(user_container.query_items(
                query=user_count_query,
                parameters=[{"name": "@org_id", "value": org_id}],
                partition_key=org_id,
                max_item_count=1
            ))
            user_count = user_count_items[0] if user_count_items else 0
            
            orgs_with_counts.append({
                'id': org_id,
                'name': org.get('name', ''),
                'azure_tenant_id': org.get('azure_tenant_id', ''),
                'subscription_tier': org.get('subscription_tier', 'trial'),
                'subscription_expires': org.get('subscription_expires'),
                'jurisdictions': org.get('jurisdictions', []),
                'is_active': org.get('is_active', True),
                'created_at': org.get('created_at'),
                'last_activity': org.get('updated_at'),
                
                'usage_stats': {
                    'total_documents': doc_count,
                    'active_users': user_count,
                    'custom_rules_enabled': org.get('custom_rules_enabled', False),
                    'advisory_hours_remaining': org.get('advisory_hours_remaining', 0)
                },
                
                'contact_info': {
                    'primary_email': org.get('primary_contact_email'),
                    'technical_email': org.get('technical_contact_email'),
                    'billing_email': org.get('billing_contact_email')
                }
            })
        
        # Calculate platform totals
        active_orgs = len([o for o in orgs if o.get('is_active', True)])
        trial_orgs = len([o for o in orgs if o.get('subscription_tier') == 'trial'])
        paying_orgs = active_orgs - trial_orgs
        
        return json_response(200, data={
            'organizations': orgs_with_counts,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_count': total_count,
                'total_pages': (total_count + page_size - 1) // page_size
            },
            'summary': {
                'total_organizations': total_count,
                'active_organizations': active_orgs,
                'trial_organizations': trial_orgs,
                'paying_organizations': paying_orgs,
                'by_tier': {
                    tier: len([o for o in orgs if o.get('subscription_tier') == tier])
                    for tier in ['trial', 'basic', 'core', 'premium', 'enterprise']
                }
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Platform organizations list failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to list organizations: {str(e)}")