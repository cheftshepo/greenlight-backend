"""Legal Advisory endpoints — FIXED"""
import azure.functions as func
import logging
import uuid
from datetime import datetime
from typing import Dict, List

from ..shared.http_utils import json_response
from ..core.database import (
    get_document,
    update_document,
    save_decision_trail,
    get_decision_trail,
    get_users_by_role
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


def handle_get_legal_advisory(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /legal/advisory
    Get legal advisory dashboard and summary
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has legal access
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        # Get all documents with legal advisory
        from ..core.database import get_container
        container = get_container('documents')
        
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id
        AND c.type = 'document'
        AND (IS_DEFINED(c.legal_advisory) OR c.workflow_status IN ('legal_review', 'dla_piper_review'))
        ORDER BY c.updated_at DESC
        LIMIT 50
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        # Get legal team members
        legal_team = get_users_by_role(org_id, 'Legal.Advisor')
        
        # Get recent legal decisions
        from ..core.database import get_audit_logs
        legal_decisions = get_audit_logs(
            org_id=org_id,
            resource_type='document',
            action='legal_decision',
            days=30,
            limit=20
        )
        
        # Get statistics
        total_advisory = len([d for d in docs if d.get('legal_advisory')])
        pending_review = len([d for d in docs if d.get('workflow_status') in ['legal_review', 'dla_piper_review']])
        
        return json_response(200, data={
            'summary': {
                'total_advisory_cases': total_advisory,
                'pending_review': pending_review,
                'legal_team_members': len(legal_team),
                'dla_piper_involved': any('DLAPiper.Advisory' in d.get('workflow_status', '') for d in docs)
            },
            'recent_cases': docs[:10],
            'legal_team': legal_team,
            'recent_decisions': legal_decisions,
            'your_role': next((r for r in user_roles if r in legal_roles), 'unknown')
        })
        
    except Exception as e:
        logger.error(f"❌ Get legal advisory failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_create_legal_advisory(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /legal/advisory
    Create a legal advisory note (for tracking)
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has legal access
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        document_id = body.get('document_id')
        
        # FIX: Accept both 'advisory' (what frontend sends) and 'advisory_text' (legacy)
        advisory_text = body.get('advisory') or body.get('advisory_text', '')
        advisory_type = body.get('advisory_type', 'internal_note')
        
        # FIX: Read 'recommendation' from body (frontend sends this)
        recommendation = body.get('recommendation', 'review')
        
        recommendations = body.get('recommendations', [])
        cited_regulations = body.get('cited_regulations', [])
        
        if not document_id:
            return json_response(400, error="document_id required")
        
        if not advisory_text:
            return json_response(400, error="advisory text required")
        
        # Get document
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        now = datetime.utcnow()
        
        # Save advisory as decision trail
        decision_trail = save_decision_trail({
            'organization_id': org_id,
            'document_id': document_id,
            'document_filename': doc.get('filename'),
            'decision': 'advisory_provided',
            'decision_type': 'legal_advisory',
            'decision_maker': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'decision_context': {
                'advisory_type': advisory_type,
                'advisory_text': advisory_text,
                'recommendation': recommendation,
                'recommendations': recommendations,
                'cited_regulations': cited_regulations,
            },
            'jurisdiction': doc.get('jurisdiction'),
            'decision_timestamp': now.isoformat() + 'Z',
        })
        
        # FIX: Update document with ALL advisory fields so the frontend can display them
        update_data = {
            'legal_advisory': advisory_text,
            'legal_advisory_type': advisory_type,
            'legal_advisory_by': user_email,
            'legal_advisory_at': now.isoformat() + 'Z',
            # These fields are what EnhancedLegalAdvisoryDisplay reads:
            'legal_recommendation': recommendation,
            'legal_reviewed_by': user_email,
            'legal_reviewed_at': now.isoformat() + 'Z',
            'legal_recommendations': recommendations,
            'cited_regulations': cited_regulations,
            'updated_at': now.isoformat() + 'Z',
        }
        
        if advisory_type == 'clearance':
            update_data['workflow_status'] = 'legal_cleared'
        elif advisory_type == 'escalation_recommended':
            update_data['workflow_status'] = 'requires_escalation'
        
        update_document(document_id, update_data, org_id)
        
        logger.info(f"✅ Legal advisory saved for doc {document_id} by {user_email}")
        
        return json_response(201, data={
            'advisory_id': decision_trail.get('id'),
            'document_id': document_id,
            'advisory_type': advisory_type,
            'recommendation': recommendation,
            'provided_by': user_email,
            'provided_at': now.isoformat() + 'Z',
            'message': '✅ Legal advisory recorded'
        })
        
    except Exception as e:
        logger.error(f"❌ Create legal advisory failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_provide_document_advisory(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /legal/documents/{documentId}/advise
    Provide legal advisory for a specific document (called from legal advisory page).
    
    This is the endpoint the legal advisory page's "Advisory" button calls.
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check legal access
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        # Extract document ID from route
        route_params = req.route_params
        document_id = route_params.get('documentId') or route_params.get('document_id')
        
        if not document_id:
            # Try extracting from URL path
            url_parts = req.url.split('/')
            try:
                doc_idx = url_parts.index('documents') + 1
                document_id = url_parts[doc_idx]
            except (ValueError, IndexError):
                return json_response(400, error="document_id required in URL")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        # FIX: Accept both field names from frontend
        advisory_text = body.get('advisory') or body.get('advisory_text', '')
        recommendation = body.get('recommendation', 'review')
        cited_regulations = body.get('cited_regulations', [])
        advisory_type = body.get('advisory_type', 'guidance')
        recommendations = body.get('recommendations', [])
        
        if not advisory_text:
            return json_response(400, error="Advisory text is required")
        
        # Get document
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Tenant isolation
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        now = datetime.utcnow()
        
        # Save to decision trail for audit
        decision_trail = save_decision_trail({
            'organization_id': org_id,
            'document_id': document_id,
            'document_filename': doc.get('filename'),
            'decision': 'advisory_provided',
            'decision_type': 'legal_advisory',
            'decision_maker': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'decision_context': {
                'advisory_type': advisory_type,
                'advisory_text': advisory_text,
                'recommendation': recommendation,
                'recommendations': recommendations,
                'cited_regulations': cited_regulations,
            },
            'document_state_at_decision': {
                'status': doc.get('status'),
                'workflow_status': doc.get('workflow_status'),
                'risk_score': doc.get('risk_score'),
                'violations_count': doc.get('violations_count'),
            },
            'jurisdiction': doc.get('jurisdiction'),
            'decision_timestamp': now.isoformat() + 'Z',
        })
        
        # Update document with ALL advisory fields
        update_data = {
            'legal_advisory': advisory_text,
            'legal_advisory_type': advisory_type,
            'legal_advisory_by': user_email,
            'legal_advisory_at': now.isoformat() + 'Z',
            'legal_recommendation': recommendation,
            'legal_reviewed_by': user_email,
            'legal_reviewed_at': now.isoformat() + 'Z',
            'legal_recommendations': recommendations,
            'cited_regulations': cited_regulations,
            'updated_at': now.isoformat() + 'Z',
        }
        
        # Update workflow status based on recommendation
        if recommendation == 'approve':
            update_data['workflow_status'] = 'legal_approved'
        elif recommendation == 'reject':
            update_data['workflow_status'] = 'legal_rejected'
        else:
            update_data['workflow_status'] = 'legal_reviewed'
        
        update_document(document_id, update_data, org_id)
        
        logger.info(f"✅ Legal advisory provided for doc {document_id} by {user_email} — recommendation: {recommendation}")
        
        return json_response(200, data={
            'success': True,
            'advisory_id': decision_trail.get('id'),
            'document_id': document_id,
            'recommendation': recommendation,
            'provided_by': user_email,
            'provided_at': now.isoformat() + 'Z',
            'workflow_status': update_data['workflow_status'],
            'message': f'✅ Legal advisory recorded — {recommendation}'
        })
        
    except Exception as e:
        logger.error(f"❌ Provide document advisory failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_legal_approve_document(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /legal/documents/{documentId}/approve
    Legal team approves a document (final decision).
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        org_id = _get_user_attr(user, 'organization_id')
        
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        route_params = req.route_params
        document_id = route_params.get('documentId') or route_params.get('document_id')
        
        if not document_id:
            url_parts = req.url.split('/')
            try:
                doc_idx = url_parts.index('documents') + 1
                document_id = url_parts[doc_idx]
            except (ValueError, IndexError):
                return json_response(400, error="document_id required")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        comments = body.get('comments', 'Approved by legal team')
        conditions = body.get('conditions', [])
        
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        now = datetime.utcnow()
        
        save_decision_trail({
            'organization_id': org_id,
            'document_id': document_id,
            'document_filename': doc.get('filename'),
            'decision': 'legal_approved',
            'decision_type': 'legal_approval',
            'decision_maker': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'decision_context': {
                'comments': comments,
                'conditions': conditions,
            },
            'document_state_at_decision': {
                'status': doc.get('status'),
                'workflow_status': doc.get('workflow_status'),
                'risk_score': doc.get('risk_score'),
            },
            'jurisdiction': doc.get('jurisdiction'),
            'decision_timestamp': now.isoformat() + 'Z',
        })
        
        update_data = {
            'status': 'approved',
            'workflow_status': 'legal_approved',
            'legal_recommendation': 'approve',
            'legal_reviewed_by': user_email,
            'legal_reviewed_at': now.isoformat() + 'Z',
            'legal_approval_comments': comments,
            'legal_conditions': conditions,
            'compliance_outcome': 'approved_by_legal',
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(document_id, update_data, org_id)
        
        logger.info(f"✅ Document {document_id} legally approved by {user_email}")
        
        return json_response(200, data={
            'success': True,
            'document_id': document_id,
            'status': 'approved',
            'approved_by': user_email,
            'message': '✅ Document approved by legal team'
        })
        
    except Exception as e:
        logger.error(f"❌ Legal approve failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_legal_reject_document(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /legal/documents/{documentId}/reject
    Legal team rejects a document (final decision).
    """
    try:
        user_roles = _get_user_attr(user, 'roles', [])
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        org_id = _get_user_attr(user, 'organization_id')
        
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        route_params = req.route_params
        document_id = route_params.get('documentId') or route_params.get('document_id')
        
        if not document_id:
            url_parts = req.url.split('/')
            try:
                doc_idx = url_parts.index('documents') + 1
                document_id = url_parts[doc_idx]
            except (ValueError, IndexError):
                return json_response(400, error="document_id required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        reason = body.get('reason', '')
        required_changes = body.get('required_changes', [])
        
        if not reason:
            return json_response(400, error="Rejection reason is required")
        
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        now = datetime.utcnow()
        
        save_decision_trail({
            'organization_id': org_id,
            'document_id': document_id,
            'document_filename': doc.get('filename'),
            'decision': 'legal_rejected',
            'decision_type': 'legal_rejection',
            'decision_maker': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'decision_context': {
                'reason': reason,
                'required_changes': required_changes,
            },
            'document_state_at_decision': {
                'status': doc.get('status'),
                'workflow_status': doc.get('workflow_status'),
                'risk_score': doc.get('risk_score'),
            },
            'jurisdiction': doc.get('jurisdiction'),
            'decision_timestamp': now.isoformat() + 'Z',
        })
        
        update_data = {
            'status': 'rejected',
            'workflow_status': 'legal_rejected',
            'legal_recommendation': 'reject',
            'legal_reviewed_by': user_email,
            'legal_reviewed_at': now.isoformat() + 'Z',
            'legal_rejection_reason': reason,
            'legal_required_changes': required_changes,
            'compliance_outcome': 'rejected_by_legal',
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(document_id, update_data, org_id)
        
        logger.info(f"✅ Document {document_id} legally rejected by {user_email}")
        
        return json_response(200, data={
            'success': True,
            'document_id': document_id,
            'status': 'rejected',
            'rejected_by': user_email,
            'message': '✅ Document rejected by legal team'
        })
        
    except Exception as e:
        logger.error(f"❌ Legal reject failed: {e}", exc_info=True)
        return json_response(500, error=str(e))