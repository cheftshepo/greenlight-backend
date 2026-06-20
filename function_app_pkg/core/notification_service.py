"""Document-specific notifications endpoint"""
import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from ..shared.http_utils import json_response
from ..core.database import (
    get_document,
    get_user_notifications,
    mark_notification_read,
    get_audit_logs
)

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


def handle_get_document_notifications(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/notifications
    Get notifications related to a specific document
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        # Verify document exists and user has access
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Get all user notifications
        limit = int(req.params.get('limit', '10'))
        all_notifications = get_user_notifications(user_email, org_id, limit=limit * 2)
        
        # Filter to only notifications for this document
        document_notifications = [
            n for n in all_notifications 
            if n.get('document_id') == doc_id
        ][:limit]
        
        return json_response(200, data={
            'document_id': doc_id,
            'filename': doc.get('filename'),
            'notifications': document_notifications,
            'total': len(document_notifications),
            'unread_count': len([n for n in document_notifications if not n.get('read', False)])
        })
        
    except Exception as e:
        logger.error(f"❌ Get document notifications failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_mark_document_notifications_read(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/notifications/mark-read
    Mark all notifications for a document as read
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        # Get all unread notifications for this document
        notifications = get_user_notifications(user_email, org_id, unread_only=True, limit=100)
        document_notifications = [n for n in notifications if n.get('document_id') == doc_id]
        
        # Mark each as read
        marked_count = 0
        for notification in document_notifications:
            if mark_notification_read(notification['id'], org_id, user_email):
                marked_count += 1
        
        return json_response(200, data={
            'document_id': doc_id,
            'marked_read': marked_count,
            'message': f'✅ Marked {marked_count} notifications as read'
        })
        
    except Exception as e:
        logger.error(f"❌ Mark document notifications read failed: {e}", exc_info=True)
        return json_response(500, error=str(e))