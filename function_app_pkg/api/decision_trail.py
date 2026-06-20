"""Decision trail API - track all compliance decisions"""
import azure.functions as func
import logging
from datetime import datetime
from function_app_pkg.core.database import get_document, get_db
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)

def _get_user_attr(user, attr: str, default=None):
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def handle_get_decision_trail(req: func.HttpRequest, user) -> func.HttpResponse:
    """Get decision trail for document"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Get decision trail from document
        decisions = doc.get('decision_trail', [])
        
        # Also check audit logs for additional decisions
        db = get_db()
        audit_container = db.get_container('audit_logs')
        
        query = """
        SELECT c.timestamp, c.action, c.user_email, c.details
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.resource_id = @doc_id
        AND c.action IN ('document.approved', 'document.rejected', 'document.escalated')
        ORDER BY c.timestamp DESC
        """
        
        audit_decisions = list(audit_container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@doc_id", "value": doc_id}
            ],
            partition_key=org_id
        ))
        
        # Combine and format decisions
        all_decisions = []
        
        # Add from document
        for decision in decisions:
            all_decisions.append({
                'id': decision.get('id', decision.get('timestamp', '')),
                'decision': decision.get('decision', decision.get('action', '')),
                'decision_type': decision.get('decision_type', 'manual'),
                'decision_maker': {
                    'email': decision.get('decision_maker', {}).get('email', decision.get('user_email', '')),
                    'name': decision.get('decision_maker', {}).get('name', ''),
                    'roles': decision.get('decision_maker', {}).get('roles', [])
                },
                'created_at': decision.get('created_at', decision.get('timestamp', '')),
                'decision_context': decision.get('decision_context', {}),
                'is_ai_override': decision.get('is_ai_override', False)
            })
        
        # Add from audit logs
        for audit in audit_decisions:
            action_map = {
                'document.approved': 'approved',
                'document.rejected': 'rejected',
                'document.escalated': 'escalated'
            }
            
            all_decisions.append({
                'id': audit.get('timestamp', ''),
                'decision': action_map.get(audit.get('action', ''), audit.get('action', '')),
                'decision_type': 'manual',
                'decision_maker': {
                    'email': audit.get('user_email', ''),
                    'name': audit.get('details', {}).get('user_name', ''),
                    'roles': []
                },
                'created_at': audit.get('timestamp', ''),
                'decision_context': audit.get('details', {}),
                'is_ai_override': False
            })
        
        # Sort by timestamp
        all_decisions.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return json_response(200, data={
            'decisions': all_decisions,
            'total': len(all_decisions)
        })
        
    except Exception as e:
        logger.error(f"❌ Get decision trail failed: {e}", exc_info=True)
        return json_response(500, error=str(e))