"""
Audit Integration Module
========================
Provides document-specific audit logging functions and helpers.
Wraps the core database.log_action for document-centric operations.

File: function_app_pkg/api/audit_integration.py
"""

import logging
from typing import Dict, Optional, Any
from datetime import datetime
import azure.functions as func

from ..core.database import log_action, get_db

logger = logging.getLogger(__name__)


# =============================================================================
# ACTION TYPE CONSTANTS
# =============================================================================

ACTION_TYPES = {
    # Document lifecycle
    "UPLOAD": "document.uploaded",
    "DELETE": "document.deleted",
    "SCAN": "document.scanned",
    "RESCAN": "document.rescanned",
    "VIEW": "document.viewed",
    "DOWNLOAD": "document.downloaded",
    
    # Workflow actions
    "SUBMIT": "workflow.submitted",
    "APPROVE": "workflow.approved",
    "REJECT": "workflow.rejected",
    "ESCALATE": "workflow.escalated",
    
    # Assignment actions
    "ASSIGN": "assignment.created",
    "REASSIGN": "assignment.reassigned",
    "COMPLETE": "assignment.completed",
    
    # AI/Chat actions
    "AI_CHAT": "ai.chat",
    "AI_ANALYSIS": "ai.analysis",
    "QUESTIONNAIRE_SUBMIT": "questionnaire.submitted",
    
    # Admin actions
    "USER_CREATE": "user.created",
    "USER_UPDATE": "user.updated",
    "USER_DELETE": "user.deleted",
    "SETTINGS_UPDATE": "settings.updated",
}


# =============================================================================
# HELPER: Extract user from request
# =============================================================================

def get_user_from_request(req: func.HttpRequest) -> Optional[Dict]:
    """
    Extract user information from request.
    Checks for user in request context or headers.
    
    Returns dict with user info or None if not authenticated.
    """
    try:
        # Check if user was injected by auth decorator
        if hasattr(req, 'user') and req.user:
            user = req.user
            if hasattr(user, '__dict__'):
                return {
                    'id': getattr(user, 'user_id', None) or getattr(user, 'id', None),
                    'user_id': getattr(user, 'user_id', None),
                    'email': getattr(user, 'email', None),
                    'name': getattr(user, 'name', None),
                    'roles': getattr(user, 'roles', []),
                    'role': getattr(user, 'roles', ['user'])[0] if getattr(user, 'roles', []) else 'user',
                    'organization_id': getattr(user, 'organization_id', None),
                }
            elif isinstance(user, dict):
                return {
                    'id': user.get('user_id') or user.get('id'),
                    'user_id': user.get('user_id'),
                    'email': user.get('email'),
                    'name': user.get('name'),
                    'roles': user.get('roles', []),
                    'role': user.get('roles', ['user'])[0] if user.get('roles') else 'user',
                    'organization_id': user.get('organization_id'),
                }
        
        # Try to get from headers (JWT claims)
        user_id = req.headers.get('X-User-Id')
        user_email = req.headers.get('X-User-Email')
        
        if user_email:
            return {
                'id': user_id,
                'user_id': user_id,
                'email': user_email,
                'name': req.headers.get('X-User-Name', ''),
                'roles': [],
                'role': 'user',
                'organization_id': req.headers.get('X-Organization-Id'),
            }
        
        return None
        
    except Exception as e:
        logger.warning(f"Failed to extract user from request: {e}")
        return None


# =============================================================================
# DOCUMENT-SPECIFIC AUDIT LOGGING
# =============================================================================

def log_document_action(
    document_id: str,
    action_type: str,
    user_info: Optional[Dict] = None,
    details: Optional[Dict] = None,
    organization_id: str = None,
    success: bool = True,
    error_message: str = ""
) -> Optional[Dict]:
    """
    Log a document-related action to the audit trail.
    
    Args:
        document_id: The document being acted upon
        action_type: One of ACTION_TYPES values (e.g., "document.deleted")
        user_info: Dict with user_id, email, roles, organization_id
        details: Additional context (filename, jurisdiction, etc.)
        organization_id: Override org_id if not in user_info
        success: Whether the action succeeded
        error_message: Error details if failed
    
    Returns:
        The created audit log entry or None on failure
    """
    try:
        # Extract user details
        if user_info is None:
            user_info = {}
        
        user_id = user_info.get('user_id') or user_info.get('id') or 'system'
        user_email = user_info.get('email') or 'system@internal'
        user_roles = user_info.get('roles') or []
        org_id = organization_id or user_info.get('organization_id')
        
        # If no org_id, try to get from document
        if not org_id:
            try:
                from ..core.database import get_document
                doc = get_document(document_id)
                if doc:
                    org_id = doc.get('organization_id', 'unknown')
            except:
                org_id = 'unknown'
        
        # Build details dict
        log_details = details or {}
        log_details['document_id'] = document_id
        log_details['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        
        # Call core log_action
        result = log_action(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            action=action_type,
            resource_type='document',
            resource_id=document_id,
            resource_name=log_details.get('filename', ''),
            details=log_details,
            success=success,
            error_message=error_message
        )
        
        logger.info(f"📝 Audit logged: {action_type} on {document_id} by {user_email}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to log document action: {e}", exc_info=True)
        return None


def log_workflow_action(
    document_id: str,
    action_type: str,
    user_info: Optional[Dict] = None,
    details: Optional[Dict] = None,
    previous_status: str = None,
    new_status: str = None
) -> Optional[Dict]:
    """
    Log workflow state changes (approve, reject, escalate).
    Includes status transition tracking.
    """
    log_details = details or {}
    
    if previous_status:
        log_details['previous_status'] = previous_status
    if new_status:
        log_details['new_status'] = new_status
    
    log_details['status_change'] = f"{previous_status or 'unknown'} → {new_status or 'unknown'}"
    
    return log_document_action(
        document_id=document_id,
        action_type=action_type,
        user_info=user_info,
        details=log_details
    )


def log_ai_interaction(
    document_id: str,
    interaction_type: str,
    user_info: Optional[Dict] = None,
    ai_model: str = None,
    tokens_used: int = None,
    intent: str = None
) -> Optional[Dict]:
    """
    Log AI/chat interactions for usage tracking.
    """
    details = {
        'interaction_type': interaction_type,
    }
    
    if ai_model:
        details['ai_model'] = ai_model
    if tokens_used:
        details['tokens_used'] = tokens_used
    if intent:
        details['detected_intent'] = intent
    
    return log_document_action(
        document_id=document_id,
        action_type=ACTION_TYPES.get('AI_CHAT', 'ai.interaction'),
        user_info=user_info,
        details=details
    )


# =============================================================================
# BATCH OPERATIONS
# =============================================================================

def log_batch_action(
    document_ids: list,
    action_type: str,
    user_info: Optional[Dict] = None,
    summary: Optional[Dict] = None
) -> Optional[Dict]:
    """
    Log a batch operation affecting multiple documents.
    Creates a single audit entry summarizing the batch.
    """
    try:
        if user_info is None:
            user_info = {}
        
        user_id = user_info.get('user_id') or user_info.get('id') or 'system'
        user_email = user_info.get('email') or 'system@internal'
        user_roles = user_info.get('roles') or []
        org_id = user_info.get('organization_id', 'unknown')
        
        details = {
            'batch_operation': True,
            'document_count': len(document_ids),
            'document_ids': document_ids[:20],  # Limit to first 20 for storage
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }
        
        if summary:
            details.update(summary)
        
        result = log_action(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            action=f"batch.{action_type}",
            resource_type='document_batch',
            resource_id=f"batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            resource_name=f"Batch {action_type}: {len(document_ids)} documents",
            details=details
        )
        
        logger.info(f"📝 Batch audit: {action_type} on {len(document_ids)} documents by {user_email}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to log batch action: {e}", exc_info=True)
        return None