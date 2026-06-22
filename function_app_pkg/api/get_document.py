"""Get document details - FIXED to include ALL name fields"""
import azure.functions as func
import logging
from function_app_pkg.core.database import get_document as db_get_document
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)

def handle(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    Get document by ID with full details
    
    FIXED: Now includes ALL name fields:
    - approved_by_name
    - rejected_by_name  
    - assigned_to_name
    - assigned_by_name
    - uploaded_by_name
    """
    try:
        doc_id = req.route_params.get('documentId')
        
        if not doc_id:
            return json_response(400, error="Document ID is required")
        
        logger.info(f"📖 Fetching document: {doc_id}")

        user_org_id = None
        if user:
            if hasattr(user, 'organization_id'):
                user_org_id = user.organization_id
            elif isinstance(user, dict):
                user_org_id = user.get('organization_id')

        # Hard block — no org_id on user means no access
        if not user_org_id:
            logger.warning("🚫 Access denied: no org_id on user token")
            return json_response(403, error="Access denied")

        doc = db_get_document(doc_id, org_id=user_org_id)

        if not doc:
            return json_response(404, error=f"Document not found: {doc_id}")

        # Hard block — doc must belong to user's org, no exceptions
        if doc.get('organization_id') != user_org_id:
            logger.warning(
                f"🚫 Org mismatch: user_org={user_org_id} "
                f"doc_org={doc.get('organization_id')} doc_id={doc_id}"
            )
            return json_response(403, error="Access denied")
        
        # ============================================================
        # ✅ FIXED: Include ALL name fields for UI display
        # ============================================================
        
        response = {
            # Core document info
            "id": doc.get('id'),
            "filename": doc.get('filename'),
            "client_id": doc.get('client_id'),
            "organization_id": doc.get('organization_id'),
            "organization_name": doc.get('organization_name'),
            "jurisdiction": doc.get('jurisdiction'),
            "status": doc.get('status'),
            "workflow_status": doc.get('workflow_status'),
            "size_bytes": doc.get('size_bytes'),
            "text_length": doc.get('text_length'),
            "extraction_method": doc.get('extraction_method'),
            "compliance_outcome": doc.get('compliance_outcome'),
            "risk_score": doc.get('risk_score', 0),
            "violation_count": doc.get('violation_count', 0),
            "created_at": doc.get('created_at'),
            "updated_at": doc.get('updated_at'),
            "text_preview": doc.get('extracted_text', '')[:200] + "..." if doc.get('extracted_text') else None,
            "blob_url": doc.get('blob_url'),
            "blob_path": doc.get('blob_path'),
            "blob_container": doc.get('blob_container', 'documents'),
            "has_original_file": bool(doc.get('blob_path')),
            "has_corrected_file": doc.get('correction_status') == 'generated',
            "corrected_blob_path": doc.get('corrected_blob_path'),

            # Briefing data
            "briefing": doc.get('briefing'),
            
            # PII data
            "pii_summary": doc.get('pii_summary'),
            "pii_items": doc.get('pii_items'),
            
            # ✅ CRITICAL FIX: Approval/rejection fields WITH NAMES
            "approved_at": doc.get('approved_at'),
            "approved_by": doc.get('approved_by'),
            "approved_by_name": doc.get('approved_by_name'),  # ✅ ADD THIS
            "approved_by_title": doc.get('approved_by_title'),  # ✅ ADD THIS
            "approval_comments": doc.get('approval_comments'),
            "approval_reasoning": doc.get('approval_reasoning'),  # ✅ ADD THIS
            "approval_conditions": doc.get('approval_conditions'),
            
            "rejected_at": doc.get('rejected_at'),
            "rejected_by": doc.get('rejected_by'),
            "rejected_by_name": doc.get('rejected_by_name'),  # ✅ ADD THIS
            "rejected_by_title": doc.get('rejected_by_title'),  # ✅ ADD THIS
            "rejection_reason": doc.get('rejection_reason'),
            "required_changes": doc.get('required_changes'),
            "rejection_severity": doc.get('rejection_severity'),  # ✅ ADD THIS
            
            # ✅ CRITICAL FIX: Assignment fields WITH NAMES
            "assigned_to": doc.get('assigned_to'),
            "assigned_to_name": doc.get('assigned_to_name'),  # ✅ ADD THIS
            "assigned_by": doc.get('assigned_by'),
            "assigned_by_name": doc.get('assigned_by_name'),  # ✅ ADD THIS
            "assigned_at": doc.get('assigned_at'),
            "assignment_id": doc.get('assignment_id'),
            "assignment_status": doc.get('assignment_status'),
            "assignment_priority": doc.get('assignment_priority'),
            "assignment_deadline": doc.get('assignment_deadline'),
            "assignment_notes": doc.get('assignment_notes'),
            "ticket_id": doc.get('ticket_id'),
            "sla_breached": doc.get('sla_breached'),
            "time_remaining_hours": doc.get('time_remaining_hours'),
            
            # Escalation
            "escalated_to_dla_piper_at": doc.get('escalated_to_dla_piper_at'),
            "escalated_by": doc.get('escalated_by'),
            "escalated_by_name": doc.get('escalated_by_name'),  # ✅ ADD THIS
            "escalation_reason": doc.get('escalation_reason'),
            "dla_piper_request_id": doc.get('dla_piper_request_id'),
            
            # Uploader info
            "uploaded_by": doc.get('uploaded_by'),
            "uploaded_by_name": doc.get('uploaded_by_name'),  # ✅ ADD THIS
            "uploaded_by_email": doc.get('uploaded_by_email'),
            
            # Scan details
            "scan_stats": doc.get('scan_stats', {}),
            "violations": doc.get('violations', []),
            "recommendations": doc.get('recommendations', []),
            
            # Questionnaire
            "compliance_questions": doc.get('compliance_questions'),
            "questions": doc.get('questions'),
            "questions_generated_at": doc.get('questions_generated_at'),
            "questionnaire_status": doc.get('questionnaire_status'),
            "submitted_answers": doc.get('submitted_answers'),
            "questionnaire_answers": doc.get('questionnaire_answers'),
            "answers_submitted_at": doc.get('answers_submitted_at'),
            "questionnaire_completed_at": doc.get('questionnaire_completed_at'),
            "questionnaire_outcome": doc.get('questionnaire_outcome'),
            "questionnaire_color": doc.get('questionnaire_color'),
            
            # Certificates
            "certificates": doc.get('certificates', []),
            "certificate_id": doc.get('certificates', [{}])[0].get('certificate_id') if doc.get('certificates') else None,
            
            # Additional metadata
            "metadata": doc.get('metadata'),
            "document_type": doc.get('document_type'),
            "ai_risk_analysis": doc.get('ai_risk_analysis'),
            "questionnaire_risk_increase": doc.get('questionnaire_risk_increase'),
            "questionnaire_no_count": doc.get('questionnaire_no_count'),
            "questionnaire_yes_count": doc.get('questionnaire_yes_count'),
            "questionnaire_uncertain_count": doc.get('questionnaire_uncertain_count'),
        }
        
        # Log what we're returning for debugging
        logger.info(f"✅ Document retrieved: {doc_id}")
        logger.info(f"   📋 Approved by: {response.get('approved_by')} ({response.get('approved_by_name')})")
        logger.info(f"   📋 Rejected by: {response.get('rejected_by')} ({response.get('rejected_by_name')})")
        logger.info(f"   📋 Assigned to: {response.get('assigned_to')} ({response.get('assigned_to_name')})")
        logger.info(f"   📋 Assigned by: {response.get('assigned_by')} ({response.get('assigned_by_name')})")
        
        return json_response(200, data=response)
        
    except Exception as e:
        logger.error(f"❌ Get document error: {e}", exc_info=True)
        return json_response(500, error=str(e))