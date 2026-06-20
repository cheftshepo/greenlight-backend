"""AI conversations API"""
import azure.functions as func
import logging
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


def handle_get_ai_conversations(req: func.HttpRequest, user) -> func.HttpResponse:
    """Get AI conversation history for document"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Get conversations from database
        db = get_db()
        container = db.get_container('documents')
        
        query = """
        SELECT *
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.document_id = @doc_id
        AND c.type = 'ai_conversation'
        ORDER BY c.created_at DESC
        """
        
        conversations = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@doc_id", "value": doc_id}
            ],
            partition_key=org_id
        ))
        
        return json_response(200, data={
            'conversations': conversations,
            'total': len(conversations)
        })
        
    except Exception as e:
        logger.error(f"❌ Get AI conversations failed: {e}", exc_info=True)
        return json_response(500, error=str(e))