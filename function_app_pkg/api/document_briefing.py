"""Document briefing API"""
import azure.functions as func
import logging
from datetime import datetime
from function_app_pkg.core.database import get_document, update_document
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


def handle_submit_briefing(req: func.HttpRequest, user) -> func.HttpResponse:
    """Submit document briefing"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        marketing_type = body.get('marketing_type', '').strip()
        distribution_media = body.get('distribution_media', '').strip()
        
        if not marketing_type or not distribution_media:
            return json_response(400, error="marketing_type and distribution_media required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Create briefing data
        briefing = {
            'marketing_type': marketing_type,
            'distribution_media': distribution_media,
            'target_audience': body.get('target_audience', ''),
            'content_type': body.get('content_type', ''),
            'submitted_by': user_email,
            'submitted_at': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Update document
        update_document(doc_id, {
            'briefing': briefing,
            'status': 'briefing_completed',
            'updated_at': datetime.utcnow().isoformat() + 'Z'
        }, org_id)
        
        logger.info(f"✅ Briefing submitted for document {doc_id}")
        
        return json_response(200, data={
            'document_id': doc_id,
            'briefing': briefing,
            'message': 'Briefing submitted successfully'
        })
        
    except Exception as e:
        logger.error(f"❌ Submit briefing failed: {e}", exc_info=True)
        return json_response(500, error=str(e))