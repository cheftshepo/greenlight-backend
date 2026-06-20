"""
RAG-ENHANCED LEGAL CHAT ENGINE
==============================
AI advisor with REAL regulatory citations for lawyers and compliance officers.
Uses Azure AI Search to retrieve actual regulatory text before responding.
"""

import logging
import os
import json
from pyexpat.errors import messages
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class LegalCitation:
    """A citation to actual regulatory text"""
    section_reference: str
    source_document: str
    regulatory_text: str
    jurisdiction: str
    category: str
    relevance_score: float
    effective_date: str = ""
    penalty_info: str = ""


class ChatEngine:
    """
    RAG-Enhanced Legal Chat Engine
    
    Key features:
    - Retrieves ACTUAL regulatory text before responding
    - Provides proper legal citations
    - Explains violations with regulatory context
    - Gives actionable, legally-defensible guidance
    """
    
    def __init__(self):
        self.openai_client = None
        self.deployment = None
        self.knowledge_base = None
        self._init_openai()
        self._init_rag()
    
    def _init_openai(self):
        """Initialize Azure OpenAI"""
        try:
            from openai import AzureOpenAI
            
            key = os.getenv('AZURE_OPENAI_API_KEY')
            endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
            deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4.1')
            api_version = os.getenv('AZURE_OPENAI_API_VERSION', '2024-12-01-preview')
            
            if not key or not endpoint:
                raise ValueError("OpenAI credentials required")
            
            self.openai_client = AzureOpenAI(
                api_key=key,
                api_version=api_version,
                azure_endpoint=endpoint,
                timeout=60.0,
                max_retries=2
            )
            self.deployment = deployment
            logger.info("✅ RAG Chat engine initialized")
            
        except Exception as e:
            logger.error(f"❌ Chat engine init failed: {e}")
            raise
    
    def _init_rag(self):
        """Initialize RAG knowledge base"""
        try:
            from .knowledge_base import RegulatoryKnowledgeBase
            self.knowledge_base = RegulatoryKnowledgeBase()
            logger.info("✅ RAG knowledge base connected")
        except Exception as e:
            logger.warning(f"⚠️ RAG init failed, will use non-RAG mode: {e}")
            self.knowledge_base = None
    
    def chat(
        self, 
        message: str, 
        document_data: Dict, 
        conversation_history: Optional[List[Dict]] = None,
        user_context: Optional[Dict] = None,
        questionnaire_context: Optional[Dict] = None
    ) -> Dict:
        """
        RAG-enhanced legal chat with real regulatory citations
        
        Args:
            message: User's question
            document_data: Full document context with violations
            conversation_history: Previous messages
            user_context: User role, expertise, preferences
            
        Returns:
            Response with legal citations and actionable guidance
        """
        
        if not self.openai_client:
            return {
                "success": False,
                "error": "Chat engine not available",
                "response": "System temporarily unavailable"
            }
        
        try:
            # Step 1: Analyze user intent
            intent = self._analyze_intent(message, document_data)
            logger.info(f"💬 Intent: {intent}")
            
            # Step 2: Retrieve relevant regulations via RAG
            citations = self._retrieve_regulations(message, document_data)
            logger.info(f"📚 Retrieved {len(citations)} regulatory citations")
            
            # Step 3: Build context with document + violations + regulations
            if questionnaire_context:
                context = self._build_context_with_questionnaire(
                    document_data, 
                    citations, 
                    intent,
                    questionnaire_context
                )
            else:
                context = self._build_legal_context(document_data, citations, intent)
            
            # Step 4: Get system prompt for legal advisor
            system_prompt = self._get_legal_system_prompt(
                document_data.get('jurisdiction', 'UK'),
                intent,
                user_context
            )
            
            # Step 5: Build conversation
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add history
            if conversation_history:
                for hist in conversation_history[-6:]:
                    messages.append({
                        "role": hist.get("role", "user"),
                        "content": hist.get("content", "")
                    })
            
            # Add current query with context
            messages.append({
                "role": "user",
                "content": f"""{context}

            ---
            USER QUESTION: {message}

            Respond naturally and concisely. Match your response length to the question complexity."""
            })
            # Step 6: Call AI
            response = self.openai_client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                temperature=0.2,  # Low for factual accuracy
                max_tokens=2500,
                top_p=0.9
            )
            
            reply = response.choices[0].message.content.strip()
            
            # Step 7: Structure response
            structured = self._structure_legal_response(reply, citations, intent)
            
            return {
                "success": True,
                "response": reply,
                "structured": structured,
                "intent": intent,
                "citations": [self._citation_to_dict(c) for c in citations],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "suggested_actions": structured.get("actions", []),
                "confidence": 0.9 if citations else 0.7
            }
            
        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Chat failed: {str(e)}",
                "response": "I encountered an error. Please try again."
            }
    
    def _retrieve_regulations(
        self, 
        message: str, 
        document_data: Dict
    ) -> List[LegalCitation]:
        """Retrieve relevant regulations from RAG knowledge base"""
        
        if not self.knowledge_base:
            return []
        
        citations = []
        jurisdiction = document_data.get('jurisdiction', 'UK')
        
        try:
            query_results = self.knowledge_base.search(
                query_text=message,
                jurisdiction=jurisdiction,
                top_k=5,
                min_score=0.5
            )
            
            for result in query_results:
                # FIX: Use dict access like rag_scanner.py
                chunk = result.get('chunk') if isinstance(result, dict) else getattr(result, 'chunk', result)
                
                if chunk:
                    section_ref = getattr(chunk, 'section_reference', '') if hasattr(chunk, 'section_reference') else chunk.get('section_reference', '')
                    source_doc = getattr(chunk, 'source_document', '') if hasattr(chunk, 'source_document') else chunk.get('source_document', '')
                    text = getattr(chunk, 'text', '') if hasattr(chunk, 'text') else chunk.get('text', '')
                    jur = getattr(chunk, 'jurisdiction', jurisdiction) if hasattr(chunk, 'jurisdiction') else chunk.get('jurisdiction', jurisdiction)
                    cat = getattr(chunk, 'category', 'general') if hasattr(chunk, 'category') else chunk.get('category', 'general')
                    eff_date = getattr(chunk, 'effective_date', '') if hasattr(chunk, 'effective_date') else chunk.get('effective_date', '')
                    penalty = getattr(chunk, 'penalty_info', '') if hasattr(chunk, 'penalty_info') else chunk.get('penalty_info', '')
                    score = result.get('similarity_score', 0.8) if isinstance(result, dict) else getattr(result, 'similarity_score', 0.8)
                    
                    citations.append(LegalCitation(
                        section_reference=section_ref,
                        source_document=source_doc,
                        regulatory_text=text[:800],
                        jurisdiction=jur,
                        category=cat,
                        relevance_score=score,
                        effective_date=eff_date,
                        penalty_info=penalty
                    ))
            
            citations.sort(key=lambda c: c.relevance_score, reverse=True)
            return citations[:8]
            
        except Exception as e:
            logger.error(f"RAG retrieval failed: {e}")
            return []
    def _build_legal_context(
        self, 
        doc_data: Dict, 
        citations: List[LegalCitation],
        intent: str
    ) -> str:
        """Build comprehensive legal context for the AI"""
        
        parts = []
        
        # Document summary
        parts.append(f"""
📄 DOCUMENT CONTEXT
==================
Filename: {doc_data.get('filename', 'Unknown')}
Jurisdiction: {doc_data.get('jurisdiction', 'UK')}
Status: {doc_data.get('status', 'unknown')}
Compliance Outcome: {doc_data.get('compliance_outcome', 'pending')}
Risk Score: {doc_data.get('risk_score', 0)}/100
Violations Found: {doc_data.get('violations_count', len(doc_data.get('violations', [])))}
""")
        
        # Document text (truncated)
        text = doc_data.get('extracted_text', '')
        if text:
            parts.append(f"""
📝 DOCUMENT EXCERPT (first 2000 chars)
======================================
{text[:2000]}
{'...[truncated]' if len(text) > 2000 else ''}
""")
        
        # Violations with details
        violations = doc_data.get('violations', [])
        if violations:
            parts.append(f"""
⚠️ DETECTED VIOLATIONS ({len(violations)} total)
=================================================""")
            
            for i, v in enumerate(violations[:5], 1):
                parts.append(f"""
VIOLATION {i}: {v.get('severity', 'MEDIUM')}
- Rule: {v.get('rule_id', v.get('regulatory_reference', 'Unknown'))}
- Description: {v.get('rule_description', 'N/A')}
- Matched Text: "{v.get('matched_text', '')[:200]}"
- Reasoning: {v.get('ai_reasoning', v.get('reasoning', 'N/A'))}
- Remediation: {v.get('remediation', 'Review required')}
""")
        
        # RAG-retrieved regulations (THE KEY DIFFERENTIATOR)
        if citations:
            parts.append(f"""
📚 APPLICABLE REGULATIONS (from regulatory database)
=====================================================""")
            
            for i, cite in enumerate(citations, 1):
                parts.append(f"""
[{i}] {cite.section_reference}
Source: {cite.source_document}
Jurisdiction: {cite.jurisdiction}
Category: {cite.category}
Relevance: {cite.relevance_score:.0%}

REGULATORY TEXT:
"{cite.regulatory_text}"

{f"⚠️ Enforcement: {cite.penalty_info}" if cite.penalty_info else ""}
---""")
        
        return "\n".join(parts)
    
    def _get_legal_system_prompt(self, jurisdiction: str, intent: str, user_context: Optional[Dict] = None) -> str:
        """System prompt for adaptive legal assistance"""
        
        # Detect user expertise from context
        user_expertise = "intermediate"  # default
        if user_context:
            roles = user_context.get('roles', [])
            if any('Compliance' in r or 'Legal' in r for r in roles):
                user_expertise = "expert"
            elif any('Marketing' in r for r in roles):
                user_expertise = "beginner"
        
        base = f"""You are a helpful compliance assistant for {jurisdiction} financial marketing documents.

    YOUR COMMUNICATION STYLE:
    - **Conversational**: Match response length to question complexity
    - **Adaptive**: Adjust explanation depth based on user's apparent knowledge
    - **Practical**: Focus on actionable guidance, not legal theory

    RESPONSE GUIDELINES:
    1. **For Simple Questions** → 2-3 sentences, direct answer
    Example: "What's next?" → "Review violations, fix critical issues, then submit for compliance review."

    2. **For Complex Questions** → Structured but readable (3-5 paragraphs max)
    Example: "How do I fix FAIS violations?" → Explain violation → Cite regulation → Give specific fix

    3. **For Follow-ups** → Remember context, don't repeat
    Example: After explaining violations, if asked "what about fees?", focus ONLY on fees

    ---

    🎯 USER PROFILING (Inferred Expertise: {user_expertise.upper()}):

    BEGINNER (Marketing Users):
    - Explain regulations in plain English
    - Use before/after examples
    - Break complex fixes into steps
    - Offer to clarify jargon

    INTERMEDIATE (General Users):
    - Balance detail with brevity
    - Reference regulations but explain impact
    - Assume basic compliance knowledge

    EXPERT (Compliance Officers):
    - Technical precision
    - Cite specific regulatory sections
    - Focus on edge cases and nuances
    - Less hand-holding

    ADAPTIVE APPROACH:
    - If user asks basic questions → Switch to beginner mode
    - If user uses technical terms → Switch to expert mode
    - If user seems confused → Ask clarifying questions

    ---

    🚫 AVOID:
    - Repeating all violations when user already knows them
    - Tables/bullet points unless specifically asked
    - Excessive legal citations (integrate naturally)
    - Generic advice (be specific to their document)

    ✅ DO:
    - Answer the actual question asked
    - Reference specific text from their document
    - Give copy-paste-ready fixes when possible
    - Ask "Would you like me to explain further?" for complex topics

    ---

    INTENT-SPECIFIC GUIDANCE:"""

        # Add intent-specific instructions
        if intent == 'fix':
            base += """
            
    FIXING VIOLATIONS:
    - Give step-by-step remediation
    - Show exact before/after text
    - Explain WHY the fix works (regulation compliance)
    - Prioritize by severity (critical first)
    """
        elif intent == 'explain':
            base += """
            
    EXPLAINING VIOLATIONS:
    - Use plain English first, legal terms second
    - Give real-world examples
    - Connect to regulations naturally
    - Check if user needs more detail
    """
        elif intent == 'priority':
            base += """
            
    PRIORITIZING ACTIONS:
    - Critical first (rejection risk)
    - High second (review required)
    - Medium/Low can wait
    - Give timeline: "Fix these TODAY" vs "Address before next review"
    """
        
        return base
    def _analyze_intent(self, message: str, doc_data: Dict) -> str:
        """Detect user intent for personalized response"""
        message_lower = message.lower()
        
        intent_map = {
            'explain': ['why', 'what does', 'explain', 'understand', 'clarify', 'mean', 'what is'],
            'fix': ['how to fix', 'correct', 'remediation', 'solution', 'change', 'amend', 'rewrite'],
            'risk': ['risk', 'penalty', 'fine', 'enforcement', 'serious', 'consequences'],
            'compare': ['compare', 'versus', 'different', 'uk vs', 'eu vs', 'difference'],
            'cite': ['cite', 'regulation', 'law', 'rule', 'section', 'article', 'provision'],
            'approve': ['approve', 'publish', 'can we', 'ok to', 'allowed', 'permitted'],
            'priority': ['priority', 'first', 'urgent', 'critical', 'important', 'start']
        }
        
        for intent, triggers in intent_map.items():
            if any(trigger in message_lower for trigger in triggers):
                return intent
        
        return 'general'
    
    def _structure_legal_response(
        self, 
        reply: str, 
        citations: List[LegalCitation],
        intent: str
    ) -> Dict:
        """Structure the response for UI consumption"""
        
        structured = {
            'summary': '',
            'actions': [],
            'citations_used': [],
            'risk_level': None,
            'confidence': 'high' if citations else 'medium'
        }
        
        # Extract first paragraph as summary
        paragraphs = reply.split('\n\n')
        if paragraphs:
            structured['summary'] = paragraphs[0][:300]
        
        # Extract action items
        lines = reply.split('\n')
        for line in lines:
            line_lower = line.lower()
            if any(word in line_lower for word in ['add', 'remove', 'replace', 'include', 'change', 'amend']):
                clean = line.strip().lstrip('•-* 123456789.')
                if clean and len(clean) > 10:
                    structured['actions'].append(clean[:200])
        
        # Track which citations were likely used
        for cite in citations:
            if cite.section_reference.lower() in reply.lower():
                structured['citations_used'].append({
                    'reference': cite.section_reference,
                    'source': cite.source_document
                })
        
        return structured
    
    def _citation_to_dict(self, citation: LegalCitation) -> Dict:
        """Convert citation to dictionary"""
        return {
            'section_reference': citation.section_reference,
            'source_document': citation.source_document,
            'jurisdiction': citation.jurisdiction,
            'category': citation.category,
            'regulatory_text': citation.regulatory_text[:500],
            'relevance_score': round(citation.relevance_score, 2),
            'effective_date': citation.effective_date,
            'penalty_info': citation.penalty_info
        }
    
    def _build_context_with_questionnaire(
        self, 
        doc_data: Dict, 
        citations: List[LegalCitation],
        intent: str,
        questionnaire_context: Dict
    ) -> str:
        """Build comprehensive context with enhanced questionnaire analysis"""
        
        base_context = self._build_legal_context(doc_data, citations, intent)
        
        # SAFETY CHECK: If questionnaire_context is empty/invalid, return base context
        if not questionnaire_context or not isinstance(questionnaire_context, dict):
            logger.warning("Empty or invalid questionnaire_context, using base context only")
            return base_context
        
        # SAFE ACCESS: Use .get() with defaults for all nested access
        try:
            # Enhanced questionnaire analysis
            questionnaire_section = f"""

    📋 USER QUESTIONNAIRE CONTEXT (ENHANCED)
    ========================================

    Based on the user's questionnaire answers:

    📊 COMPLIANCE PROFILE
    ---------------------
    Compliance Maturity: {questionnaire_context.get('compliance_maturity', 'unknown').upper()}
    Risk Profile: {questionnaire_context.get('risk_profile', 'unknown').upper()}
    Confidence Level: {questionnaire_context.get('confidence_level', 'medium').upper()}

    📝 ANSWER SUMMARY
    -----------------
    ✅ YES: {questionnaire_context.get('answer_summary', {}).get('yes', 0)} answers
    ❌ NO: {questionnaire_context.get('answer_summary', {}).get('no', 0)} answers  
    ❓ UNCERTAIN: {questionnaire_context.get('answer_summary', {}).get('uncertain', 0)} answers

    🎯 FOCUS AREAS ({len(questionnaire_context.get('focus_areas', []))} identified)
    --------------------------------------------------------------------"""
            
            # Add focus areas SAFELY
            focus_areas = questionnaire_context.get('focus_areas', [])
            if focus_areas and isinstance(focus_areas, list):
                for area in focus_areas[:5]:  # Show top 5
                    if area == 'critical_violations':
                        questionnaire_section += f"\n• ⚠️ CRITICAL VIOLATIONS: User has critical AI violations requiring immediate attention"
                    elif area == 'critical_knowledge_gaps':
                        questionnaire_section += f"\n• 📚 CRITICAL KNOWLEDGE GAPS: User uncertain about critical compliance requirements"
                    elif area == 'high_risk_answers':
                        questionnaire_section += f"\n• 🔴 HIGH-RISK ANSWERS: User answered 'no' to high-severity questions"
                    else:
                        area_name = str(area).replace('_', ' ').title()
                        questionnaire_section += f"\n• {area_name}"
            
            # Add knowledge gaps SAFELY
            knowledge_gaps = questionnaire_context.get('knowledge_gaps', [])
            if knowledge_gaps and isinstance(knowledge_gaps, list):
                questionnaire_section += f"""

    📚 KNOWLEDGE GAPS ({len(knowledge_gaps)} areas where user was uncertain)
    ------------------------------------------------------------------------"""
                
                for i, gap in enumerate(knowledge_gaps[:3], 1):  # Top 3
                    if isinstance(gap, dict):
                        questionnaire_section += f"""
    GAP {i}: {gap.get('severity', 'medium').upper()} - {gap.get('category', 'general').replace('_', ' ').title()}
    Question: "{gap.get('question', 'N/A')}"
    Notes: {gap.get('user_notes', 'No notes provided')}
    """
                
                if len(knowledge_gaps) > 3:
                    questionnaire_section += f"\n... and {len(knowledge_gaps) - 3} more areas of uncertainty"
            
            # Add AI guidance SAFELY
            suggested_approach = questionnaire_context.get('suggested_approach', 'balanced')
            compliance_maturity = questionnaire_context.get('compliance_maturity', 'intermediate')
            risk_profile = questionnaire_context.get('risk_profile', 'moderate')
            
            questionnaire_section += f"""

    🤖 AI GUIDANCE BASED ON QUESTIONNAIRE
    --------------------------------------
    Recommended Approach: {str(suggested_approach).replace('_', ' ').title()}

    Tailor your response by:
    1. {"Focusing on urgent fixes and consequences" if suggested_approach == 'urgent_detailed' else "Providing balanced guidance"}
    2. {"Explaining basic concepts clearly" if compliance_maturity == 'beginner' else "Focusing on advanced compliance strategies"}
    3. {"Being patient and educational" if knowledge_gaps else "Being direct and concise"}
    4. {"Emphasizing risk consequences" if risk_profile in ['risky', 'very_risky'] else "Focusing on best practices"}
    5. {"Referencing specific regulations" if knowledge_gaps else "Providing strategic advice"}

    REMEMBER: The user has answered the questionnaire. Your response should:
    - Acknowledge what they've confirmed (YES answers)
    - Focus on areas of uncertainty or risk (UNCERTAIN/NO answers)
    - Provide actionable guidance based on their compliance maturity level
    """
            
            return base_context + questionnaire_section
            
        except Exception as e:
            logger.error(f"Error building questionnaire context: {e}", exc_info=True)
            logger.warning("Falling back to base context without questionnaire")
            return base_context
    # Global instance


chat_engine = ChatEngine()
