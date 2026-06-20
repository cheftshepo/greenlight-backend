"""ML Training Data Export API"""
import logging
from datetime import datetime, timedelta
from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_db

logger = logging.getLogger(__name__)

def handle_export_training_data(req, user):
    """
    GET /api/ml/training-data?start_date=2024-01-01&limit=1000&include_text=false
    Export anonymized training data for ML model training
    """
    try:
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        
        # Only Platform SuperAdmins can export training data
        user_roles = user.roles if hasattr(user, 'roles') else user.get('roles', [])
        if 'Platform.SuperAdmin' not in user_roles:
            return json_response(403, error="Only Platform SuperAdmins can export training data")
        
        # Parse params
        start_date = req.params.get('start_date', (datetime.utcnow() - timedelta(days=90)).isoformat() + 'Z')
        limit = int(req.params.get('limit', 1000))
        include_text = req.params.get('include_text', 'false').lower() == 'true'
        
        db = get_db()
        container = db.get_container('documents')
        
        # Query documents with training metadata
        query = """
        SELECT c.id, c.filename, c.jurisdiction, c.document_type, c.target_audience,
               c.training_metadata, c.status, c.approval_status, c.violations_count,
               c.risk_score, c.created_at
        FROM c 
        WHERE c.type = 'document'
        AND c.training_metadata != null
        AND c.created_at >= @start_date
        ORDER BY c.created_at DESC
        OFFSET 0 LIMIT @limit
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@start_date", "value": start_date},
                {"name": "@limit", "value": limit}
            ],
            enable_cross_partition_query=True
        ))
        
        # Anonymize and format for ML
        training_data = []
        for doc in docs:
            metadata = doc.get('training_metadata', {})
            
            # Anonymize: remove identifying info
            record = {
                'record_id': doc.get('id'),  # Keep for tracking
                'jurisdiction': doc.get('jurisdiction'),
                'document_type': doc.get('document_type'),
                'target_audience': doc.get('target_audience'),
                
                # Text stats (useful for ML)
                'text_statistics': metadata.get('text_statistics', {}),
                
                # Content features
                'content_classification': metadata.get('content_classification', {}),
                
                # Compliance outcomes (labels for training)
                'compliance_journey': metadata.get('compliance_journey', {}),
                'final_status': doc.get('status'),
                'approval_status': doc.get('approval_status'),
                'violations_count': doc.get('violations_count', 0),
                'risk_score': doc.get('risk_score', 0),
                
                # AI performance (for model eval)
                'ai_processing': metadata.get('ai_processing', {}),
                
                # Human feedback (ground truth)
                'feedback': metadata.get('feedback', {}),
                
                'collected_at': metadata.get('collected_at')
            }
            
            training_data.append(record)
        
        logger.info(f"📤 Exported {len(training_data)} training records")
        
        return json_response(200, data={
            'training_data': training_data,
            'total_records': len(training_data),
            'period_start': start_date,
            'anonymized': True,
            'export_timestamp': datetime.utcnow().isoformat() + 'Z'
        })
        
    except Exception as e:
        logger.error(f"❌ ML export failed: {e}", exc_info=True)
        return json_response(500, error=str(e))