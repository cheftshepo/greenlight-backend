"""
Workflow Engine - FIXED & ENHANCED
===================================
Multi-stage approval workflows with proper tenant isolation.

FIXES:
- Removed duplicate handle_get_pending_approvals function
- Added workspace context to all responses
- Enhanced tenant isolation
"""

import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid

from ..core.database import (
    get_db,
    get_organization,
    get_document,
    update_document,
    get_users_by_org,
    log_action,
    UserRole
)
from ..shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS & DATA MODELS
# =============================================================================

class WorkflowStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentWorkflowStatus:
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PENDING_STAGE = "pending_stage"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class ApprovalType:
    ANY_ONE = "any_one"
    ALL = "all"
    MAJORITY = "majority"


# =============================================================================
# HELPER: Get user attributes safely
# =============================================================================

def _get_user_attr(user, attr: str, default=None):
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def _get_workspace_context(org_id: str) -> Dict:
    """Get workspace/organization context for responses"""
    try:
        org = get_organization(org_id)
        if org:
            return {
                'workspace_id': org_id,
                'workspace_name': org.get('name', 'Unknown'),
                'subscription_tier': org.get('subscription_tier', 'trial'),
            }
    except:
        pass
    return {'workspace_id': org_id, 'workspace_name': 'Unknown', 'subscription_tier': 'unknown'}


# =============================================================================
# CREATE WORKFLOW
# =============================================================================

def handle_create_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /workflows
    Create a custom approval workflow for your organization.
    """
    try:
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        name = body.get('name', '').strip()
        if not name:
            return json_response(400, error="Workflow name required")
        
        stages = body.get('stages', [])
        if not stages or len(stages) < 1:
            return json_response(400, error="At least one stage required")
        
        # PATCH 1: Transform frontend format to backend format
        for i, stage in enumerate(stages):
            if not stage.get('name'):
                return json_response(400, error=f"Stage {i+1} missing name")
            
            # Convert required_role to approvers list
            required_role = stage.get('required_role')
            if required_role:
                # This role string becomes the approvers list
                stage['approvers'] = [required_role]
            elif not stage.get('approvers'):
                return json_response(400, error=f"Stage {i+1} needs required_role or approvers")
            
            # Set defaults
            stage.setdefault('approval_type', 'any_one')
            stage.setdefault('sla_hours', 48)
            stage['stage_number'] = i + 1
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Create workflow
        workflow = {
            'id': str(uuid.uuid4()),
            'type': 'workflow',
            'organization_id': org_id,  # TENANT ISOLATION
            'name': name,
            'description': body.get('description', ''),
            'status': WorkflowStatus.ACTIVE,
            # PATCH 3: Add Team ID to Workflows
            'team_id': body.get('team_id'),  # Optional - if provided, workflow is team-specific
            'auto_assign_to_team': body.get('auto_assign_to_team', False),  # Auto-assign documents to team
            'stages': [
                {
                    'stage_number': i + 1,
                    'name': s.get('name'),
                    'approvers': s.get('approvers', []),
                    'approval_type': s.get('approval_type', 'any_one'),
                    'sla_hours': s.get('sla_hours', 48),
                    'auto_escalate': s.get('auto_escalate', False),
                    'escalation_target': s.get('escalation_target', 'Organization.Admin')
                }
                for i, s in enumerate(stages)
            ],
            'rejection_behavior': body.get('rejection_behavior', 'return_to_submitter'),
            'notification_settings': body.get('notification_settings', {
                'notify_on_submission': True,
                'notify_on_approval': True,
                'notify_on_rejection': True,
                'reminder_hours': [24, 48]
            }),
            'created_by': user_email,
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'updated_at': datetime.utcnow().isoformat() + 'Z',
        }
        
        # Save to Cosmos
        db = get_db()
        container = db.get_container('documents')
        container.create_item(body=workflow)
        
        logger.info(f"✅ Created workflow: {name} ({workflow['id']}) for org {org_id}")
        
        # Audit log
        log_action(
            org_id=org_id,
            user_id=user_email,
            user_email=user_email,
            user_roles=_get_user_attr(user, 'roles', []),
            action='workflow.created',
            resource_type='workflow',
            resource_id=workflow['id'],
            resource_name=name,
            details={'stages': len(stages)}
        )
        
        return json_response(201, data={
            'workflow_id': workflow['id'],
            'name': name,
            'stages': len(stages),
            'workspace': _get_workspace_context(org_id),
            'message': '✅ Workflow created successfully'
        })
        
    except Exception as e:
        logger.error(f"❌ Create workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to create workflow: {str(e)}")


# =============================================================================
# LIST WORKFLOWS
# =============================================================================

def handle_list_workflows(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /workflows
    List workflows for YOUR organization only.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        container = db.get_container('documents')
        
        # TENANT ISOLATION: Only get workflows for this org
        query = """
        SELECT c.id, c.name, c.description, c.status, c.stages, c.created_at, c.created_by
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'workflow'
        ORDER BY c.created_at DESC
        """
        
        workflows = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        workflow_summaries = []
        for w in workflows:
            workflow_summaries.append({
                'workflow_id': w.get('id'),
                'name': w.get('name'),
                'description': w.get('description', ''),
                'status': w.get('status'),
                'stage_count': len(w.get('stages', [])),
                'stages': [{'name': s.get('name'), 'sla_hours': s.get('sla_hours')} for s in w.get('stages', [])],
                'created_at': w.get('created_at'),
                'created_by': w.get('created_by'),
            })
        
        return json_response(200, data={
            'workflows': workflow_summaries,
            'total': len(workflows),
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ List workflows failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to list workflows: {str(e)}")


# =============================================================================
# GET WORKFLOW DETAILS
# =============================================================================

def handle_get_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /workflows/{workflowId}
    Get workflow details.
    """
    try:
        workflow_id = req.route_params.get('workflowId')
        if not workflow_id:
            return json_response(400, error="Workflow ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        container = db.get_container('documents')
        
        try:
            workflow = container.read_item(item=workflow_id, partition_key=org_id)
        except:
            return json_response(404, error="Workflow not found")
        
        if workflow.get('type') != 'workflow':
            return json_response(404, error="Workflow not found")
        
        # TENANT ISOLATION CHECK
        if workflow.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        workflow['workspace'] = _get_workspace_context(org_id)
        
        return json_response(200, data=workflow)
        
    except Exception as e:
        logger.error(f"❌ Get workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get workflow: {str(e)}")


# =============================================================================
# SUBMIT DOCUMENT TO WORKFLOW
# =============================================================================

def handle_submit_document(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/submit-workflow
    Submit document to approval workflow.
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        workflow_id = body.get('workflow_id')
        if not workflow_id:
            return json_response(400, error="workflow_id required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION: Verify document belongs to user's org
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Get workflow
        db = get_db()
        container = db.get_container('documents')
        
        try:
            workflow = container.read_item(item=workflow_id, partition_key=org_id)
        except:
            return json_response(404, error="Workflow not found")
        
        # TENANT ISOLATION: Verify workflow belongs to same org
        if workflow.get('organization_id') != org_id:
            return json_response(403, error="Workflow not found in your organization")
        
        if workflow.get('status') != WorkflowStatus.ACTIVE:
            return json_response(400, error="Workflow is not active")
        
        if doc.get('status') not in ['scanned', 'uploaded', 'answers_submitted']:
            return json_response(400, error="Document must be scanned before submission")
        
        # Initialize workflow state
        now = datetime.utcnow()
        stages = workflow.get('stages', [])
        first_stage = stages[0] if stages else None
        
        if not first_stage:
            return json_response(400, error="Workflow has no stages")
        
        sla_deadline = now + timedelta(hours=first_stage.get('sla_hours', 48))
        approvers = _resolve_approvers(first_stage.get('approvers', []), org_id)
        
        workflow_state = {
            'workflow_id': workflow_id,
            'workflow_name': workflow.get('name'),
            'current_stage': 1,
            'total_stages': len(stages),
            'status': DocumentWorkflowStatus.IN_PROGRESS,
            'submitted_at': now.isoformat() + 'Z',
            'submitted_by': user_email,
            'notes': body.get('notes', ''),
            'priority': body.get('priority', 'normal'),
            'stage_history': [],
            'current_stage_data': {
                'stage_number': 1,
                'stage_name': first_stage.get('name'),
                'assigned_to': approvers,
                'approval_type': first_stage.get('approval_type', 'any_one'),
                'approvals_received': [],
                'started_at': now.isoformat() + 'Z',
                'due_by': sla_deadline.isoformat() + 'Z',
                'sla_hours': first_stage.get('sla_hours', 48),
                'auto_escalate': first_stage.get('auto_escalate', False)
            }
        }
        
        update_document(doc_id, {
            'workflow_state': workflow_state,
            'workflow_status': DocumentWorkflowStatus.IN_PROGRESS,
            'status': 'pending_review',
            'submitted_for_review_at': now.isoformat() + 'Z',
            'submitted_by': user_email
        }, org_id)
        
        logger.info(f"✅ Document {doc_id} submitted to workflow {workflow_id}")
        
        log_action(
            org_id=org_id,
            user_id=user_email,
            user_email=user_email,
            user_roles=_get_user_attr(user, 'roles', []),
            action='document.submitted_to_workflow',
            resource_type='document',
            resource_id=doc_id,
            resource_name=doc.get('filename', ''),
            details={
                'workflow_id': workflow_id,
                'workflow_name': workflow.get('name'),
                'stage': 1
            }
        )
        
        return json_response(200, data={
            'document_id': doc_id,
            'workflow_id': workflow_id,
            'current_stage': 1,
            'stage_name': first_stage.get('name'),
            'assigned_to': approvers,
            'due_by': sla_deadline.isoformat() + 'Z',
            'workspace': _get_workspace_context(org_id),
            'message': '✅ Document submitted for approval'
        })
        
    except Exception as e:
        logger.error(f"❌ Submit document failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to submit document: {str(e)}")


# =============================================================================
# GET PENDING APPROVALS - SINGLE DEFINITION (FIXED)
# =============================================================================

def handle_get_pending_approvals(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /workflows/pending-approvals
    Get documents pending current user's approval.
    
    FIXED: This is now the ONLY definition of this function.
    """
    try:
        logger.info("📋 Getting pending approvals...")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_roles = _get_user_attr(user, 'roles', [])
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        logger.info(f"🔍 User: {user_email}, Org: {org_id}")
        
        db = get_db()
        container = db.get_container('documents')
        
        # PATCH 2: Fix Pending Approvals Query
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document' 
        AND (
            c.status = 'pending_review' 
            OR (IS_DEFINED(c.workflow_state) AND c.workflow_state.status = 'in_progress')
        )
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        logger.info(f"📊 Found {len(docs)} documents in workflow")
        
        pending = []
        
        for doc in docs:
            workflow_state = doc.get('workflow_state', {})
            current_stage_data = workflow_state.get('current_stage_data', {})
            
            # Simple review (no workflow)
            if not workflow_state:
                if doc.get('status') == 'pending_review':
                    # Check if assigned to this user OR user is admin
                    assigned_to = doc.get('assigned_to', '')
                    is_assigned = assigned_to.lower() == user_email.lower() if assigned_to else False
                    is_admin = any(r in user_roles for r in ['Organization.Admin', 'Compliance.Officer', 'Platform.SuperAdmin'])
                    
                    if is_assigned or is_admin or not assigned_to:
                        pending.append({
                            'document_id': doc.get('id'),
                            'filename': doc.get('filename'),
                            'ticket_id': doc.get('ticket_id', ''),
                            'assigned_to': assigned_to,
                            'reason': 'assigned_review' if is_assigned else 'unassigned_review',
                            'type': 'simple_review',
                            'submitted_by': doc.get('uploaded_by'),
                            'submitted_at': doc.get('submitted_for_review_at', doc.get('created_at')),
                            'due_by': doc.get('assignment_deadline', ''),
                            'priority': doc.get('assignment_priority', 'normal'),
                            'risk_score': doc.get('risk_score', 0),
                            'violations_count': doc.get('violations_count', 0),
                        })
                continue
            
            # Workflow-based review
            assigned_to = current_stage_data.get('assigned_to', [])
            approvals_received = current_stage_data.get('approvals_received', [])
            
            already_approved = any(a.get('approver') == user_email for a in approvals_received)
            if already_approved:
                continue
            
            is_assigned = False
            for approver in assigned_to:
                if approver == user_email or any(role in user_roles for role in [approver]):
                    is_assigned = True
                    break
            
            if is_assigned:
                pending.append({
                    'document_id': doc.get('id'),
                    'filename': doc.get('filename'),
                    'ticket_id': doc.get('ticket_id', ''),
                    'workflow_id': workflow_state.get('workflow_id'),
                    'workflow_name': workflow_state.get('workflow_name'),
                    'current_stage': workflow_state.get('current_stage'),
                    'stage_name': current_stage_data.get('stage_name'),
                    'reason': 'workflow_stage',
                    'type': 'workflow_approval',
                    'submitted_by': workflow_state.get('submitted_by'),
                    'submitted_at': workflow_state.get('submitted_at'),
                    'due_by': current_stage_data.get('due_by'),
                    'priority': workflow_state.get('priority', 'normal'),
                    'approvals_received': len(approvals_received),
                    'approvals_required': len(assigned_to),
                    'approval_type': current_stage_data.get('approval_type'),
                    'risk_score': doc.get('risk_score', 0),
                    'violations_count': doc.get('violations_count', 0),
                })
        
        # Sort by due date
        pending.sort(key=lambda x: x.get('due_by', '') or '9999')
        
        logger.info(f"✅ Found {len(pending)} pending approvals for {user_email}")
        
        return json_response(200, data={
            'pending_approvals': pending,
            'total': len(pending),
            'user': user_email,
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Get pending approvals failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get pending approvals: {str(e)}")


# =============================================================================
# APPROVE WORKFLOW STAGE
# =============================================================================

def handle_approve_stage(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/approve-stage
    Approve current workflow stage.
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        workflow_state = doc.get('workflow_state')
        if not workflow_state:
            return json_response(400, error="Document not in workflow")
        
        if workflow_state.get('status') != DocumentWorkflowStatus.IN_PROGRESS:
            return json_response(400, error="Document workflow not in progress")
        
        current_stage_data = workflow_state.get('current_stage_data', {})
        assigned_to = current_stage_data.get('assigned_to', [])
        
        # Check permission
        can_approve = False
        for approver in assigned_to:
            if approver == user_email or any(role in user_roles for role in [approver]):
                can_approve = True
                break
        
        if not can_approve:
            return json_response(403, error="You are not authorized to approve this stage")
        
        # Check if already approved
        approvals = current_stage_data.get('approvals_received', [])
        if any(a.get('approver') == user_email for a in approvals):
            return json_response(400, error="You have already approved this stage")
        
        # Add approval
        now = datetime.utcnow()
        approval_record = {
            'approver': user_email,
            'approved_at': now.isoformat() + 'Z',
            'comments': body.get('comments', ''),
            'conditions': body.get('conditions', [])
        }
        
        approvals.append(approval_record)
        current_stage_data['approvals_received'] = approvals
        
        # Check if stage is complete
        approval_type = current_stage_data.get('approval_type', 'any_one')
        stage_complete = _check_stage_complete(approvals, assigned_to, approval_type)
        
        if stage_complete:
            current_stage = workflow_state.get('current_stage', 1)
            total_stages = workflow_state.get('total_stages', 1)
            
            stage_history = workflow_state.get('stage_history', [])
            stage_history.append({
                'stage_number': current_stage,
                'stage_name': current_stage_data.get('stage_name'),
                'status': 'approved',
                'approvals': approvals,
                'completed_at': now.isoformat() + 'Z'
            })
            
            if current_stage >= total_stages:
                # Workflow complete
                workflow_state['status'] = DocumentWorkflowStatus.APPROVED
                workflow_state['completed_at'] = now.isoformat() + 'Z'
                workflow_state['stage_history'] = stage_history
                workflow_state['current_stage_data'] = {}
                
                update_document(doc_id, {
                    'workflow_state': workflow_state,
                    'workflow_status': DocumentWorkflowStatus.APPROVED,
                    'status': 'approved',
                    'approval_status': 'approved',
                    'approved_at': now.isoformat() + 'Z',
                    'approved_by': user_email
                }, org_id)
                
                return json_response(200, data={
                    'document_id': doc_id,
                    'status': 'approved',
                    'message': '🎉 Workflow complete! Document approved.',
                    'workflow_complete': True,
                    'workspace': _get_workspace_context(org_id),
                })
            else:
                # Advance to next stage
                workflow_state = _advance_to_next_stage(workflow_state, stage_history, org_id)
                
                update_document(doc_id, {
                    'workflow_state': workflow_state,
                    'workflow_status': DocumentWorkflowStatus.IN_PROGRESS,
                    'status': 'pending_review'
                }, org_id)
                
                next_stage = workflow_state.get('current_stage_data', {})
                
                return json_response(200, data={
                    'document_id': doc_id,
                    'status': 'in_progress',
                    'current_stage': workflow_state.get('current_stage'),
                    'stage_name': next_stage.get('stage_name'),
                    'assigned_to': next_stage.get('assigned_to', []),
                    'message': f"✅ Stage approved. Moved to stage {workflow_state.get('current_stage')}",
                    'workflow_complete': False,
                    'workspace': _get_workspace_context(org_id),
                })
        else:
            workflow_state['current_stage_data'] = current_stage_data
            
            update_document(doc_id, {'workflow_state': workflow_state}, org_id)
            
            return json_response(200, data={
                'document_id': doc_id,
                'status': 'in_progress',
                'message': '✅ Approval recorded',
                'approvals_received': len(approvals),
                'approvals_required': len(assigned_to),
                'approval_type': approval_type,
                'stage_complete': False,
                'workspace': _get_workspace_context(org_id),
            })
        
    except Exception as e:
        logger.error(f"❌ Approve stage failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to approve stage: {str(e)}")


# =============================================================================
# REJECT WORKFLOW STAGE
# =============================================================================

def handle_reject_stage(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/reject-stage
    Reject current workflow stage.
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Rejection reason required")
        
        reason = body.get('reason', '').strip()
        if not reason:
            return json_response(400, error="Rejection reason required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        workflow_state = doc.get('workflow_state')
        if not workflow_state:
            return json_response(400, error="Document not in workflow")
        
        current_stage_data = workflow_state.get('current_stage_data', {})
        assigned_to = current_stage_data.get('assigned_to', [])
        
        can_reject = False
        for approver in assigned_to:
            if approver == user_email or approver in user_roles:
                can_reject = True
                break
        
        if not can_reject:
            return json_response(403, error="You are not authorized to reject this stage")
        
        now = datetime.utcnow()
        
        stage_history = workflow_state.get('stage_history', [])
        stage_history.append({
            'stage_number': workflow_state.get('current_stage'),
            'stage_name': current_stage_data.get('stage_name'),
            'status': 'rejected',
            'rejected_by': user_email,
            'rejected_at': now.isoformat() + 'Z',
            'reason': reason,
            'required_changes': body.get('required_changes', [])
        })
        
        workflow_state['status'] = DocumentWorkflowStatus.REJECTED
        workflow_state['rejected_at'] = now.isoformat() + 'Z'
        workflow_state['rejected_by'] = user_email
        workflow_state['rejection_reason'] = reason
        workflow_state['stage_history'] = stage_history
        workflow_state['current_stage_data'] = {}
        
        update_document(doc_id, {
            'workflow_state': workflow_state,
            'workflow_status': DocumentWorkflowStatus.REJECTED,
            'status': 'rejected',
            'approval_status': 'rejected',
            'rejected_at': now.isoformat() + 'Z',
            'rejected_by': user_email,
            'rejection_reason': reason,
            'required_changes': body.get('required_changes', [])
        }, org_id)
        
        logger.info(f"❌ Document {doc_id} rejected at stage {workflow_state.get('current_stage')}")
        
        return json_response(200, data={
            'document_id': doc_id,
            'status': 'rejected',
            'rejected_by': user_email,
            'reason': reason,
            'required_changes': body.get('required_changes', []),
            'message': '❌ Document rejected. Submitter notified.',
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Reject stage failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to reject stage: {str(e)}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _resolve_approvers(approver_list: List[str], org_id: str) -> List[str]:
    """Resolve approver list to actual email addresses."""
    resolved = []
    users = get_users_by_org(org_id)
    
    for approver in approver_list:
        if '@' in approver:
            resolved.append(approver)
        else:
            for user in users:
                if approver in user.get('roles', []):
                    resolved.append(user.get('email'))
    
    return list(set(resolved))


def _check_stage_complete(approvals: List[Dict], assigned_to: List[str], approval_type: str) -> bool:
    """Check if stage approval requirements are met."""
    if not assigned_to:
        return True
    
    approvals_count = len(approvals)
    required_count = len(assigned_to)
    
    if approval_type == ApprovalType.ANY_ONE:
        return approvals_count >= 1
    elif approval_type == ApprovalType.ALL:
        return approvals_count >= required_count
    elif approval_type == ApprovalType.MAJORITY:
        return approvals_count > (required_count / 2)
    
    return False


def _advance_to_next_stage(workflow_state: Dict, stage_history: List[Dict], org_id: str) -> Dict:
    """Advance workflow to next stage."""
    workflow_id = workflow_state.get('workflow_id')
    
    db = get_db()
    container = db.get_container('documents')
    
    try:
        workflow = container.read_item(item=workflow_id, partition_key=org_id)
    except:
        workflow_state['status'] = DocumentWorkflowStatus.APPROVED
        return workflow_state
    
    stages = workflow.get('stages', [])
    next_stage_num = workflow_state.get('current_stage', 0) + 1
    
    if next_stage_num > len(stages):
        workflow_state['status'] = DocumentWorkflowStatus.APPROVED
        return workflow_state
    
    next_stage = stages[next_stage_num - 1]
    
    now = datetime.utcnow()
    sla_deadline = now + timedelta(hours=next_stage.get('sla_hours', 48))
    approvers = _resolve_approvers(next_stage.get('approvers', []), org_id)
    
    workflow_state['current_stage'] = next_stage_num
    workflow_state['stage_history'] = stage_history
    workflow_state['current_stage_data'] = {
        'stage_number': next_stage_num,
        'stage_name': next_stage.get('name'),
        'assigned_to': approvers,
        'approval_type': next_stage.get('approval_type', 'any_one'),
        'approvals_received': [],
        'started_at': now.isoformat() + 'Z',
        'due_by': sla_deadline.isoformat() + 'Z',
        'sla_hours': next_stage.get('sla_hours', 48),
        'auto_escalate': next_stage.get('auto_escalate', False)
    }
    
    return workflow_state

# =============================================================================
# GET WORKFLOW RECOMMENDATIONS
# =============================================================================

def handle_get_recommendations(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /workflows/recommendations
    Get AI-powered workflow recommendations for a document.
    
    Query params: document_id (required)
    """
    try:
        logger.info("🧠 Getting workflow recommendations...")
        
        document_id = req.params.get('document_id')
        if not document_id:
            return json_response(400, error="document_id query parameter required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get the document
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Get available workflows for this org
        db = get_db()
        container = db.get_container('documents')
        
        query = """
        SELECT c.id, c.name, c.description, c.stages, c.created_by
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'workflow'
        AND c.status = 'active'
        ORDER BY c.created_at DESC
        """
        
        workflows = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        if not workflows:
            return json_response(200, data={
                'recommended_workflow_id': None,
                'recommended_workflow_name': None,
                'confidence': 0.0,
                'reasoning': 'No workflows available in your organization.',
                'risk_factors': [],
                'alternative_workflows': [],
                'workspace': _get_workspace_context(org_id),
            })
        
        # Analyze document for risk factors
        risk_factors = _analyze_document_for_risk_factors(doc)
        
        # Recommend workflow based on document analysis
        recommendation = _recommend_workflow(workflows, doc, risk_factors)
        
        # Get alternative workflows (top 2 runners-up)
        alternative_workflows = []
        for workflow in workflows:
            if workflow.get('id') != recommendation.get('id') and len(alternative_workflows) < 2:
                alternative_workflows.append({
                    'id': workflow.get('id'),
                    'name': workflow.get('name'),
                    'description': workflow.get('description', ''),
                    'why_not': 'Alternative option with similar requirements'
                })
        
        logger.info(f"✅ Generated workflow recommendation for document {document_id}")
        
        return json_response(200, data={
            'recommended_workflow_id': recommendation.get('id'),
            'recommended_workflow_name': recommendation.get('name'),
            'confidence': recommendation.get('confidence', 0.85),
            'reasoning': recommendation.get('reasoning', 'Document appears to be standard marketing material.'),
            'risk_factors': risk_factors,
            'alternative_workflows': alternative_workflows,
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Get recommendations failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get recommendations: {str(e)}")


# =============================================================================
# HELPER: Analyze document for risk factors
# =============================================================================

def _analyze_document_for_risk_factors(doc: Dict) -> List[str]:
    """Analyze document to identify risk factors for workflow selection."""
    risk_factors = []
    
    # Check risk score
    risk_score = doc.get('risk_score', 0)
    if risk_score >= 70:
        risk_factors.append('high_risk_score')
    elif risk_score >= 40:
        risk_factors.append('medium_risk_score')
    
    # Check violations
    violations_count = doc.get('violations_count', 0)
    if violations_count >= 5:
        risk_factors.append('multiple_violations')
    elif violations_count >= 1:
        risk_factors.append('has_violations')
    
    # Check jurisdiction
    jurisdiction = (doc.get('jurisdiction') or '').lower()
    if 'uk' in jurisdiction or 'fca' in jurisdiction:
        risk_factors.append('uk_regulated')
    if 'eu' in jurisdiction or 'esma' in jurisdiction:
        risk_factors.append('eu_regulated')
    if 'us' in jurisdiction or 'sec' in jurisdiction:
        risk_factors.append('us_regulated')
    
    # Check document type from briefing
    briefing = doc.get('briefing', {})
    marketing_type = briefing.get('marketing_type', '').lower()
    
    if 'pre' in marketing_type:
        risk_factors.append('pre_marketing')
    if 'product' in marketing_type:
        risk_factors.append('product_related')
    
    # Check if escalated before
    if doc.get('status') == 'escalated':
        risk_factors.append('previously_escalated')
    
    return risk_factors


# =============================================================================
# HELPER: Recommend workflow based on document analysis
# =============================================================================

def _recommend_workflow(workflows: List[Dict], doc: Dict, risk_factors: List[str]) -> Dict:
    """Recommend the most suitable workflow for a document."""
    if not workflows:
        return {}
    
    # Default to first workflow
    default_workflow = workflows[0]
    
    # If only one workflow, recommend it
    if len(workflows) == 1:
        return {
            'id': default_workflow.get('id'),
            'name': default_workflow.get('name'),
            'confidence': 0.9,
            'reasoning': 'Only workflow available in your organization.'
        }
    
    # Analyze document characteristics
    risk_score = doc.get('risk_score', 0)
    violations_count = doc.get('violations_count', 0)
    briefing = doc.get('briefing', {})
    marketing_type = briefing.get('marketing_type', '').lower()
    
    # Score each workflow based on suitability
    scored_workflows = []
    
    for workflow in workflows:
        score = 0.0
        reasoning_parts = []
        
        workflow_name = (workflow.get('name') or '').lower()
        workflow_desc = (workflow.get('description') or '').lower()
        
        # Check for comprehensive/review keywords (good for high risk)
        comprehensive_keywords = ['comprehensive', 'full', 'detailed', 'thorough', 'standard']
        express_keywords = ['express', 'quick', 'basic', 'simple']
        legal_keywords = ['legal', 'escalation', 'attorney']
        
        # High risk scores need comprehensive review
        if risk_score >= 60:
            if any(keyword in workflow_name for keyword in comprehensive_keywords):
                score += 0.3
                reasoning_parts.append('High risk score requires comprehensive review')
            elif any(keyword in workflow_name for keyword in express_keywords):
                score -= 0.2
        
        # Multiple violations need thorough review
        if violations_count >= 3:
            if any(keyword in workflow_name for keyword in comprehensive_keywords):
                score += 0.2
                reasoning_parts.append('Multiple violations require thorough review')
        
        # Pre-marketing often needs legal review
        if 'pre' in marketing_type:
            if any(keyword in workflow_name for keyword in legal_keywords):
                score += 0.25
                reasoning_parts.append('Pre-marketing often requires legal review')
        
        # If no special factors, prefer comprehensive/default workflows
        if not reasoning_parts and 'comprehensive' in workflow_name:
            score += 0.1
            reasoning_parts.append('Standard comprehensive review suitable for general documents')
        
        # Base confidence
        confidence = min(0.95, 0.7 + score)
        
        scored_workflows.append({
            'id': workflow.get('id'),
            'name': workflow.get('name'),
            'score': score,
            'confidence': confidence,
            'reasoning': '; '.join(reasoning_parts) if reasoning_parts else 'Appropriate for document type and risk profile'
        })
    
    # Sort by score (descending) and pick highest
    scored_workflows.sort(key=lambda x: x['score'], reverse=True)
    best_workflow = scored_workflows[0]
    
    # Ensure minimum confidence
    best_workflow['confidence'] = max(0.65, best_workflow['confidence'])
    
    return best_workflow

# Add after handle_get_workflow (around line 200)

# =============================================================================
# UPDATE WORKFLOW
# =============================================================================

def handle_update_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /workflows/{workflowId}
    Update workflow template (admin only).
    """
    try:
        workflow_id = req.route_params.get('workflowId')
        if not workflow_id:
            return json_response(400, error="Workflow ID required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get existing workflow
        db = get_db()
        container = db.get_container('documents')
        
        try:
            workflow = container.read_item(item=workflow_id, partition_key=org_id)
        except:
            return json_response(404, error="Workflow not found")
        
        # TENANT ISOLATION
        if workflow.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Update fields
        if 'name' in body:
            workflow['name'] = body['name'].strip()
        if 'description' in body:
            workflow['description'] = body['description'].strip()
        if 'status' in body:
            workflow['status'] = body['status']
        if 'stages' in body:
            stages = body['stages']
            # Transform frontend format
            for i, stage in enumerate(stages):
                if stage.get('required_role'):
                    stage['approvers'] = [stage['required_role']]
                stage.setdefault('approval_type', 'any_one')
                stage.setdefault('sla_hours', 48)
                stage['stage_number'] = i + 1
            workflow['stages'] = stages
        
        workflow['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        workflow['updated_by'] = user_email
        
        # Save
        container.replace_item(item=workflow_id, body=workflow)
        
        logger.info(f"✅ Updated workflow: {workflow_id}")
        
        return json_response(200, data={
            'workflow_id': workflow_id,
            'name': workflow['name'],
            'workspace': _get_workspace_context(org_id),
            'message': '✅ Workflow updated'
        })
        
    except Exception as e:
        logger.error(f"❌ Update workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to update workflow: {str(e)}")


# =============================================================================
# DELETE WORKFLOW
# =============================================================================

def handle_delete_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /workflows/{workflowId}
    Delete workflow template (admin only).
    """
    try:
        workflow_id = req.route_params.get('workflowId')
        if not workflow_id:
            return json_response(400, error="Workflow ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        container = db.get_container('documents')
        
        try:
            workflow = container.read_item(item=workflow_id, partition_key=org_id)
        except:
            return json_response(404, error="Workflow not found")
        
        # TENANT ISOLATION
        if workflow.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Check if in use
        query = """
        SELECT COUNT(1) as count FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND IS_DEFINED(c.workflow_state)
        AND c.workflow_state.workflow_id = @workflow_id
        AND c.workflow_state.status IN ('in_progress', 'pending_stage')
        """
        
        result = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@workflow_id", "value": workflow_id}
            ],
            partition_key=org_id
        ))
        
        in_use_count = result[0]['count'] if result else 0
        
        if in_use_count > 0:
            return json_response(400, error=f"Cannot delete: {in_use_count} documents currently in this workflow")
        
        # Archive instead of delete (soft delete)
        workflow['status'] = WorkflowStatus.ARCHIVED
        workflow['archived_at'] = datetime.utcnow().isoformat() + 'Z'
        container.replace_item(item=workflow_id, body=workflow)
        
        logger.info(f"🗑️ Archived workflow: {workflow_id}")
        
        return json_response(200, data={
            'workflow_id': workflow_id,
            'message': '✅ Workflow archived',
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Delete workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to delete workflow: {str(e)}")


# =============================================================================
# GET DOCUMENT WORKFLOW STATE
# =============================================================================

def handle_get_document_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/workflow
    Get current workflow state for a document.
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        workflow_state = doc.get('workflow_state')
        
        if not workflow_state:
            return json_response(200, data={
                'document_id': doc_id,
                'has_workflow': False,
                'workflow_state': None,
                'message': 'Document not in workflow',
                'workspace': _get_workspace_context(org_id),
            })
        
        return json_response(200, data={
            'document_id': doc_id,
            'has_workflow': True,
            'workflow_state': workflow_state,
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Get document workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get document workflow: {str(e)}")


# =============================================================================
# ASSIGN WORKFLOW (Alias for submit_document for consistency)
# =============================================================================

def handle_assign_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/workflow/assign
    Assign a workflow template to a document (alias for submit).
    """
    return handle_submit_document(req, user)


# =============================================================================
# ADVANCE WORKFLOW (Manual progression)
# =============================================================================

def handle_advance_workflow(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/workflow/advance
    Manually advance document to next workflow step (admin only).
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        # Only admins can manually advance
        if 'Organization.Admin' not in user_roles and 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="Only admins can manually advance workflows")
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        workflow_state = doc.get('workflow_state')
        if not workflow_state:
            return json_response(400, error="Document not in workflow")
        
        if workflow_state.get('status') != DocumentWorkflowStatus.IN_PROGRESS:
            return json_response(400, error="Document workflow not in progress")
        
        current_stage = workflow_state.get('current_stage', 1)
        total_stages = workflow_state.get('total_stages', 1)
        
        if current_stage >= total_stages:
            return json_response(400, error="Already at final stage")
        
        # Force approve current stage
        current_stage_data = workflow_state.get('current_stage_data', {})
        stage_history = workflow_state.get('stage_history', [])
        
        now = datetime.utcnow()
        stage_history.append({
            'stage_number': current_stage,
            'stage_name': current_stage_data.get('stage_name'),
            'status': 'skipped',
            'skipped_by': user_email,
            'skipped_at': now.isoformat() + 'Z',
            'reason': body.get('reason', 'Manually advanced by admin')
        })
        
        # Advance to next stage
        workflow_state = _advance_to_next_stage(workflow_state, stage_history, org_id)
        
        update_document(doc_id, {
            'workflow_state': workflow_state,
            'workflow_status': workflow_state.get('status'),
        }, org_id)
        
        logger.info(f"⏭️ Manually advanced workflow for {doc_id} to stage {workflow_state.get('current_stage')}")
        
        next_stage = workflow_state.get('current_stage_data', {})
        
        return json_response(200, data={
            'document_id': doc_id,
            'current_stage': workflow_state.get('current_stage'),
            'stage_name': next_stage.get('stage_name'),
            'assigned_to': next_stage.get('assigned_to', []),
            'message': f"✅ Advanced to stage {workflow_state.get('current_stage')}",
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Advance workflow failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to advance workflow: {str(e)}")