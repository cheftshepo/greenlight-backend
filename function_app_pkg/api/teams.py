"""
TEAMS MANAGEMENT MODULE
=======================
Create and manage named teams within your organization:
- Create teams (UK Compliance, Marketing Review, etc.)
- Add/remove team members with roles
- Assign documents to teams (auto-distributes or round-robin)
- Team workload dashboards
- Team performance metrics
- Team escalation chains
- Team availability/capacity

File: function_app_pkg/api/teams.py
"""

import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid
from collections import defaultdict

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
# CONSTANTS
# =============================================================================

class TeamRole:
    """Roles within a team"""
    LEAD = "team_lead"           # Can manage team, assign work, approve
    SENIOR = "senior_reviewer"   # Can approve, mentor juniors
    REVIEWER = "reviewer"        # Standard reviewer
    JUNIOR = "junior_reviewer"   # Limited to low-risk documents
    OBSERVER = "observer"        # Read-only access


class AssignmentStrategy:
    """How to distribute work to team members"""
    ROUND_ROBIN = "round_robin"      # Rotate through members
    LEAST_LOADED = "least_loaded"    # Assign to person with fewest items
    RISK_BASED = "risk_based"        # High risk → senior, low risk → junior
    MANUAL = "manual"                # Team lead assigns manually


# =============================================================================
# HELPERS
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


def _get_team(team_id: str, org_id: str) -> Optional[Dict]:
    """Get a team by ID"""
    try:
        db = get_db()
        container = db.get_container('documents')
        team = container.read_item(item=team_id, partition_key=org_id)
        if team.get('type') == 'team' and team.get('organization_id') == org_id:
            return team
    except:
        pass
    return None


def _get_teams_for_org(org_id: str) -> List[Dict]:
    """Get all teams for an organization"""
    db = get_db()
    container = db.get_container('documents')
    
    query = """
    SELECT * FROM c 
    WHERE c.organization_id = @org_id 
    AND c.type = 'team'
    AND (NOT IS_DEFINED(c.is_archived) OR c.is_archived = false)
    ORDER BY c.name ASC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id
    ))


def _get_user_teams(user_email: str, org_id: str) -> List[Dict]:
    """Get all teams a user belongs to"""
    all_teams = _get_teams_for_org(org_id)
    user_teams = []
    
    for team in all_teams:
        members = team.get('members', [])
        for member in members:
            if member.get('email', '').lower() == user_email.lower():
                user_teams.append({
                    **team,
                    'your_role': member.get('role', TeamRole.REVIEWER)
                })
                break
    
    return user_teams


# =============================================================================
# 1. CREATE TEAM
# =============================================================================

def handle_create_team(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /teams
    Create a new team in your organization.
    
    Body:
    {
        "name": "UK Compliance Team",
        "description": "Reviews UK marketing materials",
        "assignment_strategy": "least_loaded",  // round_robin, least_loaded, risk_based, manual
        "jurisdictions": ["UK", "EU"],  // Optional: limit to specific jurisdictions
        "max_concurrent_per_member": 10,
        "default_sla_hours": 48,
        "escalation_chain": ["team_lead", "Organization.Admin"],
        "members": [
            {"email": "john@company.com", "role": "team_lead"},
            {"email": "jane@company.com", "role": "senior_reviewer"},
            {"email": "bob@company.com", "role": "reviewer"}
        ]
    }
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Check permission - only admins can create teams
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        if not is_admin:
            return json_response(403, error="Only admins can create teams")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        name = body.get('name', '').strip()
        if not name:
            return json_response(400, error="Team name required")
        
        # Check for duplicate name
        existing_teams = _get_teams_for_org(org_id)
        if any(t.get('name', '').lower() == name.lower() for t in existing_teams):
            return json_response(400, error=f"Team '{name}' already exists")
        
        # Validate members
        members_input = body.get('members', [])
        validated_members = []
        
        for member_input in members_input:
            email = member_input.get('email', '').lower().strip()
            role = member_input.get('role', TeamRole.REVIEWER)
            
            if not email:
                continue
            
            # Verify user exists in org
            member_user = get_user_by_email(email)
            if not member_user:
                return json_response(400, error=f"User {email} not found")
            if member_user.get('organization_id') != org_id:
                return json_response(400, error=f"User {email} is not in your organization")
            
            validated_members.append({
                'email': email,
                'name': member_user.get('name', email.split('@')[0]),
                'role': role,
                'added_at': datetime.utcnow().isoformat() + 'Z',
                'added_by': user_email
            })
        
        # Ensure at least one team lead
        has_lead = any(m.get('role') == TeamRole.LEAD for m in validated_members)
        if validated_members and not has_lead:
            # Make first member the lead
            validated_members[0]['role'] = TeamRole.LEAD
        
        now = datetime.utcnow()
        
        team = {
            'id': f"team_{uuid.uuid4().hex[:12]}",
            'type': 'team',
            'organization_id': org_id,
            'name': name,
            'description': body.get('description', ''),
            'assignment_strategy': body.get('assignment_strategy', AssignmentStrategy.LEAST_LOADED),
            'jurisdictions': body.get('jurisdictions', []),  # Empty = all
            'max_concurrent_per_member': body.get('max_concurrent_per_member', 10),
            'default_sla_hours': body.get('default_sla_hours', 48),
            'escalation_chain': body.get('escalation_chain', [TeamRole.LEAD, 'Organization.Admin']),
            'members': validated_members,
            'settings': {
                'auto_assign_on_upload': body.get('auto_assign_on_upload', False),
                'require_senior_for_high_risk': body.get('require_senior_for_high_risk', True),
                'notify_team_on_new_document': body.get('notify_team_on_new_document', True),
            },
            'stats': {
                'documents_assigned': 0,
                'documents_completed': 0,
                'avg_completion_hours': 0
            },
            'created_by': user_email,
            'created_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
            'is_archived': False
        }
        
        db = get_db()
        container = db.get_container('documents')
        container.create_item(body=team)
        
        logger.info(f"✅ Team created: {name} ({team['id']}) with {len(validated_members)} members")
        
        # Log action
        log_action(
            org_id=org_id,
            user_id=user_email,
            user_email=user_email,
            user_roles=user_roles,
            action='team.created',
            resource_type='team',
            resource_id=team['id'],
            resource_name=name,
            details={'members': len(validated_members)}
        )
        
        return json_response(201, data={
            'team_id': team['id'],
            'name': name,
            'members': len(validated_members),
            'assignment_strategy': team['assignment_strategy'],
            'workspace': _get_workspace_context(org_id),
            'message': f"✅ Team '{name}' created with {len(validated_members)} members"
        })
        
    except Exception as e:
        logger.error(f"❌ Create team failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 2. LIST TEAMS
# =============================================================================

def handle_list_teams(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /teams
    List all teams in your organization.
    
    Query params:
    - my_teams: true to only show teams you belong to
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        my_teams_only = req.params.get('my_teams', 'false').lower() == 'true'
        
        if my_teams_only:
            teams = _get_user_teams(user_email, org_id)
        else:
            teams = _get_teams_for_org(org_id)
        
        # Format response
        team_list = []
        for team in teams:
            members = team.get('members', [])
            
            # Find your role in team
            your_role = None
            for m in members:
                if m.get('email', '').lower() == user_email.lower():
                    your_role = m.get('role')
                    break
            
            team_list.append({
                'team_id': team.get('id'),
                'name': team.get('name'),
                'description': team.get('description', ''),
                'member_count': len(members),
                'assignment_strategy': team.get('assignment_strategy'),
                'jurisdictions': team.get('jurisdictions', []),
                'default_sla_hours': team.get('default_sla_hours', 48),
                'your_role': your_role,
                'you_are_member': your_role is not None,
                'stats': team.get('stats', {}),
                'created_at': team.get('created_at')
            })
        
        return json_response(200, data={
            'teams': team_list,
            'total': len(team_list),
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ List teams failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 3. GET TEAM DETAILS
# =============================================================================

def handle_get_team(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /teams/{teamId}
    Get team details including members and stats.
    """
    try:
        team_id = req.route_params.get('teamId')
        if not team_id:
            return json_response(400, error="Team ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        # Get current workload for each member
        db = get_db()
        container = db.get_container('documents')
        
        members_with_workload = []
        for member in team.get('members', []):
            email = member.get('email')
            
            # Count active assignments
            query = """
            SELECT VALUE COUNT(1) FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'document'
            AND c.assigned_to = @email
            AND c.assignment_status IN ('pending', 'in_progress')
            """
            
            count_result = list(container.query_items(
                query=query,
                parameters=[
                    {"name": "@org_id", "value": org_id},
                    {"name": "@email", "value": email}
                ],
                partition_key=org_id
            ))
            
            active_count = count_result[0] if count_result else 0
            max_concurrent = team.get('max_concurrent_per_member', 10)
            
            members_with_workload.append({
                **member,
                'active_assignments': active_count,
                'max_assignments': max_concurrent,
                'capacity_percentage': round((active_count / max_concurrent) * 100) if max_concurrent > 0 else 0,
                'is_at_capacity': active_count >= max_concurrent,
                'is_you': email.lower() == user_email.lower()
            })
        
        # Find your role
        your_role = None
        for m in members_with_workload:
            if m.get('is_you'):
                your_role = m.get('role')
                break
        
        return json_response(200, data={
            'team_id': team.get('id'),
            'name': team.get('name'),
            'description': team.get('description', ''),
            'assignment_strategy': team.get('assignment_strategy'),
            'jurisdictions': team.get('jurisdictions', []),
            'max_concurrent_per_member': team.get('max_concurrent_per_member', 10),
            'default_sla_hours': team.get('default_sla_hours', 48),
            'escalation_chain': team.get('escalation_chain', []),
            'settings': team.get('settings', {}),
            'members': members_with_workload,
            'member_count': len(members_with_workload),
            'your_role': your_role,
            'stats': team.get('stats', {}),
            'created_by': team.get('created_by'),
            'created_at': team.get('created_at'),
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ Get team failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 4. ADD TEAM MEMBER
# =============================================================================

def handle_add_team_member(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /teams/{teamId}/members
    Add a member to a team.
    
    Body:
    {
        "email": "newmember@company.com",
        "role": "reviewer"  // team_lead, senior_reviewer, reviewer, junior_reviewer, observer
    }
    """
    try:
        team_id = req.route_params.get('teamId')
        if not team_id:
            return json_response(400, error="Team ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        # Check permission - team lead or admin
        members = team.get('members', [])
        is_team_lead = any(
            m.get('email', '').lower() == user_email.lower() and m.get('role') == TeamRole.LEAD
            for m in members
        )
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        
        if not is_team_lead and not is_admin:
            return json_response(403, error="Only team leads or admins can add members")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        new_email = body.get('email', '').lower().strip()
        new_role = body.get('role', TeamRole.REVIEWER)
        
        if not new_email:
            return json_response(400, error="Email required")
        
        # Check if already a member
        if any(m.get('email', '').lower() == new_email for m in members):
            return json_response(400, error=f"{new_email} is already a member")
        
        # Verify user exists in org
        new_member_user = get_user_by_email(new_email)
        if not new_member_user:
            return json_response(404, error=f"User {new_email} not found")
        if new_member_user.get('organization_id') != org_id:
            return json_response(403, error=f"User {new_email} is not in your organization")
        
        # Add member
        new_member = {
            'email': new_email,
            'name': new_member_user.get('name', new_email.split('@')[0]),
            'role': new_role,
            'added_at': datetime.utcnow().isoformat() + 'Z',
            'added_by': user_email
        }
        
        members.append(new_member)
        
        db = get_db()
        container = db.get_container('documents')
        
        team['members'] = members
        team['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container.upsert_item(team)
        
        logger.info(f"✅ Added {new_email} to team {team.get('name')}")
        
        # Create notification for new member
        _create_team_notification(
            org_id=org_id,
            recipient_email=new_email,
            notification_type='added_to_team',
            title=f"You've been added to {team.get('name')}",
            message=f"{user_email} added you to the team '{team.get('name')}' as {new_role}",
            team_id=team_id,
            created_by=user_email
        )
        
        return json_response(200, data={
            'team_id': team_id,
            'member_added': new_email,
            'role': new_role,
            'total_members': len(members),
            'message': f"✅ {new_email} added to team as {new_role}"
        })
        
    except Exception as e:
        logger.error(f"❌ Add team member failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 5. REMOVE TEAM MEMBER
# =============================================================================

def handle_remove_team_member(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /teams/{teamId}/members/{memberEmail}
    Remove a member from a team.
    """
    try:
        team_id = req.route_params.get('teamId')
        member_email = req.route_params.get('memberEmail', '').lower()
        
        if not team_id or not member_email:
            return json_response(400, error="Team ID and member email required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        # Check permission
        members = team.get('members', [])
        is_team_lead = any(
            m.get('email', '').lower() == user_email.lower() and m.get('role') == TeamRole.LEAD
            for m in members
        )
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        is_self = member_email == user_email.lower()
        
        if not is_team_lead and not is_admin and not is_self:
            return json_response(403, error="Only team leads, admins, or the member themselves can remove")
        
        # Can't remove last team lead unless you're admin
        member_to_remove = next((m for m in members if m.get('email', '').lower() == member_email), None)
        if not member_to_remove:
            return json_response(404, error="Member not found in team")
        
        if member_to_remove.get('role') == TeamRole.LEAD:
            lead_count = sum(1 for m in members if m.get('role') == TeamRole.LEAD)
            if lead_count <= 1 and not is_admin:
                return json_response(400, error="Cannot remove the last team lead. Promote someone else first.")
        
        # Remove member
        members = [m for m in members if m.get('email', '').lower() != member_email]
        
        db = get_db()
        container = db.get_container('documents')
        
        team['members'] = members
        team['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container.upsert_item(team)
        
        logger.info(f"✅ Removed {member_email} from team {team.get('name')}")
        
        return json_response(200, data={
            'team_id': team_id,
            'member_removed': member_email,
            'total_members': len(members),
            'message': f"✅ {member_email} removed from team"
        })
        
    except Exception as e:
        logger.error(f"❌ Remove team member failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 6. UPDATE MEMBER ROLE
# =============================================================================

def handle_update_member_role(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /teams/{teamId}/members/{memberEmail}/role
    Update a member's role within the team.
    
    Body:
    {
        "role": "senior_reviewer"
    }
    """
    try:
        team_id = req.route_params.get('teamId')
        member_email = req.route_params.get('memberEmail', '').lower()
        
        if not team_id or not member_email:
            return json_response(400, error="Team ID and member email required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        # Check permission - team lead or admin
        members = team.get('members', [])
        is_team_lead = any(
            m.get('email', '').lower() == user_email.lower() and m.get('role') == TeamRole.LEAD
            for m in members
        )
        is_admin = any(r in user_roles for r in ['Organization.Admin', 'Platform.SuperAdmin'])
        
        if not is_team_lead and not is_admin:
            return json_response(403, error="Only team leads or admins can change roles")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        new_role = body.get('role')
        if not new_role:
            return json_response(400, error="Role required")
        
        # Find and update member
        member_found = False
        old_role = None
        
        for member in members:
            if member.get('email', '').lower() == member_email:
                old_role = member.get('role')
                member['role'] = new_role
                member['role_updated_at'] = datetime.utcnow().isoformat() + 'Z'
                member['role_updated_by'] = user_email
                member_found = True
                break
        
        if not member_found:
            return json_response(404, error="Member not found in team")
        
        # If demoting the last lead, prevent it
        if old_role == TeamRole.LEAD and new_role != TeamRole.LEAD:
            lead_count = sum(1 for m in members if m.get('role') == TeamRole.LEAD)
            if lead_count < 1:
                return json_response(400, error="Cannot demote the last team lead")
        
        db = get_db()
        container = db.get_container('documents')
        
        team['members'] = members
        team['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container.upsert_item(team)
        
        logger.info(f"✅ Updated {member_email} role from {old_role} to {new_role}")
        
        return json_response(200, data={
            'team_id': team_id,
            'member': member_email,
            'old_role': old_role,
            'new_role': new_role,
            'message': f"✅ {member_email} is now {new_role}"
        })
        
    except Exception as e:
        logger.error(f"❌ Update member role failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 7. ASSIGN DOCUMENT TO TEAM
# =============================================================================

def handle_assign_to_team(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/assign-team
    Assign a document to a team. Auto-distributes based on team strategy.
    
    Body:
    {
        "team_id": "team_abc123",
        "priority": "high",
        "notes": "Urgent review needed",
        "specific_member": "john@company.com"  // Optional: override auto-assignment
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
        
        team_id = body.get('team_id')
        if not team_id:
            return json_response(400, error="team_id required")
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        priority = body.get('priority', 'medium')
        notes = body.get('notes', '')
        specific_member = body.get('specific_member', '').lower().strip()
        
        # Check jurisdiction match if team has restrictions
        team_jurisdictions = team.get('jurisdictions', [])
        doc_jurisdiction = doc.get('jurisdiction', '')
        
        if team_jurisdictions and doc_jurisdiction not in team_jurisdictions:
            return json_response(400, error=f"Team only handles {team_jurisdictions}, document is {doc_jurisdiction}")
        
        # Determine assignee
        members = team.get('members', [])
        if not members:
            return json_response(400, error="Team has no members")
        
        assignee = None
        assignment_reason = ''
        
        if specific_member:
            # Manual override
            member = next((m for m in members if m.get('email', '').lower() == specific_member), None)
            if not member:
                return json_response(400, error=f"{specific_member} is not a member of this team")
            assignee = member
            assignment_reason = 'manual_override'
        else:
            # Auto-assign based on strategy
            strategy = team.get('assignment_strategy', AssignmentStrategy.LEAST_LOADED)
            risk_score = doc.get('risk_score', 0)
            
            assignee, assignment_reason = _select_assignee(
                members=members,
                strategy=strategy,
                risk_score=risk_score,
                org_id=org_id,
                team=team
            )
        
        if not assignee:
            return json_response(400, error="No available team members. All at capacity.")
        
        assignee_email = assignee.get('email')
        assignee_name = assignee.get('name', assignee_email.split('@')[0])
        
        now = datetime.utcnow()
        sla_hours = team.get('default_sla_hours', 48)
        
        # Adjust SLA for priority
        if priority == 'urgent':
            sla_hours = min(sla_hours, 4)
        elif priority == 'high':
            sla_hours = min(sla_hours, 24)
        
        deadline = now + timedelta(hours=sla_hours)
        ticket_id = doc.get('ticket_id') or f"TKT-{uuid.uuid4().hex[:8].upper()}"
        
        # Update document
        update_data = {
            'assigned_to': assignee_email,
            'assigned_to_name': assignee_name,
            'assigned_by': user_email,
            'assigned_at': now.isoformat() + 'Z',
            'assignment_status': 'pending',
            'assignment_priority': priority,
            'assignment_deadline': deadline.isoformat() + 'Z',
            'assignment_sla_hours': sla_hours,
            'ticket_id': ticket_id,
            'status': 'assigned',
            'team_id': team_id,
            'team_name': team.get('name'),
            'assignment_reason': assignment_reason,
            'assignment_notes': [{
                'id': str(uuid.uuid4()),
                'timestamp': now.isoformat() + 'Z',
                'author': user_email,
                'content': notes or f"Assigned to {team.get('name')} team",
                'type': 'team_assignment'
            }],
            'updated_at': now.isoformat() + 'Z'
        }
        
        update_document(doc_id, update_data, org_id)
        
        # Update team stats
        team['stats'] = team.get('stats', {})
        team['stats']['documents_assigned'] = team['stats'].get('documents_assigned', 0) + 1
        
        db = get_db()
        container = db.get_container('documents')
        container.upsert_item(team)
        
        # Notify assignee
        _create_team_notification(
            org_id=org_id,
            recipient_email=assignee_email,
            notification_type='team_assignment',
            title=f"New document assigned via {team.get('name')}",
            message=f"'{doc.get('filename')}' has been assigned to you. Priority: {priority}. Deadline: {deadline.strftime('%Y-%m-%d %H:%M')} UTC",
            document_id=doc_id,
            team_id=team_id,
            created_by=user_email
        )
        
        logger.info(f"✅ Document {doc_id} assigned to {assignee_email} via team {team.get('name')}")
        
        return json_response(200, data={
            'document_id': doc_id,
            'ticket_id': ticket_id,
            'team': {
                'id': team_id,
                'name': team.get('name')
            },
            'assignee': {
                'email': assignee_email,
                'name': assignee_name,
                'role': assignee.get('role')
            },
            'assignment_reason': assignment_reason,
            'priority': priority,
            'deadline': deadline.isoformat() + 'Z',
            'sla_hours': sla_hours,
            'message': f"✅ Assigned to {assignee_name} via {team.get('name')}"
        })
        
    except Exception as e:
        logger.error(f"❌ Team assignment failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def _select_assignee(members: List[Dict], strategy: str, risk_score: int, org_id: str, team: Dict) -> tuple:
    """Select the best assignee based on strategy"""
    db = get_db()
    container = db.get_container('documents')
    max_concurrent = team.get('max_concurrent_per_member', 10)
    
    # Get workload for each member
    member_workloads = []
    
    for member in members:
        email = member.get('email')
        role = member.get('role', TeamRole.REVIEWER)
        
        # Skip observers
        if role == TeamRole.OBSERVER:
            continue
        
        # Count active assignments
        query = """
        SELECT VALUE COUNT(1) FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.assigned_to = @email
        AND c.assignment_status IN ('pending', 'in_progress')
        """
        
        count_result = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@email", "value": email}
            ],
            partition_key=org_id
        ))
        
        active_count = count_result[0] if count_result else 0
        
        if active_count < max_concurrent:
            member_workloads.append({
                **member,
                'active_count': active_count,
                'available_capacity': max_concurrent - active_count
            })
    
    if not member_workloads:
        return None, 'no_capacity'
    
    # Apply strategy
    if strategy == AssignmentStrategy.ROUND_ROBIN:
        # Get last assigned member from team stats
        last_assigned = team.get('stats', {}).get('last_assigned_member', '')
        
        # Find next member after last assigned
        sorted_members = sorted(member_workloads, key=lambda x: x.get('email', ''))
        
        next_idx = 0
        for i, m in enumerate(sorted_members):
            if m.get('email', '').lower() == last_assigned.lower():
                next_idx = (i + 1) % len(sorted_members)
                break
        
        assignee = sorted_members[next_idx]
        
        # Update last assigned
        team['stats']['last_assigned_member'] = assignee.get('email')
        
        return assignee, 'round_robin'
    
    elif strategy == AssignmentStrategy.LEAST_LOADED:
        # Sort by workload (ascending)
        sorted_members = sorted(member_workloads, key=lambda x: x.get('active_count', 0))
        return sorted_members[0], 'least_loaded'
    
    elif strategy == AssignmentStrategy.RISK_BASED:
        # High risk (70+) → team lead or senior
        # Medium risk (40-69) → reviewer
        # Low risk (<40) → junior or reviewer
        
        if risk_score >= 70:
            # Prefer senior or lead
            seniors = [m for m in member_workloads if m.get('role') in [TeamRole.LEAD, TeamRole.SENIOR]]
            if seniors:
                return min(seniors, key=lambda x: x.get('active_count', 0)), 'risk_based_senior'
        
        elif risk_score < 40:
            # Prefer junior
            juniors = [m for m in member_workloads if m.get('role') == TeamRole.JUNIOR]
            if juniors:
                return min(juniors, key=lambda x: x.get('active_count', 0)), 'risk_based_junior'
        
        # Default to least loaded
        return min(member_workloads, key=lambda x: x.get('active_count', 0)), 'risk_based_default'
    
    else:  # MANUAL
        # Return least loaded as fallback
        return min(member_workloads, key=lambda x: x.get('active_count', 0)), 'manual_fallback'


# =============================================================================
# 8. TEAM DASHBOARD / METRICS
# =============================================================================

def handle_get_team_dashboard(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /teams/{teamId}/dashboard
    Get team performance dashboard with metrics.
    """
    try:
        team_id = req.route_params.get('teamId')
        if not team_id:
            return json_response(400, error="Team ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        team = _get_team(team_id, org_id)
        if not team:
            return json_response(404, error="Team not found")
        
        db = get_db()
        container = db.get_container('documents')
        
        # Get all documents assigned to this team
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.team_id = @team_id
        """
        
        team_docs = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@team_id", "value": team_id}
            ],
            partition_key=org_id
        ))
        
        # Calculate metrics
        now = datetime.utcnow()
        
        total_assigned = len(team_docs)
        pending = len([d for d in team_docs if d.get('assignment_status') == 'pending'])
        in_progress = len([d for d in team_docs if d.get('assignment_status') == 'in_progress'])
        completed = len([d for d in team_docs if d.get('status') in ['approved', 'rejected']])
        
        # SLA metrics
        sla_breached = 0
        sla_at_risk = 0
        on_time_completions = 0
        
        completion_times = []
        
        for doc in team_docs:
            deadline_str = doc.get('assignment_deadline', '')
            assigned_at_str = doc.get('assigned_at', '')
            completed_at_str = doc.get('approved_at') or doc.get('rejected_at')
            
            if deadline_str:
                try:
                    deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                    
                    if doc.get('status') in ['approved', 'rejected']:
                        # Completed
                        if completed_at_str:
                            completed_at = datetime.fromisoformat(completed_at_str.replace('Z', '+00:00'))
                            if completed_at <= deadline:
                                on_time_completions += 1
                            
                            # Calculate completion time
                            if assigned_at_str:
                                assigned_at = datetime.fromisoformat(assigned_at_str.replace('Z', '+00:00'))
                                hours = (completed_at - assigned_at).total_seconds() / 3600
                                completion_times.append(hours)
                    else:
                        # Still active
                        if now.replace(tzinfo=deadline.tzinfo) > deadline:
                            sla_breached += 1
                        elif (deadline - now.replace(tzinfo=deadline.tzinfo)).total_seconds() < 4 * 3600:
                            sla_at_risk += 1
                except:
                    pass
        
        # Member performance
        member_stats = []
        for member in team.get('members', []):
            email = member.get('email')
            member_docs = [d for d in team_docs if d.get('assigned_to', '').lower() == email.lower()]
            
            member_completed = len([d for d in member_docs if d.get('status') in ['approved', 'rejected']])
            member_active = len([d for d in member_docs if d.get('assignment_status') in ['pending', 'in_progress']])
            
            member_stats.append({
                'email': email,
                'name': member.get('name'),
                'role': member.get('role'),
                'total_assigned': len(member_docs),
                'completed': member_completed,
                'active': member_active,
                'completion_rate': round(member_completed / len(member_docs) * 100) if member_docs else 0
            })
        
        # Sort by completion rate descending
        member_stats.sort(key=lambda x: x.get('completion_rate', 0), reverse=True)
        
        avg_completion_hours = round(sum(completion_times) / len(completion_times), 1) if completion_times else 0
        sla_compliance_rate = round(on_time_completions / completed * 100) if completed > 0 else 100
        
        return json_response(200, data={
            'team_id': team_id,
            'team_name': team.get('name'),
            'summary': {
                'total_assigned': total_assigned,
                'pending': pending,
                'in_progress': in_progress,
                'completed': completed,
                'completion_rate': round(completed / total_assigned * 100) if total_assigned > 0 else 0
            },
            'sla': {
                'breached': sla_breached,
                'at_risk': sla_at_risk,
                'on_time_completions': on_time_completions,
                'compliance_rate': sla_compliance_rate
            },
            'performance': {
                'avg_completion_hours': avg_completion_hours,
                'target_sla_hours': team.get('default_sla_hours', 48)
            },
            'member_performance': member_stats,
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ Team dashboard failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# 9. GET MY TEAMS' QUEUE
# =============================================================================

def handle_get_my_teams_queue(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /teams/my-queue
    Get documents assigned to any of my teams that I can work on.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get all teams user belongs to
        my_teams = _get_user_teams(user_email, org_id)
        
        if not my_teams:
            return json_response(200, data={
                'queue': [],
                'total': 0,
                'message': "You're not a member of any teams"
            })
        
        team_ids = [t.get('id') for t in my_teams]
        
        db = get_db()
        container = db.get_container('documents')
        
        # Get documents assigned to user OR unassigned in their teams
        query = """
        SELECT c.id, c.filename, c.assigned_to, c.assigned_by, c.assigned_at,
               c.assignment_status, c.assignment_priority, c.assignment_deadline,
               c.ticket_id, c.risk_score, c.violations_count, c.jurisdiction,
               c.team_id, c.team_name, c.status
        FROM c
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.status IN ('assigned', 'pending_review', 'scanned')
        AND (c.assigned_to = @email OR (NOT IS_DEFINED(c.assigned_to) OR c.assigned_to = null))
        ORDER BY c.assignment_priority ASC, c.created_at ASC
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@email", "value": user_email}
            ],
            partition_key=org_id
        ))
        
        # Filter to only docs in user's teams
        queue = []
        now = datetime.utcnow()
        
        for doc in docs:
            doc_team_id = doc.get('team_id')
            
            # If assigned to user, include regardless of team
            is_mine = doc.get('assigned_to', '').lower() == user_email.lower()
            is_my_team = doc_team_id in team_ids
            
            if not is_mine and not is_my_team:
                continue
            
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
            
            queue.append({
                'document_id': doc.get('id'),
                'ticket_id': doc.get('ticket_id', ''),
                'filename': doc.get('filename'),
                'team': {
                    'id': doc_team_id,
                    'name': doc.get('team_name', 'Direct Assignment')
                },
                'assigned_to': doc.get('assigned_to'),
                'is_mine': is_mine,
                'is_unassigned': not doc.get('assigned_to'),
                'status': doc.get('assignment_status', 'pending'),
                'priority': doc.get('assignment_priority', 'medium'),
                'deadline': deadline,
                'sla_status': sla_status,
                'hours_remaining': round(hours_remaining, 1) if hours_remaining else None,
                'risk_score': doc.get('risk_score', 0),
                'violations_count': doc.get('violations_count', 0),
                'jurisdiction': doc.get('jurisdiction')
            })
        
        # Sort: my items first, then by priority and deadline
        priority_order = {'urgent': 0, 'high': 1, 'medium': 2, 'low': 3}
        queue.sort(key=lambda x: (
            not x['is_mine'],
            priority_order.get(x['priority'], 2),
            x.get('deadline', '9999')
        ))
        
        return json_response(200, data={
            'queue': queue,
            'total': len(queue),
            'my_items': len([q for q in queue if q['is_mine']]),
            'unassigned': len([q for q in queue if q['is_unassigned']]),
            'my_teams': [{'id': t.get('id'), 'name': t.get('name')} for t in my_teams],
            'workspace': _get_workspace_context(org_id)
        })
        
    except Exception as e:
        logger.error(f"❌ My teams queue failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# HELPER: Team notifications
# =============================================================================

def _create_team_notification(
    org_id: str,
    recipient_email: str,
    notification_type: str,
    title: str,
    message: str,
    team_id: str = None,
    document_id: str = None,
    created_by: str = None
):
    """Create a team-related notification"""
    try:
        db = get_db()
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
            'team_id': team_id,
            'document_id': document_id,
            'created_by': created_by,
            'created_at': now.isoformat() + 'Z',
            'read': False
        }
        
        container.create_item(notification)
        
    except Exception as e:
        logger.warning(f"⚠️ Team notification failed: {e}")