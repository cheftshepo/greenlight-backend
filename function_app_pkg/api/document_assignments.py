"""
DOCUMENT ASSIGNMENT SYSTEM - ENHANCED WITH FULL HISTORICAL CONTEXT
===================================================================
IT Ticket-style document assignment with complete timeline, decision trails,
AI conversations, and audit history.

File: function_app_pkg/api/document_assignments.py
"""

import logging
import azure.functions as func
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import uuid

from ..shared.http_utils import json_response
from ..core.database import (
    get_db, get_document, update_document, get_document_with_access_check,
    get_users_by_org, get_user_by_email, get_user,
    get_decision_trail, get_ai_conversations_for_document,
    get_activity_feed, get_audit_logs,
    save_decision_trail, log_activity,
    UserRole, Permission, ROLE_PERMISSIONS
)

logger = logging.getLogger(__name__)


# =============================================================================
# HELPER: Safe attribute extraction
# =============================================================================

def _get_attr(obj, attr: str, default=None):
    """Safely extract attribute from object or dict"""
    if obj is None:
        return default
    if hasattr(obj, attr):
        return getattr(obj, attr)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _has_permission(user, permission: Permission) -> bool:
    """Check if user has specific permission - works with both objects and dicts"""
    if not user:
        return False
    
    # Handle both dict and object
    if isinstance(user, dict):
        user_roles = user.get('roles', [])
    else:
        user_roles = getattr(user, 'roles', [])
    
    if not user_roles:
        return False
    
    # Super admin has all permissions
    if UserRole.SUPER_ADMIN.value in user_roles:
        return True
    
    # Check each role for the permission
    for role_str in user_roles:
        try:
            role = UserRole(role_str)
            if permission in ROLE_PERMISSIONS.get(role, []):
                return True
        except ValueError:
            continue
    
    return False
# =============================================================================
# ASSIGN DOCUMENT TO USER
# =============================================================================

def handle_assign_document(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/assign
    Assign document to a user for review (IT ticket style)
    
    Body:
    {
        "assignee_email": "reviewer@company.com",
        "priority": "high",  // urgent | high | medium | low
        "deadline_hours": 48,
        "notes": "Please review section 3 carefully",
        "escalate_to_legal": false
    }
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_attr(user, 'organization_id')
        assigner_email = _get_attr(user, 'email', 'system')
        assigner_name = _get_attr(user, 'name', assigner_email)
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        user_dict = {
            'roles': _get_attr(user, 'roles', []),
            'email': _get_attr(user, 'email'),
            'organization_id': _get_attr(user, 'organization_id'),
        }
        if not _has_permission(user_dict, Permission.ASSIGNMENT_ASSIGN):
            return json_response(403, error="Insufficient permissions")
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        assignee_email = body.get('assignee_email', '').strip().lower()
        if not assignee_email:
            return json_response(400, error="assignee_email required")
        
        # Validate assignee exists in org
        assignee = get_user_by_email(assignee_email)
        if not assignee:
            return json_response(404, error=f"User {assignee_email} not found")
        
        if assignee.get('organization_id') != org_id:
            return json_response(403, error="Cannot assign to user outside organization")
        
        if not assignee.get('is_active', True):
            return json_response(400, error=f"User {assignee_email} is not active")
        
        # Check if escalation to legal
        escalate_to_legal = body.get('escalate_to_legal', False)
        if escalate_to_legal:
            # Check if user has legal escalation permission
            if not _has_permission(user, Permission.APPROVAL_ESCALATE_TO_LEGAL):
                return json_response(403, error="Cannot escalate to legal")
            
            # Log escalation
            log_activity(
                org_id=org_id,
                user_email=assigner_email,
                user_name=assigner_name,
                action='escalated_to_legal',
                document_id=doc_id,
                document_name=doc.get('filename'),
                details={
                    'assignee': assignee_email,
                    'reason': body.get('notes', '')
                }
            )
        
        # Check assignee workload
        workload = _get_user_workload(assignee_email, org_id)
        if workload.get('total', 0) >= 15:
            logger.warning(f"⚠️ User {assignee_email} has high workload: {workload['total']}")
        
        # Parse assignment params
        priority = body.get('priority', 'medium')
        if priority not in ['urgent', 'high', 'medium', 'low']:
            priority = 'medium'
        
        deadline_hours = int(body.get('deadline_hours', 48))
        notes = body.get('notes', '')
        team_id = body.get('team_id')
        team_name = body.get('team_name')
        
        # Calculate deadline
        now = datetime.utcnow()
        deadline = now + timedelta(hours=deadline_hours)
        
        # Generate ticket ID
        ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        
        # Track previous assignee for handoff history
        previous_assignee = doc.get('assigned_to')
        previous_assignee_name = doc.get('assigned_to_name')
        
        # Update handoff history
        handoff_history = doc.get('handoff_history', [])
        if previous_assignee and previous_assignee != assignee_email:
            handoff_history.append({
                'from': previous_assignee,
                'from_name': previous_assignee_name,
                'to': assignee_email,
                'to_name': assignee.get('name', assignee_email),
                'by': assigner_email,
                'reason': notes or f"Reassigned by {assigner_name}",
                'timestamp': now.isoformat() + 'Z',
                'type': 'reassignment' if doc.get('assigned_to') else 'initial_assignment',
                'ticket_id': ticket_id
            })
        
        # Update document with assignment
        assignment_data = {
            'assigned_to': assignee_email,
            'assigned_to_name': assignee.get('name', assignee_email),
            'assigned_by': assigner_email,
            'assigned_by_name': assigner_name,
            'assigned_at': now.isoformat() + 'Z',
            'assignment_priority': priority,
            'assignment_deadline': deadline.isoformat() + 'Z',
            'assignment_status': 'pending',
            'assignment_notes': [{
                'id': str(uuid.uuid4()),
                'timestamp': now.isoformat() + 'Z',
                'author': assigner_email,
                'author_name': assigner_name,
                'content': notes or f"Assigned by {assigner_name}",
                'type': 'assignment',
                'ticket_id': ticket_id,
                'escalated_to_legal': escalate_to_legal
            }],
            'ticket_id': ticket_id,
            'status': 'assigned',
            'handoff_history': handoff_history,
            'last_activity_at': now.isoformat() + 'Z',
            'escalated': escalate_to_legal or doc.get('escalated', False)
        }
        
        # Add team info if provided
        if team_id:
            assignment_data['team_id'] = team_id
        if team_name:
            assignment_data['team_name'] = team_name
        
        # Add escalation info
        if escalate_to_legal:
            assignment_data['escalated'] = True
            assignment_data['escalated_at'] = now.isoformat() + 'Z'
            assignment_data['escalated_by'] = assigner_email
            assignment_data['escalation_target'] = assignee_email
        
        update_document(doc_id, assignment_data, org_id)
        
        # Log activity
        log_activity(
            org_id=org_id,
            user_email=assigner_email,
            user_name=assigner_name,
            action='assigned_document',
            document_id=doc_id,
            document_name=doc.get('filename'),
            details={
                'to': assignee_email,
                'priority': priority,
                'deadline': deadline.isoformat() + 'Z',
                'ticket_id': ticket_id
            }
        )
        
        logger.info(f"✅ Document {doc_id} assigned to {assignee_email} (Ticket: {ticket_id})")
        
        return json_response(200, data={
            'document_id': doc_id,
            'ticket_id': ticket_id,
            'assigned_to': assignee_email,
            'assigned_to_name': assignee.get('name', assignee_email),
            'assigned_by': assigner_email,
            'assigned_by_name': assigner_name,
            'priority': priority,
            'deadline': deadline.isoformat() + 'Z',
            'deadline_hours': deadline_hours,
            'escalated_to_legal': escalate_to_legal,
            'handoff_history': handoff_history,
            'message': f'✅ Document assigned to {assignee_email}',
            'assignee_workload': workload
        })
        
    except Exception as e:
        logger.error(f"❌ Assign document failed: {e}", exc_info=True)
        return json_response(500, error=f"Assignment failed: {str(e)}")


# Alias for backward compatibility
assign_document_handler = handle_assign_document


# =============================================================================
# GET MY ASSIGNMENT QUEUE - ENHANCED
# =============================================================================

def handle_get_my_queue(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/my-queue
    Get current user's assignment queue with timeline preview
    """
    try:
        user_email = _get_attr(user, 'email')
        user_name = _get_attr(user, 'name', user_email)
        org_id = _get_attr(user, 'organization_id')
        
        if not user_email or not org_id:
            return json_response(401, error="Authentication required")
        
        # Query filters
        status = req.params.get('status', 'all')
        priority = req.params.get('priority', 'all')
        overdue = req.params.get('overdue', 'false').lower() == 'true'
        limit = int(req.params.get('limit', '50'))
        offset = int(req.params.get('offset', '0'))
        
        db = get_db()
        container = db.get_container('documents')
        
        # Build query - COSMOS DB COMPATIBLE
        conditions = [
            "c.type = 'document'",
            "c.organization_id = @org_id",
            "c.assigned_to = @user_email"
        ]
        params = [
            {"name": "@user_email", "value": user_email},
            {"name": "@org_id", "value": org_id}
        ]
        
        if status and status != 'all':
            conditions.append("c.assignment_status = @status")
            params.append({"name": "@status", "value": status})
        
        if priority and priority != 'all':
            conditions.append("c.assignment_priority = @priority")
            params.append({"name": "@priority", "value": priority})
        
        where_clause = " AND ".join(conditions)
        
        # Query with ordering by priority and deadline
        query = f"""
            SELECT *
            FROM c
            WHERE {where_clause}
            ORDER BY c.assignment_deadline ASC
            """
        items = list(container.query_items(
            query=query,
            parameters=params,
            partition_key=org_id
        ))
        
        # Sort by priority in Python (since Cosmos DB doesn't support CASE)
        priority_order = {'urgent': 0, 'high': 1, 'medium': 2, 'low': 3}
        items.sort(key=lambda x: (
            priority_order.get(x.get('assignment_priority', 'low'), 3),
            x.get('assignment_deadline', '')
        ))
        
        # Apply offset and limit
        items = items[offset:offset + limit]
        
        # Process items
        now = datetime.utcnow()
        assignments = []
        overdue_count = 0
        urgent_count = 0
        
        for item in items:
            assignment = _enrich_assignment_with_timeline(item, now)
            
            if assignment['is_overdue']:
                overdue_count += 1
            
            if assignment['priority'] == 'urgent':
                urgent_count += 1
            
            # Skip if filtering for overdue only
            if overdue and not assignment['is_overdue']:
                continue
            
            assignments.append(assignment)
        
        return json_response(200, data={
            'assignments': assignments,
            'total': len(items),  # Total matching query
            'overdue_count': overdue_count,
            'urgent_count': urgent_count,
            'pagination': {
                'limit': limit,
                'offset': offset,
                'has_more': (offset + len(assignments)) < len(items)
            },
            'filters': {
                'status': status,
                'priority': priority,
                'overdue': overdue
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get my queue failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get queue: {str(e)}")


# =============================================================================
# GET ASSIGNMENT WITH FULL CONTEXT
# =============================================================================

def handle_get_assignment_with_context(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/{assignmentId}/full-context
    Get assignment with all historical context (timeline, decisions, AI convos)
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        user_name = _get_attr(user, 'name', user_email)
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Get document (assignment ID = document ID)
        doc = get_document_with_access_check(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        # Check access - user must be assigned, a watcher, or have admin role
        user_roles = _get_attr(user, 'roles', [])
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Compliance.Officer', 
                                                'Platform.SuperAdmin', 'Legal.Advisor'])
        is_assigned = doc.get('assigned_to') == user_email
        is_watcher = user_email in doc.get('watchers', [])
        
        if not (is_assigned or is_watcher or is_admin):
            return json_response(403, error="Access denied")
        
        # Get enriched data
        now = datetime.utcnow()
        enriched_assignment = _enrich_assignment_with_timeline(doc, now)
        
        # Get decision trail
        decisions = get_decision_trail(assignment_id, org_id)
        
        # Get AI conversations
        ai_conversations = get_ai_conversations_for_document(assignment_id, limit=20)
        
        # Get activity feed for this document
        activity = get_activity_feed(org_id, document_id=assignment_id, days=30)
        
        # Get audit logs for this document
        audit_logs = get_audit_logs(org_id, resource_id=assignment_id, days=30)
        
        # Build complete timeline
        complete_timeline = _build_complete_timeline(
            doc=doc,
            decisions=decisions,
            activity=activity,
            audit_logs=audit_logs
        )
        
        # Get team context if applicable
        team_context = None
        if doc.get('team_id'):
            try:
                db = get_db()
                team_container = db.get_container('documents')
                team = team_container.read_item(
                    item=doc['team_id'],
                    partition_key=org_id
                )
                if team and team.get('type') == 'team':
                    team_context = {
                        'id': team.get('id'),
                        'name': team.get('name'),
                        'members': team.get('members', []),
                        'assignment_strategy': team.get('assignment_strategy')
                    }
            except Exception as e:
                logger.warning(f"Could not fetch team context: {e}")
                team_context = None
        
        # Get watchers with details
        watchers = []
        for watcher_email in doc.get('watchers', []):
            watcher_user = get_user_by_email(watcher_email)
            if watcher_user and watcher_user.get('organization_id') == org_id:
                watchers.append({
                    'email': watcher_email,
                    'name': watcher_user.get('name', watcher_email),
                    'role': watcher_user.get('roles', [])[0] if watcher_user.get('roles') else 'User'
                })
        
        return json_response(200, data={
            'assignment': enriched_assignment,
            'timeline': {
                'events': complete_timeline,
                'total_events': len(complete_timeline),
                'types': _get_timeline_event_types(complete_timeline)
            },
            'decisions': _format_decisions(decisions),
            'ai_conversations': _format_ai_conversations(ai_conversations),
            'audit_trail': _format_audit_logs(audit_logs),
            'team': team_context,
            'watchers': watchers,
            'statistics': {
                'total_decisions': len(decisions),
                'total_comments': len(doc.get('assignment_notes', [])),
                'ai_conversations_count': len(ai_conversations),
                'escalation_count': len([d for d in doc.get('handoff_history', []) 
                                         if 'escalate' in d.get('reason', '').lower()]),
                'handoff_count': len(doc.get('handoff_history', [])),
                'watcher_count': len(watchers),
                'audit_events_count': len(audit_logs)
            },
            'permissions': {
                'can_comment': True,  # Always true for assigned/watchers/admins
                'can_update_status': is_assigned,
                'can_reassign': is_admin or _has_permission(user, Permission.ASSIGNMENT_REASSIGN),
                'can_escalate': _has_permission(user, Permission.APPROVAL_ESCALATE),
                'can_add_watchers': is_admin or is_assigned,
                'can_view_decisions': is_admin or is_assigned or any('Legal' in r for r in user_roles)
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get assignment context failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# GET ASSIGNMENT TIMELINE
# =============================================================================

def handle_get_assignment_timeline(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/{assignmentId}/timeline
    Get just the timeline/activity for an assignment
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Check access
        doc = get_document_with_access_check(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        user_roles = _get_attr(user, 'roles', [])
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Compliance.Officer', 'Platform.SuperAdmin'])
        is_assigned = doc.get('assigned_to') == user_email
        
        if not (is_assigned or is_admin):
            return json_response(403, error="Access denied")
        
        # Get all timeline components
        decisions = get_decision_trail(assignment_id, org_id)
        activity = get_activity_feed(org_id, document_id=assignment_id, days=90)
        audit_logs = get_audit_logs(org_id, resource_id=assignment_id, days=90)
        
        # Build timeline
        timeline = _build_complete_timeline(
            doc=doc,
            decisions=decisions,
            activity=activity,
            audit_logs=audit_logs
        )
        
        return json_response(200, data={
            'timeline': timeline,
            'statistics': {
                'total_events': len(timeline),
                'by_type': _count_timeline_by_type(timeline),
                'by_user': _count_timeline_by_user(timeline),
                'time_range': {
                    'first': timeline[-1]['timestamp'] if timeline else None,
                    'last': timeline[0]['timestamp'] if timeline else None
                }
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get timeline failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# ADD WATCHER TO ASSIGNMENT
# =============================================================================

def handle_add_watcher(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /assignments/{assignmentId}/watchers
    Add a watcher to an assignment
    
    Body: { "email": "watcher@company.com" }
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        user_name = _get_attr(user, 'name', user_email)
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        watcher_email = body.get('email', '').strip().lower()
        if not watcher_email:
            return json_response(400, error="Email required")
        
        # Get document
        doc = get_document_with_access_check(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        # Check permissions
        is_assigned = doc.get('assigned_to') == user_email
        if not (_has_permission(user, Permission.ASSIGNMENT_REASSIGN) or is_assigned):
            return json_response(403, error="Cannot add watchers")
        
        # Validate watcher exists in org
        watcher = get_user_by_email(watcher_email)
        if not watcher:
            return json_response(404, error=f"User {watcher_email} not found")
        
        if watcher.get('organization_id') != org_id:
            return json_response(400, error="Watcher must be in same organization")
        
        # Add watcher
        watchers = doc.get('watchers', [])
        if watcher_email not in watchers:
            watchers.append(watcher_email)
            
            update_document(assignment_id, {
                'watchers': watchers,
                'last_activity_at': datetime.utcnow().isoformat() + 'Z'
            }, org_id)
            
            # Log activity
            log_activity(
                org_id=org_id,
                user_email=user_email,
                user_name=user_name,
                action='added_watcher',
                document_id=assignment_id,
                document_name=doc.get('filename'),
                details={
                    'watcher': watcher_email,
                    'watcher_name': watcher.get('name', watcher_email)
                }
            )
        
        return json_response(200, data={
            'watchers': watchers,
            'added': watcher_email,
            'message': f'Added {watcher_email} as watcher'
        })
        
    except Exception as e:
        logger.error(f"❌ Add watcher failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# GET ASSIGNMENT DECISIONS
# =============================================================================

def handle_get_assignment_decisions(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/{assignmentId}/decisions
    Get all decisions made on this assignment
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Check access
        doc = get_document_with_access_check(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        decisions = get_decision_trail(assignment_id, org_id)
        
        return json_response(200, data={
            'decisions': _format_decisions(decisions),
            'statistics': {
                'total': len(decisions),
                'approved': len([d for d in decisions if d.get('decision') == 'approved']),
                'rejected': len([d for d in decisions if d.get('decision') == 'rejected']),
                'escalated': len([d for d in decisions if d.get('decision') == 'escalated']),
                'ai_overrides': len([d for d in decisions if d.get('is_ai_override')]),
                'by_user': _group_decisions_by_user(decisions)
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get decisions failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# HELPER: Enrich assignment with timeline data
# =============================================================================

def _enrich_assignment_with_timeline(doc: Dict, now: datetime) -> Dict:
    """Enrich assignment document with timeline and SLA data"""
    
    # Calculate SLA status
    deadline_str = doc.get('assignment_deadline', '')
    is_overdue = False
    time_remaining_hours = None
    sla_status = 'on_track'
    
    if deadline_str:
        try:
            deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
            now_tz = now.replace(tzinfo=deadline.tzinfo)
            hours_remaining = (deadline - now_tz).total_seconds() / 3600
            time_remaining_hours = round(hours_remaining, 1)
            is_overdue = hours_remaining < 0
            
            if is_overdue:
                sla_status = 'breached'
            elif hours_remaining < 4:
                sla_status = 'at_risk'
        except:
            pass
    
    # Get recent activity
    recent_notes = doc.get('assignment_notes', [])[-3:]  # Last 3 notes
    recent_activity = []
    for note in recent_notes:
        recent_activity.append({
            'type': note.get('type', 'comment'),
            'timestamp': note.get('timestamp'),
            'author': note.get('author_name', note.get('author', 'Unknown')),
            'preview': note.get('content', '')[:100]
        })
    
    # Get handoff count
    handoff_count = len(doc.get('handoff_history', []))
    
    # Build enriched assignment
    return {
        'id': doc.get('id'),
        'document_id': doc.get('id'),
        'ticket_id': doc.get('ticket_id', ''),
        'document_name': doc.get('filename', 'Unknown'),
        'filename': doc.get('filename', 'Unknown'),
        'assigned_to': doc.get('assigned_to', ''),
        'assigned_to_name': doc.get('assigned_to_name', ''),
        'assigned_by': doc.get('assigned_by', ''),
        'assigned_by_name': doc.get('assigned_by_name', ''),
        'assigned_at': doc.get('assigned_at', ''),
        'deadline': deadline_str,
        'priority': doc.get('assignment_priority', 'medium'),
        'status': doc.get('assignment_status', 'pending'),
        'workflow_status': doc.get('status', ''),
        'is_overdue': is_overdue,
        'sla_status': sla_status,
        'time_remaining_hours': time_remaining_hours,
        'risk_score': doc.get('risk_score', 0),
        'violations_count': doc.get('violations_count', 0),
        'compliance_outcome': doc.get('compliance_outcome', ''),
        'jurisdiction': doc.get('jurisdiction', 'UK'),
        'escalated': doc.get('escalated', False),
        'team_id': doc.get('team_id'),
        'team_name': doc.get('team_name'),
        'notes_count': len(doc.get('assignment_notes', [])),
        'handoff_count': handoff_count,
        'watchers_count': len(doc.get('watchers', [])),
        'recent_activity': recent_activity,
        'last_activity_at': doc.get('last_activity_at', ''),
        'created_at': doc.get('created_at', '')
    }


def _build_complete_timeline(doc: Dict, decisions: List, activity: List, audit_logs: List) -> List:
    """Build complete timeline from all sources"""
    timeline = []
    
    # Add assignment notes
    for note in doc.get('assignment_notes', []):
        timeline.append({
            'id': note.get('id', str(uuid.uuid4())),
            'type': note.get('type', 'comment'),
            'subtype': note.get('new_status') if note.get('type') == 'status_update' else None,
            'timestamp': note.get('timestamp'),
            'author': note.get('author_name', note.get('author', 'Unknown')),
            'author_email': note.get('author'),
            'content': note.get('content', ''),
            'metadata': {
                'ticket_id': note.get('ticket_id'),
                'escalated': note.get('escalated_to_legal'),
                'priority': note.get('priority')
            },
            'source': 'assignment_notes'
        })
    
    # Add decisions
    for decision in decisions:
        timeline.append({
            'id': decision.get('id'),
            'type': 'decision',
            'subtype': decision.get('decision'),
            'timestamp': decision.get('decision_timestamp', decision.get('timestamp')),
            'author': decision.get('decision_maker', {}).get('name', 'Unknown'),
            'author_email': decision.get('decision_maker', {}).get('email'),
            'content': f"Decision: {decision.get('decision', 'Unknown')}",
            'metadata': {
                'is_ai_override': decision.get('is_ai_override'),
                'ai_recommendation': decision.get('ai_context', {}).get('original_recommendation'),
                'confidence': decision.get('ai_context', {}).get('ai_confidence'),
                'regulations': decision.get('regulations_considered', [])
            },
            'source': 'decision_trail'
        })
    
    # Add activity feed
    for act in activity:
        timeline.append({
            'id': act.get('id'),
            'type': 'activity',
            'subtype': act.get('action'),
            'timestamp': act.get('timestamp'),
            'author': act.get('user_name', 'Unknown'),
            'author_email': act.get('user_email'),
            'content': _format_activity_content(act),
            'metadata': act.get('details', {}),
            'source': 'activity_feed'
        })
    
    # Add audit logs
    for audit in audit_logs:
        if audit.get('type') == 'audit_log':
            timeline.append({
                'id': audit.get('id'),
                'type': 'audit',
                'subtype': audit.get('action'),
                'timestamp': audit.get('timestamp'),
                'author': audit.get('user_email', 'Unknown'),
                'author_email': audit.get('user_email'),
                'content': f"{audit.get('action')} {audit.get('resource_type')}",
                'metadata': audit.get('details', {}),
                'source': 'audit_logs'
            })
    
    # Add handoff history
    for handoff in doc.get('handoff_history', []):
        timeline.append({
            'id': str(uuid.uuid4()),
            'type': 'handoff',
            'subtype': handoff.get('type', 'reassignment'),
            'timestamp': handoff.get('timestamp'),
            'author': handoff.get('by', 'System'),
            'author_email': handoff.get('by'),
            'content': f"Reassigned from {handoff.get('from_name', handoff.get('from'))} to {handoff.get('to_name', handoff.get('to'))}",
            'metadata': handoff,
            'source': 'handoff_history'
        })
    
    # Add document creation event
    timeline.append({
        'id': str(uuid.uuid4()),
        'type': 'document_created',
        'subtype': 'upload',
        'timestamp': doc.get('created_at'),
        'author': doc.get('uploaded_by_name', doc.get('uploaded_by', 'Unknown')),
        'author_email': doc.get('uploaded_by'),
        'content': f"Document uploaded: {doc.get('filename', 'Unknown')}",
        'metadata': {
            'filename': doc.get('filename'),
            'size_bytes': doc.get('size_bytes'),
            'jurisdiction': doc.get('jurisdiction')
        },
        'source': 'document_created'
    })
    
    # Sort by timestamp (newest first)
    timeline.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    return timeline


def _format_activity_content(activity: Dict) -> str:
    """Format activity feed item into readable content"""
    action = activity.get('action', '')
    document_name = activity.get('document_name', 'document')
    
    if action == 'assigned_document':
        details = activity.get('details', {})
        return f"Assigned {document_name} to {details.get('to', 'user')}"
    elif action == 'added_watcher':
        details = activity.get('details', {})
        return f"Added {details.get('watcher_name', 'watcher')} as watcher"
    elif action == 'escalated_to_legal':
        return f"Escalated {document_name} to legal review"
    elif action == 'status_updated':
        return f"Updated status of {document_name}"
    elif action == 'completed_assignment':
        return f"Completed review of {document_name}"
    elif action == 'added_comment':
        return f"Added comment to {document_name}"
    else:
        return f"{action.replace('_', ' ').title()} {document_name}"


def _format_decisions(decisions: List) -> List:
    """Format decisions for frontend"""
    formatted = []
    for decision in decisions:
        formatted.append({
            'id': decision.get('id'),
            'decision': decision.get('decision'),
            'decision_type': decision.get('decision_type'),
            'decision_maker': decision.get('decision_maker', {}),
            'timestamp': decision.get('decision_timestamp', decision.get('timestamp')),
            'is_ai_override': decision.get('is_ai_override', False),
            'ai_context': decision.get('ai_context', {}),
            'regulations_considered': decision.get('regulations_considered', []),
            'time_to_decision_hours': decision.get('time_to_decision_hours'),
            'confidence_score': decision.get('ai_context', {}).get('ai_confidence'),
            'override_details': decision.get('override_details')
        })
    return formatted


def _format_ai_conversations(conversations: List) -> List:
    """Format AI conversations for frontend"""
    formatted = []
    for conv in conversations:
        formatted.append({
            'id': conv.get('id'),
            'user_email': conv.get('user_email'),
            'created_at': conv.get('created_at'),
            'updated_at': conv.get('updated_at'),
            'message_count': len(conv.get('messages', [])),
            'last_message': conv.get('messages', [{}])[-1].get('content', '')[:100] if conv.get('messages') else '',
            'context': conv.get('context', {})
        })
    return formatted


def _format_audit_logs(audit_logs: List) -> List:
    """Format audit logs for frontend"""
    formatted = []
    for log in audit_logs:
        if log.get('type') == 'audit_log':
            formatted.append({
                'id': log.get('id'),
                'action': log.get('action'),
                'user_email': log.get('user_email'),
                'resource_type': log.get('resource_type'),
                'resource_name': log.get('resource_name'),
                'timestamp': log.get('timestamp'),
                'success': log.get('success', True),
                'details': log.get('details', {})
            })
    return formatted


def _get_timeline_event_types(timeline: List) -> Dict:
    """Count timeline events by type"""
    types = {}
    for event in timeline:
        event_type = event.get('type', 'unknown')
        types[event_type] = types.get(event_type, 0) + 1
    return types


def _count_timeline_by_type(timeline: List) -> Dict:
    """Count timeline events by type"""
    return _get_timeline_event_types(timeline)


def _count_timeline_by_user(timeline: List) -> Dict:
    """Count timeline events by user"""
    users = {}
    for event in timeline:
        author = event.get('author', 'Unknown')
        users[author] = users.get(author, 0) + 1
    return users


def _group_decisions_by_user(decisions: List) -> Dict:
    """Group decisions by decision maker"""
    by_user = {}
    for decision in decisions:
        maker = decision.get('decision_maker', {}).get('name', 'Unknown')
        if maker not in by_user:
            by_user[maker] = []
        by_user[maker].append(decision.get('decision', 'unknown'))
    return by_user


# =============================================================================
# GET ASSIGNMENT DETAILS
# =============================================================================

def handle_get_assignment(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/{assignmentId}
    Get assignment details (basic version - use full-context for more)
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Get document (assignment ID = document ID)
        doc = get_document(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        # Check access - user must be assigned or have admin role
        user_roles = _get_attr(user, 'roles', [])
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Compliance.Officer', 'Platform.SuperAdmin'])
        
        if doc.get('assigned_to') != user_email and not is_admin:
            return json_response(403, error="Access denied")
        
        # Build enriched assignment
        now = datetime.utcnow()
        assignment = _enrich_assignment_with_timeline(doc, now)
        
        # Add a few recent notes
        recent_notes = doc.get('assignment_notes', [])[-5:]  # Last 5 notes
        assignment['recent_notes'] = recent_notes
        
        return json_response(200, data={'assignment': assignment})
        
    except Exception as e:
        logger.error(f"❌ Get assignment failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# UPDATE ASSIGNMENT STATUS
# =============================================================================

def handle_update_assignment(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /assignments/{assignmentId}
    Update assignment status (start, complete, etc.)
    
    Body:
    {
        "status": "in_progress" | "completed" | "blocked" | "reassign_requested",
        "notes": "Started review...",
        "decision_trail": { ... }  // Optional: record decision
    }
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        user_name = _get_attr(user, 'name', user_email)
        
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        new_status = body.get('status', '').strip()
        notes = body.get('notes', '')
        decision_trail = body.get('decision_trail')
        
        valid_statuses = ['pending', 'in_progress', 'completed', 'blocked', 'reassign_requested']
        if new_status and new_status not in valid_statuses:
            return json_response(400, error=f"Invalid status. Must be: {valid_statuses}")
        
        # Get document
        doc = get_document(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        # Check permission
        if doc.get('assigned_to') != user_email and not _has_permission(user, Permission.APPROVAL_APPROVE):
            return json_response(403, error="Only assigned user or approvers can update")
        
        # Build updates
        now = datetime.utcnow()
        update_data = {
            'assignment_status': new_status or doc.get('assignment_status'),
            'updated_at': now.isoformat() + 'Z',
            'last_activity_at': now.isoformat() + 'Z'
        }
        
        # Track status transitions
        if new_status == 'in_progress' and not doc.get('started_at'):
            update_data['started_at'] = now.isoformat() + 'Z'
        
        if new_status == 'completed':
            update_data['completed_at'] = now.isoformat() + 'Z'
            update_data['status'] = 'reviewed'  # Update document status too
            
            # Calculate time taken if assigned_at exists
            if doc.get('assigned_at'):
                try:
                    assigned_at = datetime.fromisoformat(doc['assigned_at'].replace('Z', '+00:00'))
                    time_taken = (now - assigned_at).total_seconds() / 3600
                except:
                    time_taken = None
            
            # Log completion activity
            log_activity(
                org_id=org_id,
                user_email=user_email,
                user_name=user_name,
                action='completed_assignment',
                document_id=assignment_id,
                document_name=doc.get('filename'),
                details={
                    'status': new_status,
                    'time_taken_hours': time_taken,
                    'previous_status': doc.get('assignment_status')
                }
            )
        
        # Add note if provided
        if notes:
            existing_notes = doc.get('assignment_notes', [])
            existing_notes.append({
                'id': str(uuid.uuid4()),
                'timestamp': now.isoformat() + 'Z',
                'author': user_email,
                'author_name': user_name,
                'content': notes,
                'type': 'status_update',
                'new_status': new_status
            })
            update_data['assignment_notes'] = existing_notes
        
        # Save decision trail if provided
        if decision_trail and isinstance(decision_trail, dict):
            decision_trail.update({
                'organization_id': org_id,
                'document_id': assignment_id,
                'document_filename': doc.get('filename'),
                'decision_maker': {
                    'email': user_email,
                    'name': user_name,
                    'role': user.get('roles', [])[0] if user.get('roles') else 'User'
                }
            })
            save_decision_trail(decision_trail)
        
        update_document(assignment_id, update_data, org_id)
        
        logger.info(f"✅ Assignment {assignment_id} updated to {new_status}")
        
        return json_response(200, data={
            'document_id': assignment_id,
            'status': new_status,
            'updated_at': update_data['updated_at'],
            'message': f'Assignment updated to {new_status}'
        })
        
    except Exception as e:
        logger.error(f"❌ Update assignment failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# ADD COMMENT TO ASSIGNMENT
# =============================================================================

def handle_add_comment(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /assignments/{assignmentId}/comments
    Add comment to assignment thread
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        org_id = _get_attr(user, 'organization_id')
        user_email = _get_attr(user, 'email')
        user_name = _get_attr(user, 'name', user_email)
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        content = body.get('content', '').strip()
        if not content:
            return json_response(400, error="Comment content required")
        
        # Get document
        doc = get_document(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        # Add comment
        now = datetime.utcnow()
        comment = {
            'id': str(uuid.uuid4()),
            'timestamp': now.isoformat() + 'Z',
            'author': user_email,
            'author_name': user_name,
            'content': content,
            'type': 'comment'
        }
        
        existing_notes = doc.get('assignment_notes', [])
        existing_notes.append(comment)
        
        update_document(assignment_id, {
            'assignment_notes': existing_notes,
            'last_activity_at': now.isoformat() + 'Z'
        }, org_id)
        
        # Log activity
        log_activity(
            org_id=org_id,
            user_email=user_email,
            user_name=user_name,
            action='added_comment',
            document_id=assignment_id,
            document_name=doc.get('filename'),
            details={
                'comment_preview': content[:100]
            }
        )
        
        logger.info(f"💬 Comment added to {assignment_id}")
        
        return json_response(201, data={
            'comment': comment,
            'message': 'Comment added'
        })
        
    except Exception as e:
        logger.error(f"❌ Add comment failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# TEAM WORKLOAD
# =============================================================================

def handle_get_team_workload(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/teamworkload
    Get workload distribution across team members
    """
    try:
        org_id = _get_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        container = db.get_container('documents')
        
        # Get all assigned documents
        query = """
        SELECT c.assigned_to, c.assignment_priority, c.assignment_status, c.assignment_deadline
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.assigned_to != null
        AND c.assignment_status IN ('pending', 'in_progress')
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        # Aggregate by user
        now = datetime.utcnow()
        workload = {}
        
        for doc in docs:
            assignee = doc.get('assigned_to')
            if not assignee:
                continue
            
            if assignee not in workload:
                workload[assignee] = {
                    'user_email': assignee,
                    'total': 0,
                    'pending_count': 0,      # ← Changed from 'pending'
                    'in_progress_count': 0,  # ← Changed from 'in_progress'
                    'urgent': 0,
                    'high': 0,
                    'overdue': 0
                }
            
            workload[assignee]['total'] += 1
            
            status = doc.get('assignment_status', 'pending')
            if status == 'pending':
                workload[assignee]['pending_count'] += 1  # ← Changed
            elif status == 'in_progress':
                workload[assignee]['in_progress_count'] += 1  # ← Changed
            
            priority = doc.get('assignment_priority', 'medium')
            if priority == 'urgent':
                workload[assignee]['urgent'] += 1
            elif priority == 'high':
                workload[assignee]['high'] += 1
            
            # Check overdue
            deadline_str = doc.get('assignment_deadline', '')
            if deadline_str:
                try:
                    deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                    if deadline < now.replace(tzinfo=deadline.tzinfo):
                        workload[assignee]['overdue'] += 1
                except:
                    pass
        
        # Get user names
        users = get_users_by_org(org_id)
        user_names = {u.get('email'): u.get('name', u.get('email')) for u in users}
        
        by_member = []  # ← Changed from team_workload
        for email, data in workload.items():
            data['user_name'] = user_names.get(email, email)
            by_member.append(data)
        
        # Sort by total (busiest first)
        by_member.sort(key=lambda x: x['total'], reverse=True)
        
        # ← CHANGED: Return format that matches frontend expectations
        return json_response(200, data={
            'by_member': by_member,  # ← Key name matches frontend
            'total_assigned': len(docs),
            'team_members_with_work': len(workload)
        })
        
    except Exception as e:
        logger.error(f"❌ Team workload failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# ASSIGNMENT ANALYTICS
# =============================================================================

def handle_get_assignment_analytics(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/analytics
    Get assignment performance metrics
    """
    try:
        org_id = _get_attr(user, 'organization_id')
        days = int(req.params.get('days', '30'))
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        container = db.get_container('documents')
        
        # Get assigned documents from last N days
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        
        query = """
        SELECT c.assigned_at, c.completed_at, c.assignment_status, 
               c.assignment_priority, c.assignment_deadline, c.assigned_to
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.assigned_at >= @start_date
        AND c.assigned_to != null
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@start_date", "value": start_date}
            ],
            partition_key=org_id
        ))
        
        # Calculate metrics
        total = len(docs)
        completed = sum(1 for d in docs if d.get('assignment_status') == 'completed')
        pending = sum(1 for d in docs if d.get('assignment_status') == 'pending')
        in_progress = sum(1 for d in docs if d.get('assignment_status') == 'in_progress')
        
        # Calculate average completion time
        completion_times = []
        on_time = 0
        late = 0
        
        for doc in docs:
            if doc.get('completed_at') and doc.get('assigned_at'):
                try:
                    assigned = datetime.fromisoformat(doc['assigned_at'].replace('Z', '+00:00'))
                    completed_at = datetime.fromisoformat(doc['completed_at'].replace('Z', '+00:00'))
                    hours = (completed_at - assigned).total_seconds() / 3600
                    completion_times.append(hours)
                    
                    # Check if on time
                    if doc.get('assignment_deadline'):
                        deadline = datetime.fromisoformat(doc['assignment_deadline'].replace('Z', '+00:00'))
                        if completed_at <= deadline:
                            on_time += 1
                        else:
                            late += 1
                except:
                    pass
        
        avg_completion_hours = sum(completion_times) / len(completion_times) if completion_times else 0
        
        # By priority
        by_priority = {
            'urgent': sum(1 for d in docs if d.get('assignment_priority') == 'urgent'),
            'high': sum(1 for d in docs if d.get('assignment_priority') == 'high'),
            'medium': sum(1 for d in docs if d.get('assignment_priority') == 'medium'),
            'low': sum(1 for d in docs if d.get('assignment_priority') == 'low')
        }
        
        return json_response(200, data={
            'period_days': days,
            'total_assignments': total,
            'status_breakdown': {
                'completed': completed,
                'pending': pending,
                'in_progress': in_progress
            },
            'completion_rate': round(completed / total * 100, 1) if total > 0 else 0,
            'avg_completion_hours': round(avg_completion_hours, 1),
            'sla_performance': {
                'on_time': on_time,
                'late': late,
                'on_time_rate': round(on_time / (on_time + late) * 100, 1) if (on_time + late) > 0 else 100
            },
            'by_priority': by_priority
        })
        
    except Exception as e:
        logger.error(f"❌ Assignment analytics failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_user_workload(user_email: str, org_id: str) -> Dict:
    """Get workload for a specific user"""
    db = get_db()
    container = db.get_container('documents')
    
    query = """
    SELECT c.assignment_priority, c.assignment_status
    FROM c 
    WHERE c.organization_id = @org_id 
    AND c.assigned_to = @user_email
    AND c.assignment_status IN ('pending', 'in_progress')
    """
    
    docs = list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@user_email", "value": user_email}
        ],
        partition_key=org_id
    ))
    
    return {
        'total': len(docs),
        'urgent': sum(1 for d in docs if d.get('assignment_priority') == 'urgent'),
        'high': sum(1 for d in docs if d.get('assignment_priority') == 'high'),
        'pending': sum(1 for d in docs if d.get('assignment_status') == 'pending'),
        'in_progress': sum(1 for d in docs if d.get('assignment_status') == 'in_progress')
    }


def add_assignment_comment(
    assignment_id: str,
    user_id: str,
    user_email: str,
    content: str,
    org_id: str,
    comment_type: str = 'comment'
) -> Dict:
    """Add comment to assignment (internal helper)"""
    doc = get_document(assignment_id, org_id)
    if not doc:
        raise ValueError(f"Assignment {assignment_id} not found")
    
    comment = {
        'id': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'author': user_email,
        'content': content,
        'type': comment_type
    }
    
    existing_notes = doc.get('assignment_notes', [])
    existing_notes.append(comment)
    
    update_document(assignment_id, {'assignment_notes': existing_notes}, org_id)
    
    return comment


# =============================================================================
# EXPORT HANDLERS
# =============================================================================

__all__ = [
    'handle_assign_document',
    'assign_document_handler',
    'handle_get_my_queue',
    'handle_get_assignment',
    'handle_get_assignment_with_context',  # NEW
    'handle_get_assignment_timeline',      # NEW
    'handle_get_assignment_decisions',     # NEW
    'handle_add_watcher',                  # NEW
    'handle_update_assignment',
    'handle_add_comment',
    'handle_get_team_workload',
    'handle_get_assignment_analytics',
    '_get_user_workload',
    'add_assignment_comment'
]