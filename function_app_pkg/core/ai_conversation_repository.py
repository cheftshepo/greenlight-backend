# function_app_pkg/core/ai_conversation_repository.py
"""
AI CONVERSATION REPOSITORY - PRODUCTION READY
Save AI chat conversations for compliance officer review
"""

import logging
import uuid
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field

# Import database functions
from .database import get_db

logger = logging.getLogger(__name__)


@dataclass
class AIConversation:
    """AI conversation for audit trail"""
    id: str
    document_id: str
    organization_id: str
    conversation_type: str  # 'document_review', 'compliance_chat', 'briefing_analysis'
    user_id: str
    user_email: str
    user_role: str
    messages: List[Dict]
    ai_model: str
    questionnaire_context: Optional[Dict] = None
    document_context: Optional[Dict] = None
    decision_influenced: bool = False
    related_decision_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for database storage"""
        d = asdict(self)
        d["type"] = "ai_conversation"
        d["partition_key"] = self.document_id
        return d
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AIConversation':
        """Create from dictionary, stripping Cosmos DB metadata"""
        # Remove Cosmos DB metadata
        clean_data = {
            k: v for k, v in data.items() 
            if k not in [
                'type', 'partition_key', '_rid', '_self', '_etag', 
                '_attachments', '_ts', 'ttl'
            ]
        }
        return cls(**clean_data)


class AIConversationRepository:
    """Repository for saving and retrieving AI conversations"""
    
    def __init__(self):
        self.db = get_db()
        self.container_name = "ai_conversations"
        self.container = self.db.get_container(self.container_name)
    
    def _sanitize_messages(self, messages: List[Dict]) -> List[Dict]:
        """Sanitize messages to prevent injection and limit size"""
        sanitized = []
        for msg in messages[-10:]:  # Keep only last 10 messages
            if not isinstance(msg, dict):
                continue
                
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            
            # Validate role
            if role not in ['user', 'assistant', 'system']:
                continue
            
            # Limit content size and sanitize
            if content and isinstance(content, str):
                content = content[:5000]  # Character limit
                # Basic XSS prevention
                content = content.replace('<script>', '').replace('</script>', '')
                
                sanitized.append({
                    'role': role,
                    'content': content,
                    'timestamp': msg.get('timestamp', datetime.utcnow().isoformat() + 'Z')
                })
        
        return sanitized
    
    def save_conversation(
        self,
        document_id: str,
        organization_id: str,
        user_id: str,
        user_email: str,
        user_role: str,
        conversation_type: str,
        messages: List[Dict],
        ai_model: str,
        questionnaire_context: Optional[Dict] = None,
        document_context: Optional[Dict] = None
    ) -> AIConversation:
        """Save an AI conversation to Cosmos DB"""
        
        try:
            # Create conversation object
            conversation = AIConversation(
                id=f"conv_{uuid.uuid4().hex[:12]}",
                document_id=document_id,
                organization_id=organization_id,
                conversation_type=conversation_type,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                messages=self._sanitize_messages(messages),
                ai_model=ai_model,
                questionnaire_context=questionnaire_context,
                document_context=document_context
            )
            
            # Save to database
            self.container.create_item(conversation.to_dict())
            
            logger.info(f"✅ AI conversation saved: {conversation.id} for doc {document_id}")
            return conversation
            
        except Exception as e:
            logger.error(f"❌ Failed to save AI conversation: {e}")
            # Create a conversation object anyway (without saving) so the chat can continue
            return AIConversation(
                id=f"conv_error_{uuid.uuid4().hex[:8]}",
                document_id=document_id,
                organization_id=organization_id,
                conversation_type=conversation_type,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                messages=messages,
                ai_model=ai_model,
                questionnaire_context=questionnaire_context,
                document_context=document_context
            )
    
    def get_conversations_by_document(
        self, 
        document_id: str, 
        organization_id: str = None,
        limit: int = 50
    ) -> List[AIConversation]:
        """Get all AI conversations for a document"""
        try:
            query = """
                SELECT * FROM c 
                WHERE c.document_id = @document_id 
                AND c.type = 'ai_conversation'
            """
            
            parameters = [{"name": "@document_id", "value": document_id}]
            
            if organization_id:
                query += " AND c.organization_id = @org_id"
                parameters.append({"name": "@org_id", "value": organization_id})
            
            query += " ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
            parameters.append({"name": "@limit", "value": limit})
            
            items = list(self.container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            
            conversations = []
            for item in items:
                try:
                    conversations.append(AIConversation.from_dict(item))
                except Exception as e:
                    logger.warning(f"Failed to parse conversation: {e}")
            
            return conversations
            
        except Exception as e:
            logger.error(f"Failed to get conversations: {e}")
            return []
    
    def get_conversation_by_id(self, conversation_id: str, document_id: str) -> Optional[AIConversation]:
        """Get a specific conversation by ID"""
        try:
            item = self.container.read_item(conversation_id, partition_key=document_id)
            return AIConversation.from_dict(item)
        except Exception as e:
            logger.error(f"Failed to get conversation {conversation_id}: {e}")
            return None
    
    def link_to_decision(
        self, 
        conversation_id: str, 
        document_id: str, 
        decision_id: str, 
        decision_type: str
    ) -> bool:
        """Link conversation to a decision (approval/rejection)"""
        try:
            # Get conversation
            conversation = self.get_conversation_by_id(conversation_id, document_id)
            if not conversation:
                return False
            
            # Update with decision info
            conversation.decision_influenced = True
            conversation.related_decision_id = decision_id
            conversation.updated_at = datetime.utcnow().isoformat() + "Z"
            
            # Save back
            self.container.upsert_item(conversation.to_dict())
            logger.info(f"✅ Linked conversation {conversation_id} to decision {decision_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to link conversation to decision: {e}")
            return False
    
    def delete_conversation(self, conversation_id: str, document_id: str) -> bool:
        """Delete a conversation"""
        try:
            self.container.delete_item(conversation_id, partition_key=document_id)
            logger.info(f"✅ Deleted conversation {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete conversation {conversation_id}: {e}")
            return False