"""Platform Admin - Cost & Resource Monitoring (SuperAdmin Only)"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List
from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_db

logger = logging.getLogger(__name__)

def _get_user_attr(user, attr: str, default=None):
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def handle_platform_usage(req, user) -> Dict:
    """
    GET /api/platform/usage?period=month&start=2024-12-01
    Platform-wide usage and cost dashboard (SuperAdmin only)
    """
    try:
        # Verify SuperAdmin
        user_roles = _get_user_attr(user, 'roles', [])
        if 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="SuperAdmin access required")
        
        period = req.params.get('period', 'month')
        start_date = req.params.get('start', (datetime.utcnow().replace(day=1)).isoformat() + 'Z')
        
        db = get_db()
        
        # Get all organizations
        org_query = "SELECT * FROM c WHERE c.type = 'organization'"
        orgs = list(db.get_container('organizations').query_items(
            query=org_query,
            enable_cross_partition_query=True
        ))
        
        # Get all documents in period
        doc_query = """
        SELECT c.organization_id, c.uploaded_by, c.status, c.training_metadata,
               c.created_at, c.scan_completed_at
        FROM c 
        WHERE c.type = 'document'
        AND c.created_at >= @start_date
        """
        
        docs = list(db.get_container('documents').query_items(
            query=doc_query,
            parameters=[{"name": "@start_date", "value": start_date}],
            enable_cross_partition_query=True
        ))
        
        # Aggregate platform totals
        total_orgs = len(orgs)
        active_orgs = len([o for o in orgs if o.get('is_active', True)])
        
        total_docs_uploaded = len(docs)
        total_docs_scanned = len([d for d in docs if d.get('scan_completed_at')])
        total_docs_approved = len([d for d in docs if d.get('status') == 'approved'])
        total_docs_rejected = len([d for d in docs if d.get('status') == 'rejected'])
        total_docs_pending = total_docs_uploaded - total_docs_approved - total_docs_rejected
        
        # Calculate AI costs from metadata
        total_tokens = 0
        total_openai_cost = 0.0
        
        for doc in docs:
            metadata = doc.get('training_metadata', {})
            ai_processing = metadata.get('ai_processing', {})
            
            tokens = ai_processing.get('total_tokens', 0)
            cost = ai_processing.get('cost_usd', 0.0)
            
            total_tokens += tokens
            total_openai_cost += cost
        
        # Estimate other Azure costs (simplified)
        storage_gb = total_docs_uploaded * 0.05  # Avg 50KB per doc
        storage_cost = storage_gb * 0.18  # $0.18 per GB/month
        
        cosmos_ru = total_docs_uploaded * 10  # ~10 RU per operation
        cosmos_cost = (cosmos_ru / 1000000) * 0.25  # $0.25 per million RU
        
        function_executions = total_docs_uploaded * 5  # Avg 5 function calls per doc
        function_cost = (function_executions / 1000000) * 0.20  # $0.20 per million
        
        total_cost = total_openai_cost + storage_cost + cosmos_cost + function_cost
        
        # Revenue (simplified - would come from billing system)
        total_revenue = active_orgs * 5000  # Avg $5k/org/month
        
        gross_margin = ((total_revenue - total_cost) / total_revenue * 100) if total_revenue > 0 else 0
        
        # By organization breakdown
        by_organization = []
        
        for org in orgs:
            org_id = org.get('id')
            org_docs = [d for d in docs if d.get('organization_id') == org_id]
            
            # Count users
            user_query = """
            SELECT VALUE COUNT(1) FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'user'
            """
            user_count = list(db.get_container('users').query_items(
                query=user_query,
                parameters=[{"name": "@org_id", "value": org_id}],
                partition_key=org_id
            ))
            users_total = user_count[0] if user_count else 0
            
            # Calculate org costs
            org_tokens = sum(
                d.get('training_metadata', {}).get('ai_processing', {}).get('total_tokens', 0)
                for d in org_docs
            )
            org_openai_cost = sum(
                d.get('training_metadata', {}).get('ai_processing', {}).get('cost_usd', 0.0)
                for d in org_docs
            )
            
            org_storage = len(org_docs) * 0.05
            org_storage_cost = org_storage * 0.18
            
            org_total_cost = org_openai_cost + org_storage_cost
            
            # Revenue
            tier = org.get('subscription_tier', 'trial')
            tier_revenue = {
                'trial': 0,
                'basic': 1000,
                'core': 5000,
                'premium': 15000
            }.get(tier, 0)
            
            org_margin = tier_revenue - org_total_cost
            org_margin_percent = (org_margin / tier_revenue * 100) if tier_revenue > 0 else 0
            
            by_organization.append({
                'org_id': org_id,
                'org_name': org.get('name'),
                'subscription_tier': tier,
                'status': 'active' if org.get('is_active', True) else 'inactive',
                
                'usage': {
                    'users_total': users_total,
                    'documents_uploaded': len(org_docs),
                    'documents_scanned': len([d for d in org_docs if d.get('scan_completed_at')]),
                    'storage_gb': round(org_storage, 2)
                },
                
                'ai_consumption': {
                    'tokens_total': org_tokens
                },
                
                'costs': {
                    'openai_usd': round(org_openai_cost, 2),
                    'storage_usd': round(org_storage_cost, 2),
                    'total_cost_usd': round(org_total_cost, 2)
                },
                
                'revenue': {
                    'subscription_usd': tier_revenue,
                    'total_revenue_usd': tier_revenue
                },
                
                'margin': {
                    'margin_usd': round(org_margin, 2),
                    'margin_percent': round(org_margin_percent, 1)
                },
                
                'health_indicators': {
                    'engagement_score': min(len(org_docs) / 50, 1.0),  # Based on usage
                    'churn_risk': 'low' if len(org_docs) > 10 else 'medium'
                }
            })
        
        # Sort by revenue
        by_organization.sort(key=lambda x: x['revenue']['total_revenue_usd'], reverse=True)
        
        return json_response(200, data={
            'period': {
                'start': start_date,
                'end': datetime.utcnow().isoformat() + 'Z'
            },
            
            'platform_totals': {
                'total_organizations': total_orgs,
                'active_organizations': active_orgs,
                
                'documents': {
                    'uploaded': total_docs_uploaded,
                    'scanned': total_docs_scanned,
                    'approved': total_docs_approved,
                    'rejected': total_docs_rejected,
                    'pending': total_docs_pending
                },
                
                'ai_usage': {
                    'total_tokens': total_tokens,
                    'openai_cost_usd': round(total_openai_cost, 2)
                },
                
                'storage': {
                    'total_gb': round(storage_gb, 2),
                    'storage_cost_usd': round(storage_cost, 2)
                },
                
                'total_cost_usd': round(total_cost, 2),
                'total_revenue_usd': round(total_revenue, 2),
                'gross_margin_percent': round(gross_margin, 1)
            },
            
            'by_organization': by_organization
        })
        
    except Exception as e:
        logger.error(f"❌ Platform usage failed: {e}", exc_info=True)
        return json_response(500, error=str(e))