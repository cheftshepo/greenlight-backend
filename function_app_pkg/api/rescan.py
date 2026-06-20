"""
Re-scan Document Endpoint
POST /api/documents/{documentId}/rescan
Enterprise-ready with proper permissions and audit logging
"""

import azure.functions as func
import logging
from datetime import datetime
from typing import Dict, Any

from function_app_pkg.core.database import get_document, update_document, log_action, get_db
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Re-scan a document that has already been scanned
    
    Features:
    - Permission checking (uploader, compliance officer, admin)
    - Resets questionnaire if needed
    - Comprehensive audit logging
    - Comparison with previous scan
    """
    
    try:
        # Get document ID from route
        document_id = req.route_params.get('documentId')
        if not document_id:
            return json_response(400, error="Document ID required")
        
        logger.info(f"🔄 Re-scan requested for document: {document_id}")
        
        # Parse optional request body for re-scan notes
        rescan_reason = None
        try:
            if req.get_body():
                body = req.get_json()
                rescan_reason = body.get('reason', 'User requested re-scan')
        except:
            rescan_reason = 'User requested re-scan'
        
        # Get the document
        doc = get_document(document_id)
        if not doc:
            logger.error(f"Document not found: {document_id}")
            return json_response(404, error=f"Document not found: {document_id}")
        
        logger.info(f"📄 Document found: {doc.get('filename')}")
        
        # Extract user info
        user_id = None
        user_email = None
        user_roles = []
        
        if user:
            if hasattr(user, 'user_id'):
                user_id = user.user_id
                user_email = user.email
                user_roles = user.roles if hasattr(user, 'roles') else []
            elif isinstance(user, dict):
                user_id = user.get('user_id')
                user_email = user.get('email')
                user_roles = user.get('roles', [])
        
        # Check permissions - allow if:
        # 1. User uploaded the document
        # 2. User is compliance officer or admin
        # 3. User has any role with "Admin" or "Compliance" in name
        is_owner = doc.get('uploaded_by') == user_email
        has_permission = any(
            'admin' in role.lower() or 'compliance' in role.lower() or 'super' in role.lower()
            for role in user_roles
        )
        
        if not (is_owner or has_permission):
            logger.warning(f"🚫 Permission denied for user {user_email} to re-scan {document_id}")
            return json_response(403, error="You don't have permission to re-scan this document")
        
        logger.info(f"✅ Permission granted for user {user_email} to re-scan")
        
        # Get document text (with fallbacks)
        text = (
            doc.get('text_content') or 
            doc.get('extracted_text') or 
            doc.get('text') or 
            ''
        )
        
        if not text or len(text.strip()) < 10:
            logger.error(f"❌ Document {document_id} has no readable text")
            return json_response(400, error="Document has no readable text. Please re-upload the document.")
        
        # Get original scan details for comparison
        original_risk_score = doc.get('risk_score', 0)
        original_violations = doc.get('violations', [])
        original_violation_count = doc.get('violations_count', 0)
        original_status = doc.get('status', 'unknown')
        
        logger.info(f"📊 Original scan: risk={original_risk_score}, violations={original_violation_count}, status={original_status}")
        
        # Perform the re-scan
        scan_result = _perform_rescan(
            text=text,
            filename=doc.get('filename', 'Unknown'),
            jurisdiction=doc.get('jurisdiction', 'UK'),
            organization_id=doc.get('organization_id'),
            user_email=user_email,
            is_rescan=True
        )
        
        if not scan_result.get('success', False):
            error_msg = scan_result.get('error', 'Unknown scan error')
            logger.error(f"❌ Re-scan failed: {error_msg}")
            return json_response(500, error=f"Re-scan failed: {error_msg}")
        
        # Extract scan results
        violations = scan_result.get('violations', [])
        risk_score = scan_result.get('risk_score', 0)
        compliance_outcome = scan_result.get('compliance_outcome', 'unknown')
        scan_stats = scan_result.get('stats', {})
        
        # Determine if questionnaire should be reset
        should_reset_questionnaire = False
        if original_status in ['questions_generated', 'answers_submitted', 'pending_review']:
            # If violations changed significantly, reset questionnaire
            violation_change = abs(len(violations) - original_violation_count)
            if violation_change > 2:  # More than 2 violations changed
                should_reset_questionnaire = True
                logger.info(f"🔄 Resetting questionnaire due to significant violation change: {violation_change}")
        
        # Prepare update data
        update_data = {
            'status': 'scanned',
            'rescan_count': (doc.get('rescan_count', 0) + 1),
            'last_rescanned_at': datetime.utcnow().isoformat() + 'Z',
            'rescan_by': user_email,
            'rescan_reason': rescan_reason,
            'violations': violations,
            'violations_count': len(violations),
            'risk_score': risk_score,
            'compliance_outcome': compliance_outcome,
            'scan_stats': scan_stats,
            'updated_at': datetime.utcnow().isoformat() + 'Z',
            'workflow_status': 'scanned'  # Reset workflow status
        }
        
        # Reset questionnaire if needed
        if should_reset_questionnaire:
            update_data.update({
                'compliance_questions': [],
                'questionnaire_status': 'pending',
                'questionnaire_answers': None,
                'questionnaire_completed_at': None,
                'questionnaire_outcome': None,
                'questionnaire_color': None,
                'questionnaire_id': None
            })
            logger.info("✅ Questionnaire data reset")
        
        # Update the document
        updated_doc = update_document(document_id, update_data)
        if not updated_doc:
            logger.error(f"❌ Failed to update document {document_id}")
            return json_response(500, error="Failed to save re-scan results")
        
        logger.info(f"✅ Document updated successfully")
        
        # Log audit action
        try:
            log_action(
                org_id=doc.get('organization_id'),
                user_id=user_email,
                user_email=user_email,
                user_roles=user_roles,
                action='document.re_scanned',
                resource_type='document',
                resource_id=document_id,
                resource_name=doc.get('filename'),
                details={
                    'original_risk_score': original_risk_score,
                    'new_risk_score': risk_score,
                    'original_violations': original_violation_count,
                    'new_violations': len(violations),
                    'violations_change': len(violations) - original_violation_count,
                    'rescan_reason': rescan_reason,
                    'questionnaire_reset': should_reset_questionnaire,
                    'risk_score_change': risk_score - original_risk_score
                }
            )
            logger.info(f"✅ Audit log created for re-scan")
        except Exception as e:
            logger.warning(f"⚠️ Could not create audit log: {e}")
        
        # Calculate improvement/deterioration
        risk_improvement = original_risk_score - risk_score  # Positive = improvement
        violation_improvement = original_violation_count - len(violations)  # Positive = improvement
        
        # Build comprehensive response
        response_data = {
            'document_id': document_id,
            'filename': doc.get('filename'),
            'rescan_successful': True,
            'rescan_count': update_data['rescan_count'],
            'rescan_timestamp': update_data['last_rescanned_at'],
            'new_scan_results': {
                'risk_score': risk_score,
                'violations_found': len(violations),
                'compliance_outcome': compliance_outcome,
                'scan_duration': scan_stats.get('duration_seconds', 0),
                'scan_method': scan_stats.get('method', 'unknown')
            },
            'comparison_with_previous': {
                'previous_risk_score': original_risk_score,
                'previous_violations': original_violation_count,
                'risk_score_change': risk_improvement,
                'violations_change': violation_improvement,
                'improvement': risk_improvement > 0 or violation_improvement > 0,
                'risk_trend': 'improved' if risk_improvement > 0 else 'worsened' if risk_improvement < 0 else 'unchanged',
                'violations_trend': 'improved' if violation_improvement > 0 else 'worsened' if violation_improvement < 0 else 'unchanged'
            },
            'questionnaire_status': {
                'was_reset': should_reset_questionnaire,
                'current_status': 'pending' if should_reset_questionnaire else doc.get('questionnaire_status', 'n/a'),
                'next_action': 'Generate new questions' if should_reset_questionnaire else 'Continue with existing questionnaire'
            },
            'next_steps': [
                'Review the new scan results',
                'Check if violations require attention',
                f"{'Generate new compliance questions' if should_reset_questionnaire else 'Continue with existing questionnaire'}",
                'Submit for review when ready'
            ],
            'summary': f"Re-scan completed. Risk score: {risk_score}/100, Violations: {len(violations)}"
        }
        
        logger.info(f"✅ Re-scan completed successfully for {document_id}")
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error(f"❌ Re-scan failed: {e}", exc_info=True)
        return json_response(500, error=f"Re-scan failed: {str(e)[:200]}")


def _perform_rescan(
    text: str,
    filename: str,
    jurisdiction: str,
    organization_id: str,
    user_email: str,
    is_rescan: bool = True
) -> Dict[str, Any]:
    """
    Perform the actual re-scan using available scanners
    
    Tries:
    1. RAG scanner (if available and configured)
    2. Fallback to basic scanner
    3. Returns standardized result format
    """
    
    logger.info(f"🔍 Performing re-scan for {filename} ({jurisdiction})")
    
    # Try RAG scanner first (preferred)
    try:
        from function_app_pkg.core.rag_scanner import scan_document_with_rag
        
        logger.info("🤖 Using RAG scanner for re-scan")
        
        scan_result = scan_document_with_rag(
            text=text,
            jurisdiction=jurisdiction,
            filename=filename,
            organization_id=organization_id,
            user_context={
                'email': user_email,
                'is_rescan': is_rescan,
                'scan_mode': 're-scan'
            },
            document_metadata={
                'filename': filename,
                'is_rescan': True,
                'original_upload_timestamp': datetime.utcnow().isoformat() + 'Z'
            }
        )
        
        # Standardize the response format
        if scan_result.get('success', False):
            return {
                'success': True,
                'violations': scan_result.get('violations', []),
                'risk_score': scan_result.get('risk_score', 0),
                'compliance_outcome': scan_result.get('compliance_outcome', 'requires_review'),
                'stats': scan_result.get('stats', {}),
                'scanner_used': 'rag',
                'error': None
            }
        else:
            raise Exception(scan_result.get('error', 'RAG scan failed'))
            
    except ImportError as e:
        logger.warning(f"⚠️ RAG scanner not available: {e}")
        # Fall back to basic scanner
        pass
    
    # Try basic scanner
    try:
        from function_app_pkg.core.scanner import scan_document
        
        logger.info("📝 Using basic scanner for re-scan")
        
        scan_result = scan_document(
            text=text,
            jurisdiction=jurisdiction,
            filename=filename,
            organization_id=organization_id
        )
        
        return {
            'success': True,
            'violations': scan_result.get('violations', []),
            'risk_score': scan_result.get('risk_score', 0),
            'compliance_outcome': scan_result.get('compliance_outcome', 'requires_review'),
            'stats': scan_result.get('stats', {}),
            'scanner_used': 'basic',
            'error': None
        }
        
    except ImportError as e:
        logger.error(f"❌ No scanner available: {e}")
        return {
            'success': False,
            'error': 'No scanner available. Please check scanner configuration.',
            'violations': [],
            'risk_score': 0,
            'compliance_outcome': 'unknown',
            'stats': {},
            'scanner_used': 'none'
        }