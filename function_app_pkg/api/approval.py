"""
Workflow and Approval API Handlers - WITH LEGAL ESCALATION
==========================================================
- Documents MUST be assigned before approval/rejection
- Escalation now routes to Legal team
- Full audit trail with AI override detection
- Strict multi-tenant isolation
"""
import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from function_app_pkg.core.database import (
    get_document, 
    update_document, 
    get_document_with_access_check,
    save_decision_trail,
    save_analytics_event,
    get_ai_conversations_for_document,
    get_users_by_org,
    create_notification,
    log_activity
)
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =============================================================================
# ESCALATION TARGETS
# =============================================================================

class EscalationTarget:
    """Where documents can be escalated to"""
    LEGAL = "legal_review"           # Internal legal team
    DLA_PIPER = "dla_piper_review"   # External DLA Piper advisory
    SENIOR_COMPLIANCE = "senior_compliance"  # Senior compliance officer
    MANAGEMENT = "management_review"  # Management escalation


# =============================================================================
# ASSIGNMENT ENFORCEMENT HELPER
# =============================================================================

def _check_assignment_permission(doc: Dict, user, action: str) -> Optional[str]:
    """
    Check if user has permission to approve/reject based on assignment.
    
    Rules:
    1. Document MUST be assigned to someone
    2. User must be the assignee OR have admin/compliance role
    3. Assignment must be in 'in_progress' status (user acknowledged it)
    
    Returns: Error message if not permitted, None if permitted
    """
    user_email = getattr(user, 'email', '') or ''
    user_roles = getattr(user, 'roles', []) or []
    
    # Super admins can always act
    if 'Platform.SuperAdmin' in user_roles:
        return None
    
    # Legal team can act on escalated documents
    if any(role in user_roles for role in ['Legal.Advisor', 'DLAPiper.Advisory']):
        workflow_status = doc.get('workflow_status', '')
        if workflow_status in ['escalated', 'legal_review', 'dla_piper_review']:
            return None  # Legal can act on escalated docs
    
    # Check if document is assigned
    assigned_to = doc.get('assigned_to')
    assignment_status = doc.get('assignment_status', 'unassigned')
    
    if not assigned_to or assignment_status == 'unassigned':
        return f"Document must be assigned before {action}. Use POST /documents/{{id}}/assign first."
    
    # Check if user is the assignee
    is_assignee = assigned_to.lower() == user_email.lower()
    
    # Check if user has elevated role
    elevated_roles = ['Organization.Admin', 'Compliance.Officer', 'DLAPiper.Advisory', 'Legal.Advisor']
    has_elevated_role = any(role in user_roles for role in elevated_roles)
    
    if not is_assignee and not has_elevated_role:
        return f"Only the assigned reviewer ({assigned_to}) or admins can {action} this document."
    
    # Check assignment status - must be 'in_progress' (user has started review)
    if assignment_status == 'pending' and is_assignee:
        return f"Please start your review first. Update assignment status to 'in_progress' before {action}."
    
    # Warn if someone other than assignee is acting (but allow it for admins)
    if not is_assignee and has_elevated_role:
        logger.warning(f"⚠️ Admin override: {user_email} {action}ing document assigned to {assigned_to}")
    
    return None  # Permitted


def _get_user_attr(user, attr: str, default=None):
    """Safely extract attribute from user object or dict"""
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def _get_legal_team_members(org_id: str) -> List[Dict]:
    """Get all users with Legal.Advisor role in the organization"""
    try:
        all_users = get_users_by_org(org_id)
        legal_users = [
            u for u in all_users 
            if 'Legal.Advisor' in u.get('roles', []) and u.get('is_active', True)
        ]
        return legal_users
    except Exception as e:
        logger.error(f"Failed to get legal team: {e}")
        return []


def _notify_legal_team(doc: Dict, escalated_by: str, reason: str, org_id: str):
    """Send notifications to legal team members"""
    try:
        legal_users = _get_legal_team_members(org_id)
        
        for legal_user in legal_users:
            create_notification({
                'organization_id': org_id,
                'recipient_email': legal_user.get('email'),
                'notification_type': 'escalation_to_legal',
                'title': f"Document Escalated for Legal Review",
                'message': f"Document '{doc.get('filename')}' has been escalated by {escalated_by}. Reason: {reason}",
                'document_id': doc.get('id'),
                'created_by': escalated_by,
            })
        
        logger.info(f"✅ Notified {len(legal_users)} legal team members")
        
        # Also send email notifications
        try:
            from function_app_pkg.core.email_service import send_escalation_notification
            for legal_user in legal_users:
                send_escalation_notification(
                    to_email=legal_user.get('email'),
                    to_name=legal_user.get('name', legal_user.get('email')),
                    document_name=doc.get('filename'),
                    document_id=doc.get('id'),
                    escalated_by=escalated_by,
                    reason=reason,
                    priority='high'
                )
        except ImportError:
            logger.warning("Email service not available - skipping email notifications")
        except Exception as e:
            logger.warning(f"Failed to send email notifications: {e}")
            
    except Exception as e:
        logger.error(f"Failed to notify legal team: {e}")


# =============================================================================
# APPROVAL HANDLER - WITH ASSIGNMENT ENFORCEMENT
# =============================================================================

def handle_approve(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Approve a document - REQUIRES ASSIGNMENT
    
    POST /documents/{documentId}/approve
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        user_id = _get_user_attr(user, 'user_id', '') or _get_user_attr(user, 'id', '')
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_name = _get_user_attr(user, 'name', user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get document with tenant isolation
        doc = get_document_with_access_check(doc_id, org_id)
        
        if not doc:
            if 'Platform.SuperAdmin' in user_roles:
                doc = get_document(doc_id)
            if not doc:
                return json_response(404, error="Document not found or access denied")
        
        # Verify organization match
        doc_org_id = doc.get('organization_id')
        if doc_org_id != org_id and 'Platform.SuperAdmin' not in user_roles:
            logger.warning(f"🚨 Tenant isolation violation attempt: user org {org_id}, doc org {doc_org_id}")
            return json_response(403, error="Access denied - document belongs to different organization")
        
        # Assignment enforcement
        assignment_error = _check_assignment_permission(doc, user, 'approve')
        if assignment_error:
            return json_response(403, error=assignment_error)
        
        # Parse request
        try:
            body = req.get_json()
        except Exception as e:
            return json_response(400, error=f"Invalid JSON: {str(e)}")
        
        comments = body.get('approval_notes') or body.get('comments', '')
        auto_certificate = body.get('generate_certificate', True)
        conditions = body.get('conditions', [])
        reasoning = body.get('reasoning', '')
        reviewed_violations = body.get('reviewed_violations', [])
        expiry_date = body.get('expiry_date')
        approved_by_name = body.get('approved_by_name', '') or user_name
        approved_by_title = body.get('approved_by_title', '')
        
        now = datetime.utcnow()
        
        # Request metadata
        request_metadata = {
            'ip_address': req.headers.get('X-Forwarded-For', req.headers.get('X-Real-IP', 'unknown')),
            'user_agent': req.headers.get('User-Agent', 'unknown'),
            'session_id': req.headers.get('X-Session-ID', ''),
            'request_timestamp': now.isoformat() + 'Z',
        }
        
        # AI override detection
        risk_score = doc.get('risk_score', 0)
        violations_count = doc.get('violations_count', len(doc.get('violations', [])))
        compliance_outcome = doc.get('compliance_outcome', '')
        
        if compliance_outcome == 'non_compliant' or risk_score >= 70:
            ai_recommendation = 'reject'
        elif compliance_outcome == 'requires_review' or risk_score >= 45:
            ai_recommendation = 'review'
        else:
            ai_recommendation = 'approve'
        
        is_ai_override = ai_recommendation in ['reject', 'review']
        
        if is_ai_override:
            logger.warning(f"⚠️ AI OVERRIDE: {user_email} approving despite AI saying {ai_recommendation}")
        
        # Get regulations & AI context
        regulations_considered = []
        for violation in doc.get('violations', []):
            regulations_considered.append({
                'violation_id': violation.get('violation_id', violation.get('id', '')),
                'regulation': violation.get('regulation', ''),
                'category': violation.get('category', ''),
                'severity': violation.get('severity', ''),
                'user_reviewed': violation.get('violation_id', '') in reviewed_violations,
            })
        
        try:
            ai_conversations = get_ai_conversations_for_document(doc_id, limit=20)
            ai_conversation_ids = [c.get('id') for c in ai_conversations]
        except:
            ai_conversations = []
            ai_conversation_ids = []
        
        # Document state snapshot
        document_state_at_decision = {
            'status': doc.get('status'),
            'workflow_status': doc.get('workflow_status'),
            'risk_score': risk_score,
            'violations_count': violations_count,
            'compliance_outcome': compliance_outcome,
            'jurisdiction': doc.get('jurisdiction'),
            'pii_count': doc.get('pii_summary', {}).get('count', 0),
            'assigned_to': doc.get('assigned_to'),
            'assignment_status': doc.get('assignment_status'),
            'ticket_id': doc.get('ticket_id'),
        }
        
        # Save decision trail
        try:
            decision_trail_data = {
                'organization_id': org_id,
                'document_id': doc_id,
                'document_filename': doc.get('filename'),
                'decision': 'approved',
                'decision_type': 'approval',
                
                'decision_maker': {
                    'user_id': user_id,
                    'email': user_email,
                    'name': approved_by_name,
                    'title': approved_by_title,
                    'roles': user_roles,
                    'was_assignee': doc.get('assigned_to', '').lower() == user_email.lower(),
                },
                
                'decision_context': {
                    'comments': comments,
                    'reasoning': reasoning,
                    'conditions': conditions,
                    'reviewed_violations': reviewed_violations,
                    'expiry_date': expiry_date,
                },
                
                'document_state_at_decision': document_state_at_decision,
                
                'ai_context': {
                    'ai_recommendation': ai_recommendation,
                    'ai_risk_score': risk_score,
                    'ai_violations_found': violations_count,
                    'user_agreed_with_ai': not is_ai_override,
                    'total_ai_conversations': len(ai_conversations),
                    'ai_conversation_ids': ai_conversation_ids,
                },
                
                'is_ai_override': is_ai_override,
                'override_details': {
                    'ai_said': ai_recommendation,
                    'user_decided': 'approve',
                } if is_ai_override else None,
                
                'assignment_context': {
                    'assigned_to': doc.get('assigned_to'),
                    'assigned_by': doc.get('assigned_by'),
                    'assigned_at': doc.get('assigned_at'),
                    'ticket_id': doc.get('ticket_id'),
                },
                
                'regulations_considered': regulations_considered,
                'jurisdiction': doc.get('jurisdiction'),
                'request_metadata': request_metadata,
                'time_to_decision_hours': _calculate_time_to_decision(doc, now),
                'decision_timestamp': now.isoformat() + 'Z',
            }
            
            decision_trail = save_decision_trail(decision_trail_data)
            decision_trail_id = decision_trail.get('id')
            
        except Exception as e:
            logger.error(f"❌ Decision trail save failed: {e}")
            decision_trail_id = None
        
        # Track AI accuracy
        try:
            ai_predicted_non_compliant = violations_count > 0 or risk_score >= 50
            accuracy_result = 'FALSE_POSITIVE' if ai_predicted_non_compliant else 'TRUE_NEGATIVE'
            
            save_analytics_event({
                'organization_id': org_id,
                'type': 'ai_accuracy',
                'document_id': doc_id,
                'ai_violations_count': violations_count,
                'ai_risk_score': risk_score,
                'ai_predicted_non_compliant': ai_predicted_non_compliant,
                'human_decision': 'approved',
                'accuracy_result': accuracy_result,
                'is_override': is_ai_override,
                'decision_maker': user_email,
            })
        except Exception as e:
            logger.warning(f"⚠️ AI accuracy tracking failed: {e}")
        
        # Track ROI
        try:
            time_saved_hours = 0.5
            cost_saved_gbp = time_saved_hours * 150
            
            save_analytics_event({
                'organization_id': org_id,
                'type': 'roi_event',
                'event_subtype': 'approval_completed',
                'document_id': doc_id,
                'metrics': {
                    'time_saved_hours': time_saved_hours,
                    'cost_saved_gbp': cost_saved_gbp,
                    'time_to_decision_hours': _calculate_time_to_decision(doc, now),
                },
                'user_email': user_email,
            })
        except:
            pass
        
        # Update document
        update_data = {
            'status': 'approved',
            'workflow_status': 'approved',
            'approval_status': 'approved',
            'approved_by': user_email,
            'approved_by_id': user_id,
            'approved_by_name': approved_by_name,
            'approved_by_title': approved_by_title,
            'approved_by_roles': user_roles,
            'approved_at': now.isoformat() + 'Z',
            'approval_comments': comments,
            'approval_reasoning': reasoning,
            'approval_conditions': conditions,
            'expiry_date': expiry_date,
            'decision_trail_id': decision_trail_id,
            'is_ai_override': is_ai_override,
            'ai_recommendation_at_approval': ai_recommendation,
            'assignment_status': 'completed',
            'assignment_completed_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(doc_id, update_data, org_id)
        
        # Log activity
        try:
            log_activity(
                org_id=org_id,
                user_email=user_email,
                user_name=user_name,
                action='document_approved',
                document_id=doc_id,
                document_name=doc.get('filename'),
                details={'is_ai_override': is_ai_override}
            )
        except:
            pass
        
        # Send notification to uploader
        try:
            from function_app_pkg.core.email_service import send_approval_notification
            send_approval_notification(
                to_email=doc.get('uploaded_by'),
                to_name=doc.get('uploaded_by_name', doc.get('uploaded_by')),
                document_name=doc.get('filename'),
                document_id=doc_id,
                approved_by=approved_by_name,
                comments=comments
            )
        except:
            pass
        
        logger.info(f"✅ Document {doc_id} APPROVED by {user_email} (assigned to: {doc.get('assigned_to')})")
        
        # Generate certificate
        certificate_data = None
        if auto_certificate:
            try:
                from function_app_pkg.core.certificate_generator import get_certificate_generator
                from function_app_pkg.core.database import get_organization
                
                org = get_organization(org_id)
                org_name = org.get('name', 'Unknown') if org else 'Unknown'
                
                generator = get_certificate_generator()
                cert_result = generator.generate_and_store(
                    document_id=doc_id,
                    document_filename=doc.get('filename', 'Unknown'),
                    organization_id=org_id,
                    organization_name=org_name,
                    jurisdiction=doc.get('jurisdiction'),
                    compliance_outcome='compliant',
                    risk_score=risk_score,
                    scan_date=doc.get('scanned_at', now.isoformat() + 'Z'),
                    violations_count=violations_count,
                    issued_by=user_email,
                    reviewer_name=approved_by_name,
                    reviewer_email=user_email,
                    notes=comments or "Document approved"
                )
                
                certificate_data = {
                    'certificate_id': cert_result.certificate_id,
                    'verification_url': cert_result.verification_url,
                    'generated_at': cert_result.generated_at
                }
                
            except Exception as e:
                logger.warning(f"⚠️ Certificate generation failed: {e}")
        
        # Build response
        response_data = {
            'document_id': doc_id,
            'status': 'approved',
            'approved_by': user_email,
            'approved_by_name': approved_by_name,
            'approved_at': update_data['approved_at'],
            'decision_trail_id': decision_trail_id,
            'is_ai_override': is_ai_override,
            'ai_recommendation': ai_recommendation,
            'assignment': {
                'was_assigned_to': doc.get('assigned_to'),
                'ticket_id': doc.get('ticket_id'),
                'assignment_completed': True,
            },
            'message': '✅ Document approved for publication',
        }
        
        if certificate_data:
            response_data['certificate'] = certificate_data
        
        if is_ai_override:
            response_data['override_warning'] = f"Note: AI recommended '{ai_recommendation}' but document was approved."
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error(f"❌ Approval error: {e}", exc_info=True)
        return json_response(500, error=f"Approval failed: {str(e)}")


# =============================================================================
# REJECTION HANDLER - WITH ASSIGNMENT ENFORCEMENT
# =============================================================================

def handle_reject(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Reject a document - REQUIRES ASSIGNMENT
    
    POST /documents/{documentId}/reject
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        user_id = _get_user_attr(user, 'user_id', '') or _get_user_attr(user, 'id', '')
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_name = _get_user_attr(user, 'name', user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc = get_document_with_access_check(doc_id, org_id)
        
        if not doc:
            if 'Platform.SuperAdmin' in user_roles:
                doc = get_document(doc_id)
            if not doc:
                return json_response(404, error="Document not found or access denied")
        
        doc_org_id = doc.get('organization_id')
        if doc_org_id != org_id and 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="Access denied - document belongs to different organization")
        
        # Assignment enforcement
        assignment_error = _check_assignment_permission(doc, user, 'reject')
        if assignment_error:
            return json_response(403, error=assignment_error)
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        reason = body.get('rejection_reason') or body.get('reason', '')
        if not reason:
            return json_response(400, error="Rejection reason is required")
        
        required_changes = body.get('required_changes', [])
        cited_violations = body.get('cited_violations', [])
        severity = body.get('severity', 'medium')
        rejected_by_name = body.get('rejected_by_name', '') or user_name
        rejected_by_title = body.get('rejected_by_title', '')
        
        now = datetime.utcnow()
        
        request_metadata = {
            'ip_address': req.headers.get('X-Forwarded-For', 'unknown'),
            'user_agent': req.headers.get('User-Agent', 'unknown'),
            'request_timestamp': now.isoformat() + 'Z',
        }
        
        # AI analysis
        risk_score = doc.get('risk_score', 0)
        violations_count = doc.get('violations_count', len(doc.get('violations', [])))
        compliance_outcome = doc.get('compliance_outcome', '')
        
        if compliance_outcome == 'non_compliant' or risk_score >= 70:
            ai_recommendation = 'reject'
        elif compliance_outcome == 'requires_review' or risk_score >= 45:
            ai_recommendation = 'review'
        else:
            ai_recommendation = 'approve'
        
        ai_recommended_reject = ai_recommendation in ['reject', 'review']
        user_agrees_with_ai = ai_recommended_reject
        is_ai_override = not ai_recommended_reject
        
        # Document state snapshot
        document_state_at_decision = {
            'status': doc.get('status'),
            'risk_score': risk_score,
            'violations_count': violations_count,
            'compliance_outcome': compliance_outcome,
            'jurisdiction': doc.get('jurisdiction'),
            'assigned_to': doc.get('assigned_to'),
            'ticket_id': doc.get('ticket_id'),
        }
        
        try:
            ai_conversations = get_ai_conversations_for_document(doc_id, limit=20)
            ai_conversation_ids = [c.get('id') for c in ai_conversations]
        except:
            ai_conversations = []
            ai_conversation_ids = []
        
        # Save decision trail
        try:
            decision_trail_data = {
                'organization_id': org_id,
                'document_id': doc_id,
                'document_filename': doc.get('filename'),
                'decision': 'rejected',
                'decision_type': 'rejection',
                
                'decision_maker': {
                    'user_id': user_id,
                    'email': user_email,
                    'name': rejected_by_name,
                    'title': rejected_by_title,
                    'roles': user_roles,
                    'was_assignee': doc.get('assigned_to', '').lower() == user_email.lower(),
                },
                
                'decision_context': {
                    'reason': reason,
                    'severity': severity,
                    'required_changes': required_changes,
                    'cited_violations': cited_violations,
                },
                
                'document_state_at_decision': document_state_at_decision,
                
                'ai_context': {
                    'ai_recommendation': ai_recommendation,
                    'ai_risk_score': risk_score,
                    'user_agreed_with_ai': user_agrees_with_ai,
                    'ai_conversation_ids': ai_conversation_ids,
                },
                
                'is_ai_override': is_ai_override,
                
                'assignment_context': {
                    'assigned_to': doc.get('assigned_to'),
                    'assigned_by': doc.get('assigned_by'),
                    'ticket_id': doc.get('ticket_id'),
                },
                
                'request_metadata': request_metadata,
                'time_to_decision_hours': _calculate_time_to_decision(doc, now),
                'decision_timestamp': now.isoformat() + 'Z',
            }
            
            decision_trail = save_decision_trail(decision_trail_data)
            decision_trail_id = decision_trail.get('id')
            
        except Exception as e:
            logger.error(f"❌ Decision trail save failed: {e}")
            decision_trail_id = None
        
        # Track AI accuracy
        try:
            ai_predicted_non_compliant = violations_count > 0 or risk_score >= 50
            accuracy_result = 'TRUE_POSITIVE' if ai_predicted_non_compliant else 'FALSE_NEGATIVE'
            
            save_analytics_event({
                'organization_id': org_id,
                'type': 'ai_accuracy',
                'document_id': doc_id,
                'ai_predicted_non_compliant': ai_predicted_non_compliant,
                'human_decision': 'rejected',
                'accuracy_result': accuracy_result,
                'is_override': is_ai_override,
                'decision_maker': user_email,
            })
        except:
            pass
        
        # Track risk prevented
        try:
            FINE_AMOUNTS = {'CRITICAL': 500000, 'HIGH': 100000, 'MEDIUM': 25000, 'LOW': 5000}
            violations = doc.get('violations', [])
            fines_prevented = sum(
                FINE_AMOUNTS.get(v.get('severity', 'MEDIUM').upper(), 25000)
                for v in violations
            )
            
            save_analytics_event({
                'organization_id': org_id,
                'type': 'risk_prevented',
                'document_id': doc_id,
                'violations_caught': len(violations),
                'potential_fines_prevented_gbp': fines_prevented,
                'decision_maker': user_email,
            })
        except:
            pass
        
        # Update document
        update_data = {
            'status': 'rejected',
            'workflow_status': 'rejected',
            'approval_status': 'rejected',
            'rejected_by': user_email,
            'rejected_by_id': user_id,
            'rejected_by_name': rejected_by_name,
            'rejected_by_title': rejected_by_title,
            'rejected_at': now.isoformat() + 'Z',
            'rejection_reason': reason,
            'rejection_severity': severity,
            'required_changes': required_changes,
            'cited_violations': cited_violations,
            'decision_trail_id': decision_trail_id,
            'assignment_status': 'completed',
            'assignment_completed_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(doc_id, update_data, org_id)
        
        # Log activity
        try:
            log_activity(
                org_id=org_id,
                user_email=user_email,
                user_name=user_name,
                action='document_rejected',
                document_id=doc_id,
                document_name=doc.get('filename'),
                details={'reason': reason}
            )
        except:
            pass
        
        # Send notification to uploader
        try:
            from function_app_pkg.core.email_service import send_rejection_notification
            send_rejection_notification(
                to_email=doc.get('uploaded_by'),
                to_name=doc.get('uploaded_by_name', doc.get('uploaded_by')),
                document_name=doc.get('filename'),
                document_id=doc_id,
                rejected_by=rejected_by_name,
                reason=reason,
                required_changes=required_changes
            )
        except:
            pass
        
        logger.info(f"❌ Document {doc_id} REJECTED by {user_email}")
        
        return json_response(200, data={
            'document_id': doc_id,
            'status': 'rejected',
            'rejected_by': user_email,
            'rejected_by_name': rejected_by_name,
            'rejected_at': update_data['rejected_at'],
            'reason': reason,
            'required_changes': required_changes,
            'decision_trail_id': decision_trail_id,
            'ai_recommendation': ai_recommendation,
            'user_agreed_with_ai': user_agrees_with_ai,
            'assignment': {
                'was_assigned_to': doc.get('assigned_to'),
                'ticket_id': doc.get('ticket_id'),
                'assignment_completed': True,
            },
            'message': '❌ Document rejected - requires amendments',
        })
        
    except Exception as e:
        logger.error(f"❌ Rejection error: {e}", exc_info=True)
        return json_response(500, error=f"Rejection failed: {str(e)}")


# =============================================================================
# ESCALATION HANDLER - NOW WITH LEGAL ROUTING
# =============================================================================

def handle_escalate(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Escalate document - routes to Legal team or DLA Piper
    
    POST /documents/{documentId}/escalate
    
    Body:
    {
        "reason": "Complex regulatory question",
        "escalate_to": "legal" | "dla_piper" | "senior_compliance",
        "priority": "normal" | "high" | "urgent",
        "specific_questions": ["Question 1", "Question 2"]
    }
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_name = _get_user_attr(user, 'name', user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        doc = get_document_with_access_check(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id and 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="Access denied")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        reason = body.get('reason', 'Escalated for advisory review')
        if not reason:
            return json_response(400, error="Escalation reason is required")
        
        escalate_to = body.get('escalate_to', 'legal').lower().replace('_', '')
        priority = body.get('priority', 'normal')
        specific_questions = body.get('specific_questions', [])
        
        escalate_map = {
            'legal': 'legal',
            'internal_legal': 'legal',
            'seniorcompliance': 'senior_compliance',
            'management': 'management',
            'dlapiper': 'dla_piper',
        }
        escalate_to = escalate_map.get(escalate_to, 'legal')
        
        target_status_map = {
            'legal': EscalationTarget.LEGAL,
            'dla_piper': EscalationTarget.DLA_PIPER,
            'senior_compliance': EscalationTarget.SENIOR_COMPLIANCE,
            'management': EscalationTarget.MANAGEMENT,
        }
        workflow_status = target_status_map.get(escalate_to, EscalationTarget.LEGAL)
        
        now = datetime.utcnow()
        
        try:
            decision_trail = save_decision_trail({
                'organization_id': org_id,
                'document_id': doc_id,
                'document_filename': doc.get('filename'),
                'decision': 'escalated',
                'decision_type': 'escalation',
                'decision_maker': {
                    'email': user_email,
                    'name': user_name,
                    'roles': user_roles,
                },
                'decision_context': {
                    'reason': reason,
                    'escalate_to': escalate_to,
                    'priority': priority,
                    'specific_questions': specific_questions,
                },
                'document_state_at_decision': {
                    'risk_score': doc.get('risk_score', 0),
                    'violations_count': doc.get('violations_count', 0),
                    'assigned_to': doc.get('assigned_to'),
                },
                'decision_timestamp': now.isoformat() + 'Z',
            })
            decision_trail_id = decision_trail.get('id')
        except Exception as e:
            logger.error(f"Decision trail save failed: {e}")
            decision_trail_id = None
        
        update_data = {
            'status': 'escalated',
            'workflow_status': workflow_status,
            'escalated': True,
            'escalated_at': now.isoformat() + 'Z',
            'escalated_by': user_email,
            'escalated_by_name': user_name,
            'escalation_reason': reason,
            'escalation_target': escalate_to,
            'escalation_priority': priority,
            'escalation_questions': specific_questions,
            'decision_trail_id': decision_trail_id,
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(doc_id, update_data, org_id)
        
        if escalate_to == 'legal':
            _notify_legal_team(doc, user_email, reason, org_id)
            target_description = "internal legal team"
        elif escalate_to == 'dla_piper':
            target_description = "DLA Piper advisory team"
            logger.info(f"Document {doc_id} escalated to DLA Piper")
        elif escalate_to == 'senior_compliance':
            target_description = "senior compliance officer"
        else:
            target_description = "management"
        
        try:
            log_activity(
                org_id=org_id,
                user_email=user_email,
                user_name=user_name,
                action='document_escalated',
                document_id=doc_id,
                document_name=doc.get('filename'),
                details={'escalate_to': escalate_to, 'reason': reason}
            )
        except Exception as e:
            logger.warning(f"Activity log failed: {e}")
        
        logger.info(f"Document {doc_id} ESCALATED to {escalate_to} by {user_email}")
        
        return json_response(200, data={
            'document_id': doc_id,
            'status': 'escalated',
            'escalated_by': user_email,
            'escalated_at': update_data['escalated_at'],
            'escalated_to': escalate_to,
            'workflow_status': workflow_status,
            'reason': reason,
            'priority': priority,
            'decision_trail_id': decision_trail_id,
            'message': f'Document escalated to {target_description}',
            'next_steps': f'The {target_description} has been notified and will review this document.',
        })
        
    except Exception as e:
        logger.error(f"Escalation error: {e}", exc_info=True)
        return json_response(500, error=f"Escalation failed: {str(e)}")

# =============================================================================
# LEGAL QUEUE - Documents waiting for legal review
# =============================================================================

def handle_get_legal_queue(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /legal/queue
    Get documents escalated to legal team
    Only accessible by Legal.Advisor, DLAPiper.Advisory, or Platform.SuperAdmin
    """
    try:
        if not user:
            return json_response(401, error="Authentication required")
        
        user_roles = _get_user_attr(user, 'roles', [])
        org_id = _get_user_attr(user, 'organization_id')
        
        # Check if user has legal access
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Platform.SuperAdmin']
        if not any(role in user_roles for role in legal_roles):
            return json_response(403, error="Legal team access required")
        
        from function_app_pkg.core.database import get_container
        container = get_container('documents')
        
        # Build query based on role
        if 'DLAPiper.Advisory' in user_roles:
            # DLA Piper sees only docs escalated to them
            statuses = ['dla_piper_review']
        elif 'Platform.SuperAdmin' in user_roles:
            # Super admin sees all escalated
            statuses = ['legal_review', 'dla_piper_review', 'escalated']
        else:
            # Internal legal sees legal_review
            statuses = ['legal_review', 'escalated']
        
        status_list = ', '.join([f"'{s}'" for s in statuses])
        
        # Super admin can see all orgs
        if 'Platform.SuperAdmin' in user_roles:
            query = f"""
            SELECT * FROM c 
            WHERE c.type = 'document'
            AND c.workflow_status IN ({status_list})
            ORDER BY c.escalation_priority DESC, c.escalated_at ASC
            """
            params = []
        else:
            query = f"""
            SELECT * FROM c 
            WHERE c.organization_id = @org_id
            AND c.type = 'document'
            AND c.workflow_status IN ({status_list})
            ORDER BY c.escalation_priority DESC, c.escalated_at ASC
            """
            params = [{"name": "@org_id", "value": org_id}]
        
        docs = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        # Format response
        queue_items = []
        for doc in docs:
            queue_items.append({
                'document_id': doc.get('id'),
                'filename': doc.get('filename'),
                'organization_id': doc.get('organization_id'),
                'organization_name': doc.get('organization_name', 'Unknown'),
                'escalated_by': doc.get('escalated_by'),
                'escalated_by_name': doc.get('escalated_by_name'),
                'escalated_at': doc.get('escalated_at'),
                'escalation_reason': doc.get('escalation_reason'),
                'escalation_priority': doc.get('escalation_priority', 'normal'),
                'escalation_questions': doc.get('escalation_questions', []),
                'workflow_status': doc.get('workflow_status'),
                'risk_score': doc.get('risk_score', 0),
                'violations_count': doc.get('violations_count', 0),
                'jurisdiction': doc.get('jurisdiction'),
            })
        
        # Stats
        urgent = len([d for d in queue_items if d.get('escalation_priority') == 'urgent'])
        high = len([d for d in queue_items if d.get('escalation_priority') == 'high'])
        
        return json_response(200, data={
            'items': queue_items,
            'total': len(queue_items),
            'stats': {
                'urgent': urgent,
                'high': high,
                'normal': len(queue_items) - urgent - high,
            },
            'your_role': next((r for r in user_roles if r in legal_roles), 'unknown'),
        })
        
    except Exception as e:
        logger.error(f"❌ Legal queue error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get legal queue: {str(e)}")


# =============================================================================
# SUBMIT FOR REVIEW
# =============================================================================

def handle_submit_for_review(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """Submit document for compliance review"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        user_email = _get_user_attr(user, 'email', 'unknown')
        org_id = _get_user_attr(user, 'organization_id')
        
        doc = get_document_with_access_check(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        notes = body.get('notes', '')
        priority = body.get('priority', 'normal')
        
        now = datetime.utcnow()
        
        update_data = {
            'status': 'pending_review',
            'workflow_status': 'pending_review',
            'submitted_for_review_at': now.isoformat() + 'Z',
            'submitted_by': user_email,
            'review_notes': notes,
            'review_priority': priority,
            'updated_at': now.isoformat() + 'Z',
        }
        
        update_document(doc_id, update_data, org_id)
        
        try:
            save_analytics_event({
                'organization_id': org_id,
                'type': 'usage_event',
                'action': 'document_submitted_for_review',
                'user_email': user_email,
                'document_id': doc_id,
            })
        except:
            pass
        
        return json_response(200, data={
            'document_id': doc_id,
            'status': 'pending_review',
            'submitted_by': user_email,
            'submitted_at': update_data['submitted_for_review_at'],
            'message': '📤 Document submitted for compliance review. Assign a reviewer to proceed.',
            'next_step': 'POST /documents/{documentId}/assign to assign a reviewer',
        })
        
    except Exception as e:
        logger.error(f"❌ Submit for review error: {e}", exc_info=True)
        return json_response(500, error=f"Submit failed: {str(e)}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _calculate_time_to_decision(doc: Dict, decision_time: datetime) -> float:
    """Calculate hours from upload/scan to decision"""
    start_time_str = doc.get('scanned_at') or doc.get('created_at')
    if not start_time_str:
        return 0
    
    try:
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        if decision_time.tzinfo is None:
            decision_time = decision_time.replace(tzinfo=start_time.tzinfo)
        delta = decision_time - start_time
        return round(delta.total_seconds() / 3600, 2)
    except:
        return 0