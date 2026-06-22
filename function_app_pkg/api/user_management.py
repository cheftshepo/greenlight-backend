"""
COMPLETE USER MANAGEMENT API - Consolidated Edition
===================================================
Handles both admin user management AND user profile endpoints in one file.

File: function_app_pkg/api/user_management.py
"""

import azure.functions as func
import logging
import hmac
import hashlib
import base64
import json as _json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter

from ..core.database import (
    get_db,
    create_user,
    get_user_by_email,
    get_users_by_org,
    update_user,
    get_organization,
    log_action,
    UserRole,
    get_user_activity,
    get_decisions_by_user,
    get_org_analytics_summary
)
from ..shared.http_utils import json_response
from ..shared.validators import validate_email, validate_phone

logger = logging.getLogger(__name__)


# =============================================================================
# HELPER: Safely get attributes from user (dict or object)
# =============================================================================

def _get_user_attr(user, attr: str, default=None):
    """Safely extract attribute from user (handles both dict and AuthenticatedUser object)"""
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


# =============================================================================
# USER PROFILE ENDPOINTS (Self)
# =============================================================================

def handle_get_profile(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /users/profile
    Get current user's own profile
    """
    try:
        user_email = _get_user_attr(user, 'email')
        
        if not user_email:
            return json_response(401, error="Authentication required")
        
        # Get user from database
        db_user = get_user_by_email(user_email)
        if not db_user:
            return json_response(404, error="User profile not found")
        
        # Get organization
        org_id = db_user.get('organization_id')
        org = get_organization(org_id) if org_id else None
        
        # Get activity stats
        from ..core.database import get_container
        container = get_container('documents')
        
        # Count documents uploaded
        doc_query = "SELECT VALUE COUNT(1) FROM c WHERE c.uploaded_by = @user_email AND c.type = 'document'"
        doc_count_result = list(container.query_items(
            query=doc_query,
            parameters=[{"name": "@user_email", "value": user_email}],
            enable_cross_partition_query=True
        ))
        documents_uploaded = doc_count_result[0] if doc_count_result else 0
        
        # Count approved documents
        approved_query = """
        SELECT VALUE COUNT(1) FROM c 
        WHERE c.uploaded_by = @user_email 
        AND c.type = 'document' 
        AND c.workflow_status = 'approved'
        """
        approved_result = list(container.query_items(
            query=approved_query,
            parameters=[{"name": "@user_email", "value": user_email}],
            enable_cross_partition_query=True
        ))
        documents_approved = approved_result[0] if approved_result else 0
        
        # Build response
        response = {
            'user_id': db_user.get('id'),
            'email': db_user.get('email'),
            'name': db_user.get('name'),
            'roles': db_user.get('roles', []),
            'organization_id': db_user.get('organization_id'),
            'organization_name': org.get('name', 'Unknown Organization') if org else 'Unknown Organization',
            'subscription_tier': org.get('subscription_tier', 'trial') if org else 'trial',
            'department': db_user.get('department', ''),
            'job_title': db_user.get('job_title', ''),
            'phone': db_user.get('phone', ''),
            'preferred_jurisdiction': db_user.get('preferred_jurisdiction', 'UK'),
            'notification_settings': db_user.get('notification_settings', {
                'email_on_assignment': True,
                'email_on_approval': True,
                'email_on_rejection': True,
                'email_daily_summary': False,
            }),
            'is_active': db_user.get('is_active', True),
            'last_login': db_user.get('last_login', ''),
            'login_count': db_user.get('login_count', 0),
            'created_at': db_user.get('created_at'),
            
            # Activity stats
            'documents_uploaded': documents_uploaded,
            'documents_approved': documents_approved,
        }
        
        logger.info(f"✅ Profile retrieved for: {user_email}")
        return json_response(200, data=response)
        
    except Exception as e:
        logger.error(f"❌ Get profile failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get profile: {str(e)}")


def handle_update_profile(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /users/profile
    Update current user's own profile
    
    Body:
    {
        "name": "Updated Name",
        "department": "Marketing",
        "job_title": "Marketing Manager",
        "phone": "+1234567890",
        "preferred_jurisdiction": "UK",
        "notification_settings": {
            "email_on_assignment": true,
            "email_on_approval": true,
            "email_on_rejection": true,
            "email_daily_summary": false
        }
    }
    """
    try:
        user_email = _get_user_attr(user, 'email')
        
        if not user_email:
            return json_response(401, error="Authentication required")
        
        # Parse body
        try:
            body = req.get_json()
        except ValueError:
            return json_response(400, error="Invalid JSON body")
        
        # Get user from database
        db_user = get_user_by_email(user_email)
        if not db_user:
            return json_response(404, error="User not found")
        
        # Build updates (only allow certain fields for self-update)
        updates = {}
        
        if 'name' in body:
            name = body['name'].strip()
            if name:
                updates['name'] = name
        
        if 'department' in body:
            updates['department'] = body['department']
        
        if 'job_title' in body:
            updates['job_title'] = body['job_title']
        
        if 'phone' in body:
            phone = body['phone'].strip()
            if phone:
                if not validate_phone(phone):
                    return json_response(400, error="Invalid phone number")
                updates['phone'] = phone
        
        if 'preferred_jurisdiction' in body:
            jurisdiction = body['preferred_jurisdiction'].strip().upper()
            if jurisdiction in ['UK', 'AU', 'US']:
                updates['preferred_jurisdiction'] = jurisdiction
        
        if 'notification_settings' in body:
            settings = body['notification_settings']
            if isinstance(settings, dict):
                # Merge with existing settings
                current_settings = db_user.get('notification_settings', {})
                current_settings.update(settings)
                updates['notification_settings'] = current_settings
        
        if not updates:
            return json_response(400, error="No valid fields to update")
        
        # Update user
        updated_user = update_user(
            db_user['id'],
            updates,
            db_user.get('organization_id')
        )
        
        if not updated_user:
            return json_response(500, error="Failed to update profile")
        
        # Audit log
        log_action(
            org_id=db_user.get('organization_id'),
            user_id=user_email,
            user_email=user_email,
            user_roles=_get_user_attr(user, 'roles', []),
            action='profile.updated',
            resource_type='user',
            resource_id=db_user['id'],
            resource_name=user_email,
            details={'updated_fields': list(updates.keys())}
        )
        
        logger.info(f"✅ Profile updated for: {user_email}")
        
        return json_response(200, data={
            'user_id': db_user['id'],
            'email': user_email,
            'updated_fields': list(updates.keys()),
            'message': '✅ Profile updated successfully'
        })
        
    except Exception as e:
        logger.error(f"❌ Update profile failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to update profile: {str(e)}")


# =============================================================================
# ADMIN USER MANAGEMENT ENDPOINTS
# =============================================================================

def handle_list_users(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /user-management/all
    List all users in organization with filtering and pagination.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Parse query params
        limit = min(int(req.params.get('limit', 100)), 500)
        offset = int(req.params.get('offset', 0))
        role_filter = req.params.get('role')
        status_filter = req.params.get('status')
        search_query = req.params.get('search', '').lower()
        department_filter = req.params.get('department')
        
        # Get all users
        all_users = get_users_by_org(org_id, limit=500)
        
        # Apply filters
        filtered_users = all_users
        
        if role_filter:
            filtered_users = [u for u in filtered_users if role_filter in u.get('roles', [])]
        
        if status_filter:
            is_active = status_filter.lower() == 'active'
            filtered_users = [u for u in filtered_users if u.get('is_active', True) == is_active]
        
        if department_filter:
            filtered_users = [u for u in filtered_users if u.get('department', '') == department_filter]
        
        if search_query:
            filtered_users = [
                u for u in filtered_users 
                if search_query in u.get('name', '').lower() 
                or search_query in u.get('email', '').lower()
            ]
        
        # Pagination
        total = len(filtered_users)
        paginated = filtered_users[offset:offset + limit]
        
        # Build response
        users_summary = []
        for u in paginated:
            users_summary.append({
                'user_id': u.get('id'),
                'email': u.get('email'),
                'name': u.get('name'),
                'roles': u.get('roles', []),
                'department': u.get('department', ''),
                'job_title': u.get('job_title', ''),
                'is_active': u.get('is_active', True),
                'last_login': u.get('last_login', ''),
                'login_count': u.get('login_count', 0),
                'created_at': u.get('created_at'),
            })
        
        return json_response(200, data={
            'users': users_summary,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + limit < total
        })
        
    except Exception as e:
        logger.error(f"❌ List users failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to list users: {str(e)}")


def handle_get_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /user-management/{userId}
    Get detailed user information including activity stats.
    """
    try:
        user_id = req.route_params.get('userId')
        if not user_id:
            return json_response(400, error="User ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get all users and find the requested one
        all_users = get_users_by_org(org_id)
        target_user = next((u for u in all_users if u.get('id') == user_id), None)
        
        if not target_user:
            return json_response(404, error="User not found")
        
        # Get user activity stats
        db = get_db()
        
        # Count documents uploaded by this user
        doc_query = "SELECT VALUE COUNT(1) FROM c WHERE c.uploaded_by = @user_id AND c.type = 'document'"
        doc_count_result = list(db.get_container('documents').query_items(
            query=doc_query,
            parameters=[{"name": "@user_id", "value": user_id}],
            partition_key=org_id
        ))
        documents_uploaded = doc_count_result[0] if doc_count_result else 0
        
        # Count audit logs (activity)
        audit_query = "SELECT VALUE COUNT(1) FROM c WHERE c.user_id = @user_id"
        audit_count_result = list(db.get_container('audit_logs').query_items(
            query=audit_query,
            parameters=[{"name": "@user_id", "value": user_id}],
            enable_cross_partition_query=True
        ))
        total_actions = audit_count_result[0] if audit_count_result else 0
        
        # Get permissions for roles
        def _get_permissions_for_roles(roles: List[str]) -> List[str]:
            """Get all permissions for given roles"""
            from ..core.database import ROLE_PERMISSIONS, UserRole
            
            permissions = set()
            for role_str in roles:
                try:
                    role = UserRole(role_str)
                    permissions.update(ROLE_PERMISSIONS.get(role, []))
                except ValueError:
                    continue
            
            return [p.value for p in permissions]
        
        # Build detailed response
        response_data = {
            'user_id': target_user.get('id'),
            'email': target_user.get('email'),
            'name': target_user.get('name'),
            'roles': target_user.get('roles', []),
            'permissions': _get_permissions_for_roles(target_user.get('roles', [])),
            'organization_id': target_user.get('organization_id'),
            'department': target_user.get('department', ''),
            'job_title': target_user.get('job_title', ''),
            'phone': target_user.get('phone', ''),
            'is_active': target_user.get('is_active', True),
            'last_login': target_user.get('last_login', ''),
            'login_count': target_user.get('login_count', 0),
            'created_at': target_user.get('created_at'),
            'preferred_jurisdiction': target_user.get('preferred_jurisdiction', 'UK'),
            'notification_settings': target_user.get('notification_settings', {}),
            
            # Activity stats
            'activity_stats': {
                'documents_uploaded': documents_uploaded,
                'total_actions': total_actions,
                'documents_reviewed': 0,
                'approvals_given': 0,
            }
        }
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error(f"❌ Get user failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get user: {str(e)}")


def handle_create_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /user-management
    Create/invite a new user.
    """
    try:
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        # Validate required fields
        email = body.get('email', '').strip().lower()
        name = body.get('name', '').strip()
        
        if not email or not name:
            return json_response(400, error="Email and name are required")
        
        if not validate_email(email):
            return json_response(400, error="Invalid email address")
        
        # Check if user already exists
        existing = get_user_by_email(email)
        if existing:
            return json_response(409, error="User with this email already exists")
        
        # Get organization
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        org = get_organization(org_id)
        
        if not org:
            return json_response(500, error="Organization not found")
        
        # Check user limit for subscription tier
        from function_app_pkg.core.usage_service import check_user_limit

        user_allowed, user_reason = check_user_limit(org_id)
        if not user_allowed:
            return json_response(402, data={
                'error': user_reason,
                'upgrade_url': '/settings/billing',
                'limit_reached': True,
            })
            
        # Parse roles
        roles = body.get('roles', ['Marketing.User'])
        if not isinstance(roles, list):
            roles = [roles]
        
        # Validate roles
        valid_roles = [r.value for r in UserRole]
        for role in roles:
            if role not in valid_roles:
                return json_response(400, error=f"Invalid role: {role}. Valid roles: {valid_roles}")
        
        # Validate phone if provided
        phone = body.get('phone', '').strip()
        if phone and not validate_phone(phone):
            return json_response(400, error="Invalid phone number")
        
        # Create user
        new_user = create_user({
            'email': email,
            'name': name,
            'organization_id': org_id,
            'roles': roles,
            'department': body.get('department', ''),
            'job_title': body.get('job_title', ''),
            'phone': phone,
            'is_active': True,
            'created_at': datetime.utcnow().isoformat() + 'Z',
        })
        
        # Audit log
        admin_email = _get_user_attr(user, 'email', 'system')
        admin_roles = _get_user_attr(user, 'roles', [])
        
        log_action(
            org_id=org_id,
            user_id=admin_email,
            user_email=admin_email,
            user_roles=admin_roles,
            action='user.created',
            resource_type='user',
            resource_id=new_user['id'],
            resource_name=email,
            details={
                'invited_email': email,
                'assigned_roles': roles,
                'department': body.get('department', '')
            }
        )
        
        # Send invitation email with a signed token
        try:
            _send_invite_email(
                to_email=email,
                to_name=name,
                org_name=org.get('name', 'your organisation'),
                invited_by=_get_user_attr(user, 'name') or _get_user_attr(user, 'email'),
                user_id=new_user['id'],
                org_id=org_id,
            )
        except Exception as email_err:
            logger.warning(f"⚠️ Invite email failed (user still created): {email_err}")

        return json_response(201, data={
            'user_id': new_user['id'],
            'email': email,
            'name': name,
            'roles': roles,
            'message': '✅ User created successfully',
            'next_steps': [
                'Invitation email sent',
                'User will receive login instructions',
                'User can access the platform immediately'
            ]
        })
        
    except Exception as e:
        logger.error(f"❌ Create user failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to create user: {str(e)}")


def handle_update_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /user-management/{userId}
    Update user information.
    """
    try:
        user_id = req.route_params.get('userId')
        if not user_id:
            return json_response(400, error="User ID required")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get target user
        all_users = get_users_by_org(org_id)
        target_user = next((u for u in all_users if u.get('id') == user_id), None)
        
        if not target_user:
            return json_response(404, error="User not found")
        
        # Build updates
        updates = {}
        
        if 'name' in body:
            updates['name'] = body['name'].strip()
        
        if 'roles' in body:
            roles = body['roles']
            if not isinstance(roles, list):
                roles = [roles]
            
            valid_roles = [r.value for r in UserRole]
            for role in roles:
                if role not in valid_roles:
                    return json_response(400, error=f"Invalid role: {role}")
            
            updates['roles'] = roles
        
        if 'department' in body:
            updates['department'] = body['department']
        
        if 'job_title' in body:
            updates['job_title'] = body['job_title']
        
        if 'phone' in body:
            phone = body['phone'].strip()
            if phone and not validate_phone(phone):
                return json_response(400, error="Invalid phone number")
            updates['phone'] = phone
        
        if 'is_active' in body:
            updates['is_active'] = bool(body['is_active'])
        
        if 'notification_settings' in body:
            updates['notification_settings'] = body['notification_settings']
        
        if not updates:
            return json_response(400, error="No valid fields to update")
        
        # Update user
        updated_user = update_user(user_id, updates, org_id)
        
        if not updated_user:
            return json_response(500, error="Failed to update user")
        
        # Audit log
        admin_email = _get_user_attr(user, 'email', 'system')
        admin_roles = _get_user_attr(user, 'roles', [])
        
        log_action(
            org_id=org_id,
            user_id=admin_email,
            user_email=admin_email,
            user_roles=admin_roles,
            action='user.updated',
            resource_type='user',
            resource_id=user_id,
            resource_name=target_user.get('email'),
            details={'updates': updates}
        )
        
        return json_response(200, data={
            'user_id': user_id,
            'message': '✅ User updated successfully',
            'updated_fields': list(updates.keys())
        })
        
    except Exception as e:
        logger.error(f"❌ Update user failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to update user: {str(e)}")


def handle_delete_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /user-management/{userId}
    Deactivate a user (soft delete).
    """
    try:
        user_id = req.route_params.get('userId')
        if not user_id:
            return json_response(400, error="User ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get target user
        all_users = get_users_by_org(org_id)
        target_user = next((u for u in all_users if u.get('id') == user_id), None)
        
        if not target_user:
            return json_response(404, error="User not found")
        
        # Check if user is trying to delete themselves
        current_user_id = _get_user_attr(user, 'user_id') or _get_user_attr(user, 'id')
        if user_id == current_user_id:
            return json_response(400, error="Cannot delete your own account")
        
        # Soft delete (deactivate)
        updated_user = update_user(user_id, {'is_active': False}, org_id)
        
        if not updated_user:
            return json_response(500, error="Failed to deactivate user")
        
        # Audit log
        admin_email = _get_user_attr(user, 'email', 'system')
        admin_roles = _get_user_attr(user, 'roles', [])
        
        log_action(
            org_id=org_id,
            user_id=admin_email,
            user_email=admin_email,
            user_roles=admin_roles,
            action='user.deleted',
            resource_type='user',
            resource_id=user_id,
            resource_name=target_user.get('email'),
            details={'deactivated': True}
        )
        
        return json_response(200, data={
            'user_id': user_id,
            'message': '✅ User deactivated successfully',
            'note': 'User can be reactivated by updating is_active to true'
        })
        
    except Exception as e:
        logger.error(f"❌ Delete user failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to delete user: {str(e)}")

def handle_get_workload_dashboard(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /manage/users/workload
    Get comprehensive workload view for all users in organization
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        
        # Get all users in org
        users = get_users_by_org(org_id)
        
        # Get all assigned documents
        doc_query = """
        SELECT c.id, c.assigned_to, c.assignment_status, c.assignment_priority,
               c.assigned_at, c.assignment_deadline, c.completed_at, c.risk_score,
               c.violations_count
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.assigned_to != null
        """
        
        docs = list(db.get_container('documents').query_items(
            query=doc_query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        # Aggregate by user
        now = datetime.utcnow()
        user_workloads = []
        total_pending = 0
        total_in_progress = 0
        total_overdue = 0
        
        for u in users:
            if not u.get('is_active', True):
                continue
            
            email = u.get('email')
            user_docs = [d for d in docs if d.get('assigned_to') == email]
            
            # Calculate metrics
            pending = sum(1 for d in user_docs if d.get('assignment_status') == 'pending')
            in_progress = sum(1 for d in user_docs if d.get('assignment_status') == 'in_progress')
            completed_total = sum(1 for d in user_docs if d.get('assignment_status') == 'completed')
            
            # Overdue count
            overdue = 0
            for d in user_docs:
                if d.get('assignment_status') in ['pending', 'in_progress']:
                    deadline_str = d.get('assignment_deadline')
                    if deadline_str:
                        try:
                            deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                            if deadline < now.replace(tzinfo=deadline.tzinfo):
                                overdue += 1
                        except:
                            pass
            
            # Completion time stats (last 30 days)
            recent_completed = [
                d for d in user_docs 
                if d.get('assignment_status') == 'completed' 
                and d.get('completed_at')
            ]
            
            completion_times = []
            for d in recent_completed:
                try:
                    assigned = datetime.fromisoformat(d['assigned_at'].replace('Z', '+00:00'))
                    completed = datetime.fromisoformat(d['completed_at'].replace('Z', '+00:00'))
                    hours = (completed - assigned).total_seconds() / 3600
                    completion_times.append(hours)
                except:
                    pass
            
            avg_completion_hours = sum(completion_times) / len(completion_times) if completion_times else 0
            
            # SLA compliance (completed on time vs late)
            on_time = 0
            late = 0
            for d in recent_completed:
                try:
                    completed = datetime.fromisoformat(d['completed_at'].replace('Z', '+00:00'))
                    deadline = datetime.fromisoformat(d['assignment_deadline'].replace('Z', '+00:00'))
                    if completed <= deadline:
                        on_time += 1
                    else:
                        late += 1
                except:
                    pass
            
            sla_compliance_rate = on_time / (on_time + late) if (on_time + late) > 0 else 1.0
            
            # Current documents with details
            current_docs = []
            for d in user_docs:
                if d.get('assignment_status') in ['pending', 'in_progress']:
                    deadline_str = d.get('assignment_deadline', '')
                    is_overdue = False
                    time_remaining_hours = 0
                    
                    if deadline_str:
                        try:
                            deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                            is_overdue = deadline < now.replace(tzinfo=deadline.tzinfo)
                            time_remaining_hours = (deadline - now.replace(tzinfo=deadline.tzinfo)).total_seconds() / 3600
                        except:
                            pass
                    
                    current_docs.append({
                        'assignment_id': d.get('id'),
                        'document_id': d.get('id'),
                        'ticket_id': d.get('ticket_id', ''),
                        'priority': d.get('assignment_priority', 'medium'),
                        'status': d.get('assignment_status', 'pending'),
                        'assigned_at': d.get('assigned_at'),
                        'deadline': deadline_str,
                        'sla_status': 'breached' if is_overdue else ('at_risk' if time_remaining_hours < 4 else 'on_track'),
                        'time_remaining_hours': round(time_remaining_hours, 1),
                        'risk_score': d.get('risk_score', 0),
                        'violations_count': d.get('violations_count', 0)
                    })
            
            # Capacity score (0-1, higher = busier)
            total_current = pending + in_progress
            capacity_score = min(total_current / 8, 1.0)  # 8 = recommended max
            
            user_workloads.append({
                'user_id': u.get('id'),
                'email': email,
                'name': u.get('name'),
                'roles': u.get('roles', []),
                'status': 'active' if u.get('is_active', True) else 'inactive',
                'last_active': u.get('last_login', ''),
                
                # Workload metrics
                'workload': {
                    'current_assignments': total_current,
                    'pending': pending,
                    'in_progress': in_progress,
                    'overdue': overdue,
                    'capacity_score': round(capacity_score, 2),
                    'recommended_max': 8
                },
                
                # Performance metrics
                'performance': {
                    'completed_this_month': len(recent_completed),
                    'avg_completion_hours': round(avg_completion_hours, 1),
                    'sla_compliance_rate': round(sla_compliance_rate, 2),
                    'on_time_completions': on_time,
                    'late_completions': late
                },
                
                # Current work
                'current_documents': current_docs[:5]  # Top 5 by priority/deadline
            })
            
            # Update totals
            total_pending += pending
            total_in_progress += in_progress
            total_overdue += overdue
        
        # Sort by workload (busiest first)
        user_workloads.sort(key=lambda x: x['workload']['current_assignments'], reverse=True)
        
        # Identify bottlenecks and idle users
        bottleneck_users = [
            u['email'] for u in user_workloads 
            if u['workload']['capacity_score'] >= 0.9
        ]
        idle_users = [
            u['email'] for u in user_workloads 
            if u['workload']['current_assignments'] == 0
        ]
        
        return json_response(200, data={
            'users': user_workloads,
            'org_summary': {
                'total_users': len(users),
                'active_users': len([u for u in users if u.get('is_active', True)]),
                'total_pending_assignments': total_pending,
                'total_in_progress': total_in_progress,
                'total_overdue': total_overdue,
                'bottleneck_users': bottleneck_users,
                'idle_users': idle_users
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Workload dashboard failed: {e}", exc_info=True)
        return json_response(500, error=str(e))

def handle_invite_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /manage/users/invite
    Invite user to organization (alias for create_user)
    """
    return handle_create_user(req, user)


def handle_update_user_role(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    PUT /manage/users/{userId}/role
    Update user role (alias for update_user but only roles)
    """
    try:
        user_id = req.route_params.get('userId')
        if not user_id:
            return json_response(400, error="User ID required")
        
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Only allow updating roles via this endpoint
        if 'roles' not in body:
            return json_response(400, error="Roles field required")
        
        roles = body['roles']
        if not isinstance(roles, list):
            roles = [roles]
        
        # Validate roles
        valid_roles = [r.value for r in UserRole]
        for role in roles:
            if role not in valid_roles:
                return json_response(400, error=f"Invalid role: {role}")
        
        # Update only roles
        updated_user = update_user(user_id, {'roles': roles}, org_id)
        
        if not updated_user:
            return json_response(500, error="Failed to update user role")
        
        return json_response(200, data={
            'user_id': user_id,
            'roles': roles,
            'message': '✅ User roles updated successfully'
        })
        
    except Exception as e:
        logger.error(f"❌ Update user role failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to update user role: {str(e)}")


def handle_deactivate_user(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    DELETE /manage/users/{userId}
    Deactivate user (alias for delete_user)
    """
    return handle_delete_user(req, user)


def handle_bulk_invite(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /user-management/bulk-invite
    Invite multiple users at once.
    """
    try:
        # Parse body
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        users_to_create = body.get('users', [])
        
        if not users_to_create:
            return json_response(400, error="No users provided")
        
        if len(users_to_create) > 50:
            return json_response(400, error="Maximum 50 users per bulk invite")
        
        org_id = _get_user_attr(user, 'organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        results = {
            'created': [],
            'failed': [],
            'skipped': []
        }
        
        for user_data in users_to_create:
            email = user_data.get('email', '').strip().lower()
            name = user_data.get('name', '').strip()
            
            if not email or not name:
                results['failed'].append({
                    'email': email,
                    'reason': 'Missing email or name'
                })
                continue
            
            # Check if exists
            existing = get_user_by_email(email)
            if existing:
                results['skipped'].append({
                    'email': email,
                    'reason': 'User already exists'
                })
                continue
            
            # Create user
            try:
                new_user = create_user({
                    'email': email,
                    'name': name,
                    'organization_id': org_id,
                    'roles': user_data.get('roles', ['Marketing.User']),
                    'department': user_data.get('department', ''),
                    'job_title': user_data.get('job_title', ''),
                    'phone': user_data.get('phone', ''),
                    'is_active': True,
                })
                
                results['created'].append({
                    'email': email,
                    'user_id': new_user['id']
                })
                
            except Exception as e:
                results['failed'].append({
                    'email': email,
                    'reason': str(e)
                })
        
        logger.info(f"✅ Bulk invite: {len(results['created'])} created, {len(results['failed'])} failed, {len(results['skipped'])} skipped")
        
        return json_response(200, data={
            'summary': {
                'total': len(users_to_create),
                'created': len(results['created']),
                'failed': len(results['failed']),
                'skipped': len(results['skipped'])
            },
            'results': results
        })
        
    except Exception as e:
        logger.error(f"❌ Bulk invite failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to bulk invite: {str(e)}")


# =============================================================================
# ADMIN: VIEW USER ACTIVITY & DECISIONS
# =============================================================================

def handle_get_user_activity(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /manage/users/{userId}/activity?days=30
    Admin view of what a specific user has been doing
    """
    try:
        target_user_id = req.route_params.get('userId')
        if not target_user_id:
            return json_response(400, error="User ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        days = int(req.params.get('days', '30'))
        
        # Get target user
        all_users = get_users_by_org(org_id)
        target_user = next((u for u in all_users if u.get('id') == target_user_id), None)
        
        if not target_user:
            return json_response(404, error="User not found")
        
        target_email = target_user.get('email')
        
        # Get activity from database
        activity = get_user_activity(target_email, org_id, days)
        decisions = get_decisions_by_user(target_email, org_id, days)
        
        # Summarize activity
        activity_summary = {
            'documents_uploaded': 0,
            'documents_scanned': 0,
            'documents_approved': 0,
            'documents_rejected': 0,
            'ai_chats': 0,
            'assignments_completed': 0
        }
        
        for act in activity:
            action = act.get('action', '')
            if 'upload' in action:
                activity_summary['documents_uploaded'] += 1
            elif 'scan' in action:
                activity_summary['documents_scanned'] += 1
            elif 'approved' in action:
                activity_summary['documents_approved'] += 1
            elif 'rejected' in action:
                activity_summary['documents_rejected'] += 1
            elif 'chat' in action:
                activity_summary['ai_chats'] += 1
        
        return json_response(200, data={
            'user': {
                'id': target_user_id,
                'email': target_email,
                'name': target_user.get('name'),
                'roles': target_user.get('roles', [])
            },
            'period_days': days,
            'activity_summary': activity_summary,
            'decisions': [{
                'decision_id': d.get('id'),
                'document_id': d.get('document_id'),
                'document_filename': d.get('document_filename'),
                'decision': d.get('decision'),
                'reasoning': d.get('decision_context', {}).get('reasoning', ''),
                'timestamp': d.get('timestamp'),
                'document_risk_score': d.get('document_state_at_decision', {}).get('risk_score', 0),
                'used_ai_help': d.get('ai_context', {}).get('ai_helped_decision', False)
            } for d in decisions],
            'recent_activity': activity[:50]  # Last 50 actions
        })
        
    except Exception as e:
        logger.error(f"❌ Get user activity failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_get_user_decisions(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /manage/users/{userId}/decisions?days=30
    View all approval/rejection decisions made by a user with full context
    """
    try:
        target_user_id = req.route_params.get('userId')
        if not target_user_id:
            return json_response(400, error="User ID required")
        
        org_id = _get_user_attr(user, 'organization_id')
        days = int(req.params.get('days', '30'))
        
        # Get target user
        all_users = get_users_by_org(org_id)
        target_user = next((u for u in all_users if u.get('id') == target_user_id), None)
        
        if not target_user:
            return json_response(404, error="User not found")
        
        target_email = target_user.get('email')
        
        decisions = get_decisions_by_user(target_email, org_id, days)
        
        # Calculate decision stats
        total = len(decisions)
        approved = sum(1 for d in decisions if d.get('decision') == 'approved')
        rejected = sum(1 for d in decisions if d.get('decision') == 'rejected')
        
        # Accuracy analysis
        true_positives = sum(1 for d in decisions if d.get('decision') == 'rejected' and d.get('document_state_at_decision', {}).get('violations_count', 0) > 0)
        false_positives_overridden = sum(1 for d in decisions if d.get('decision') == 'approved' and d.get('document_state_at_decision', {}).get('violations_count', 0) > 0)
        
        # Average time to decision
        decision_times = [d.get('time_to_decision_hours', 0) for d in decisions if d.get('time_to_decision_hours')]
        avg_decision_time = sum(decision_times) / len(decision_times) if decision_times else 0
        
        return json_response(200, data={
            'user': {
                'id': target_user_id,
                'email': target_email,
                'name': target_user.get('name')
            },
            'period_days': days,
            'statistics': {
                'total_decisions': total,
                'approved': approved,
                'rejected': rejected,
                'approval_rate': round(approved / total * 100, 1) if total > 0 else 0,
                'avg_decision_time_hours': round(avg_decision_time, 2),
                'ai_violations_upheld': true_positives,
                'ai_violations_overridden': false_positives_overridden
            },
            'decisions': [{
                'decision_id': d.get('id'),
                'document_id': d.get('document_id'),
                'document_filename': d.get('document_filename'),
                'decision': d.get('decision'),
                'timestamp': d.get('timestamp'),
                'context': d.get('decision_context', {}),
                'document_state': d.get('document_state_at_decision', {}),
                'ai_context': d.get('ai_context', {}),
                'time_to_decision_hours': d.get('time_to_decision_hours', 0)
            } for d in decisions]
        })
        
    except Exception as e:
        logger.error(f"❌ Get user decisions failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_get_org_overview(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /manage/overview?days=30
    Organization-wide dashboard for admins
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        days = int(req.params.get('days', '30'))
        
        # Get analytics summary
        summary = get_org_analytics_summary(org_id, days)
        
        # Get users with activity
        users = get_users_by_org(org_id)
        
        # Get top performers (by decisions made)
        db = get_db()
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        
        # Note: Cosmos doesn't support GROUP BY well, so we'll do it in Python
        decisions = list(db.get_container('audit_logs').query_items(
            query="""
            SELECT c.decision_maker.email as email 
            FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'decision_trail'
            AND c.timestamp >= @cutoff
            """,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff}
            ],
            enable_cross_partition_query=True
        ))
        
        # Count by user
        decision_counts = Counter(d.get('email') for d in decisions if d.get('email'))
        top_reviewers = [
            {'email': email, 'decisions': count}
            for email, count in decision_counts.most_common(10)
        ]
        
        return json_response(200, data={
            'organization_id': org_id,
            'period_days': days,
            'summary': summary,
            'team': {
                'total_users': len(users),
                'active_users': len([u for u in users if u.get('is_active', True)]),
                'top_reviewers': top_reviewers
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Get org overview failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def _send_invite_email(to_email: str, to_name: str, org_name: str, invited_by: str, user_id: str, org_id: str):
    """
    Send an invitation email. Uses Azure Communication Services if configured,
    falls back to logging the invite link so you can test without email infra.
    """
    import os

    # Build a signed invite token (HMAC-SHA256, expires 72h)
    secret = os.getenv('JWT_SECRET', 'dev-secret')
    payload = _json.dumps({
        'user_id': user_id,
        'org_id': org_id,
        'email': to_email,
        'exp': (datetime.utcnow() + timedelta(hours=72)).isoformat(),
    })
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}||{sig}".encode()).decode()

    frontend_url = os.getenv('FRONTEND_URL', 'https://your-app.com')
    invite_link = f"{frontend_url}/accept-invite?token={token}"

    logger.info(f"📧 INVITE LINK for {to_email}: {invite_link}")

    # Try Azure Communication Services email if configured
    acs_conn = os.getenv('AZURE_COMMUNICATION_CONNECTION_STRING')
    sender = os.getenv('INVITE_SENDER_EMAIL')

    if not acs_conn or not sender:
        logger.info("ℹ️  AZURE_COMMUNICATION_CONNECTION_STRING or INVITE_SENDER_EMAIL not set — invite link logged above only")
        return

    try:
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(acs_conn)
        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": to_email, "displayName": to_name}]},
            "content": {
                "subject": f"You've been invited to {org_name} on Greenlight",
                "plainText": (
                    f"Hi {to_name},\n\n"
                    f"{invited_by} has invited you to join {org_name} on the Greenlight Compliance Platform.\n\n"
                    f"Click the link below to set up your account (expires in 72 hours):\n{invite_link}\n\n"
                    f"If you didn't expect this invitation, you can ignore this email."
                ),
                "html": f"""
                <div style="font-family:sans-serif;max-width:560px;margin:0 auto">
                  <h2 style="color:#1e40af">You've been invited to {org_name}</h2>
                  <p>{invited_by} has invited you to join <strong>{org_name}</strong> on the Greenlight Compliance Platform.</p>
                  <a href="{invite_link}" style="display:inline-block;padding:12px 24px;background:#1e40af;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;margin:16px 0">
                    Accept Invitation
                  </a>
                  <p style="color:#666;font-size:13px">This link expires in 72 hours. If you didn't expect this, ignore this email.</p>
                </div>"""
            }
        }
        poller = client.begin_send(message)
        result = poller.result()
        logger.info(f"✅ Invite email sent to {to_email}: {result.get('id')}")
    except ImportError:
        logger.warning("azure-communication-email not installed. Run: pip install azure-communication-email")
    except Exception as e:
        logger.error(f"❌ Email send failed: {e}")
        raise


def handle_list_org_members(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /organization/members?search=&role=&limit=200
    Lightweight org member list for ANY authenticated user.
    Returns minimal fields: email, name, roles, department, job_title, is_active.
    
    Used by:
      - Assignment user picker (document detail page)
      - @mention autocomplete (discussions)
      - Team member display
      - Legal team display
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")

        limit = min(int(req.params.get('limit', '200')), 500)
        search_query = (req.params.get('search') or req.params.get('q') or '').lower().strip()
        role_filter = req.params.get('role', '').strip()

        all_users = get_users_by_org(org_id, limit=500)

        # Apply filters
        filtered = all_users

        if role_filter:
            # Support comma-separated roles: "Legal.Advisor,DLAPiper.Advisory"
            wanted_roles = [r.strip() for r in role_filter.split(',') if r.strip()]
            filtered = [
                u for u in filtered
                if any(wr in u.get('roles', []) for wr in wanted_roles)
            ]

        if search_query and len(search_query) >= 1:
            filtered = [
                u for u in filtered
                if search_query in u.get('name', '').lower()
                or search_query in u.get('email', '').lower()
                or search_query in u.get('department', '').lower()
                or any(search_query in r.lower() for r in u.get('roles', []))
            ]

        # Only active users by default
        show_inactive = req.params.get('include_inactive', 'false').lower() == 'true'
        if not show_inactive:
            filtered = [u for u in filtered if u.get('is_active', True)]

        # Paginate
        filtered = filtered[:limit]

        # Return lightweight fields only (no sensitive data)
        members = []
        for u in filtered:
            members.append({
                'id': u.get('id', ''),
                'email': u.get('email', ''),
                'name': u.get('name', u.get('email', '').split('@')[0]),
                'roles': u.get('roles', []),
                'department': u.get('department', ''),
                'job_title': u.get('job_title', ''),
                'is_active': u.get('is_active', True),
                'last_login': u.get('last_login', ''),
            })

        return json_response(200, data={
            'members': members,
            'total': len(members),
        })

    except Exception as e:
        logger.error(f"❌ List org members failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to list members: {str(e)}")
