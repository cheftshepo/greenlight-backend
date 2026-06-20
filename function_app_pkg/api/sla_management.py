"""SLA Management & Tracking System"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List
from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_db, get_organization, update_organization

logger = logging.getLogger(__name__)

def _get_user_attr(user, attr: str, default=None):
    """Safely get attribute from user"""
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


DEFAULT_SLA_CONFIG = {
    "default_hours": 48,
    "by_priority": {
        "urgent": 4,
        "high": 24,
        "medium": 48,
        "low": 72
    },
    "by_risk_score": {
        "high_risk": 24,     # risk_score > 70
        "medium_risk": 48,   # risk_score 40-70
        "low_risk": 72       # risk_score < 40
    },
    "business_hours_only": True,
    "business_hours": {
        "start": "09:00",
        "end": "17:30",
        "timezone": "Europe/London",
        "working_days": [1, 2, 3, 4, 5]  # Mon-Fri
    },
    "escalation_rules": [
        {
            "id": "esc_001",
            "trigger_type": "percent_elapsed",
            "trigger_value": 50,
            "actions": [
                {"type": "notify", "target": "assignee", "channel": "email"}
            ]
        },
        {
            "id": "esc_002",
            "trigger_type": "percent_elapsed",
            "trigger_value": 80,
            "actions": [
                {"type": "notify", "target": "assignee", "channel": "email"},
                {"type": "notify", "target": "manager", "channel": "email"}
            ]
        },
        {
            "id": "esc_003",
            "trigger_type": "breached",
            "trigger_value": 0,
            "actions": [
                {"type": "notify", "target": "admin", "channel": "email"},
                {"type": "flag_in_dashboard"}
            ]
        }
    ],
    "holidays": []
}


def handle_get_sla_config(req, user) -> Dict:
    """GET /api/settings/sla - Get SLA configuration"""
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        org = get_organization(org_id)
        if not org:
            return json_response(404, error="Organization not found")
        
        sla_config = org.get('sla_config', DEFAULT_SLA_CONFIG)
        
        return json_response(200, data={
            'sla_config': sla_config,
            'organization_id': org_id
        })
        
    except Exception as e:
        logger.error(f"❌ Get SLA config failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_update_sla_config(req, user) -> Dict:
    """PUT /api/settings/sla - Update SLA configuration"""
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        if 'sla_config' not in body:
            return json_response(400, error="sla_config required in body")
        
        new_config = body['sla_config']
        
        # Validate config has required fields
        required = ['default_hours', 'by_priority']
        missing = [f for f in required if f not in new_config]
        if missing:
            return json_response(400, error=f"Missing required fields: {missing}")
        
        # Update organization
        update_organization(org_id, {'sla_config': new_config})
        
        logger.info(f"✅ SLA config updated for org {org_id}")
        
        return json_response(200, data={
            'sla_config': new_config,
            'message': 'SLA configuration updated successfully'
        })
        
    except Exception as e:
        logger.error(f"❌ Update SLA config failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


def handle_sla_dashboard(req, user) -> Dict:
    """GET /api/sla/dashboard - Get SLA compliance dashboard"""
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        db = get_db()
        
        # Get all active assignments
        query = """
        SELECT c.id, c.filename, c.assigned_to, c.assigned_at, c.assignment_deadline,
               c.assignment_status, c.assignment_priority, c.completed_at, c.ticket_id
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.assigned_to != null
        """
        
        docs = list(db.get_container('documents').query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        now = datetime.utcnow()
        
        # Calculate SLA metrics
        total_active = 0
        on_track = 0
        at_risk = 0
        breached = 0
        completed_on_time = 0
        completed_late = 0
        
        by_priority = {
            'urgent': {'total': 0, 'on_track': 0, 'at_risk': 0, 'breached': 0},
            'high': {'total': 0, 'on_track': 0, 'at_risk': 0, 'breached': 0},
            'medium': {'total': 0, 'on_track': 0, 'at_risk': 0, 'breached': 0},
            'low': {'total': 0, 'on_track': 0, 'at_risk': 0, 'breached': 0}
        }
        
        breached_items = []
        at_risk_items = []
        
        for doc in docs:
            status = doc.get('assignment_status', 'pending')
            priority = doc.get('assignment_priority', 'medium')
            deadline_str = doc.get('assignment_deadline', '')
            
            if not deadline_str:
                continue
            
            try:
                deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                assigned_at = datetime.fromisoformat(doc.get('assigned_at', '').replace('Z', '+00:00'))
            except:
                continue
            
            # Active assignments
            if status in ['pending', 'in_progress']:
                total_active += 1
                by_priority[priority]['total'] += 1
                
                time_remaining = (deadline - now.replace(tzinfo=deadline.tzinfo)).total_seconds() / 3600
                total_time = (deadline - assigned_at).total_seconds() / 3600
                percent_elapsed = ((total_time - time_remaining) / total_time * 100) if total_time > 0 else 0
                
                if time_remaining < 0:
                    # Breached
                    breached += 1
                    by_priority[priority]['breached'] += 1
                    
                    breached_items.append({
                        'assignment_id': doc.get('id'),
                        'ticket_id': doc.get('ticket_id', ''),
                        'document_name': doc.get('filename'),
                        'assignee': doc.get('assigned_to'),
                        'deadline': deadline_str,
                        'breached_at': deadline_str,
                        'hours_overdue': round(abs(time_remaining), 1),
                        'priority': priority
                    })
                elif percent_elapsed > 80:
                    # At risk (>80% time elapsed)
                    at_risk += 1
                    by_priority[priority]['at_risk'] += 1
                    
                    at_risk_items.append({
                        'assignment_id': doc.get('id'),
                        'ticket_id': doc.get('ticket_id', ''),
                        'document_name': doc.get('filename'),
                        'assignee': doc.get('assigned_to'),
                        'deadline': deadline_str,
                        'time_remaining_hours': round(time_remaining, 1),
                        'percent_elapsed': round(percent_elapsed, 1),
                        'priority': priority
                    })
                else:
                    # On track
                    on_track += 1
                    by_priority[priority]['on_track'] += 1
            
            # Completed assignments
            elif status == 'completed':
                completed_at = doc.get('completed_at')
                if completed_at:
                    try:
                        completed_time = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                        if completed_time <= deadline:
                            completed_on_time += 1
                        else:
                            completed_late += 1
                    except:
                        pass
        
        # Calculate overall health
        total_completed = completed_on_time + completed_late
        compliance_rate = completed_on_time / total_completed if total_completed > 0 else 1.0
        
        if compliance_rate >= 0.9:
            health_status = "healthy"
        elif compliance_rate >= 0.75:
            health_status = "at_risk"
        else:
            health_status = "critical"
        
        # Trend (simplified - would need historical data)
        trend = "stable"
        
        return json_response(200, data={
            'current_health': {
                'overall_score': round(compliance_rate, 2),
                'status': health_status,
                'trend': trend
            },
            'summary': {
                'total_active': total_active,
                'on_track': on_track,
                'at_risk': at_risk,
                'breached': breached
            },
            'by_priority': by_priority,
            'breached_items': breached_items,
            'at_risk_items': at_risk_items,
            'historical': {
                'completed_on_time': completed_on_time,
                'completed_late': completed_late,
                'compliance_rate': round(compliance_rate, 2)
            }
        })
        
    except Exception as e:
        logger.error(f"❌ SLA dashboard failed: {e}", exc_info=True)
        return json_response(500, error=str(e))