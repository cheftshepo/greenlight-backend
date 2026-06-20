"""
Team Workload endpoint — NEW
Fixes: GET /assignments/teamworkload 404

Add to your assignment handlers file or create as new module.
Register in function_app.py:

    @app.route(route="assignments/teamworkload", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
    def get_team_workload(req):
        return require_auth(req, handle_get_team_workload)
"""

import azure.functions as func
import logging
from datetime import datetime
from typing import Dict, List
from collections import defaultdict

from ..shared.http_utils import json_response
from ..core.database import (
    get_container,
    get_users_by_org,
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


def handle_get_team_workload(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /assignments/teamworkload
    Returns workload summary across all org members who have assignments.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_roles = _get_user_attr(user, 'roles', [])
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Only admins, compliance officers, and legal can see team workload
        allowed_roles = [
            'Platform.SuperAdmin', 'Organization.Admin',
            'Compliance.Officer', 'Legal.Advisor', 'DLAPiper.Advisory'
        ]
        if not any(role in user_roles for role in allowed_roles):
            return json_response(403, error="Insufficient permissions to view team workload")
        
        container = get_container('documents')
        
        # Query all assigned documents in the org
        query = """
        SELECT 
            c.id,
            c.assigned_to,
            c.assigned_to_name,
            c.assignment_status,
            c.assignment_priority,
            c.assignment_deadline,
            c.status,
            c.risk_score
        FROM c
        WHERE c.organization_id = @org_id
        AND c.type = 'document'
        AND IS_DEFINED(c.assigned_to)
        AND c.assigned_to != ''
        AND c.assigned_to != null
        """
        
        assigned_docs = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        # Group by assignee
        now = datetime.utcnow()
        member_workloads: Dict[str, Dict] = defaultdict(lambda: {
            'email': '',
            'name': '',
            'pending_count': 0,
            'in_progress_count': 0,
            'completed_count': 0,
            'overdue_count': 0,
            'at_risk_count': 0,
            'total_assigned': 0,
            'high_risk_count': 0,
        })
        
        for doc in assigned_docs:
            email = (doc.get('assigned_to') or '').lower()
            if not email:
                continue
            
            wl = member_workloads[email]
            wl['email'] = email
            wl['name'] = doc.get('assigned_to_name') or email.split('@')[0]
            wl['total_assigned'] += 1
            
            status = (doc.get('assignment_status') or doc.get('status') or '').lower()
            
            if status in ('pending', 'assigned'):
                wl['pending_count'] += 1
            elif status in ('in_progress', 'in-progress'):
                wl['in_progress_count'] += 1
            elif status in ('completed', 'approved', 'rejected'):
                wl['completed_count'] += 1
            
            # Check deadline
            deadline = doc.get('assignment_deadline')
            if deadline:
                try:
                    deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                    now_tz = now.replace(tzinfo=deadline_dt.tzinfo)
                    hours_remaining = (deadline_dt - now_tz).total_seconds() / 3600
                    
                    if hours_remaining < 0:
                        wl['overdue_count'] += 1
                    elif hours_remaining < 8:
                        wl['at_risk_count'] += 1
                except (ValueError, TypeError):
                    pass
            
            # High risk
            risk_score = doc.get('risk_score', 0)
            if isinstance(risk_score, (int, float)) and risk_score >= 70:
                wl['high_risk_count'] += 1
        
        # Convert to list sorted by total assigned (descending)
        by_member = sorted(
            member_workloads.values(),
            key=lambda x: x['total_assigned'],
            reverse=True
        )
        
        # Calculate totals
        total_assignments = len(assigned_docs)
        total_overdue = sum(m['overdue_count'] for m in by_member)
        total_at_risk = sum(m['at_risk_count'] for m in by_member)
        total_pending = sum(m['pending_count'] for m in by_member)
        total_in_progress = sum(m['in_progress_count'] for m in by_member)
        
        return json_response(200, data={
            'total_assignments': total_assignments,
            'total_members_with_work': len(by_member),
            'total_overdue': total_overdue,
            'total_at_risk': total_at_risk,
            'total_pending': total_pending,
            'total_in_progress': total_in_progress,
            'by_member': by_member,
        })
        
    except Exception as e:
        logger.error(f"❌ Get team workload failed: {e}", exc_info=True)
        return json_response(500, error=str(e))