"""Admin Rules endpoints (for custom rules)"""
import azure.functions as func
import logging
from typing import Dict, List

from ..shared.http_utils import json_response
from ..core.custom_rules import custom_rules_engine, CustomRule
from ..core.database import get_container

logger = logging.getLogger(__name__)

def _get_user_attr(user, attr: str, default=None):
    """Safely extract attribute from user object or dict"""
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def handle_get_rules_admin(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /admin/rules
    Admin view of all rules (including disabled) with stats
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has admin access
        admin_roles = ['Organization.Admin', 'Platform.SuperAdmin', 'Compliance.Officer']
        if not any(role in user_roles for role in admin_roles):
            return json_response(403, error="Admin access required")
        
        # Get all rules for organization
        rules = custom_rules_engine.get_rules_for_org(org_id, enabled_only=False)
        
        # Get rule usage stats from documents
        container = get_container('documents')
        query = """
        SELECT c.violations, c.custom_rule_violations 
        FROM c 
        WHERE c.organization_id = @org_id
        AND c.type = 'document'
        AND IS_DEFINED(c.violations)
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id,
            max_item_count=1000
        ))
        
        # Count rule hits
        rule_stats = {}
        for doc in docs:
            violations = doc.get('violations', [])
            for violation in violations:
                if violation.get('source') == 'custom_rule':
                    rule_id = violation.get('rule_id')
                    if rule_id:
                        rule_stats[rule_id] = rule_stats.get(rule_id, 0) + 1
        
        # Combine rules with stats
        rules_with_stats = []
        for rule in rules:
            rules_with_stats.append({
                **rule.to_dict(),
                'violations_count': rule_stats.get(rule.id, 0),
                'last_used': 'N/A'  # Would need to track this
            })
        
        # Get rule categories
        categories = {}
        for rule in rules:
            cat = rule.category
            categories[cat] = categories.get(cat, 0) + 1
        
        return json_response(200, data={
            'rules': rules_with_stats,
            'stats': {
                'total_rules': len(rules),
                'enabled_rules': len([r for r in rules if r.enabled]),
                'disabled_rules': len([r for r in rules if not r.enabled]),
                'by_category': categories,
                'total_violations': sum(rule_stats.values()),
                'most_common_rule': max(rule_stats, key=rule_stats.get) if rule_stats else None,
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get rules admin failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_enable_disable_rule(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /admin/rules/{ruleId}/toggle
    Enable or disable a custom rule
    """
    try:
        rule_id = req.route_params.get('ruleId')
        if not rule_id:
            return json_response(400, error="Rule ID required")
        
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has admin access
        admin_roles = ['Organization.Admin', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in admin_roles):
            return json_response(403, error="Admin access required")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        enabled = body.get('enabled', True)
        
        # Get rule from database
        container = get_container('custom_rules')
        
        query = "SELECT * FROM c WHERE c.id = @id AND c.organization_id = @org_id"
        rules = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": rule_id},
                {"name": "@org_id", "value": org_id}
            ],
            partition_key=org_id
        ))
        
        if not rules:
            return json_response(404, error="Rule not found")
        
        rule = rules[0]
        rule['enabled'] = enabled
        rule['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container.upsert_item(rule)
        
        action = "enabled" if enabled else "disabled"
        return json_response(200, data={
            'rule_id': rule_id,
            'enabled': enabled,
            'message': f'✅ Rule {action}'
        })
        
    except Exception as e:
        logger.error(f"❌ Toggle rule failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_delete_rule(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /admin/rules/{ruleId}
    Delete a custom rule
    """
    try:
        rule_id = req.route_params.get('ruleId')
        if not rule_id:
            return json_response(400, error="Rule ID required")
        
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has admin access
        admin_roles = ['Organization.Admin', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in admin_roles):
            return json_response(403, error="Admin access required")
        
        container = get_container('custom_rules')
        
        try:
            container.delete_item(item=rule_id, partition_key=org_id)
            return json_response(200, data={
                'rule_id': rule_id,
                'deleted': True,
                'message': '✅ Rule deleted'
            })
        except exceptions.CosmosResourceNotFoundError:
            return json_response(404, error="Rule not found")
        
    except Exception as e:
        logger.error(f"❌ Delete rule failed: {e}", exc_info=True)
        return json_response(500, error=str(e))