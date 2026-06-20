"""
ADVANCED ANALYTICS API
======================
Performance metrics, trends, costs, and insights

File: function_app_pkg/api/advanced_analytics.py
"""

import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
from ..core.database import save_analytics_event

from ..core.database import get_db
from ..shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =============================================================================
# USER PERFORMANCE METRICS
# =============================================================================

def handle_user_performance(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /analytics/user-performance?period=30d
    
    Metrics:
    - Documents reviewed per user
    - Average review time
    - Approval vs rejection rate
    - Accuracy (compared to AI predictions)
    """
    try:
        org_id = _get_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        period_days = int(req.params.get('period', '30').replace('d', ''))
        cutoff_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat()
        
        db = get_db()
        
        # Get all assignments in period
        assignments_query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document_assignment'
        AND c.assigned_at >= @cutoff
        """
        
        assignments = list(db.get_container('audit_logs').query_items(
            query=assignments_query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            enable_cross_partition_query=True
        ))
        
        # Get audit logs for approvals/rejections
        audit_query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id
        AND c.action IN ('document.approved', 'document.rejected')
        AND c.timestamp >= @cutoff
        """
        
        audits = list(db.get_container('audit_logs').query_items(
            query=audit_query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            enable_cross_partition_query=True
        ))
        
        # Calculate per-user metrics
        user_metrics = defaultdict(lambda: {
            'user_id': '',
            'user_name': '',
            'user_email': '',
            'documents_assigned': 0,
            'documents_completed': 0,
            'documents_approved': 0,
            'documents_rejected': 0,
            'avg_review_time_hours': 0,
            'total_review_time_hours': 0,
            'approval_rate': 0,
            'completion_rate': 0,
            'avg_daily_throughput': 0
        })
        
        # Process assignments
        for assignment in assignments:
            user_key = assignment.get('assigned_to')
            
            user_metrics[user_key]['user_id'] = user_key
            user_metrics[user_key]['user_name'] = assignment.get('assigned_to_name')
            user_metrics[user_key]['user_email'] = assignment.get('assigned_to_email')
            user_metrics[user_key]['documents_assigned'] += 1
            
            if assignment.get('status') == 'completed':
                user_metrics[user_key]['documents_completed'] += 1
                
                # Calculate review time
                if assignment.get('completion_time_seconds'):
                    review_hours = assignment['completion_time_seconds'] / 3600
                    user_metrics[user_key]['total_review_time_hours'] += review_hours
        
        # Process audit logs
        for audit in audits:
            user_key = audit.get('user_id')
            
            if audit.get('action') == 'document.approved':
                user_metrics[user_key]['documents_approved'] += 1
            elif audit.get('action') == 'document.rejected':
                user_metrics[user_key]['documents_rejected'] += 1
        
        # Calculate derived metrics
        for user_key, metrics in user_metrics.items():
            if metrics['documents_completed'] > 0:
                metrics['avg_review_time_hours'] = round(
                    metrics['total_review_time_hours'] / metrics['documents_completed'], 2
                )
                metrics['completion_rate'] = round(
                    metrics['documents_completed'] / metrics['documents_assigned'] * 100, 1
                )
                metrics['avg_daily_throughput'] = round(
                    metrics['documents_completed'] / period_days, 2
                )
            
            total_decisions = metrics['documents_approved'] + metrics['documents_rejected']
            if total_decisions > 0:
                metrics['approval_rate'] = round(
                    metrics['documents_approved'] / total_decisions * 100, 1
                )
        
        # Sort by throughput
        performance_list = list(user_metrics.values())
        performance_list.sort(key=lambda x: x['documents_completed'], reverse=True)
        
        return json_response(200, data={
            'period_days': period_days,
            'user_performance': performance_list,
            'summary': {
                'total_reviewers': len(performance_list),
                'total_documents_reviewed': sum(m['documents_completed'] for m in performance_list),
                'avg_review_time_hours': round(
                    sum(m['avg_review_time_hours'] for m in performance_list) / len(performance_list), 2
                ) if performance_list else 0,
                'top_performer': performance_list[0]['user_name'] if performance_list else None
            }
        })
        
    except Exception as e:
        logger.error(f"❌ User performance error: {e}", exc_info=True)
        return json_response(500, error=str(e))

    finally:
        save_analytics_event({
            'organization_id': org_id,
            'type': 'analytics_query',
            'query_type': 'user_performance',
            'period_days': period_days
        })

# =============================================================================
# VIOLATION TRENDS
# =============================================================================

def handle_violation_trends(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /analytics/violation-trends?period=90d
    
    Shows which violations are increasing/decreasing over time
    """
    try:
        org_id = _get_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        period_days = int(req.params.get('period', '90').replace('d', ''))
        cutoff_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat()
        
        db = get_db()
        
        # Get all documents with violations in period
        query = """
        SELECT c.created_at, c.violations, c.jurisdiction 
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.created_at >= @cutoff
        AND ARRAY_LENGTH(c.violations) > 0
        ORDER BY c.created_at ASC
        """
        
        docs = list(db.get_container('documents').query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            partition_key=org_id
        ))
        
        # Group violations by week and category
        weekly_violations = defaultdict(lambda: defaultdict(int))
        
        for doc in docs:
            doc_date = datetime.fromisoformat(doc.get('created_at', '').replace('Z', '+00:00'))
            week_key = doc_date.strftime('%Y-W%W')  # Year-Week format
            
            for violation in doc.get('violations', []):
                category = violation.get('category', 'unknown')
                weekly_violations[week_key][category] += 1
        
        # Convert to time series
        trend_data = []
        for week, categories in sorted(weekly_violations.items()):
            week_data = {'week': week, 'total': sum(categories.values())}
            week_data.update(categories)
            trend_data.append(week_data)
        
        # Calculate trends (increasing/decreasing)
        category_trends = {}
        all_categories = set()
        for week_data in trend_data:
            all_categories.update(week_data.keys())
        all_categories.discard('week')
        all_categories.discard('total')
        
        for category in all_categories:
            values = [week.get(category, 0) for week in trend_data]
            if len(values) >= 2:
                # Simple trend: compare first half vs second half
                mid = len(values) // 2
                first_half_avg = sum(values[:mid]) / mid if mid > 0 else 0
                second_half_avg = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) > 0 else 0
                
                if second_half_avg > first_half_avg * 1.2:
                    trend = 'increasing'
                elif second_half_avg < first_half_avg * 0.8:
                    trend = 'decreasing'
                else:
                    trend = 'stable'
                
                category_trends[category] = {
                    'trend': trend,
                    'change_percent': round((second_half_avg - first_half_avg) / first_half_avg * 100, 1) if first_half_avg > 0 else 0,
                    'total_count': sum(values)
                }
        
        return json_response(200, data={
            'period_days': period_days,
            'weekly_data': trend_data,
            'category_trends': category_trends,
            'top_violations': sorted(
                category_trends.items(),
                key=lambda x: x[1]['total_count'],
                reverse=True
            )[:10]
        })
        
    except Exception as e:
        logger.error(f"❌ Violation trends error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# COST ATTRIBUTION
# =============================================================================

def handle_cost_attribution(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /analytics/cost-attribution?period=30d
    
    Track OpenAI API costs per organization/user/department
    """
    try:
        org_id = _get_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        period_days = int(req.params.get('period', '30').replace('d', ''))
        cutoff_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat()
        
        db = get_db()
        
        # Get AI conversations (they track token usage)
        chat_query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id
            AND c.type = 'ai_conversation'
            AND c.created_at >= @cutoff
            """
        
        conversations = list(db.get_container('ai_conversations').query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            enable_cross_partition_query=True
        ))
        
        # Calculate costs (approximate)
        # GPT-4: $0.03/1K input tokens, $0.06/1K output tokens
        INPUT_COST_PER_1K = 0.03
        OUTPUT_COST_PER_1K = 0.06
        
        user_costs = defaultdict(lambda: {
            'user_id': '',
            'user_email': '',
            'total_conversations': 0,
            'total_messages': 0,
            'estimated_input_tokens': 0,
            'estimated_output_tokens': 0,
            'estimated_cost_usd': 0
        })
        
        for conv in conversations:
            user_key = conv.get('user_id')
            
            user_costs[user_key]['user_id'] = user_key
            user_costs[user_key]['user_email'] = conv.get('user_email')
            user_costs[user_key]['total_conversations'] += 1
            
            messages = conv.get('messages', [])
            user_costs[user_key]['total_messages'] += len(messages)
            
            # Estimate tokens (rough: 1 token ≈ 4 chars)
            for msg in messages:
                content = msg.get('content', '')
                tokens = len(content) / 4
                
                if msg.get('role') == 'user':
                    user_costs[user_key]['estimated_input_tokens'] += tokens
                else:
                    user_costs[user_key]['estimated_output_tokens'] += tokens
        
        # Calculate costs
        for user_key, costs in user_costs.items():
            input_cost = (costs['estimated_input_tokens'] / 1000) * INPUT_COST_PER_1K
            output_cost = (costs['estimated_output_tokens'] / 1000) * OUTPUT_COST_PER_1K
            costs['estimated_cost_usd'] = round(input_cost + output_cost, 2)
        
        cost_list = list(user_costs.values())
        cost_list.sort(key=lambda x: x['estimated_cost_usd'], reverse=True)
        
        total_cost = sum(c['estimated_cost_usd'] for c in cost_list)
        
        return json_response(200, data={
            'period_days': period_days,
            'user_costs': cost_list,
            'summary': {
                'total_cost_usd': round(total_cost, 2),
                'total_conversations': sum(c['total_conversations'] for c in cost_list),
                'total_messages': sum(c['total_messages'] for c in cost_list),
                'avg_cost_per_user': round(total_cost / len(cost_list), 2) if cost_list else 0,
                'highest_user': cost_list[0]['user_email'] if cost_list else None
            },
            'note': 'Costs are estimates based on average token usage'
        })
        
    except Exception as e:
        logger.error(f"❌ Cost attribution error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# DOCUMENT LIFECYCLE
# =============================================================================

def handle_document_lifecycle(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /analytics/document-lifecycle?period=30d
    
    Analyze document journey: Upload → Scan → Review → Approval
    """
    try:
        org_id = _get_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        period_days = int(req.params.get('period', '30').replace('d', ''))
        cutoff_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat()
        
        db = get_db()
        
        # Get documents
        query = """
        SELECT c.id, c.created_at, c.scanned_at, c.assigned_at, 
               c.reviewed_at, c.approved_at, c.rejected_at, c.status
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document'
        AND c.created_at >= @cutoff
        """
        
        docs = list(db.get_container('documents').query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            partition_key=org_id
        ))
        
        # Calculate lifecycle metrics
        lifecycle_metrics = {
            'total_documents': len(docs),
            'avg_upload_to_scan_hours': 0,
            'avg_scan_to_review_hours': 0,
            'avg_review_to_decision_hours': 0,
            'avg_total_lifecycle_hours': 0,
            'status_distribution': defaultdict(int),
            'bottlenecks': []
        }
        
        times_upload_to_scan = []
        times_scan_to_review = []
        times_review_to_decision = []
        times_total = []
        
        for doc in docs:
            lifecycle_metrics['status_distribution'][doc.get('status', 'unknown')] += 1
            
            created = doc.get('created_at')
            scanned = doc.get('scanned_at')
            assigned = doc.get('assigned_at')
            reviewed = doc.get('reviewed_at')
            approved = doc.get('approved_at') or doc.get('rejected_at')
            
            if created and scanned:
                delta = (datetime.fromisoformat(scanned.replace('Z', '+00:00')) - 
                        datetime.fromisoformat(created.replace('Z', '+00:00')))
                times_upload_to_scan.append(delta.total_seconds() / 3600)
            
            if scanned and (assigned or reviewed):
                target = assigned or reviewed
                delta = (datetime.fromisoformat(target.replace('Z', '+00:00')) - 
                        datetime.fromisoformat(scanned.replace('Z', '+00:00')))
                times_scan_to_review.append(delta.total_seconds() / 3600)
            
            if reviewed and approved:
                delta = (datetime.fromisoformat(approved.replace('Z', '+00:00')) - 
                        datetime.fromisoformat(reviewed.replace('Z', '+00:00')))
                times_review_to_decision.append(delta.total_seconds() / 3600)
            
            if created and approved:
                delta = (datetime.fromisoformat(approved.replace('Z', '+00:00')) - 
                        datetime.fromisoformat(created.replace('Z', '+00:00')))
                times_total.append(delta.total_seconds() / 3600)
        
        # Calculate averages
        if times_upload_to_scan:
            lifecycle_metrics['avg_upload_to_scan_hours'] = round(sum(times_upload_to_scan) / len(times_upload_to_scan), 2)
        
        if times_scan_to_review:
            lifecycle_metrics['avg_scan_to_review_hours'] = round(sum(times_scan_to_review) / len(times_scan_to_review), 2)
        
        if times_review_to_decision:
            lifecycle_metrics['avg_review_to_decision_hours'] = round(sum(times_review_to_decision) / len(times_review_to_decision), 2)
        
        if times_total:
            lifecycle_metrics['avg_total_lifecycle_hours'] = round(sum(times_total) / len(times_total), 2)
        
        # Identify bottlenecks
        if lifecycle_metrics['avg_scan_to_review_hours'] > 24:
            lifecycle_metrics['bottlenecks'].append({
                'stage': 'assignment',
                'avg_hours': lifecycle_metrics['avg_scan_to_review_hours'],
                'recommendation': 'Consider auto-assignment or increase reviewer capacity'
            })
        
        if lifecycle_metrics['avg_review_to_decision_hours'] > 48:
            lifecycle_metrics['bottlenecks'].append({
                'stage': 'decision',
                'avg_hours': lifecycle_metrics['avg_review_to_decision_hours'],
                'recommendation': 'Reviewers taking too long - check workload or provide training'
            })
        
        return json_response(200, data=lifecycle_metrics)
        
    except Exception as e:
        logger.error(f"❌ Document lifecycle error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# SLA COMPLIANCE
# =============================================================================

def handle_sla_compliance(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /analytics/sla-compliance?period=30d
    
    Track SLA adherence for document reviews
    """
    try:
        org_id = _get_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        period_days = int(req.params.get('period', '30').replace('d', ''))
        cutoff_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat()
        
        db = get_db()
        
        # Get assignments
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'document_assignment'
        AND c.assigned_at >= @cutoff
        """
        
        assignments = list(db.get_container('audit_logs').query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff_date}
            ],
            enable_cross_partition_query=True
        ))
        
        sla_metrics = {
            'total_assignments': len(assignments),
            'completed_on_time': 0,
            'completed_late': 0,
            'still_pending': 0,
            'overdue': 0,
            'sla_compliance_rate': 0,
            'avg_completion_time_vs_sla': 0
        }
        
        now = datetime.utcnow()
        
        for assignment in assignments:
            status = assignment.get('status')
            due_date = datetime.fromisoformat(assignment.get('due_date', '').replace('Z', '+00:00'))
            
            if status == 'completed':
                completed_at = datetime.fromisoformat(assignment.get('completed_at', '').replace('Z', '+00:00'))
                
                if completed_at <= due_date:
                    sla_metrics['completed_on_time'] += 1
                else:
                    sla_metrics['completed_late'] += 1
            
            elif status in ['pending', 'in_progress']:
                sla_metrics['still_pending'] += 1
                
                if due_date < now:
                    sla_metrics['overdue'] += 1
        
        # Calculate compliance rate
        total_completed = sla_metrics['completed_on_time'] + sla_metrics['completed_late']
        if total_completed > 0:
            sla_metrics['sla_compliance_rate'] = round(
                sla_metrics['completed_on_time'] / total_completed * 100, 1
            )
        
        return json_response(200, data=sla_metrics)
        
    except Exception as e:
        logger.error(f"❌ SLA compliance error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# HELPERS
# =============================================================================

def _get_org_id(user):
    if user is None:
        return None
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id')
    return None

# ============================================================================
# CRITICAL MISSING ANALYTICS - ADD THESE FOR SALES IMPACT
# ============================================================================

# 1. ROI CALCULATOR (Most important for selling!)
# Add to scan.py after document update (~line 450)

def _calculate_roi_impact(doc: Dict, scan_duration: float) -> Dict:
    """Calculate money/time saved vs manual review"""
    
    # Industry benchmarks
    MANUAL_REVIEW_HOURS = 15  # Lawyer spends 15h per doc
    LAWYER_HOURLY_RATE = 200  # £200/hour
    AI_REVIEW_HOURS = scan_duration / 3600  # Convert seconds to hours
    HUMAN_VERIFICATION_HOURS = 2  # Officer checks AI results for 2h
    
    manual_cost = MANUAL_REVIEW_HOURS * LAWYER_HOURLY_RATE
    ai_cost = (AI_REVIEW_HOURS * 5) + (HUMAN_VERIFICATION_HOURS * LAWYER_HOURLY_RATE)  # £5/hour AI cost
    
    savings = manual_cost - ai_cost
    time_saved_hours = MANUAL_REVIEW_HOURS - (AI_REVIEW_HOURS + HUMAN_VERIFICATION_HOURS)
    
    return {
        'manual_cost_gbp': round(manual_cost, 2),
        'ai_cost_gbp': round(ai_cost, 2),
        'savings_gbp': round(savings, 2),
        'time_saved_hours': round(time_saved_hours, 2),
        'roi_percentage': round((savings / manual_cost) * 100, 1),
        'efficiency_gain': round((time_saved_hours / MANUAL_REVIEW_HOURS) * 100, 1)
    }

# Call this in scan.py and save to document:
roi_impact = _calculate_roi_impact(doc, duration)
doc['roi_metrics'] = roi_impact

# ============================================================================
# 2. REGULATORY RISK PREVENTION (What disasters were avoided?)
# ============================================================================

def _calculate_risk_prevented(violations: List[Dict]) -> Dict:
    """Calculate potential fines avoided"""
    
    # Regulatory fine amounts by severity
    FINE_AMOUNTS = {
        'CRITICAL': 500000,  # £500K for critical violations (guaranteed returns, etc.)
        'HIGH': 100000,      # £100K for high severity
        'MEDIUM': 25000,     # £25K for medium
        'LOW': 5000          # £5K for low
    }
    
    total_potential_fines = 0
    risk_breakdown = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    
    for v in violations:
        severity = v.get('severity', 'MEDIUM')
        fine = FINE_AMOUNTS.get(severity, 0)
        total_potential_fines += fine
        risk_breakdown[severity] += 1
    
    return {
        'total_potential_fines_gbp': total_potential_fines,
        'violations_by_severity': risk_breakdown,
        'critical_risks_prevented': risk_breakdown['CRITICAL'],
        'regulatory_action_avoided': risk_breakdown['CRITICAL'] > 0,
        'reputational_damage_risk': 'HIGH' if risk_breakdown['CRITICAL'] > 0 else 'MEDIUM' if risk_breakdown['HIGH'] > 0 else 'LOW'
    }

# Add to scan.py after violations are found:
risk_prevented = _calculate_risk_prevented(violations)
doc['risk_prevented_metrics'] = risk_prevented

# =============================
def _get_org_id(user):
    """Safely extract organization_id from user (handles dict or object)"""
    if user is None:
        return None
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id')
    # Handle SimpleNamespace or other objects
    try:
        return user.organization_id
    except (AttributeError, TypeError):
        pass
    try:
        return user.get('organization_id')
    except (AttributeError, TypeError):
        pass
    return None
# ===============================================
# 3. CLIENT BILLING METRICS (How much to charge clients)
# ============================================================================

def _calculate_billable_value(doc: Dict, violations: List[Dict]) -> Dict:
    """Calculate what to charge client for this compliance review"""
    
    BASE_REVIEW_FEE = 500  # £500 base fee
    PER_VIOLATION_FEE = 50  # £50 per violation found
    CERTIFICATE_FEE = 200   # £200 for compliance certificate
    URGENT_FEE = 300        # £300 if marked urgent
    
    violation_fees = len(violations) * PER_VIOLATION_FEE
    
    total_billable = BASE_REVIEW_FEE + violation_fees
    
    if doc.get('priority') == 'urgent':
        total_billable += URGENT_FEE
    
    if doc.get('certificates'):
        total_billable += CERTIFICATE_FEE * len(doc['certificates'])
    
    return {
        'base_fee_gbp': BASE_REVIEW_FEE,
        'violation_analysis_fee_gbp': violation_fees,
        'certificate_fee_gbp': CERTIFICATE_FEE if doc.get('certificates') else 0,
        'urgent_fee_gbp': URGENT_FEE if doc.get('priority') == 'urgent' else 0,
        'total_billable_gbp': total_billable,
        'recommended_client_charge': round(total_billable * 1.3, 2)  # 30% margin
    }

# Add to scan.py:
billing = _calculate_billable_value(doc, violations)
doc['billing_metrics'] = billing

# ============================================================================
# 4. ACCURACY TRACKING (How often is AI right?)
# ============================================================================

def track_ai_accuracy(document_id: str, ai_violations: List[Dict], human_decision: str):
    """
    Track AI accuracy by comparing AI predictions to human decisions
    Call this in approval.py when compliance officer approves/rejects
    """
    from function_app_pkg.core.database import get_container
    
    container = get_container('analytics')
    
    # AI predicted non-compliant if violations found
    ai_predicted_non_compliant = len(ai_violations) > 0
    
    # Human decided if it's actually non-compliant
    human_says_non_compliant = human_decision in ['rejected', 'non_compliant']
    
    # Determine accuracy
    if ai_predicted_non_compliant == human_says_non_compliant:
        accuracy_result = 'TRUE_POSITIVE' if ai_predicted_non_compliant else 'TRUE_NEGATIVE'
    else:
        accuracy_result = 'FALSE_POSITIVE' if ai_predicted_non_compliant else 'FALSE_NEGATIVE'
    
    accuracy_record = {
        'id': f"accuracy_{document_id}_{int(time.time())}",
        'type': 'ai_accuracy',
        'document_id': document_id,
        'ai_violations_count': len(ai_violations),
        'ai_predicted_non_compliant': ai_predicted_non_compliant,
        'human_decision': human_decision,
        'accuracy_result': accuracy_result,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }
    
    container.upsert_item(accuracy_record)
    
    return accuracy_result

# Add to approval.py handle_approve() and handle_reject():
from function_app_pkg.api.advanced_analytics import track_ai_accuracy
track_ai_accuracy(doc_id, doc.get('violations', []), 'approved')  # or 'rejected'

# ============================================================================
# 5. USAGE HEATMAP (When do people use the system?)
# ============================================================================

def log_usage_event(user_id: str, org_id: str, action: str, timestamp: str = None):
    """
    Track every user action for usage patterns
    Call this in EVERY endpoint
    """
    from function_app_pkg.core.database import get_container
    import time
    
    if not timestamp:
        timestamp = datetime.utcnow().isoformat() + 'Z'
    
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    
    usage_event = {
        'id': f"usage_{int(time.time())}_{user_id}",
        'type': 'usage_event',
        'user_id': user_id,
        'organization_id': org_id,
        'action': action,  # 'upload', 'scan', 'approve', 'chat', etc.
        'timestamp': timestamp,
        'hour_of_day': dt.hour,
        'day_of_week': dt.strftime('%A'),
        'day_of_month': dt.day,
        'week_of_year': dt.isocalendar()[1]
    }
    
    container = get_container('analytics')
    container.upsert_item(usage_event)

# Add to EVERY endpoint handler (upload, scan, approve, etc.):
log_usage_event(user.email, org_id, 'document_uploaded')

# ============================================================================
# 6. COMPARATIVE ANALYTICS (How do we compare to competitors?)
# ============================================================================

INDUSTRY_BENCHMARKS = {
    'avg_review_time_hours': 15,      # Manual review takes 15h
    'avg_violations_per_doc': 8,      # Typical marketing doc has 8 violations
    'compliance_rate': 0.65,          # 65% of docs are compliant first time
    'avg_cost_per_review_gbp': 3000,  # £3K for manual lawyer review
    'sla_compliance_rate': 0.70       # 70% of reviews meet SLA
}

def calculate_competitive_advantage(org_metrics: Dict) -> Dict:
    """Show how client performs vs industry"""
    
    return {
        'time_vs_industry': {
            'your_avg_hours': org_metrics['avg_review_time_hours'],
            'industry_avg_hours': INDUSTRY_BENCHMARKS['avg_review_time_hours'],
            'improvement_percentage': round(
                (1 - org_metrics['avg_review_time_hours'] / INDUSTRY_BENCHMARKS['avg_review_time_hours']) * 100, 1
            )
        },
        'cost_vs_industry': {
            'your_avg_cost_gbp': org_metrics['avg_cost_per_review_gbp'],
            'industry_avg_cost_gbp': INDUSTRY_BENCHMARKS['avg_cost_per_review_gbp'],
            'savings_percentage': round(
                (1 - org_metrics['avg_cost_per_review_gbp'] / INDUSTRY_BENCHMARKS['avg_cost_per_review_gbp']) * 100, 1
            )
        },
        'sla_vs_industry': {
            'your_sla_rate': org_metrics['sla_compliance_rate'],
            'industry_sla_rate': INDUSTRY_BENCHMARKS['sla_compliance_rate'],
            'better_by': round(
                (org_metrics['sla_compliance_rate'] - INDUSTRY_BENCHMARKS['sla_compliance_rate']) * 100, 1
            )
        }
    }


def _get_org_id(user):
    """Safely extract organization_id from user (handles dict or object)"""
    if user is None:
        return None
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id')
    return None


def _get_org_id(user):
    """Safely extract organization_id from user (handles dict or object)"""
    if user is None:
        return None
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id')
    # Handle SimpleNamespace or other objects
    try:
        return user.organization_id
    except (AttributeError, TypeError):
        pass
    try:
        return user.get('organization_id')
    except (AttributeError, TypeError):
        pass
    return None
