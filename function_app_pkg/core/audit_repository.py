"""
Complete audit trail for compliance investigations
FIXED: Updated log_action signature to accept organization_id
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)

class AuditRepository:
    def __init__(self):
        self.container = None
        self._initialized = False
    
    def _ensure_initialized(self):
        if self._initialized:
            return
        try:
            from .database import get_audits_container
            self.container = get_audits_container()
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize audit container: {e}")
            raise
    

    def log_action(
        self, 
        action_type: str, 
        document_id: str = None,
        user_id: str = None, 
        user_email: str = None,  # ← ADD THIS PARAMETER
        user_role: str = None,
        organization_id: str = None,
        details: Dict = None,
        metadata: Dict = None
    ) -> Dict:
        """Log any action for full audit trail"""
        self._ensure_initialized()
        
        # Generate partition key
        partition_key = document_id or organization_id or "system"
        
        audit_record = {
            "id": f"audit_{uuid.uuid4().hex[:12]}_{int(datetime.utcnow().timestamp())}",
            "type": "audit_log",
            "document_id": document_id or "",
            "organization_id": organization_id or "",
            "partition_key": partition_key,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action_type": action_type,
            "user_id": user_id or "system",
            "user_email": user_email or (user_id if '@' in str(user_id) else ''),  # ← ADD THIS
            "user_role": user_role or "system",
            "details": details or {},
            "metadata": metadata or {}
        }
        
        # Try to extract email from metadata if not provided
        if not audit_record.get('user_email') and metadata:
            if 'uploaded_by' in metadata:
                audit_record['user_email'] = metadata['uploaded_by']
            elif 'user_email' in metadata:
                audit_record['user_email'] = metadata['user_email']
        
        try:
            created = self.container.create_item(body=audit_record)
            logger.info(f"✅ Audit log created: {action_type} by {audit_record.get('user_email', 'Unknown')}")
            return created
        except Exception as e:
            logger.error(f"❌ Failed to create audit log: {e}")
            return audit_record


    def get_document_history(self, document_id: str) -> List[Dict]:
        """Get complete audit history for a document"""
        self._ensure_initialized()
        
        query = """
        SELECT * FROM c 
        WHERE c.document_id = @doc_id
        ORDER BY c.timestamp DESC
        """
        params = [{"name": "@doc_id", "value": document_id}]
        
        try:
            audit_logs = list(self.container.query_items(
                query=query,
                parameters=params,
                partition_key=document_id,
                enable_cross_partition_query=False
            ))
            return audit_logs
        except Exception as e:
            logger.error(f"Failed to get document history: {e}")
            # Try cross-partition query
            try:
                return list(self.container.query_items(
                    query=query,
                    parameters=params,
                    enable_cross_partition_query=True
                ))
            except:
                return []
    
    def get_org_audit_logs(
        self, 
        organization_id: str,
        action_type: str = None,
        date_from: str = None,
        date_to: str = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get audit logs for an organization"""
        self._ensure_initialized()
        
        query = "SELECT * FROM c WHERE c.organization_id = @org_id"
        params = [{"name": "@org_id", "value": organization_id}]
        
        if action_type:
            query += " AND c.action_type = @action_type"
            params.append({"name": "@action_type", "value": action_type})
        
        if date_from:
            query += " AND c.timestamp >= @date_from"
            params.append({"name": "@date_from", "value": date_from})
        
        if date_to:
            query += " AND c.timestamp <= @date_to"
            params.append({"name": "@date_to", "value": date_to})
        
        query += f" ORDER BY c.timestamp DESC OFFSET 0 LIMIT {limit}"
        
        try:
            return list(self.container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
        except Exception as e:
            logger.error(f"Failed to get org audit logs: {e}")
            return []
    
    def search_audit_logs(
        self, 
        client_id: str = None,
        action_type: str = None,
        date_from: str = None,
        date_to: str = None,
        document_id: str = None,
        organization_id: str = None
    ) -> List[Dict]:
        """Search audit logs for investigations"""
        self._ensure_initialized()
        
        # If we have document_id, use partition query
        if document_id:
            return self.get_document_history(document_id)
        
        # Otherwise use cross-partition search
        query = "SELECT * FROM c WHERE c.type = 'audit_log'"
        params = []
        
        if organization_id:
            query += " AND c.organization_id = @org_id"
            params.append({"name": "@org_id", "value": organization_id})
        
        if client_id:
            query += " AND c.metadata.client_id = @client_id"
            params.append({"name": "@client_id", "value": client_id})
        
        if action_type:
            query += " AND c.action_type = @action_type"
            params.append({"name": "@action_type", "value": action_type})
        
        if date_from:
            query += " AND c.timestamp >= @date_from"
            params.append({"name": "@date_from", "value": date_from})
        
        if date_to:
            query += " AND c.timestamp <= @date_to"
            params.append({"name": "@date_to", "value": date_to})
        
        query += " ORDER BY c.timestamp DESC"
        
        try:
            return list(self.container.query_items(
                query=query,
                parameters=params if params else None,
                enable_cross_partition_query=True
            ))
        except Exception as e:
            logger.error(f"Failed to search audit logs: {e}")
            return []
