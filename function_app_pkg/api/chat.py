"""Document AI Chat Endpoint
Handles chat interactions with AI about compliance documents.
Saves conversations to ai_conversations container for audit trail.
"""

import azure.functions as func
import logging
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from function_app_pkg.core.chat_engine import chat_engine
from function_app_pkg.core.database import get_document
from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.ai_conversation_repository import AIConversationRepository

logger = logging.getLogger(__name__)

# Shared repository instance
_conv_repo = None

def _get_conv_repo():
    """Lazy-init conversation repository"""
    global _conv_repo
    if _conv_repo is None:
        _conv_repo = AIConversationRepository()
    return _conv_repo


def handle(req: func.HttpRequest, user: any = None) -> func.HttpResponse:
    """Main endpoint for chat with document AI — saves to AI logs"""
    try:
        # 1. Basic validation
        document_id = req.route_params.get('documentId')
        if not document_id:
            return json_response(400, error="Document ID required")

        # 2. Get user info (handle both dict and object)
        user_email = None
        org_id = None
        user_roles = []
        user_name = ''
        user_id = ''

        if hasattr(user, 'email'):  # Object
            user_email = user.email
            org_id = user.organization_id
            user_roles = getattr(user, 'roles', [])
            user_name = getattr(user, 'name', user.email)
            user_id = getattr(user, 'user_id', getattr(user, 'id', user.email))
        elif isinstance(user, dict):  # Dict
            user_email = user.get('email')
            org_id = user.get('organization_id')
            user_roles = user.get('roles', [])
            user_name = user.get('name', user.get('email', ''))
            user_id = user.get('user_id', user.get('id', user.get('email', '')))

        if not user_email or not org_id:
            return json_response(401, error="Authentication required")

        # 3. Get document
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")

        # 4. Parse request body
        try:
            body = req.get_json() or {}
        except ValueError:
            return json_response(400, error="Invalid JSON")

        message = body.get('message') or body.get('question', '')
        if not message:
            return json_response(400, error="Message required")

        # Optional: conversation_id to continue an existing conversation
        conversation_id = body.get('conversation_id')

        # 5. Call chat engine
        document_data = {
            "filename": doc.get('filename', 'Unknown'),
            "jurisdiction": doc.get('jurisdiction', 'UK'),
            "status": doc.get('status', 'unknown'),
            "extracted_text": doc.get('extracted_text', doc.get('text_content', '')),
            "violations": doc.get('violations', []),
        }

        user_context = {
            "email": user_email,
            "roles": user_roles,
            "organization_id": org_id,
        }

        response = chat_engine.chat(
            message=message,
            document_data=document_data,
            user_context=user_context
        )

        if not response.get('success'):
            return json_response(500, error="AI service failed")

        ai_answer = response.get('response', '')

        # 6. SAVE CONVERSATION TO AI LOGS
        now = datetime.utcnow().isoformat() + 'Z'
        
        user_msg = {
            'role': 'user',
            'content': message,
            'timestamp': now,
        }
        assistant_msg = {
            'role': 'assistant',
            'content': ai_answer,
            'timestamp': now,
            'intent': response.get('intent'),
            'citations': response.get('citations'),
            'suggested_actions': response.get('suggested_actions'),
        }

        saved_conversation_id = None
        conversation_saved = False

        try:
            conv_repo = _get_conv_repo()

            if conversation_id:
                # Continue existing conversation — append messages
                existing = conv_repo.get_conversation_by_id(conversation_id, document_id)
                if existing:
                    existing.messages.append(user_msg)
                    existing.messages.append(assistant_msg)
                    existing.updated_at = now
                    # Upsert back
                    conv_repo.container.upsert_item(existing.to_dict())
                    saved_conversation_id = existing.id
                    conversation_saved = True
                    logger.info(f"✅ Appended to conversation {existing.id}")
                else:
                    # Conversation not found, create new
                    conversation_id = None

            if not conversation_id:
                # Create new conversation
                conv = conv_repo.save_conversation(
                    document_id=document_id,
                    organization_id=org_id,
                    user_id=user_id,
                    user_email=user_email,
                    user_role=user_roles[0] if user_roles else 'unknown',
                    conversation_type='document_review',
                    messages=[user_msg, assistant_msg],
                    ai_model='gpt-4',
                    document_context={
                        'filename': doc.get('filename'),
                        'jurisdiction': doc.get('jurisdiction'),
                        'risk_score': doc.get('risk_score'),
                        'violations_count': doc.get('violations_count', 0),
                    }
                )
                saved_conversation_id = conv.id
                conversation_saved = not conv.id.startswith('conv_error_')
                logger.info(f"✅ New conversation saved: {conv.id}")

        except Exception as save_err:
            logger.error(f"⚠️ Failed to save conversation (chat still works): {save_err}")

        # 7. Return response with conversation tracking
        return json_response(200, data={
            "answer": ai_answer,
            "response": ai_answer,
            "document_id": document_id,
            "conversation_id": saved_conversation_id,
            "conversation_saved": conversation_saved,
            "intent": response.get('intent'),
            "citations": response.get('citations'),
            "suggested_actions": response.get('suggested_actions'),
        })

    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        return json_response(500, error="Internal server error")


def get_chat_history(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """Get chat history for a document.
    
    GET /documents/{documentId}/chat/history
    """
    try:
        document_id = req.route_params.get('documentId')
        
        if not document_id:
            return json_response(400, error="Document ID required")
        
        # Verify document exists and user has access
        doc = get_document(document_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if user:
            user_org_id = (user.organization_id if hasattr(user, 'organization_id') 
                          else user.get('organization_id'))
            if user_org_id and doc.get('organization_id') != user_org_id:
                return json_response(403, error="Access denied")
        
        try:
            conv_repo = _get_conv_repo()
            conversations = conv_repo.get_conversations_by_document(document_id, limit=20)
            
            history = []
            for conv in conversations:
                history.append({
                    'conversation_id': conv.id,
                    'created_at': conv.created_at,
                    'user_email': conv.user_email,
                    'user_role': conv.user_role,
                    'messages': conv.messages,
                    'message_count': len(conv.messages)
                })
            
            return json_response(200, data={
                'document_id': document_id,
                'conversations': history,
                'total': len(history)
            })
            
        except Exception as e:
            logger.error(f"Failed to get chat history: {e}")
            return json_response(500, error="Failed to load chat history")
        
    except Exception as e:
        logger.error(f"Chat history error: {e}", exc_info=True)
        return json_response(500, error=str(e))