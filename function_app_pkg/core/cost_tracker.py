# ENHANCED cost_tracker.py
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from .database import get_db
import asyncio
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)

# More realistic Azure pricing (update with your actual rates)
COSTS = {
    'openai': {
        'gpt-4': {'input_per_1k': 0.03, 'output_per_1k': 0.06},
        'gpt-4-turbo': {'input_per_1k': 0.01, 'output_per_1k': 0.03},
        'gpt-35-turbo': {'input_per_1k': 0.001, 'output_per_1k': 0.002},
    },
    'azure_ai': {
        'pii_detection': {'per_doc': 0.001, 'per_char': 0.000001},
        'language_detection': {'per_call': 0.0001},
        'translation': {'per_char': 0.00001},
    },
    'cosmos': {
        'ru_per_million': 0.008,
        'storage_gb_month': 0.0025,
    },
    'function': {
        'execution_ms': 0.000000016,  # per GB-second
        'memory_gb': 1.0,  # Assuming 1GB memory allocation
    },
    'storage': {
        'blob_read_10k': 0.000005,
        'blob_write_10k': 0.00001,
    }
}

# Thread-safe accumulator for batch writes
_cost_buffer = defaultdict(list)
_buffer_lock = threading.Lock()
_last_flush = datetime.utcnow()

class CostAccumulator:
    """Thread-safe cost accumulator for batch processing"""
    def __init__(self, flush_interval_seconds: int = 30, batch_size: int = 100):
        self.flush_interval = flush_interval_seconds
        self.batch_size = batch_size
        self._buffer = defaultdict(list)
        self._lock = threading.Lock()
        self._last_flush = datetime.utcnow()
    
    def add_cost(self, org_id: str, cost_event: dict):
        with self._lock:
            self._buffer[org_id].append(cost_event)
            
            # Check if we should flush
            now = datetime.utcnow()
            buffer_size = sum(len(v) for v in self._buffer.values())
            
            if (now - self._last_flush).seconds >= self.flush_interval or buffer_size >= self.batch_size:
                self._flush_to_db()
    
    def _flush_to_db(self):
        try:
            db = get_db()
            container = db.get_container('cost_events')
            
            all_events = []
            for org_id, events in self._buffer.items():
                all_events.extend(events)
            
            if all_events:
                # Batch create items
                for event in all_events:
                    container.create_item(body=event)
                
                logger.info(f"📊 Flushed {len(all_events)} cost events to database")
            
            # Clear buffer
            self._buffer.clear()
            self._last_flush = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"❌ Cost batch flush failed: {e}")
            # Keep events in buffer for retry

# Global accumulator instance
_cost_accumulator = CostAccumulator()

def log_cost_event(
    org_id: str,
    user_email: str,
    resource_type: str,
    usage: Dict,
    document_id: Optional[str] = None,
    operation: Optional[str] = None,
    metadata: Optional[Dict] = None
):
    """Log a cost event with detailed breakdown"""
    try:
        # Calculate cost based on resource type
        cost_usd = _calculate_cost(resource_type, usage)
        
        # Build cost event with metadata
        cost_event = {
            'id': f"cost_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}",
            'type': 'cost_event',
            'organization_id': org_id,
            'user_email': user_email,
            'resource_type': resource_type,
            'operation': operation or resource_type,
            'usage': usage,
            'cost_usd': round(cost_usd, 6),
            'document_id': document_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'metadata': metadata or {},
            'partition_key': f"{org_id}_{datetime.utcnow().strftime('%Y%m')}"  # For partitioning
        }
        
        # Add to accumulator for batch processing
        _cost_accumulator.add_cost(org_id, cost_event)
        
        # Also log to console for real-time monitoring
        if cost_usd > 0.01:  # Only log significant costs
            logger.info(f"💰 Cost: ${cost_usd:.4f} | {resource_type} | Doc: {document_id or 'N/A'}")
        
        return cost_usd
        
    except Exception as e:
        logger.error(f"❌ Cost logging failed: {e}")
        return 0

def _calculate_cost(resource_type: str, usage: Dict) -> float:
    """Calculate cost based on usage metrics"""
    cost = 0
    
    if resource_type == 'openai':
        model = usage.get('model', 'gpt-4')
        model_costs = COSTS['openai'].get(model, COSTS['openai']['gpt-4'])
        
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        
        cost = (
            (input_tokens / 1000) * model_costs['input_per_1k'] +
            (output_tokens / 1000) * model_costs['output_per_1k']
        )
        
    elif resource_type == 'azure_ai':
        service = usage.get('service', 'pii_detection')
        service_costs = COSTS['azure_ai'].get(service, {})
        
        if service == 'pii_detection':
            cost = (
                service_costs.get('per_doc', 0.001) +
                (usage.get('characters', 0) * service_costs.get('per_char', 0))
            )
        elif service == 'translation':
            cost = usage.get('characters', 0) * service_costs.get('per_char', 0.00001)
            
    elif resource_type == 'cosmos':
        rus = usage.get('rus', 0)
        cost = (rus / 1_000_000) * COSTS['cosmos']['ru_per_million']
        
    elif resource_type == 'function':
        # Calculate based on execution time and memory
        duration_ms = usage.get('duration_ms', 0)
        memory_gb = usage.get('memory_gb', COSTS['function']['memory_gb'])
        
        # Convert ms to seconds
        duration_seconds = duration_ms / 1000
        gb_seconds = duration_seconds * memory_gb
        
        cost = gb_seconds * COSTS['function']['execution_ms'] * 1000  # Convert to per GB-second
        
    elif resource_type == 'storage':
        operation = usage.get('operation', 'read')
        size_bytes = usage.get('size_bytes', 0)
        
        # Convert bytes to 10k operations
        operations_10k = max(1, size_bytes / 10000)
        
        if operation == 'read':
            cost = operations_10k * COSTS['storage']['blob_read_10k']
        else:  # write
            cost = operations_10k * COSTS['storage']['blob_write_10k']
    
    return cost

def get_organization_costs(org_id: str, start_date: str = None, end_date: str = None) -> Dict:
    """Get cost summary for an organization"""
    try:
        db = get_db()
        container = db.get_container('cost_events')
        
        # Build query
        query = f"""
        SELECT 
            c.resource_type,
            SUM(c.cost_usd) as total_cost,
            COUNT(1) as event_count,
            MIN(c.timestamp) as first_event,
            MAX(c.timestamp) as last_event
        FROM c
        WHERE c.organization_id = @org_id
        AND c.type = 'cost_event'
        """
        
        parameters = [{"name": "@org_id", "value": org_id}]
        
        if start_date:
            query += " AND c.timestamp >= @start_date"
            parameters.append({"name": "@start_date", "value": start_date})
        
        if end_date:
            query += " AND c.timestamp <= @end_date"
            parameters.append({"name": "@end_date", "value": end_date})
        
        query += " GROUP BY c.resource_type"
        
        results = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        # Calculate totals
        total_cost = sum(r['total_cost'] for r in results)
        
        return {
            'period': {
                'start': start_date,
                'end': end_date
            },
            'total_cost': round(total_cost, 4),
            'by_resource': results,
            'estimated_monthly': round(total_cost * 30, 2) if start_date and end_date else None,
            'recommendations': _generate_cost_recommendations(results)
        }
        
    except Exception as e:
        logger.error(f"❌ Cost query failed: {e}")
        return {'error': str(e)}

def _generate_cost_recommendations(cost_data: list) -> list:
    """Generate cost optimization recommendations"""
    recommendations = []
    
    for item in cost_data:
        resource = item['resource_type']
        cost = item['total_cost']
        
        if resource == 'openai' and cost > 50:
            recommendations.append({
                'resource': 'openai',
                'issue': 'High OpenAI costs',
                'recommendation': 'Consider using gpt-3.5-turbo for non-critical tasks',
                'potential_savings': f"${round(cost * 0.3, 2)}/month (30%)",
                'priority': 'high'
            })
        
        elif resource == 'function' and cost > 20:
            recommendations.append({
                'resource': 'function',
                'issue': 'High compute costs',
                'recommendation': 'Optimize function execution time and memory allocation',
                'potential_savings': f"${round(cost * 0.2, 2)}/month (20%)",
                'priority': 'medium'
            })
    
    return recommendations[:5]  # Return top 5 recommendations

# Usage in your scan file:
"""
# Add cost tracking throughout:

# 1. AI PII analysis
log_cost_event(
    org_id=org_id,
    user_email=user_email,
    resource_type='openai',
    usage={
        'input_tokens': response.usage.prompt_tokens,
        'output_tokens': response.usage.completion_tokens,
        'model': 'gpt-4'
    },
    document_id=doc_id,
    operation='pii_analysis',
    metadata={'document_type': document_type}
)

# 2. Language service
log_cost_event(
    org_id=org_id,
    user_email=user_email,
    resource_type='azure_ai',
    usage={
        'service': 'pii_detection',
        'characters': len(text),
        'documents': 1
    },
    document_id=doc_id
)

# 3. Cosmos DB operations
log_cost_event(
    org_id=org_id,
    user_email=user_email,
    resource_type='cosmos',
    usage={'rus': 50},  # Estimated RU consumption
    document_id=doc_id
)
"""