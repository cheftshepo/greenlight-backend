"""
ENHANCED LEGAL ADVISORY HANDLER - Built for Legal Teams
========================================================
Professional legal workflow with comprehensive context and decision support
"""
import logging
import os
import json
from datetime import datetime
from typing import Dict, List, Optional
import azure.functions as func
from openai import AzureOpenAI

from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import (
    get_document, update_document, save_decision_trail, log_activity
)

logger = logging.getLogger(__name__)

def handle_provide_advisory(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    POST /documents/{documentId}/legal-advisory
    
    Provide comprehensive legal advisory with full context awareness
    """
    
    try:
        # =====================================================================
        # STEP 1: AUTHENTICATION & AUTHORIZATION
        # =====================================================================
        if not user:
            return json_response(401, error="Not authenticated")
        
        org_id = getattr(user, 'organization_id', None) or user.get('organization_id')
        user_email = getattr(user, 'email', None) or user.get('email')
        user_name = getattr(user, 'name', None) or user.get('name', user_email)
        user_roles = getattr(user, 'roles', None) or user.get('roles', [])
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Check if user has legal role
        legal_roles = ['Legal.Advisor', 'DLAPiper.Advisory', 'Organization.Admin', 'Platform.SuperAdmin']
        has_legal_access = any(role in user_roles for role in legal_roles)
        
        if not has_legal_access:
            return json_response(403, error="Legal advisory access required")
        
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        logger.info(f"⚖️ Legal advisory from {user_email} for {doc_id}")
        
        # =====================================================================
        # STEP 2: GET REQUEST DATA
        # =====================================================================
        try:
            body = req.get_json()
        except:
            return json_response(400, error="Invalid JSON body")
        
        recommendation = body.get('recommendation')  # 'approve', 'reject', 'review_required'
        advisory_text = body.get('advisory_text', '').strip()
        cited_regulations = body.get('cited_regulations', [])
        legal_conditions = body.get('legal_conditions', [])
        legal_recommendations = body.get('legal_recommendations', [])
        advisory_type = body.get('advisory_type', 'standard')  # standard, urgent, complex
        confidence_level = body.get('confidence_level', 'high')  # high, medium, low
        requires_external_counsel = body.get('requires_external_counsel', False)
        
        # Validation
        if not recommendation:
            return json_response(400, error="Legal recommendation required")
        
        if recommendation not in ['approve', 'reject', 'review_required']:
            return json_response(400, error="Invalid recommendation. Use: approve, reject, review_required")
        
        if not advisory_text:
            return json_response(400, error="Advisory text required")
        
        # =====================================================================
        # STEP 3: GET DOCUMENT WITH FULL CONTEXT
        # =====================================================================
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # =====================================================================
        # STEP 4: BUILD COMPREHENSIVE LEGAL CONTEXT
        # =====================================================================
        
        violations = doc.get('violations', [])
        discussions = doc.get('discussions', [])
        briefing = doc.get('briefing', {})
        jurisdiction = doc.get('jurisdiction', 'UK')
        risk_score = doc.get('risk_score', 0)
        pii_summary = doc.get('pii_summary', {})
        
        # Critical violations (HIGH/CRITICAL severity)
        critical_violations = [
            v for v in violations 
            if v.get('severity', '').upper() in ['CRITICAL', 'HIGH']
        ]
        
        # Medium/Low violations
        moderate_violations = [
            v for v in violations 
            if v.get('severity', '').upper() in ['MEDIUM', 'LOW']
        ]
        
        # PII risks
        pii_high_risk_count = pii_summary.get('high_risk_count', 0) + pii_summary.get('critical_risk_count', 0)
        
        # Discussion insights
        team_concerns = [
            d.get('content', '')[:200] 
            for d in discussions[-5:] 
            if not d.get('is_ai_generated')
        ]
        
        # =====================================================================
        # STEP 5: GENERATE AI LEGAL ANALYSIS (OPTIONAL ENHANCEMENT)
        # =====================================================================
        
        ai_legal_analysis = None
        
        if body.get('generate_ai_analysis', False):
            try:
                ai_legal_analysis = _generate_ai_legal_analysis(
                    doc=doc,
                    violations=violations,
                    jurisdiction=jurisdiction,
                    user_advisory=advisory_text,
                    user_recommendation=recommendation
                )
            except Exception as ai_err:
                logger.warning(f"⚠️ AI legal analysis failed: {ai_err}")
        
        # =====================================================================
        # STEP 6: UPDATE DOCUMENT
        # =====================================================================
        
        now = datetime.utcnow().isoformat() + 'Z'
        
        update_data = {
            'legal_advisory': advisory_text,
            'legal_recommendation': recommendation,
            'legal_reviewed_by': user_email,
            'legal_reviewed_by_name': user_name,
            'legal_reviewed_at': now,
            'legal_advisory_by': user_email,
            'legal_advisory_at': now,
            'legal_advisory_type': advisory_type,
            'cited_regulations': cited_regulations,
            'legal_conditions': legal_conditions,
            'legal_recommendations': legal_recommendations,
            'legal_confidence_level': confidence_level,
            'requires_external_counsel': requires_external_counsel,
            'status': 'legal_review' if recommendation == 'review_required' else doc.get('status'),
            'workflow_status': 'legal_review',
            'updated_at': now,
        }
        
        # Add AI analysis if generated
        if ai_legal_analysis:
            update_data['ai_legal_analysis'] = ai_legal_analysis
        
        # Update legal context summary
        update_data['legal_context'] = {
            'reviewer': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'review_summary': {
                'recommendation': recommendation,
                'advisory_type': advisory_type,
                'confidence': confidence_level,
                'critical_violations': len(critical_violations),
                'moderate_violations': len(moderate_violations),
                'pii_high_risk': pii_high_risk_count,
                'cited_regulations_count': len(cited_regulations),
                'conditions_count': len(legal_conditions),
            },
            'reviewed_at': now,
        }
        
        updated_doc = update_document(doc_id, update_data, org_id)
        
        # =====================================================================
        # STEP 7: LOG DECISION TRAIL
        # =====================================================================
        
        save_decision_trail({
            'organization_id': org_id,
            'document_id': doc_id,
            'document_filename': doc.get('filename'),
            'decision': f'legal_{recommendation}',
            'decision_type': 'legal_advisory',
            'decision_maker': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'decision_context': {
                'advisory_text': advisory_text[:500],
                'advisory_type': advisory_type,
                'confidence_level': confidence_level,
                'cited_regulations': cited_regulations,
                'conditions_count': len(legal_conditions),
                'recommendations_count': len(legal_recommendations),
                'requires_external_counsel': requires_external_counsel,
            },
            'document_state_at_decision': {
                'status': doc.get('status'),
                'risk_score': risk_score,
                'violations_count': len(violations),
                'critical_violations': len(critical_violations),
                'pii_high_risk': pii_high_risk_count,
                'jurisdiction': jurisdiction,
            },
            'jurisdiction': jurisdiction,
            'decision_timestamp': now,
        })
        
        # =====================================================================
        # STEP 8: LOG ACTIVITY
        # =====================================================================
        
        log_activity(
            org_id=org_id,
            user_email=user_email,
            user_name=user_name,
            action='legal_advisory_provided',
            document_id=doc_id,
            document_name=doc.get('filename'),
            details={
                'recommendation': recommendation,
                'advisory_type': advisory_type,
                'confidence': confidence_level,
            }
        )
        
        # =====================================================================
        # STEP 9: BUILD COMPREHENSIVE RESPONSE
        # =====================================================================
        
        response_data = {
            'document_id': doc_id,
            'legal_advisory': advisory_text,
            'recommendation': recommendation,
            'advisory_type': advisory_type,
            'confidence_level': confidence_level,
            'reviewed_by': {
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'reviewed_at': now,
            'cited_regulations': cited_regulations,
            'legal_conditions': legal_conditions,
            'legal_recommendations': legal_recommendations,
            'requires_external_counsel': requires_external_counsel,
            'context_summary': {
                'jurisdiction': jurisdiction,
                'risk_score': risk_score,
                'total_violations': len(violations),
                'critical_violations': len(critical_violations),
                'moderate_violations': len(moderate_violations),
                'pii_high_risk_count': pii_high_risk_count,
                'team_concerns': len(team_concerns),
            },
            'message': f'Legal advisory provided: {recommendation.upper()}',
        }
        
        # Add AI analysis if available
        if ai_legal_analysis:
            response_data['ai_legal_analysis'] = ai_legal_analysis
        
        logger.info(f"✅ Legal advisory saved: {recommendation.upper()}")
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error(f"❌ Legal advisory failed: {e}")
        logger.exception(e)
        return json_response(500, error=str(e))


def handle_get_legal_advisory(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /documents/{documentId}/legal-advisory
    
    Get legal advisory for a document with full context
    """
    
    try:
        # Auth
        if not user:
            return json_response(401, error="Not authenticated")
        
        org_id = getattr(user, 'organization_id', None) or user.get('organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        # Get document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Check if has legal advisory
        if not doc.get('legal_advisory'):
            return json_response(404, error="No legal advisory found for this document")
        
        # Build response
        response_data = {
            'document_id': doc_id,
            'legal_advisory': doc.get('legal_advisory'),
            'recommendation': doc.get('legal_recommendation'),
            'advisory_type': doc.get('legal_advisory_type'),
            'confidence_level': doc.get('legal_confidence_level'),
            'reviewed_by': {
                'email': doc.get('legal_reviewed_by') or doc.get('legal_advisory_by'),
                'name': doc.get('legal_reviewed_by_name'),
            },
            'reviewed_at': doc.get('legal_reviewed_at') or doc.get('legal_advisory_at'),
            'cited_regulations': doc.get('cited_regulations', []),
            'legal_conditions': doc.get('legal_conditions', []),
            'legal_recommendations': doc.get('legal_recommendations', []),
            'requires_external_counsel': doc.get('requires_external_counsel', False),
            'legal_context': doc.get('legal_context', {}),
            'ai_legal_analysis': doc.get('ai_legal_analysis'),
        }
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error(f"❌ Get legal advisory failed: {e}")
        return json_response(500, error=str(e))


def _generate_ai_legal_analysis(
    doc: Dict,
    violations: List[Dict],
    jurisdiction: str,
    user_advisory: str,
    user_recommendation: str
) -> Dict:
    """
    Generate AI-powered legal analysis to support human lawyer's decision
    
    This ASSISTS legal staff, doesn't replace them
    """
    
    try:
        client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            timeout=60.0
        )
        
        # Format violations
        critical_violations = [v for v in violations if v.get('severity', '').upper() in ['CRITICAL', 'HIGH']]
        
        violations_summary = "\n".join([
            f"• [{v.get('severity', 'MEDIUM')}] {v.get('category', 'unknown')}: {v.get('description', '')[:150]}"
            for v in violations[:15]
        ])
        
        prompt = f"""You are an AI legal assistant analyzing a {jurisdiction} financial marketing document.

A human lawyer has reviewed this document and provided the following advisory:

LAWYER'S RECOMMENDATION: {user_recommendation.upper()}
LAWYER'S ADVISORY: {user_advisory}

DOCUMENT CONTEXT:
- Jurisdiction: {jurisdiction}
- Risk Score: {doc.get('risk_score', 0)}/100
- Total Violations: {len(violations)}
- Critical/High Violations: {len(critical_violations)}

KEY VIOLATIONS FOUND:
{violations_summary}

---

YOUR TASK (AI Legal Assistant):

Provide a structured legal analysis that SUPPORTS the human lawyer's decision.

OUTPUT (JSON):
{{
  "risk_assessment": {{
    "overall_risk": "critical|high|medium|low",
    "regulatory_exposure": "Description of potential regulatory exposure",
    "litigation_risk": "Assessment of litigation risk",
    "reputational_risk": "Assessment of reputational damage risk"
  }},
  "regulatory_analysis": {{
    "primary_regulations_violated": ["List of main regulations"],
    "jurisdiction_specific_concerns": ["Concerns specific to {jurisdiction}"],
    "precedent_cases": ["Relevant precedent or enforcement actions if known"]
  }},
  "legal_reasoning": {{
    "supports_lawyer_decision": true/false,
    "key_legal_issues": ["List of 3-5 key legal issues"],
    "mitigating_factors": ["Factors that reduce risk"],
    "aggravating_factors": ["Factors that increase risk"]
  }},
  "recommended_actions": {{
    "immediate_actions": ["Must-do actions"],
    "short_term_actions": ["Actions within 1-2 weeks"],
    "long_term_safeguards": ["Systemic improvements"]
  }},
  "alternative_approaches": {{
    "if_approve": "What safeguards/conditions should apply",
    "if_reject": "What changes would make it approvable",
    "escalation_triggers": "When to escalate to external counsel"
  }},
  "confidence_assessment": {{
    "analysis_confidence": 0.0-1.0,
    "areas_of_uncertainty": ["Areas where legal uncertainty exists"],
    "recommended_validation": "What human lawyer should double-check"
  }}
}}

IMPORTANT: This analysis ASSISTS the human lawyer, not replaces their judgment."""

        response = client.chat.completions.create(
            model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
            messages=[
                {
                    "role": "system",
                    "content": f"You are an AI legal assistant for {jurisdiction} compliance. Provide structured analysis to support human lawyers."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"}
        )
        
        ai_analysis = json.loads(response.choices[0].message.content)
        
        logger.info("✅ AI legal analysis generated")
        
        return ai_analysis
        
    except Exception as e:
        logger.error(f"❌ AI legal analysis failed: {e}")
        raise


# Export handlers
__all__ = ['handle_provide_advisory', 'handle_get_legal_advisory']