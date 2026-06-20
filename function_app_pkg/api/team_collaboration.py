"""
TEAM COLLABORATION MODULE
=========================
Real teamwork features for compliance review:
- Document watchers (follow docs you care about)
- Threaded discussions on documents
- @mentions to notify teammates
- Assignment handoffs
- Team activity feeds
- Shared team queues
- Collaboration on specific violations

File: function_app_pkg/api/team_collaboration.py
"""

import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid
import re

from ..core.database import (
    get_db,
    get_document,
    update_document,
    get_users_by_org,
    get_user_by_email,
    get_organization,
    log_action,
    save_analytics_event
)
from ..shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_user_attr(user, attr: str, default=None):
    """Safely extract attribute from user object or dict"""
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def _extract_mentions(text: str) -> List[str]:
    """Extract @mentions from text"""
    # Match @email or @name patterns
    pattern = r'@([\w\.\-]+@[\w\.\-]+|[\w\.\-]+)'
    matches = re.findall(pattern, text)
    return list(set(matches))


def _resolve_mention(mention: str, org_id: str) -> Optional[Dict]:
    """Resolve a mention to a user"""
    users = get_users_by_org(org_id)
    
    mention_lower = mention.lower()
    
    for user in users:
        email = user.get('email', '').lower()
        name = user.get('name', '').lower()
        
        # Match by email
        if email == mention_lower or email.startswith(mention_lower + '@'):
            return user
        
        # Match by name (first name or full name)
        if name == mention_lower or name.split()[0].lower() == mention_lower:
            return user
    
    return None


def _get_workspace_context(org_id: str) -> Dict:
    """Get workspace context for responses"""
    try:
        org = get_organization(org_id)
        if org:
            return {
                'workspace_id': org_id,
                'workspace_name': org.get('name', 'Unknown'),
            }
    except:
        pass
    return {'workspace_id': org_id}


# =============================================================================
# 1. DOCUMENT WATCHERS - Follow documents you care about
# =============================================================================

def handle_add_watcher(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/watchers
    Add yourself or someone else as a watcher on a document.
    
    Body (optional):
    {
        "user_email": "colleague@company.com"  // If omitted, adds current user
    }
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Tenant isolation
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            body = {}
        
        # Determine who to add as watcher
        watcher_email = body.get('user_email', user_email).lower().strip()
        
        # If adding someone else, verify they exist in org
        if watcher_email != user_email.lower():
            watcher_user = get_user_by_email(watcher_email)
            if not watcher_user:
                return json_response(404, error=f"User {watcher_email} not found")
            if watcher_user.get('organization_id') != org_id:
                return json_response(403, error="Cannot add watchers from other organizations")
        
        # Get current watchers
        watchers = doc.get('watchers', [])
        
        # Check if already watching
        if watcher_email in [w.lower() for w in watchers]:
            return json_response(200, data={
                'document_id': doc_id,
                'watcher': watcher_email,
                'message': 'Already watching this document',
                'total_watchers': len(watchers)
            })
        
        # Add watcher
        watchers.append(watcher_email)
        
        update_document(doc_id, {'watchers': watchers}, org_id)
        
        logger.info(f"👀 {watcher_email} now watching document {doc_id}")
        
        # Create notification for the watcher if added by someone else
        if watcher_email != user_email.lower():
            _create_notification(
                org_id=org_id,
                recipient_email=watcher_email,
                notification_type='added_as_watcher',
                title=f"You've been added as a watcher",
                message=f"{user_email} added you as a watcher on '{doc.get('filename')}'",
                document_id=doc_id,
                created_by=user_email
            )
        
        return json_response(200, data={
            'document_id': doc_id,
            'watcher': watcher_email,
            'added_by': user_email,
            'total_watchers': len(watchers),
            'message': f"✅ {watcher_email} is now watching this document"
        })
        
    except Exception as e:
        logger.error(f"❌ Add watcher failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_remove_watcher(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /documents/{documentId}/watchers/{watcherEmail}
    Remove a watcher from a document.
    """
    try:
        doc_id = req.route_params.get('documentId')
        watcher_email = req.route_params.get('watcherEmail', '').lower()
        
        if not doc_id or not watcher_email:
            return json_response(400, error="Document ID and watcher email required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Can only remove yourself or if you're admin
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        if watcher_email != user_email.lower() and not is_admin:
            return json_response(403, error="You can only remove yourself as a watcher")
        
        watchers = doc.get('watchers', [])
        watchers = [w for w in watchers if w.lower() != watcher_email]
        
        update_document(doc_id, {'watchers': watchers}, org_id)
        
        return json_response(200, data={
            'document_id': doc_id,
            'removed_watcher': watcher_email,
            'total_watchers': len(watchers),
            'message': f"✅ {watcher_email} is no longer watching this document"
        })
        
    except Exception as e:
        logger.error(f"❌ Remove watcher failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_get_watchers(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/watchers
    Get all watchers on a document.
    """
    try:
        doc_id = req.route_params.get('documentId')
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        watchers = doc.get('watchers', [])
        
        # Get user details for each watcher
        users = get_users_by_org(org_id)
        user_map = {u.get('email', '').lower(): u for u in users}
        
        watcher_details = []
        for watcher_email in watchers:
            user_info = user_map.get(watcher_email.lower(), {})
            watcher_details.append({
                'email': watcher_email,
                'name': user_info.get('name', watcher_email.split('@')[0]),
                'is_you': watcher_email.lower() == user_email.lower()
            })
        
        return json_response(200, data={
            'document_id': doc_id,
            'watchers': watcher_details,
            'total': len(watchers),
            'you_are_watching': user_email.lower() in [w.lower() for w in watchers]
        })
        
    except Exception as e:
        logger.error(f"❌ Get watchers failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 2. THREADED DISCUSSIONS - Comments on documents with @mentions
# =============================================================================

def handle_add_discussion(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions
    Add a discussion/comment to a document.
    
    Body:
    {
        "content": "Hey @john.smith, can you review section 3? I think there might be a GDPR issue.",
        "parent_id": null,  // For replies to other comments
        "violation_id": "v_123"  // Optional - link to specific violation
    }
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        content = body.get('content', '').strip()
        if not content:
            return json_response(400, error="Comment content required")
        
        parent_id = body.get('parent_id')
        violation_id = body.get('violation_id')
        
        now = datetime.utcnow()
        
        # Create discussion entry
        discussion = {
            'id': f"disc_{uuid.uuid4().hex[:12]}",
            'document_id': doc_id,
            'organization_id': org_id,
            'author_email': user_email,
            'author_name': user_name,
            'content': content,
            'parent_id': parent_id,
            'violation_id': violation_id,
            'created_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
            'reactions': [],
            'is_resolved': False
        }
        
        # Extract and process @mentions
        mentions = _extract_mentions(content)
        mentioned_users = []
        
        for mention in mentions:
            resolved_user = _resolve_mention(mention, org_id)
            if resolved_user:
                mentioned_users.append({
                    'email': resolved_user.get('email'),
                    'name': resolved_user.get('name'),
                    'mention_text': f"@{mention}"
                })
                
                # Create notification for mentioned user
                _create_notification(
                    org_id=org_id,
                    recipient_email=resolved_user.get('email'),
                    notification_type='mentioned',
                    title=f"You were mentioned in a discussion",
                    message=f"{user_name} mentioned you: \"{content[:100]}...\"" if len(content) > 100 else f"{user_name} mentioned you: \"{content}\"",
                    document_id=doc_id,
                    created_by=user_email,
                    discussion_id=discussion['id']
                )
        
        discussion['mentions'] = mentioned_users
        
        # Get existing discussions
        discussions = doc.get('discussions', [])
        discussions.append(discussion)
        
        # Update document
        update_document(doc_id, {
            'discussions': discussions,
            'last_activity_at': now.isoformat() + 'Z'
        }, org_id)
        
        # Notify watchers (except author and mentioned users)
        watchers = doc.get('watchers', [])
        mentioned_emails = [m['email'].lower() for m in mentioned_users]
        
        for watcher in watchers:
            if watcher.lower() != user_email.lower() and watcher.lower() not in mentioned_emails:
                _create_notification(
                    org_id=org_id,
                    recipient_email=watcher,
                    notification_type='new_discussion',
                    title=f"New comment on watched document",
                    message=f"{user_name} commented on '{doc.get('filename')}'",
                    document_id=doc_id,
                    created_by=user_email,
                    discussion_id=discussion['id']
                )
        
        # Log activity
        _log_activity(
            org_id=org_id,
            user_email=user_email,
            user_name=user_name,
            action='discussion_added',
            document_id=doc_id,
            document_name=doc.get('filename'),
            details={
                'discussion_id': discussion['id'],
                'mentions': [m['email'] for m in mentioned_users],
                'is_reply': parent_id is not None,
                'violation_id': violation_id
            }
        )
        
        logger.info(f"💬 Discussion added to {doc_id} by {user_email}")
        
        return json_response(201, data={
            'discussion': discussion,
            'mentions_notified': [m['email'] for m in mentioned_users],
            'watchers_notified': len([w for w in watchers if w.lower() != user_email.lower()]),
            'message': '✅ Comment added'
        })
        
    except Exception as e:
        logger.error(f"❌ Add discussion failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_get_discussions(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/discussions
    Get all discussions for a document.
    
    Query params:
    - violation_id: Filter by specific violation
    - include_resolved: Include resolved discussions (default: true)
    """
    try:
        doc_id = req.route_params.get('documentId')
        org_id = _get_user_attr(user, 'organization_id')
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        discussions = doc.get('discussions', [])
        
        # Filter by violation if specified
        violation_id = req.params.get('violation_id')
        if violation_id:
            discussions = [d for d in discussions if d.get('violation_id') == violation_id]
        
        # Filter resolved
        include_resolved = req.params.get('include_resolved', 'true').lower() == 'true'
        if not include_resolved:
            discussions = [d for d in discussions if not d.get('is_resolved')]
        
        # Organize into threads (top-level comments with replies)
        threads = []
        reply_map = {}
        
        for disc in discussions:
            if disc.get('parent_id'):
                parent_id = disc['parent_id']
                if parent_id not in reply_map:
                    reply_map[parent_id] = []
                reply_map[parent_id].append(disc)
            else:
                threads.append(disc)
        
        # Attach replies to their parents
        for thread in threads:
            thread['replies'] = reply_map.get(thread['id'], [])
            thread['reply_count'] = len(thread['replies'])
        
        # Sort by most recent
        threads.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return json_response(200, data={
            'document_id': doc_id,
            'discussions': threads,
            'total_threads': len(threads),
            'total_comments': len(discussions)
        })
        
    except Exception as e:
        logger.error(f"❌ Get discussions failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_resolve_discussion(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions/{discussionId}/resolve
    Mark a discussion thread as resolved.
    """
    try:
        doc_id = req.route_params.get('documentId')
        discussion_id = req.route_params.get('discussionId')
        
        if not doc_id or not discussion_id:
            return json_response(400, error="Document ID and Discussion ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        discussions = doc.get('discussions', [])
        
        # Find and update the discussion
        found = False
        for disc in discussions:
            if disc['id'] == discussion_id:
                disc['is_resolved'] = True
                disc['resolved_at'] = datetime.utcnow().isoformat() + 'Z'
                disc['resolved_by'] = user_email
                found = True
                break
        
        if not found:
            return json_response(404, error="Discussion not found")
        
        update_document(doc_id, {'discussions': discussions}, org_id)
        
        return json_response(200, data={
            'discussion_id': discussion_id,
            'resolved': True,
            'resolved_by': user_email,
            'message': '✅ Discussion marked as resolved'
        })
        
    except Exception as e:
        logger.error(f"❌ Resolve discussion failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 3. ASSIGNMENT HANDOFFS - Transfer work to teammates
# =============================================================================

def handle_handoff_assignment(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /assignments/{assignmentId}/handoff
    Transfer an assignment to another team member.
    
    Body:
    {
        "new_assignee_email": "colleague@company.com",
        "reason": "Going on leave, please take over",
        "retain_as_watcher": true
    }
    """
    try:
        assignment_id = req.route_params.get('assignmentId')
        if not assignment_id:
            return json_response(400, error="Assignment ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        
        # Get document (assignment_id = document_id)
        doc = get_document(assignment_id, org_id)
        if not doc:
            return json_response(404, error="Assignment not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Check if user can handoff (must be current assignee or admin)
        current_assignee = doc.get('assigned_to', '').lower()
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        
        if current_assignee != user_email.lower() and not is_admin:
            return json_response(403, error="Only the current assignee or admin can handoff")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        new_assignee_email = body.get('new_assignee_email', '').strip().lower()
        if not new_assignee_email:
            return json_response(400, error="new_assignee_email required")
        
        reason = body.get('reason', '')
        retain_as_watcher = body.get('retain_as_watcher', True)
        
        # Validate new assignee
        new_assignee = get_user_by_email(new_assignee_email)
        if not new_assignee:
            return json_response(404, error=f"User {new_assignee_email} not found")
        
        if new_assignee.get('organization_id') != org_id:
            return json_response(403, error="Cannot assign to user in different organization")
        
        if not new_assignee.get('is_active', True):
            return json_response(400, error=f"User {new_assignee_email} is not active")
        
        now = datetime.utcnow()
        
        # Record handoff in notes
        handoff_note = {
            'id': str(uuid.uuid4()),
            'timestamp': now.isoformat() + 'Z',
            'author': user_email,
            'content': f"Assignment handed off from {current_assignee} to {new_assignee_email}. Reason: {reason or 'Not specified'}",
            'type': 'handoff'
        }
        
        assignment_notes = doc.get('assignment_notes', [])
        assignment_notes.append(handoff_note)
        
        # Update watchers if requested
        watchers = doc.get('watchers', [])
        if retain_as_watcher and current_assignee not in [w.lower() for w in watchers]:
            watchers.append(current_assignee)
        
        # Update document
        update_data = {
            'assigned_to': new_assignee_email,
            'assigned_by': user_email,
            'assigned_at': now.isoformat() + 'Z',
            'assignment_notes': assignment_notes,
            'assignment_status': 'pending',  # Reset to pending for new assignee
            'watchers': watchers,
            'handoff_history': doc.get('handoff_history', []) + [{
                'from': current_assignee,
                'to': new_assignee_email,
                'by': user_email,
                'reason': reason,
                'timestamp': now.isoformat() + 'Z'
            }],
            'updated_at': now.isoformat() + 'Z'
        }
        
        update_document(assignment_id, update_data, org_id)
        
        # Notify new assignee
        _create_notification(
            org_id=org_id,
            recipient_email=new_assignee_email,
            notification_type='assignment_handoff',
            title=f"Assignment transferred to you",
            message=f"{user_name} has transferred a document review to you: '{doc.get('filename')}'. Reason: {reason or 'Not specified'}",
            document_id=assignment_id,
            created_by=user_email
        )
        
        # Notify previous assignee if different from current user
        if current_assignee != user_email.lower():
            _create_notification(
                org_id=org_id,
                recipient_email=current_assignee,
                notification_type='assignment_transferred',
                title=f"Assignment transferred",
                message=f"Your assignment on '{doc.get('filename')}' has been transferred to {new_assignee_email}",
                document_id=assignment_id,
                created_by=user_email
            )
        
        # Log activity
        _log_activity(
            org_id=org_id,
            user_email=user_email,
            user_name=user_name,
            action='assignment_handoff',
            document_id=assignment_id,
            document_name=doc.get('filename'),
            details={
                'from': current_assignee,
                'to': new_assignee_email,
                'reason': reason
            }
        )
        
        logger.info(f"🔄 Assignment {assignment_id} handed off from {current_assignee} to {new_assignee_email}")
        
        return json_response(200, data={
            'document_id': assignment_id,
            'previous_assignee': current_assignee,
            'new_assignee': new_assignee_email,
            'handed_off_by': user_email,
            'reason': reason,
            'previous_assignee_watching': retain_as_watcher,
            'message': f"✅ Assignment handed off to {new_assignee_email}"
        })
        
    except Exception as e:
        logger.error(f"❌ Handoff failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 4. TEAM ACTIVITY FEED - See what's happening in your workspace
# =============================================================================

def handle_get_activity_feed(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /team/activity
    Get activity feed for your workspace.
    
    Query params:
    - limit: Max items (default 50)
    - filter: all|my_documents|mentions|assigned (default: all)
    - days: How many days back (default: 7)
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        limit = int(req.params.get('limit', '50'))
        filter_type = req.params.get('filter', 'all')
        days = int(req.params.get('days', '7'))
        
        db = get_db()
        container = db.get_container('audit_logs')
        
        # Calculate cutoff
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        
        # Build query based on filter
        if filter_type == 'my_documents':
            # Activity on documents I uploaded or am assigned to
            query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.timestamp >= @cutoff
            AND (c.user_email = @user_email OR c.details.assigned_to = @user_email)
            ORDER BY c.timestamp DESC
            """
        elif filter_type == 'mentions':
            # Activity where I was mentioned
            query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.timestamp >= @cutoff
            AND ARRAY_CONTAINS(c.details.mentions, @user_email)
            ORDER BY c.timestamp DESC
            """
        elif filter_type == 'assigned':
            # Activity on documents assigned to me
            query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.timestamp >= @cutoff
            AND c.details.assigned_to = @user_email
            ORDER BY c.timestamp DESC
            """
        else:
            # All activity in workspace
            query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.timestamp >= @cutoff
            AND c.type = 'activity'
            ORDER BY c.timestamp DESC
            """
        
        activities = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff},
                {"name": "@user_email", "value": user_email}
            ],
            enable_cross_partition_query=True,
            max_item_count=limit
        ))
        
        # Format activities
        formatted = []
        for activity in activities[:limit]:
            formatted.append({
                'id': activity.get('id'),
                'action': activity.get('action'),
                'actor': {
                    'email': activity.get('user_email'),
                    'name': activity.get('user_name', activity.get('user_email', '').split('@')[0])
                },
                'document': {
                    'id': activity.get('document_id'),
                    'name': activity.get('document_name')
                },
                'details': activity.get('details', {}),
                'timestamp': activity.get('timestamp'),
                'is_your_action': activity.get('user_email', '').lower() == user_email.lower()
            })
        
        return json_response(200, data={
            'activities': formatted,
            'total': len(formatted),
            'filter': filter_type,
            'days': days,
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ Activity feed failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 5. TEAM QUEUES - Shared work distribution
# =============================================================================

def handle_get_team_queue(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /team/queue
    Get team-wide assignment queue.
    
    Query params:
    - status: pending|in_progress|all (default: all)
    - role: Filter by assignee role
    - unassigned: true to show only unassigned documents
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        status_filter = req.params.get('status', 'all')
        show_unassigned = req.params.get('unassigned', 'false').lower() == 'true'
        
        db = get_db()
        container = db.get_container('documents')
        
        # Build query
        conditions = [
            "c.organization_id = @org_id",
            "c.type = 'document'",
            "c.status IN ('pending_review', 'scanned', 'assigned')"
        ]
        
        if show_unassigned:
            conditions.append("(NOT IS_DEFINED(c.assigned_to) OR c.assigned_to = null OR c.assigned_to = '')")
        
        if status_filter != 'all':
            conditions.append("c.assignment_status = @status")
        
        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT c.id, c.filename, c.assigned_to, c.assigned_by, c.assigned_at,
                c.assignment_status, c.assignment_priority, c.assignment_deadline,
                c.ticket_id, c.risk_score, c.violations_count, c.jurisdiction,
                c.uploaded_by, c.created_at, c.status
            FROM c
            WHERE {where_clause}
            """

        
        params = [{"name": "@org_id", "value": org_id}]
        if status_filter != 'all':
            params.append({"name": "@status", "value": status_filter})
        
        docs = list(container.query_items(
            query=query,
            parameters=params,
            partition_key=org_id
        ))
        
        # Get team members for context
        users = get_users_by_org(org_id)
        user_map = {u.get('email', '').lower(): u for u in users}
        
        # Format queue
        now = datetime.utcnow()
        queue_items = []
        
        for doc in docs:
            assignee_email = doc.get('assigned_to', '')
            assignee_info = user_map.get(assignee_email.lower(), {}) if assignee_email else {}
            
            # Calculate SLA status
            deadline = doc.get('assignment_deadline', '')
            sla_status = 'no_deadline'
            hours_remaining = None
            
            if deadline:
                try:
                    deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                    hours_remaining = (deadline_dt - now.replace(tzinfo=deadline_dt.tzinfo)).total_seconds() / 3600
                    
                    if hours_remaining < 0:
                        sla_status = 'breached'
                    elif hours_remaining < 4:
                        sla_status = 'at_risk'
                    else:
                        sla_status = 'on_track'
                except:
                    pass
            
            queue_items.append({
                'document_id': doc.get('id'),
                'ticket_id': doc.get('ticket_id', ''),
                'filename': doc.get('filename'),
                'uploaded_by': doc.get('uploaded_by'),
                'created_at': doc.get('created_at'),
                'assignee': {
                    'email': assignee_email,
                    'name': assignee_info.get('name', assignee_email.split('@')[0] if assignee_email else 'Unassigned'),
                } if assignee_email else None,
                'assigned_by': doc.get('assigned_by'),
                'assigned_at': doc.get('assigned_at'),
                'status': doc.get('assignment_status', 'unassigned'),
                'priority': doc.get('assignment_priority', 'medium'),
                'deadline': deadline,
                'sla_status': sla_status,
                'hours_remaining': round(hours_remaining, 1) if hours_remaining else None,
                'risk_score': doc.get('risk_score', 0),
                'violations_count': doc.get('violations_count', 0),
                'jurisdiction': doc.get('jurisdiction'),
                'is_mine': assignee_email.lower() == user_email.lower() if assignee_email else False
            })
        
        # Calculate stats
        stats = {
            'total': len(queue_items),
            'unassigned': len([q for q in queue_items if not q['assignee']]),
            'pending': len([q for q in queue_items if q['status'] == 'pending']),
            'in_progress': len([q for q in queue_items if q['status'] == 'in_progress']),
            'at_risk': len([q for q in queue_items if q['sla_status'] == 'at_risk']),
            'breached': len([q for q in queue_items if q['sla_status'] == 'breached']),
            'my_items': len([q for q in queue_items if q['is_mine']])
        }
        
        return json_response(200, data={
            'queue': queue_items,
            'stats': stats,
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ Team queue failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_claim_from_queue(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /team/queue/{documentId}/claim
    Claim an unassigned document from the team queue.
    """
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Check if already assigned
        current_assignee = doc.get('assigned_to', '')
        if current_assignee:
            return json_response(400, error=f"Document already assigned to {current_assignee}")
        
        try:
            body = req.get_json()
        except:
            body = {}
        
        priority = body.get('priority', 'medium')
        deadline_hours = int(body.get('deadline_hours', 48))
        
        now = datetime.utcnow()
        deadline = now + timedelta(hours=deadline_hours)
        ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        
        update_data = {
            'assigned_to': user_email,
            'assigned_by': user_email,  # Self-assigned
            'assigned_at': now.isoformat() + 'Z',
            'assignment_status': 'pending',
            'assignment_priority': priority,
            'assignment_deadline': deadline.isoformat() + 'Z',
            'ticket_id': ticket_id,
            'status': 'assigned',
            'assignment_notes': [{
                'id': str(uuid.uuid4()),
                'timestamp': now.isoformat() + 'Z',
                'author': user_email,
                'content': f"Claimed from team queue by {user_name}",
                'type': 'claimed'
            }],
            'updated_at': now.isoformat() + 'Z'
        }
        
        update_document(doc_id, update_data, org_id)
        
        # Log activity
        _log_activity(
            org_id=org_id,
            user_email=user_email,
            user_name=user_name,
            action='claimed_from_queue',
            document_id=doc_id,
            document_name=doc.get('filename'),
            details={'ticket_id': ticket_id}
        )
        
        logger.info(f"✋ {user_email} claimed document {doc_id} from queue")
        
        return json_response(200, data={
            'document_id': doc_id,
            'ticket_id': ticket_id,
            'claimed_by': user_email,
            'deadline': deadline.isoformat() + 'Z',
            'message': f"✅ Document claimed. Ticket ID: {ticket_id}"
        })
        
    except Exception as e:
        logger.error(f"❌ Claim failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 6. NOTIFICATIONS - Central notification system
# =============================================================================

def _create_notification(
    org_id: str,
    recipient_email: str,
    notification_type: str,
    title: str,
    message: str,
    document_id: str = None,
    created_by: str = None,
    discussion_id: str = None,
    **kwargs
) -> Dict:
    """Create a notification for a user"""
    try:
        db = get_db()
        
        # Use audit_logs container for notifications (could be separate)
        container = db.get_container('audit_logs')
        
        now = datetime.utcnow()
        month = now.strftime('%Y-%m')
        
        notification = {
            'id': f"notif_{uuid.uuid4().hex[:12]}",
            'type': 'notification',
            'notification_type': notification_type,
            'partition_key': f"{org_id}_{month}",
            'organization_id': org_id,
            'recipient_email': recipient_email,
            'title': title,
            'message': message,
            'document_id': document_id,
            'discussion_id': discussion_id,
            'created_by': created_by,
            'created_at': now.isoformat() + 'Z',
            'read': False,
            'read_at': None,
            **kwargs
        }
        
        container.create_item(notification)
        logger.debug(f"🔔 Notification created for {recipient_email}: {title}")
        
        return notification
        
    except Exception as e:
        logger.error(f"❌ Failed to create notification: {e}")
        return {}


def handle_get_notifications(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /notifications
    Get notifications for current user.
    
    Query params:
    - unread_only: true/false (default: false)
    - limit: max items (default: 50)
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        unread_only = req.params.get('unread_only', 'false').lower() == 'true'
        limit = int(req.params.get('limit', '50'))
        
        db = get_db()
        container = db.get_container('audit_logs')
        
        # Query notifications
        conditions = [
            "c.type = 'notification'",
            "c.organization_id = @org_id",
            "c.recipient_email = @email"
        ]
        
        if unread_only:
            conditions.append("c.read = false")
        
        where_clause = " AND ".join(conditions)
        query = f"""
        SELECT * FROM c 
        WHERE {where_clause}
        ORDER BY c.created_at DESC
        """
        
        notifications = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@email", "value": user_email}
            ],
            enable_cross_partition_query=True,
            max_item_count=limit
        ))
        
        unread_count = len([n for n in notifications if not n.get('read')])
        
        return json_response(200, data={
            'notifications': notifications[:limit],
            'total': len(notifications),
            'unread_count': unread_count
        })
        
    except Exception as e:
        logger.error(f"❌ Get notifications failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_mark_notification_read(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /notifications/{notificationId}/read
    Mark a notification as read.
    """
    try:
        notification_id = req.route_params.get('notificationId')
        if not notification_id:
            return json_response(400, error="Notification ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        db = get_db()
        container = db.get_container('audit_logs')
        
        # Find notification
        query = """
        SELECT * FROM c 
        WHERE c.id = @id 
        AND c.organization_id = @org_id 
        AND c.recipient_email = @email
        AND c.type = 'notification'
        """
        
        notifications = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": notification_id},
                {"name": "@org_id", "value": org_id},
                {"name": "@email", "value": user_email}
            ],
            enable_cross_partition_query=True
        ))
        
        if not notifications:
            return json_response(404, error="Notification not found")
        
        notification = notifications[0]
        notification['read'] = True
        notification['read_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container.upsert_item(notification)
        
        return json_response(200, data={
            'notification_id': notification_id,
            'read': True,
            'message': '✅ Notification marked as read'
        })
        
    except Exception as e:
        logger.error(f"❌ Mark notification read failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_mark_all_notifications_read(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /notifications/mark-all-read
    Mark all notifications as read.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        db = get_db()
        container = db.get_container('audit_logs')
        
        # Get all unread notifications
        query = """
        SELECT * FROM c 
        WHERE c.type = 'notification'
        AND c.organization_id = @org_id 
        AND c.recipient_email = @email
        AND c.read = false
        """
        
        notifications = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@email", "value": user_email}
            ],
            enable_cross_partition_query=True
        ))
        
        now = datetime.utcnow().isoformat() + 'Z'
        
        for notification in notifications:
            notification['read'] = True
            notification['read_at'] = now
            container.upsert_item(notification)
        
        return json_response(200, data={
            'marked_read': len(notifications),
            'message': f"✅ {len(notifications)} notifications marked as read"
        })
        
    except Exception as e:
        logger.error(f"❌ Mark all read failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 7. ACTIVITY LOGGING HELPER
# =============================================================================

def _log_activity(
    org_id: str,
    user_email: str,
    user_name: str,
    action: str,
    document_id: str = None,
    document_name: str = None,
    details: Dict = None
):
    """Log an activity to the feed"""
    try:
        db = get_db()
        container = db.get_container('audit_logs')
        
        now = datetime.utcnow()
        month = now.strftime('%Y-%m')
        
        activity = {
            'id': f"activity_{uuid.uuid4().hex[:12]}",
            'type': 'activity',
            'partition_key': f"{org_id}_{month}",
            'organization_id': org_id,
            'user_email': user_email,
            'user_name': user_name,
            'action': action,
            'document_id': document_id,
            'document_name': document_name,
            'details': details or {},
            'timestamp': now.isoformat() + 'Z'
        }
        
        container.create_item(activity)
        
    except Exception as e:
        logger.warning(f"⚠️ Activity logging failed: {e}")


# =============================================================================
# 8. TEAM MEMBERS LIST
# =============================================================================

def handle_get_team_members(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /team/members
    Get all team members in your workspace for @mentions, assignments, etc.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        users = get_users_by_org(org_id)
        
        # Format for team display
        members = []
        for u in users:
            if not u.get('is_active', True):
                continue
            
            members.append({
                'user_id': u.get('id'),
                'email': u.get('email'),
                'name': u.get('name'),
                'roles': u.get('roles', []),
                'department': u.get('department', ''),
                'job_title': u.get('job_title', ''),
                'is_you': u.get('email', '').lower() == user_email.lower(),
                'last_active': u.get('last_login', '')
            })
        
        # Sort: you first, then by name
        members.sort(key=lambda x: (not x['is_you'], x['name'].lower()))
        
        return json_response(200, data={
            'members': members,
            'total': len(members),
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ Get team members failed: {e}", exc_info=True)
        return json_response(500, error=str(e))