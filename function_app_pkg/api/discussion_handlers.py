"""
Document Discussions handlers with AI Contribution & Legal Advisory integration
===============================================================================
UPDATED: AI contribution now uses ChatEngine for full RAG-enhanced context
including real regulatory citations, full document text, all violations,
legal advisory, questionnaire answers, and intent-aware responses.
"""

import azure.functions as func
import logging
import re
import uuid
import json
from datetime import datetime
from typing import Dict, List, Optional

from ..shared.http_utils import json_response
from ..core.database import (
    get_document,
    get_container,
    create_notification,
    get_user_by_email,
    update_document,
)
from ..core.ai_service import get_openai_client

logger = logging.getLogger(__name__)


def _get_user_attr(user, attr: str, default=None):
    if user is None:
        return default
    if hasattr(user, attr):
        return getattr(user, attr)
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def _extract_document_id(req: func.HttpRequest) -> Optional[str]:
    """Extract documentId from route params or URL."""
    doc_id = req.route_params.get('documentId') or req.route_params.get('document_id')
    if doc_id:
        return doc_id
    url_parts = req.url.split('/')
    try:
        idx = url_parts.index('documents') + 1
        return url_parts[idx]
    except (ValueError, IndexError):
        return None


def _extract_mentions(content: str) -> List[str]:
    """Extract @email mentions from content."""
    email_pattern = r'@([\w.+-]+@[\w-]+\.[\w.-]+)'
    mentions = re.findall(email_pattern, content)
    return [m.lower() for m in mentions]


def _notify_mentions(mentions: List[str], org_id: str, document_id: str, 
                     document_name: str, author_name: str, content_preview: str):
    """Create notifications for mentioned users."""
    for email in mentions:
        try:
            create_notification({
                'organization_id': org_id,
                'recipient_email': email,
                'notification_type': 'mention',
                'title': f'{author_name} mentioned you in a discussion',
                'message': f'On document "{document_name}": {content_preview[:100]}...',
                'document_id': document_id,
                'created_by': author_name,
            })
        except Exception as e:
            logger.warning(f"Failed to notify {email}: {e}")


# =============================================================================
# LIST DISCUSSIONS
# =============================================================================

def handle_list_discussions(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/discussions
    List all discussions for a document (includes user comments, AI contributions, legal advisory).
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        document_id = _extract_document_id(req)
        
        if not document_id:
            return json_response(400, error="document_id required")
        
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        container = get_container('documents')
        
        query = """
        SELECT * FROM c
        WHERE c.organization_id = @org_id
        AND c.type = 'discussion'
        AND c.document_id = @doc_id
        ORDER BY c.created_at ASC
        """
        
        all_discussions = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@doc_id", "value": document_id},
            ],
            partition_key=org_id
        ))
        
        # Build threaded structure
        threads = []
        replies_map: Dict[str, List] = {}
        
        for disc in all_discussions:
            parent_id = disc.get('parent_id')
            if not parent_id:
                threads.append(disc)
                replies_map[disc['id']] = []
            else:
                if parent_id not in replies_map:
                    replies_map[parent_id] = []
                replies_map[parent_id].append(disc)
        
        for thread in threads:
            thread['replies'] = replies_map.get(thread['id'], [])
            thread['reply_count'] = len(thread['replies'])
        
        return json_response(200, data={
            'discussions': threads,
            'total': len(threads),
            'total_with_replies': len(all_discussions),
        })
        
    except Exception as e:
        logger.error(f"❌ List discussions failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# CREATE DISCUSSION
# =============================================================================

def handle_create_discussion(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions
    Create a new discussion thread.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_name = _get_user_attr(user, 'name', user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        
        document_id = _extract_document_id(req)
        if not document_id:
            return json_response(400, error="document_id required")
        
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        content = (body.get('content') or body.get('comment') or '').strip()
        parent_id = body.get('parent_id')
        
        if not content:
            return json_response(400, error="Discussion content is required")
        
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        now = datetime.utcnow()
        mentions = _extract_mentions(content)
        
        discussion = {
            'id': f"disc_{uuid.uuid4().hex[:12]}",
            'type': 'discussion',
            'organization_id': org_id,
            'document_id': document_id,
            'parent_id': parent_id,
            'author_email': user_email,
            'author_name': user_name,
            'author_role': user_roles[0] if user_roles else '',
            'content': content,
            'mentions': mentions,
            'created_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
            'is_resolved': False,
            'resolved_by': None,
            'resolved_at': None,
            'reply_count': 0,
            'is_ai_generated': False,
            'is_legal_advisory': False,
        }
        
        container = get_container('documents')
        container.create_item(body=discussion)
        
        # Update parent reply count if reply
        if parent_id:
            try:
                parent_query = """
                SELECT * FROM c 
                WHERE c.id = @id AND c.type = 'discussion' AND c.organization_id = @org_id
                """
                parents = list(container.query_items(
                    query=parent_query,
                    parameters=[
                        {"name": "@id", "value": parent_id},
                        {"name": "@org_id", "value": org_id},
                    ],
                    partition_key=org_id
                ))
                if parents:
                    parent_disc = parents[0]
                    parent_disc['reply_count'] = parent_disc.get('reply_count', 0) + 1
                    parent_disc['updated_at'] = now.isoformat() + 'Z'
                    container.upsert_item(parent_disc)
            except Exception as e:
                logger.warning(f"Failed to update parent reply count: {e}")
        
        # Notify mentioned users
        if mentions:
            _notify_mentions(
                mentions, org_id, document_id,
                doc.get('filename', 'Unknown document'),
                user_name, content
            )
        
        # Update document activity
        try:
            update_document(document_id, {
                'last_activity_at': now.isoformat() + 'Z',
            }, org_id)
        except:
            pass
        
        logger.info(f"✅ Discussion created on doc {document_id} by {user_email}")
        
        return json_response(201, data={
            'discussion': discussion,
            'message': '✅ Discussion posted'
        })
        
    except Exception as e:
        logger.error(f"❌ Create discussion failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# AI CONTRIBUTION — ENHANCED: Uses ChatEngine for RAG + full context
# =============================================================================

def _build_discussion_thread_text(discussions: list) -> str:
    """Format discussion thread for AI context."""
    if not discussions:
        return "(No discussion messages yet)"
    
    lines = []
    for d in discussions[-20:]:  # Last 20 messages
        author = d.get('author_name') or d.get('author_email', 'Unknown')
        tag = ' [AI]' if d.get('is_ai_generated') else ''
        tag += ' [LEGAL]' if d.get('is_legal_advisory') else ''
        lines.append(f"[{author}{tag}]: {d.get('content', '')}")
    return "\n\n".join(lines)


def handle_ai_contribution(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions/ai-contribution
    
    ENHANCED: Uses the RAG ChatEngine for full document context + real
    regulatory citations. The ChatEngine provides:
    - Full document text (extracted_text)
    - All violations with details
    - RAG-retrieved regulatory citations from Azure AI Search
    - Intent-aware response tailoring
    - Jurisdiction-specific guidance
    - Questionnaire context (if available)
    
    Optional body:
    {
        "focus": "violations|legal|remediation|general",
        "question": "optional specific question to address"
    }
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        user_roles = _get_user_attr(user, 'roles', [])
        
        document_id = _extract_document_id(req)
        if not document_id:
            return json_response(400, error="document_id required")
        
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # Parse optional body
        focus = 'general'
        specific_question = ''
        try:
            body = req.get_json()
            focus = body.get('focus', 'general')
            specific_question = body.get('question', '').strip()
        except:
            pass

        container = get_container('documents')
        
        # Get ALL existing discussions (including AI ones for thread context)
        query = """
        SELECT * FROM c
        WHERE c.organization_id = @org_id
        AND c.type = 'discussion'
        AND c.document_id = @doc_id
        ORDER BY c.created_at ASC
        """
        
        all_discussions = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@doc_id", "value": document_id},
            ],
            partition_key=org_id
        ))

        # Filter human-only for the "nothing to analyze" check
        human_discussions = [d for d in all_discussions if not d.get('is_ai_generated')]
        violations = doc.get('violations', [])
        
        # Allow AI contribution even with zero discussions if document has content
        if not human_discussions and not violations and not doc.get('legal_advisory'):
            return json_response(400, error="No discussions or document context to analyze")
        
        # ─── USE CHATENGINE for RAG-enhanced response ───────────────────────
        # The ChatEngine automatically:
        #   1. Builds full document context (text, violations, risk score, etc.)
        #   2. Retrieves real regulatory citations via Azure AI Search (RAG)
        #   3. Analyzes intent for tailored response
        #   4. Includes questionnaire context if available
        
        discussion_thread = _build_discussion_thread_text(all_discussions)
        
        # Build the message to send to ChatEngine
        focus_prompts = {
            'violations': 'Analyze the detected violations in detail: explain their severity, regulatory impact, and provide specific remediation steps with before/after text examples.',
            'legal': 'Provide legal advisory analysis: review the legal recommendation, cited regulations, and suggest next steps from a compliance perspective.',
            'remediation': 'Focus on actionable fixes: for each violation, provide specific replacement text that would make the document compliant.',
            'general': 'Provide a comprehensive compliance perspective addressing key issues. Be specific about violations, regulatory requirements, and next steps.',
        }
        
        ai_message = focus_prompts.get(focus, focus_prompts['general'])
        
        if specific_question:
            ai_message = f"{specific_question}\n\nAdditional context: {ai_message}"
        
        if discussion_thread and discussion_thread != "(No discussion messages yet)":
            ai_message += f"\n\nThe team discussion so far:\n{discussion_thread}\n\nAddress unresolved concerns from the discussion."
        
        # Prepare document_data dict for ChatEngine (it expects this shape)
        document_data = {
            'filename': doc.get('filename', 'Unknown'),
            'jurisdiction': doc.get('jurisdiction', 'UK'),
            'status': doc.get('status', 'unknown'),
            'compliance_outcome': doc.get('compliance_outcome', 'pending'),
            'risk_score': doc.get('risk_score', 0),
            'violations_count': len(violations),
            'violations': violations,
            'extracted_text': doc.get('extracted_text') or doc.get('text_content', ''),
            'document_summary': doc.get('document_summary', {}),
            'legal_advisory': doc.get('legal_advisory', ''),
            'legal_recommendation': doc.get('legal_recommendation', ''),
            'legal_reviewed_by': doc.get('legal_reviewed_by', ''),
            'cited_regulations': doc.get('cited_regulations', []),
            'legal_conditions': doc.get('legal_conditions', []),
            'legal_recommendations': doc.get('legal_recommendations', []),
            'recommendations': doc.get('recommendations', []),
            'briefing': doc.get('briefing', {}),
            'answers': doc.get('answers', []),
            'compliance_questions': doc.get('compliance_questions', []),
            'assigned_to': doc.get('assigned_to', ''),
            'assigned_to_name': doc.get('assigned_to_name', ''),
            'assignment_priority': doc.get('assignment_priority', ''),
            'assignment_deadline': doc.get('assignment_deadline', ''),
            'ticket_id': doc.get('ticket_id', ''),
        }
        
        user_context = {
            'email': user_email,
            'roles': user_roles,
        }
        
        # Questionnaire context if answers exist
        questionnaire_context = None
        if doc.get('answers') and doc.get('compliance_questions'):
            answers = doc.get('answers', [])
            questions = doc.get('compliance_questions', [])
            yes_count = sum(1 for a in answers if str(a.get('answer', '')).lower() in ['yes', 'true'])
            no_count = sum(1 for a in answers if str(a.get('answer', '')).lower() in ['no', 'false'])
            uncertain_count = sum(1 for a in answers if str(a.get('answer', '')).lower() in ['uncertain', 'unsure', 'maybe'])
            
            questionnaire_context = {
                'compliance_maturity': 'beginner' if no_count > yes_count else 'intermediate',
                'risk_profile': 'risky' if no_count > 3 else 'moderate',
                'confidence_level': 'low' if uncertain_count > 2 else 'medium',
                'answer_summary': {'yes': yes_count, 'no': no_count, 'uncertain': uncertain_count},
                'focus_areas': [],
                'knowledge_gaps': [],
                'suggested_approach': 'urgent_detailed' if no_count > 3 else 'balanced',
            }
        
        # Try ChatEngine first (RAG-enhanced), fall back to direct OpenAI
        chat_result = None
        used_rag = False
        
        try:
            from ..core.chat_engine import chat_engine
            
            if chat_engine and chat_engine.openai_client:
                chat_result = chat_engine.chat(
                    message=ai_message,
                    document_data=document_data,
                    conversation_history=None,  # Fresh analysis, no prior chat history
                    user_context=user_context,
                    questionnaire_context=questionnaire_context,
                )
                used_rag = True
                logger.info(f"✅ AI contribution via ChatEngine (RAG={'enabled' if chat_engine.knowledge_base else 'disabled'})")
            else:
                raise RuntimeError("ChatEngine not initialized")
                
        except Exception as chat_err:
            logger.warning(f"⚠️ ChatEngine unavailable ({chat_err}), falling back to direct OpenAI")
            
            # ─── FALLBACK: Direct OpenAI call with manually built context ────
            import os
            _deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4.1')
            
            try:
                client = get_openai_client()
                
                # Build a rich prompt manually (subset of what ChatEngine does)
                violations_text = ""
                for i, v in enumerate(violations[:10], 1):
                    violations_text += f"\n[{i}] {v.get('severity', 'MEDIUM').upper()} — {v.get('description', v.get('rule_description', 'N/A'))}"
                    violations_text += f"\n    Matched: \"{(v.get('matched_text') or '')[:150]}\""
                    violations_text += f"\n    Rule: {v.get('rule_id', v.get('regulatory_reference', 'N/A'))}"
                    violations_text += f"\n    Remediation: {v.get('remediation', 'Review required')}\n"
                
                doc_text = (doc.get('extracted_text') or doc.get('text_content') or '')[:3000]
                legal_text = doc.get('legal_advisory', '') or ''
                briefing = doc.get('briefing', {})
                
                fallback_prompt = f"""You are an expert financial compliance analyst providing authoritative guidance.

DOCUMENT: {doc.get('filename', 'Unknown')}
JURISDICTION: {doc.get('jurisdiction', 'UK')}
RISK SCORE: {doc.get('risk_score', 0)}/100
STATUS: {doc.get('status', 'unknown')}
COMPLIANCE OUTCOME: {doc.get('compliance_outcome', 'pending')}

DOCUMENT TEXT (excerpt):
{doc_text}{'...[truncated]' if len(doc_text) >= 3000 else ''}

VIOLATIONS ({len(violations)} total):
{violations_text or '(none detected)'}

LEGAL ADVISORY: {legal_text or 'None provided'}
LEGAL RECOMMENDATION: {doc.get('legal_recommendation', 'pending')}
{f"CITED REGULATIONS: {', '.join(doc.get('cited_regulations', []))}" if doc.get('cited_regulations') else ''}

{f"BRIEFING: Marketing Type={briefing.get('marketing_type', 'N/A')}, Target={briefing.get('target_audience', 'N/A')}, Distribution={briefing.get('distribution_media', 'N/A')}" if briefing else ''}

DISCUSSION THREAD:
{discussion_thread}

TASK: {ai_message}

Respond with specific, actionable compliance guidance. Reference violations by number. 
Cite specific regulatory sections where applicable. Keep under 400 words unless complexity demands more."""
                
                response = client.chat.completions.create(
                    model=_deployment,
                    messages=[
                        {"role": "system", "content": "You are an expert financial compliance analyst. Be specific, actionable, and cite regulations."},
                        {"role": "user", "content": fallback_prompt}
                    ],
                    temperature=0.2,
                    max_tokens=800
                )
                
                chat_result = {
                    'success': True,
                    'response': response.choices[0].message.content.strip(),
                    'citations': [],
                    'intent': focus,
                    'usage': {
                        'prompt_tokens': response.usage.prompt_tokens,
                        'completion_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens,
                    },
                    'suggested_actions': [],
                }
                
            except Exception as fallback_err:
                logger.error(f"❌ Fallback OpenAI also failed: {fallback_err}")
                return json_response(500, error="AI service unavailable")
        
        # ─── Check result ────────────────────────────────────────────────────
        if not chat_result or not chat_result.get('success'):
            error_msg = chat_result.get('error', 'Unknown error') if chat_result else 'No response'
            return json_response(500, error=f"AI analysis failed: {error_msg}")
        
        ai_response = chat_result.get('response', '')
        citations = chat_result.get('citations', [])
        usage = chat_result.get('usage', {})
        
        # ─── Create AI discussion entry ──────────────────────────────────────
        now = datetime.utcnow()
        
        ai_discussion = {
            'id': f"disc_{uuid.uuid4().hex[:12]}",
            'type': 'discussion',
            'organization_id': org_id,
            'document_id': document_id,
            'parent_id': None,
            'author_email': 'ai-assistant@system',
            'author_name': 'AI Compliance Assistant',
            'author_role': 'system',
            'content': ai_response,
            'mentions': [],
            'created_at': now.isoformat() + 'Z',
            'updated_at': now.isoformat() + 'Z',
            'is_resolved': False,
            'resolved_by': None,
            'resolved_at': None,
            'reply_count': 0,
            'is_ai_generated': True,
            'is_legal_advisory': False,
            'ai_metadata': {
                'engine': 'chat_engine_rag' if used_rag else 'direct_openai',
                'intent': chat_result.get('intent', focus),
                'focus': focus,
                'specific_question': specific_question or None,
                'analyzed_discussions': len(all_discussions),
                'violations_in_context': len(violations),
                'has_legal_advisory': bool(doc.get('legal_advisory')),
                'has_document_text': bool(doc.get('extracted_text') or doc.get('text_content')),
                'has_questionnaire': questionnaire_context is not None,
                'rag_citations_count': len(citations),
                'tokens': usage,
                'confidence': chat_result.get('confidence', 0.7),
                'triggered_by': user_email,
                'timestamp': now.isoformat() + 'Z',
            },
            # Store citations separately for UI rendering
            'ai_citations': citations[:5] if citations else [],
        }
        
        container.create_item(body=ai_discussion)
        
        logger.info(
            f"✅ AI contribution for doc {document_id} "
            f"(engine={'RAG' if used_rag else 'fallback'}, focus={focus}, "
            f"violations={len(violations)}, discussions={len(all_discussions)}, "
            f"citations={len(citations)}, tokens={usage.get('total_tokens', 0)})"
        )
        
        return json_response(201, data={
            'discussion': ai_discussion,
            'message': '✅ AI analysis added to discussion',
            'context_summary': {
                'violations_analyzed': len(violations),
                'discussions_analyzed': len(all_discussions),
                'has_legal_advisory': bool(doc.get('legal_advisory')),
                'has_document_text': bool(doc.get('extracted_text') or doc.get('text_content')),
                'rag_citations': len(citations),
                'engine': 'chat_engine_rag' if used_rag else 'direct_openai',
                'focus': focus,
            }
        })
        
    except Exception as e:
        logger.error(f"❌ AI contribution failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# REPLY TO DISCUSSION
# =============================================================================

def handle_reply_discussion(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions/{discussionId}/reply
    """
    try:
        body = req.get_json()
    except:
        return json_response(400, error="Invalid JSON body")
    
    discussion_id = req.route_params.get('discussionId') or req.route_params.get('discussion_id')
    if not discussion_id:
        url_parts = req.url.split('/')
        try:
            idx = url_parts.index('discussions') + 1
            discussion_id = url_parts[idx]
        except (ValueError, IndexError):
            return json_response(400, error="discussion_id required")
    
    body['parent_id'] = discussion_id
    return handle_create_discussion(req, user)


# =============================================================================
# RESOLVE DISCUSSION
# =============================================================================

def handle_resolve_discussion(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    POST /documents/{documentId}/discussions/{discussionId}/resolve
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        discussion_id = req.route_params.get('discussionId') or req.route_params.get('discussion_id')
        if not discussion_id:
            url_parts = req.url.split('/')
            try:
                idx = url_parts.index('discussions') + 1
                discussion_id = url_parts[idx]
            except (ValueError, IndexError):
                return json_response(400, error="discussion_id required")
        
        container = get_container('documents')
        
        query = """
        SELECT * FROM c 
        WHERE c.id = @id AND c.type = 'discussion' AND c.organization_id = @org_id
        """
        
        discussions = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": discussion_id},
                {"name": "@org_id", "value": org_id},
            ],
            partition_key=org_id
        ))
        
        if not discussions:
            return json_response(404, error="Discussion not found")
        
        discussion = discussions[0]
        now = datetime.utcnow()
        
        discussion['is_resolved'] = True
        discussion['resolved_by'] = user_email
        discussion['resolved_at'] = now.isoformat() + 'Z'
        discussion['updated_at'] = now.isoformat() + 'Z'
        
        container.upsert_item(discussion)
        
        logger.info(f"✅ Discussion {discussion_id} resolved by {user_email}")
        
        return json_response(200, data={
            'discussion': discussion,
            'message': '✅ Discussion resolved'
        })
        
    except Exception as e:
        logger.error(f"❌ Resolve discussion failed: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# SEARCH USERS FOR @MENTIONS
# =============================================================================

def handle_search_users(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/discussions/search-users?q=john
    Search organization users for @mentions autocomplete.
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        query = req.params.get('q', '').lower().strip()
        
        if not query or len(query) < 2:
            return json_response(200, data={'users': []})
        
        container = get_container('documents')
        
        # Search users in organization
        user_query = """
        SELECT DISTINCT c.email, c.name, c.roles
        FROM c
        WHERE c.type = 'user'
        AND c.organization_id = @org_id
        AND (CONTAINS(LOWER(c.email), @query) OR CONTAINS(LOWER(c.name), @query))
        """
        
        users = list(container.query_items(
            query=user_query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@query", "value": query},
            ],
            partition_key=org_id
        ))
        
        # Format for autocomplete
        results = [
            {
                'email': u.get('email'),
                'name': u.get('name', u.get('email')),
                'roles': u.get('roles', []),
            }
            for u in users[:10]  # Limit to 10 results
        ]
        
        return json_response(200, data={'users': results})
        
    except Exception as e:
        logger.error(f"❌ Search users failed: {e}", exc_info=True)
        return json_response(500, error=str(e))