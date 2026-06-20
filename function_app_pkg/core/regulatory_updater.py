"""
Manages regulatory updates based on client subscription tiers
"""
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List
from .database import _init, _rules_container

logger = logging.getLogger(__name__)

class RegulatoryUpdater:
    def __init__(self):
        _init()
    
    def add_regulation_update(self, jurisdiction: str, 
                             regulation_data: Dict, 
                             update_type: str = "monthly") -> str:
        """Add new regulation update to the system"""
        update_id = f"reg_update_{jurisdiction}_{datetime.utcnow().strftime('%Y%m%d')}"
        
        update_record = {
            "id": update_id,
            "type": "regulation_update",
            "jurisdiction": jurisdiction,
            "update_type": update_type,
            "effective_date": regulation_data.get("effective_date"),
            "published_date": datetime.utcnow().isoformat() + "Z",
            "changes": regulation_data.get("changes", []),
            "summary": regulation_data.get("summary"),
            "impact_level": regulation_data.get("impact_level", "medium"),
            "documents": regulation_data.get("documents", []),
            "status": "pending_review"
        }
        
        _rules_container.create_item(body=update_record)
        logger.info(f"Added regulation update: {update_id}")
        return update_id
    
    def apply_update_to_client(self, client_id: str, update_id: str) -> bool:
        """Apply a regulatory update to a specific client's rules"""
        # Get the update
        query = "SELECT * FROM c WHERE c.id = @update_id"
        items = list(_rules_container.query_items(
            query=query,
            parameters=[{"name": "@update_id", "value": update_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            logger.error(f"Update not found: {update_id}")
            return False
        
        update = items[0]
        
        # Apply based on client's subscription tier
        # Get client's current rules and merge updates
        client_rules = self._get_client_rules(client_id, update["jurisdiction"])
        
        if client_rules:
            # Merge updates
            updated_rules = self._merge_updates(client_rules, update["changes"])
            
            # Save updated rules
            self._save_client_rules(client_id, update["jurisdiction"], updated_rules)
            
            # Log the update
            self._log_client_update(client_id, update_id)
            
            logger.info(f"Applied update {update_id} to client {client_id}")
            return True
        
        return False
    
    def get_pending_updates(self, jurisdiction: str = None) -> List[Dict]:
        """Get all pending regulatory updates"""
        query = "SELECT * FROM c WHERE c.type = 'regulation_update' AND c.status = 'pending_review'"
        params = []
        
        if jurisdiction:
            query += " AND c.jurisdiction = @jurisdiction"
            params.append({"name": "@jurisdiction", "value": jurisdiction})
        
        query += " ORDER BY c.published_date DESC"
        
        return list(_rules_container.query_items(
            query=query,
            parameters=params if params else None,
            enable_cross_partition_query=True
        ))
    
    def get_update_schedule(self, client_tier: str) -> Dict:
        """Get update schedule based on subscription tier"""
        schedules = {
            "basic": {"frequency": "bi-yearly", "next_update": self._calculate_next_update("bi-yearly")},
            "core": {"frequency": "quarterly", "next_update": self._calculate_next_update("quarterly")},
            "premium": {"frequency": "monthly", "next_update": self._calculate_next_update("monthly")}
        }
        
        return schedules.get(client_tier.lower(), schedules["basic"])
    
    def _calculate_next_update(self, frequency: str) -> str:
        """Calculate next update date"""
        now = datetime.utcnow()
        
        if frequency == "monthly":
            next_date = now + timedelta(days=30)
        elif frequency == "quarterly":
            next_date = now + timedelta(days=90)
        elif frequency == "bi-yearly":
            next_date = now + timedelta(days=182)
        else:
            next_date = now + timedelta(days=365)
        
        return next_date.isoformat() + "Z"
    
    def _get_client_rules(self, client_id: str, jurisdiction: str) -> Dict:
        """Get client-specific rules (simplified for POC)"""
        # TODO: Implement client-specific rule storage
        return None
    
    def _save_client_rules(self, client_id: str, jurisdiction: str, rules: Dict):
        """Save client-specific rules"""
        # TODO: Implement
        pass
    
    def _log_client_update(self, client_id: str, update_id: str):
        """Log that an update was applied to a client"""
        log_record = {
            "id": f"client_update_{client_id}_{datetime.utcnow().isoformat()}",
            "type": "client_update_log",
            "client_id": client_id,
            "update_id": update_id,
            "applied_at": datetime.utcnow().isoformat() + "Z",
            "status": "applied"
        }
        
        _rules_container.create_item(body=log_record)
    
    def _merge_updates(self, existing_rules: Dict, changes: List[Dict]) -> Dict:
        """Merge regulatory changes into existing rules"""
        updated_rules = existing_rules.copy()
        
        for change in changes:
            rule_id = change.get("rule_id")
            action = change.get("action")  # add, modify, delete
            
            if action == "add":
                updated_rules[rule_id] = change.get("new_rule")
            elif action == "modify" and rule_id in updated_rules:
                updated_rules[rule_id].update(change.get("updates", {}))
            elif action == "delete" and rule_id in updated_rules:
                del updated_rules[rule_id]
        
        return updated_rules