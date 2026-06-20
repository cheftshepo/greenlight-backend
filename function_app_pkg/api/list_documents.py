"""List documents - FIXED to include ALL name fields for dashboard"""
import azure.functions as func
import logging
from function_app_pkg.core.database import list_documents_by_organization
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)

def handle(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    List documents for current organization
    
    FIXED: Now includes ALL name fields in the response:
    - approved_by_name
    - rejected_by_name
    - assigned_to_name
    - assigned_by_name
    - uploaded_by_name
    - escalated_by_name
    """
    try:
        if not user:
            return json_response(401, error="Authentication required")
        
        # Get organization_id from user (keeping your existing logic)
        if hasattr(user, 'organization_id'):
            organization_id = user.organization_id
        elif isinstance(user, dict) and 'organization_id' in user:
            organization_id = user.get('organization_id')
        else:
            logger.error("User missing organization_id")
            return json_response(400, error="User organization not found")
        
        # Get query parameters
        limit = int(req.params.get('limit', 100))
        offset = int(req.params.get('offset', 0))
        jurisdiction = req.params.get('jurisdiction')
        status = req.params.get('status')
        
        logger.info(f"📚 Listing documents for organization: {organization_id}")
        
        # Get documents - Using your existing function call
        documents = list_documents_by_organization(
            org_id=organization_id,  # Your existing parameter name
            limit=limit,
            offset=offset,
            jurisdiction=jurisdiction,
            status=status
        )
        
        # ✅ FIXED: Create summaries with ALL name fields
        summaries = []
        for doc in documents:
            summary = {
                # Your existing fields
                'document_id': doc.get('id'),
                'filename': doc.get('filename'),
                'jurisdiction': doc.get('jurisdiction'),
                'client_id': doc.get('client_id'),
                'organization_id': doc.get('organization_id'),
                'status': doc.get('status'),
                'workflow_status': doc.get('workflow_status', ''),
                'compliance_outcome': doc.get('compliance_outcome', ''),
                'risk_score': doc.get('risk_score', 0),
                'violation_count': doc.get('violations_count', len(doc.get('violations', []))),
                'created_at': doc.get('created_at'),
                'updated_at': doc.get('updated_at', doc.get('created_at')),
                'uploaded_by': doc.get('uploaded_by', ''),
                
                # ✅ ADD: Assignment fields WITH NAMES
                'assignment_id': doc.get('assignment_id'),
                'assigned_to': doc.get('assigned_to'),
                'assigned_to_name': doc.get('assigned_to_name'),  # ✅ CRITICAL
                'assigned_by': doc.get('assigned_by'),
                'assigned_by_name': doc.get('assigned_by_name'),  # ✅ CRITICAL
                'assigned_at': doc.get('assigned_at'),
                'assignment_status': doc.get('assignment_status'),
                'assignment_priority': doc.get('assignment_priority'),
                'assignment_deadline': doc.get('assignment_deadline'),
                'assignment_notes': doc.get('assignment_notes'),
                'assignment_comments_count': len(doc.get('assignment_notes', [])) if doc.get('assignment_notes') else 0,
                'sla_status': doc.get('sla_status'),
                'time_remaining_hours': doc.get('time_remaining_hours'),
                'sla_breached': doc.get('sla_breached', False),
                'ticket_id': doc.get('ticket_id'),
                
                # ✅ ADD: Approval fields WITH NAMES
                'approved_by': doc.get('approved_by'),
                'approved_by_name': doc.get('approved_by_name'),  # ✅ CRITICAL
                'approved_at': doc.get('approved_at'),
                'approval_reasoning': doc.get('approval_reasoning'),
                
                # ✅ ADD: Rejection fields WITH NAMES
                'rejected_by': doc.get('rejected_by'),
                'rejected_by_name': doc.get('rejected_by_name'),  # ✅ CRITICAL
                'rejected_at': doc.get('rejected_at'),
                'rejection_reason': doc.get('rejection_reason'),
                
                # ✅ ADD: Uploader WITH NAME
                'uploaded_by_name': doc.get('uploaded_by_name'),  # ✅ CRITICAL
                'organization_name': doc.get('organization_name'),
                
                # ✅ ADD: Escalation WITH NAME
                'escalated_by': doc.get('escalated_by'),
                'escalated_by_name': doc.get('escalated_by_name'),  # ✅ CRITICAL
                'escalated_at': doc.get('escalated_at'),
                
                # ✅ ADD: Certificate info
                'certificate_id': doc.get('certificates', [{}])[0].get('certificate_id') if doc.get('certificates') else None,
            }
            summaries.append(summary)
        
        logger.info(f"✅ Found {len(summaries)} documents")
        
        return json_response(200, data={
            'documents': summaries,
            'total': len(summaries),
            'limit': limit,
            'offset': offset
        })
        
    except Exception as e:
        logger.error(f"❌ List documents error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to list documents: {str(e)}")