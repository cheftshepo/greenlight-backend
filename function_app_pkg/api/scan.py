"""
Scan API - ENHANCED ENTERPRISE COMPLIANCE SCANNER
=========================================
Enterprise-level scanning with full context awareness
"""

import azure.functions as func
import logging
import os
import time
import gc
import json
from datetime import datetime
from typing import List, Dict
import uuid

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Cache scanner instances
_rag_scanner = None



# Add this helper function after imports
def _log_scan_audit(org_id: str, user, doc_id: str, doc: dict, scan_result: dict, duration: float):
    """Log scan completion to audit trail, decision trail, and analytics"""
    try:
        from function_app_pkg.core.database import log_action, save_decision_trail, save_analytics_event

        user_id = user.id if hasattr(user, 'id') else user.get('id', '')
        user_email = user.email if hasattr(user, 'email') else user.get('email', '')
        user_roles = user.roles if hasattr(user, 'roles') else user.get('roles', [])

        # 1. Audit Log
        log_action(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            action='document.scan',
            resource_type='document',
            resource_id=doc_id,
            resource_name=doc.get('filename', ''),
            details={
                'jurisdiction': doc.get('jurisdiction'),
                'risk_score': scan_result.get('risk_score', 0),
                'violations_count': scan_result.get('violations_count', 0),
                'compliance_outcome': scan_result.get('compliance_outcome'),
                'scan_mode': scan_result.get('scan_mode'),
                'duration_seconds': duration,
            },
            success=True
        )

        # 2. Decision Trail
        save_decision_trail({
            'organization_id': org_id,
            'document_id': doc_id,
            'document_filename': doc.get('filename', ''),
            'decision': 'scanned',
            'decision_type': 'scan',
            'decision_maker': {
                'user_id': user_id,
                'email': user_email,
                'roles': user_roles,
            },
            'decision_context': {
                'recommendation': scan_result.get('recommendation'),
                'duration_seconds': duration,
            },
            'document_state_at_decision': {
                'status': 'scanned',
                'jurisdiction': doc.get('jurisdiction'),
                'risk_score': scan_result.get('risk_score', 0),
                'violations_count': scan_result.get('violations_count', 0),
                'compliance_outcome': scan_result.get('compliance_outcome'),
                'pii_count': scan_result.get('pii_items_count', 0),
            },
            'ai_context': {
                'scan_mode': scan_result.get('scan_mode'),
                'questions_generated': scan_result.get('questions_generated', 0),
                'briefing_used': scan_result.get('scan_context', {}).get('briefing_used', False),
            },
            'decision_timestamp': datetime.utcnow().isoformat() + 'Z',
        })
        # 3. Analytics Event - ROI Tracking
        time_saved_hours = 0.5
        cost_saved_gbp = time_saved_hours * 75
        violations = scan_result.get('violations_count', 0)
        potential_fines_prevented = violations * 5000

        save_analytics_event({
            'organization_id': org_id,
            'event_type': 'scan_completed',
            'document_id': doc_id,
            'user_email': user_email,
            'metrics': {
                'risk_score': scan_result.get('risk_score', 0),
                'violations_count': violations,
                'pii_items_count': scan_result.get('pii_items_count', 0),
                'questions_generated': scan_result.get('questions_generated', 0),
                'scan_duration_seconds': duration,
                'text_length': scan_result.get('text_length', 0),
                'time_saved_hours': time_saved_hours,
                'cost_saved_gbp': cost_saved_gbp,
                'potential_fines_prevented_gbp': potential_fines_prevented,
            },
            'dimensions': {
                'jurisdiction': doc.get('jurisdiction'),
                'compliance_outcome': scan_result.get('compliance_outcome'),
                'scan_mode': scan_result.get('scan_mode'),
                'had_briefing': scan_result.get('scan_context', {}).get('briefing_used', False),
            },
        })

        logger.info(f"📊 Scan audit logged: {doc_id}")

    except Exception as e:
        logger.warning(f"⚠️ Scan audit logging failed: {e}")


def _get_rag_scanner():
    """Cached ENHANCED RAG scanner import"""
    global _rag_scanner
    if _rag_scanner is None:
        try:
            from function_app_pkg.core.rag_scanner import rag_scanner
            _rag_scanner = rag_scanner
            logger.info("✅ ENHANCED RAG scanner loaded")
        except Exception as e:
            logger.error(f"❌ ENHANCED RAG scanner failed: {e}")
            raise
    return _rag_scanner

def get_scanner_safe():
    """Get the ENHANCED RAG scanner"""
    return _get_rag_scanner(), "ENHANCED_RAG"

def _analyze_pii_with_ai(pii_items: List[Dict], text: str, jurisdiction: str, document_type: str = 'marketing_material') -> Dict:
    """
    Use AI to analyze PII findings with DOCUMENT TYPE awareness
    """
    try:
        from openai import AzureOpenAI
        
        client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            timeout=30.0
        )
        
        # Prepare PII data for analysis
        pii_summary = {}
        for item in pii_items:
            pii_type = item.get('type', 'unknown')
            if pii_type not in pii_summary:
                pii_summary[pii_type] = []
            pii_summary[pii_type].append({
                'text': item.get('text', '')[:100],
                'confidence': item.get('confidence', 0)
            })
        
        # Get context around PII for better analysis
        pii_contexts = []
        for item in pii_items[:10]:  # Analyze first 10 PII items
            offset = item.get('offset', 0)
            length = item.get('length', 50)
            start = max(0, offset - 100)
            end = min(len(text), offset + length + 100)
            context = text[start:end]
            pii_contexts.append({
                'type': item.get('type'),
                'text': item.get('text', '')[:50],
                'context': context
            })
        
        # AI prompt for PII analysis with document type context
        prompt = f"""You are a Data Protection Officer analyzing PII in a {document_type}.

JURISDICTION: {jurisdiction}
DOCUMENT TYPE: {document_type}  ← CRITICAL CONTEXT
REGULATIONS: {'GDPR + POPIA' if 'ZA' in jurisdiction else 'GDPR' if 'EU' in jurisdiction else 'Data Protection Act'}

🚨 IMPORTANT CONTEXT: This is a {document_type.upper()}, not necessarily marketing material.

DOCUMENT TYPE RULES:
- **CV/Resume**: Personal info (name, email, phone, address) is EXPECTED and APPROPRIATE
- **Marketing**: Client/customer personal data is PROBLEMATIC
- **Internal docs**: Different risk profile than public-facing

PII DETECTED:
{json.dumps(pii_summary, indent=2)}

CONTEXT EXAMPLES:
{chr(10).join([f"{ctx['type']}: '{ctx['text']}' → context: '{ctx['context'][:150]}...'" for ctx in pii_contexts])}

---

🎯 YOUR ANALYSIS TASK:

1. **CATEGORIZE BY RISK FOR {document_type.upper()}**:

FOR CV/RESUME DOCUMENTS:
- **LOW**: Name, email, phone, address (this is NORMAL and EXPECTED in CVs)
- **MEDIUM**: Work history with client names (might need anonymizing)
- **HIGH**: Only truly sensitive data like SSN, credit cards (shouldn't be in CVs)
- **CRITICAL**: Financial account numbers, passwords (NEVER in CVs)

FOR MARKETING DOCUMENTS:
- **CRITICAL**: Real personal data identifying individuals (real client emails, phones, IDs)
- **HIGH**: Names + context that could identify (e.g., "John Smith from Manchester managing £50k")
- **MEDIUM**: Generic personal data (first names only, job titles)
- **LOW**: Obviously fake/example data (test@example.com, "John Doe")

2. **IDENTIFY FALSE POSITIVES**:
- Company contact info (info@company.com, support@firm.com) → NOT PII (any document type)
- Example data (john.doe@example.com, 555-1234) → NOT PII
- In CVs: Candidate's own contact info → EXPECTED, not a violation
- Generic names in hypotheticals ("Let's say Jane invests...") → MAYBE PII (context-dependent)

3. **SMART REMEDIATION (context-aware)**:

FOR CV/RESUME:
✅ "Personal contact info is appropriate in a CV/Resume - no action needed"
⚠️ "Consider anonymizing previous employer client names: 'Client A (Financial Services)'"

FOR MARKETING:
✅ "Replace 'john.smith@gmail.com' with 'client.contact@example.com'"
✅ "Change 'Sarah Johnson, age 45' to 'Client A (pseudonym), 40-50 age bracket'"

4. **REGULATORY COMPLIANCE FOR {jurisdiction}**:
- Cite specific {jurisdiction} data protection rules
- Consider document type in legal assessment
- Explain if usage is legitimate or violation

---

OUTPUT JSON:
{{
  "risk_assessment": {{
    "overall_risk": "critical|high|medium|low",
    "jurisdiction_compliance": "compliant|partial|non_compliant",
    "document_type_considered": "{document_type}",
    "pii_expected_for_type": true/false,  # true for CVs, false for marketing
    "legal_basis_present": true/false,
    "consent_required": true/false
  }},
  "pii_by_risk_level": {{
    "critical": [
      {{
        "type": "email",
        "count": 3,
        "samples": ["john@gmail.com", "..."],
        "why_critical": "Real personal email addresses that identify individuals",
        "applies_to_document_type": "Explain why this matters for {document_type}"
      }}
    ],
    "high": [...],
    "medium": [...],
    "low": [...]
  }},
  "relevant_pii": [
    {{
      "type": "email",
      "reason": "Appears to be real client contact info" OR "Candidate contact info (appropriate for CV)",
      "legitimate_use": true/false,
      "requires_action": true/false
    }}
  ],
  "false_positives": [
    {{
      "type": "email",
      "text": "info@company.com",
      "reason": "Company contact information - not personal data"
    }}
  ],
  "smart_suggestions": [
    "Context-aware suggestions based on document type",
    "For CVs: 'Personal contact info is appropriate'",
    "For Marketing: 'Replace real client data with anonymized examples'"
  ],
  "regulatory_references": [
    "GDPR Article 6 - Lawful basis (with {document_type} context)",
    "{jurisdiction}-specific rules applicable to {document_type}"
  ]
}}

---

⚠️ CRITICAL: 
- Consider document type in ALL assessments
- CVs SHOULD have personal info - don't flag as violations
- Marketing SHOULD NOT have client PII - flag appropriately
- Be accurate. Credibility with law firms depends on precision."""
        
        response = client.chat.completions.create(
            model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
            messages=[
                {
                    "role": "system",
                    "content": "You are a data protection compliance expert. Analyze PII findings critically and provide actionable insights."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"}
        )
        
        ai_analysis = json.loads(response.choices[0].message.content)
        
        # Merge AI analysis with PII items
        enhanced_items = []
        for item in pii_items:
            pii_type = item.get('type', 'unknown')
            
            # Get risk level from AI analysis
            risk_level = 'medium'
            for risk_cat, items in ai_analysis.get('pii_by_risk_level', {}).items():
                for risk_item in items:
                    if risk_item.get('type') == pii_type:
                        risk_level = risk_cat
                        break
            
            # Check if it's relevant
            relevant = True
            reason = "Personal data detected"
            
            for irr_item in ai_analysis.get('irrelevant_pii', []):
                if irr_item.get('type') == pii_type:
                    relevant = False
                    reason = irr_item.get('reason', 'Not relevant PII')
                    break
            
            # Create enhanced PII item
            enhanced_item = item.copy()
            enhanced_item.update({
                'risk_level': risk_level,
                'relevant': relevant,
                'relevance_reason': reason,
                'suggestion': next(
                    (s for s in ai_analysis.get('smart_suggestions', []) 
                     if pii_type.lower() in s.lower()),
                    f"Review {pii_type} for compliance"
                ),
                'ai_analyzed': True
            })
            
            enhanced_items.append(enhanced_item)
        
        return {
            'ai_analysis': ai_analysis,
            'enhanced_pii_items': enhanced_items,
            'summary': {
                'total_pii': len(pii_items),
                'relevant_pii': len([i for i in enhanced_items if i['relevant']]),
                'critical_risk': len([i for i in enhanced_items if i['risk_level'] == 'critical']),
                'high_risk': len([i for i in enhanced_items if i['risk_level'] == 'high']),
                'ai_confidence': 0.85
            }
        }
        
    except Exception as e:
        logger.error(f"❌ AI PII analysis failed: {e}")
        # Fallback analysis
        return _fallback_pii_analysis(pii_items, jurisdiction)

def _fallback_pii_analysis(pii_items: List[Dict], jurisdiction: str) -> Dict:
    """Fallback PII analysis when AI fails"""
    # Simple rule-based analysis
    high_risk_types = ['email', 'phone', 'id_number', 'socialsecuritynumber', 'creditcardnumber']
    medium_risk_types = ['name', 'address', 'dateofbirth']
    
    enhanced_items = []
    for item in pii_items:
        pii_type = item.get('type', '').lower()
        
        # Determine risk level
        if any(risk in pii_type for risk in high_risk_types):
            risk_level = 'high'
        elif any(risk in pii_type for risk in medium_risk_types):
            risk_level = 'medium'
        else:
            risk_level = 'low'
        
        # Check relevance (simple rules)
        text = item.get('text', '').lower()
        relevant = True
        
        # Common false positives
        if 'example' in text or 'test' in text or 'company.com' in text:
            relevant = False
        
        enhanced_item = item.copy()
        enhanced_item.update({
            'risk_level': risk_level,
            'relevant': relevant,
            'relevance_reason': 'Personal data' if relevant else 'Appears to be example/test data',
            'suggestion': f"Review {pii_type} for compliance with {jurisdiction} data protection laws",
            'ai_analyzed': False
        })
        
        enhanced_items.append(enhanced_item)
    
    return {
        'ai_analysis': {
            'risk_assessment': {'overall_risk': 'medium', 'jurisdiction_compliance': 'needs_review'},
            'smart_suggestions': ['Review all PII items manually', 'Check jurisdiction-specific requirements']
        },
        'enhanced_pii_items': enhanced_items,
        'summary': {
            'total_pii': len(pii_items),
            'relevant_pii': len([i for i in enhanced_items if i['relevant']]),
            'critical_risk': 0,
            'high_risk': len([i for i in enhanced_items if i['risk_level'] == 'high']),
            'ai_confidence': 0.5
        }
    }

def _aggregate_pii_summary(pii_items: List[Dict], ai_analysis: Dict, jurisdiction: str = '') -> Dict:
    """Convert PII items into structured summary for frontend"""
    if not pii_items:
        return {
            "count": 0,
            "by_type": {},
            "samples": {},
            "high_risk_count": 0,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "relevant_count": 0,
            "false_positive_indicators": [],
            "smart_suggestions": [],
            "ai_analysis_summary": {}
        }
    
    # Count by type
    by_type = {}
    samples = {}
    risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    relevant_count = 0
    
    for item in pii_items:
        pii_type = item.get('type', 'unknown')
        
        # Map to standard types
        type_mapping = {
            'person': 'name',
            'phonenumber': 'phone',
            'emailaddress': 'email',
            'address': 'address',
            'idnumber': 'id_number',
            'financial': 'financial',
            'socialsecuritynumber': 'id_number',
            'creditcardnumber': 'financial'
        }
        
        mapped_type = type_mapping.get(pii_type.lower(), pii_type)
        
        # Count by type
        by_type[mapped_type] = by_type.get(mapped_type, 0) + 1
        
        # Collect samples (max 2 per type)
        if mapped_type not in samples:
            samples[mapped_type] = []
        if len(samples[mapped_type]) < 2:
            samples[mapped_type].append(item.get('text', '')[:50])
        
        # Count risk levels
        risk_level = item.get('risk_level', 'medium')
        if risk_level in risk_counts:
            risk_counts[risk_level] += 1
        
        # Count relevant PII
        if item.get('relevant', True):
            relevant_count += 1
    
    # Get smart suggestions from AI analysis
    smart_suggestions = ai_analysis.get('ai_analysis', {}).get('smart_suggestions', [])
    
    # Identify false positives
    false_positives = []
    for item in pii_items:
        if not item.get('relevant', True):
            reason = item.get('relevance_reason', 'Not relevant')
            text = item.get('text', '')[:30]
            false_positives.append(f"{reason}: {text}")
    
    return {
        "count": len(pii_items),
        "relevant_count": relevant_count,
        "by_type": by_type,
        "samples": {k: v[:3] for k, v in samples.items()},
        "critical_risk_count": risk_counts["critical"],
        "high_risk_count": risk_counts["high"],
        "medium_risk_count": risk_counts["medium"],
        "low_risk_count": risk_counts["low"],
        "false_positive_indicators": list(set(false_positives))[:5],
        "smart_suggestions": smart_suggestions[:5],
        "ai_analysis_summary": {
            "overall_risk": ai_analysis.get('ai_analysis', {}).get('risk_assessment', {}).get('overall_risk', 'medium'),
            "jurisdiction_compliance": ai_analysis.get('ai_analysis', {}).get('risk_assessment', {}).get('jurisdiction_compliance', 'needs_review'),
            "gdpr_applicable": 'GDPR' in jurisdiction or 'EU' in jurisdiction,
            "popia_applicable": 'ZA' in jurisdiction
        }
    }

def _create_pii_violations(pii_items: List[Dict], doc_id: str, jurisdiction: str) -> List[Dict]:
    """Create structured PII violations from detected PII items"""
    violations = []
    
    # Group PII by type for better violation reporting
    grouped_pii = {}
    for item in pii_items:
        pii_type = item.get('type', 'unknown')
        if pii_type not in grouped_pii:
            grouped_pii[pii_type] = []
        grouped_pii[pii_type].append(item)
    
    for pii_type, items in list(grouped_pii.items())[:10]:  # Limit to 10 violation types
        # Take first 3 items for display
        sample_items = items[:3]
        total_count = len(items)
        
        # Determine severity
        max_risk = max([item.get('risk_level', 'medium') for item in items], 
                      key=lambda x: ['critical', 'high', 'medium', 'low'].index(x))
        
        severity_map = {
            'critical': 'CRITICAL',
            'high': 'HIGH',
            'medium': 'MEDIUM',
            'low': 'LOW'
        }
        
        severity = severity_map.get(max_risk, 'MEDIUM')
        
        # Create violation
        violation = {
            "violation_id": f"v_pii_{doc_id}_{pii_type}_{int(time.time())}",
            "category": "pii_detection",
            "matched_text": f"Contains {total_count} {pii_type} items",
            "ai_reasoning": f"{total_count} {pii_type} items detected. " +
                           f"Risk level: {max_risk}. " +
                           f"{'Some appear to be false positives.' if any(not i.get('relevant', True) for i in items) else 'All appear to be relevant PII.'}",
            "severity": severity,
            "remediation": f"Review and remove/anonymize {pii_type} items. " +
                          f"Consider using generic placeholders for contact information.",
            "pii_items": sample_items,
            "pii_details": {
                "type": pii_type,
                "count": total_count,
                "sample_texts": [item.get('text', '')[:50] for item in sample_items],
                "risk_level": max_risk,
                "suggestions": items[0].get('suggestion', '') if items else ''
            },
            "regulation": "GDPR Article 6" if 'EU' in jurisdiction else 
                         "POPIA Section 19" if 'ZA' in jurisdiction else 
                         "Data Protection Act",
            "regulatory_reference": "Data Protection Regulations",
            "confidence": 0.85
        }
        
        violations.append(violation)
    
    return violations

def _generate_smart_questions(violations: List[Dict], pii_summary: Dict, doc: Dict) -> List[Dict]:
    """Generate context-aware compliance questions based on violations and PII findings"""
    try:
        from openai import AzureOpenAI
        
        client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
        )
        
        # Prepare context
        violation_summary = {}
        for violation in violations:
            category = violation.get('category', 'unknown')
            if category not in violation_summary:
                violation_summary[category] = 0
            violation_summary[category] += 1
        
        # Create prompt
        prompt = f"""Generate compliance questions for a financial document.

DOCUMENT CONTEXT:
- Jurisdiction: {doc.get('jurisdiction', 'UK')}
- Type: {doc.get('document_type', 'marketing_material')}
- Distribution: {doc.get('distribution_media', 'unknown')}
- Risk Score: {doc.get('risk_score', 0)}/100

VIOLATIONS FOUND:
{json.dumps(violation_summary, indent=2)}

PII FINDINGS:
- Total PII items: {pii_summary.get('count', 0)}
- High risk PII: {pii_summary.get('high_risk_count', 0)}
- PII types: {', '.join(pii_summary.get('by_type', {}).keys())}

GENERATE 5-10 SPECIFIC questions that:
1. Address the violations found
2. Focus on high-risk areas (especially PII)
3. Are actionable (Yes/No/Uncertain answers)
4. Include jurisdiction-specific considerations
5. Provide clear help text and regulatory references

FORMAT EACH QUESTION AS:
{{
  "question_id": "q_[UUID]",
  "question": "Clear verification question",
  "verification_question": "Alternative phrasing if needed",
  "severity": "critical|high|medium|low",
  "category": "pii_detection|risk_disclosure|past_performance|etc",
  "answer_options": ["yes", "no", "uncertain"],
  "help_text": "What to look for and why it matters",
  "required": true/false,
  "exact_fix": "Specific remediation if answer is 'no'",
  "regulatory_reference": "Specific regulation (e.g., 'FAIS Act s2.1')"
}}

OUTPUT JSON:
{{
  "questions": [
    // array of question objects
  ]
}}"""

        response = client.chat.completions.create(
            model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
            messages=[
                {
                    "role": "system",
                    "content": "You are a compliance officer generating specific, actionable questions for document review. Make questions precise and relevant to the findings."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        questions = result.get('questions', [])
        
        # Add UUIDs and timestamps
        for i, q in enumerate(questions):
            q['question_id'] = q.get('question_id', f"q_{uuid.uuid4().hex[:8]}_{i}")
            q['generated_at'] = datetime.utcnow().isoformat() + 'Z'
            q['document_id'] = doc.get('id', '')
            q['jurisdiction'] = doc.get('jurisdiction', 'UK')
        
        logger.info(f"🤖 Generated {len(questions)} smart questions")
        return questions
        
    except Exception as e:
        logger.error(f"❌ Smart question generation failed: {e}")
        return []

def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    ENHANCED ENTERPRISE scan handler with full context awareness
    """
    from function_app_pkg.shared.http_utils import json_response
    
    logger.info("=" * 80)
    logger.info("🚀 ENHANCED ENTERPRISE COMPLIANCE SCAN STARTING")
    logger.info("=" * 80)
    
    start_time = time.time()
    
    try:
        # ===== STEP 1: AUTHENTICATION =====
        logger.info("Step 1: Authentication")
        
        if user is not None:
            current_user = user
            logger.info(f"✅ User from decorator: {user}")
        else:
            try:
                from function_app_pkg.api.auth import authenticate_request
                auth_user, error = authenticate_request(req)
                if error:
                    return json_response(401, error=error)
                current_user = auth_user
            except Exception as e:
                logger.error(f"❌ Auth failed: {e}")
                return json_response(401, error="Authentication failed")
        
        # Extract org_id
        if hasattr(current_user, 'organization_id'):
            org_id = current_user.organization_id
        elif isinstance(current_user, dict):
            org_id = current_user.get('organization_id')
        else:
            org_id = None
        
        if not org_id:
            logger.error("❌ No organization_id found")
            return json_response(400, error="Organization ID required")
        
        logger.info(f"🏢 Organization: {org_id}")

         # ===== STEP 1b: SUBSCRIPTION LIMIT CHECK =====
        from function_app_pkg.core.usage_service import (
            check_scan_limit,
            check_jurisdiction_access,
            increment_scan_count,
        )

        allowed, reason = check_scan_limit(org_id)
        if not allowed:
            logger.warning(f"Scan blocked for org {org_id}: {reason}")
            return json_response(402, data={
                'error': reason,
                'upgrade_url': '/settings/billing',
                'limit_reached': True,
            })
        # ===== STEP 1b: SUBSCRIPTION LIMIT CHECK =====
        from function_app_pkg.core.usage_service import (
            check_scan_limit,
            check_jurisdiction_access,
            increment_scan_count,
        )

        allowed, reason = check_scan_limit(org_id)
        if not allowed:
            logger.warning(f"Scan blocked for org {org_id}: {reason}")
            return json_response(402, data={
                'error': reason,
                'upgrade_url': '/settings/billing',
                'limit_reached': True,
            })
        
        # ===== STEP 2: GET DOCUMENT =====
        logger.info("Step 2: Get document")
        
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        try:
            from function_app_pkg.core.database import get_container
            
            # Get document container
            container = get_container("documents")
            
            # Try direct read first
            try:
                doc = container.read_item(item=doc_id, partition_key=org_id)
                logger.info(f"✅ Document found with partition key: {org_id}")
            except Exception:
                # Fallback to cross-partition query
                logger.warning(f"⚠️ Document read with partition key failed, trying cross-partition...")
                query = "SELECT * FROM c WHERE c.id = @id AND c.type = 'document'"
                items = list(container.query_items(
                    query=query,
                    parameters=[{"name": "@id", "value": doc_id}],
                    enable_cross_partition_query=True
                ))
                
                if not items:
                    return json_response(404, error=f"Document not found: {doc_id}")
                
                doc = items[0]
                logger.info(f"✅ Document found via cross-partition query")
            
            # Verify organization access
            actual_org_id = doc.get('organization_id')
            if not actual_org_id:
                logger.error(f"❌ Document has no organization_id")
                return json_response(500, error="Document missing organization_id")
            
            if actual_org_id != org_id:
                # Check if user is super admin
                is_super_admin = False
                if hasattr(current_user, 'has_role'):
                    is_super_admin = current_user.has_role('Platform.SuperAdmin')
                elif isinstance(current_user, dict):
                    is_super_admin = 'Platform.SuperAdmin' in current_user.get('roles', [])
                
                if not is_super_admin:
                    logger.warning(f"⚠️ Org ID mismatch: user={org_id}, doc={actual_org_id}")
                    return json_response(403, error="Access denied")
                
                logger.info(f"✅ Super admin override - accessing doc from org {actual_org_id}")
            
            org_id = actual_org_id  # Use document's org_id
            
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            logger.exception(e)
            return json_response(500, error=f"Database error: {str(e)[:200]}")
        
        logger.info(f"✅ Document: {doc.get('filename', 'Unknown')}")
        logger.info(f"✅ Using org_id: {org_id}")
        
        # ===== STEP 3: EXTRACT TEXT =====
        logger.info("Step 3: Extract text")
        
        text = ""
        text_sources = [
            ('extracted_text', 'extracted_text'),
            ('text_content', 'text_content'),
            ('text', 'text'),
            ('upload_metadata.text', 'upload_metadata.text'),
            ('content', 'content')
        ]
        
        for field_name, log_name in text_sources:
            if field_name == 'upload_metadata.text':
                upload_meta = doc.get('upload_metadata', {})
                field_value = upload_meta.get('text', '')
            else:
                field_value = doc.get(field_name, '')
            
            if field_value and len(str(field_value).strip()) > 10:
                text = str(field_value)
                logger.info(f"📝 Found text in '{log_name}': {len(text)} chars")
                break
        
        # Extract from storage if needed
        extraction_metadata = {}
        if not text or len(text.strip()) < 10:
            logger.warning("⚠️ No text in database, checking blob storage...")
            try:
                from function_app_pkg.core.storage import get_blob_service_client
                from function_app_pkg.core.text_extractor import extractor
                
                blob_name = doc.get('blob_name') or doc.get('storage_path') or f"documents/{doc_id}"
                filename = doc.get('filename', 'document.txt')
                mimetype = doc.get('mimetype', 'text/plain')
                
                blob_service_client = get_blob_service_client()
                container_client = blob_service_client.get_container_client("documents")
                blob_client = container_client.get_blob_client(blob_name)
                
                download_stream = blob_client.download_blob()
                file_content = download_stream.readall()
                
                extraction_result = extractor.extract(file_content, filename, mimetype)
                
                if extraction_result.get('success'):
                    text = extraction_result.get('text', '')
                    extraction_metadata = extraction_result.get('metadata', {})
                    logger.info(f"✅ Extracted text from storage: {len(text)} chars")
                    
                    # Update document with extraction metadata
                    if extraction_metadata:
                        doc['extraction_metadata'] = extraction_metadata
                    
                    # Update document text
                    doc['extracted_text'] = text
                    doc['text_content'] = text
                else:
                    logger.error(f"❌ Failed to extract text from storage")
                    
            except Exception as e:
                logger.warning(f"⚠️ Blob extraction failed: {e}")
        
        # Final validation
        if not text or len(text.strip()) < 10:
            logger.error("❌ Document has no readable text after all attempts")
            return json_response(400, error="Document has no readable text. Please re-upload.")
        
        logger.info(f"📝 FINAL Text length: {len(text)} chars")
        
        # Truncate if too long for ENHANCED scanner
        MAX_TEXT_LENGTH = 50000  # Matches EnhancedRAGScanner limit
        if len(text) > MAX_TEXT_LENGTH:
            logger.warning(f"⚠️ Text truncated from {len(text)} to {MAX_TEXT_LENGTH}")
            text = text[:MAX_TEXT_LENGTH]
            doc['text_truncated'] = True
            doc['original_text_length'] = len(text)
        
        # ===== STEP 4: LANGUAGE DETECTION =====
        logger.info("Step 4: Language detection")
        
        try:
            from function_app_pkg.core.translator import translator
            
            detected_lang = translator.detect_language(text)
            logger.info(f"🌍 Detected language: {detected_lang}")
            
            if detected_lang != 'en':
                logger.info(f"🔄 Translating from {detected_lang} to English...")
                try:
                    translated_text = translator.translate(text, target_language='en')
                    if translated_text and len(translated_text) > 10:
                        text = translated_text
                        doc['original_language'] = detected_lang
                        doc['text_translated'] = True
                        logger.info(f"✅ Translated to English: {len(text)} chars")
                    else:
                        logger.warning("⚠️ Translation returned empty text")
                        doc['original_language'] = 'en'
                        doc['text_translated'] = False
                except Exception as trans_error:
                    logger.warning(f"⚠️ Translation failed: {trans_error}")
                    doc['original_language'] = 'en'
                    doc['text_translated'] = False
            else:
                doc['original_language'] = 'en'
                doc['text_translated'] = False
                
        except Exception as e:
            logger.warning(f"⚠️ Translation service unavailable: {e}")
            doc['original_language'] = 'en'
            doc['text_translated'] = False
        
        # ===== STEP 5: PREPARE ENHANCED SCAN CONTEXT =====
        logger.info("Step 5: Prepare enhanced scan context")
        
        jurisdiction = doc.get('jurisdiction')
        if not jurisdiction:
            logger.error(f"❌ Document {doc_id} has no jurisdiction set")
            return json_response(400, error="Document has no jurisdiction. Please re-upload with a jurisdiction selected.")

        logger.info(f"📍 Scanning with jurisdiction: {jurisdiction}")
        # Jurisdiction access check
        j_allowed, j_reason = check_jurisdiction_access(org_id, jurisdiction)
        if not j_allowed:
            logger.warning(f"Jurisdiction {jurisdiction} blocked for org {org_id}: {j_reason}")
            return json_response(402, data={
                'error': j_reason,
                'upgrade_url': '/settings/billing',
                'limit_reached': True,
            })
        
        # Jurisdiction access check
        j_allowed, j_reason = check_jurisdiction_access(org_id, jurisdiction)
        if not j_allowed:
            logger.warning(f"Jurisdiction {jurisdiction} blocked for org {org_id}: {j_reason}")
            return json_response(402, data={
                'error': j_reason,
                'upgrade_url': '/settings/billing',
                'limit_reached': True,
            })
        # Get briefing context
        briefing = doc.get('briefing', {})
        if briefing:
            logger.info(f"📋 Using briefing context: {briefing.get('marketing_type', 'Unknown')}")
        
        # Get image analysis from extraction metadata
        image_analysis = extraction_metadata.get('image_analysis', '')
        if image_analysis:
            logger.info(f"🖼️ Image analysis available: {len(image_analysis)} chars")
        
        # Get metadata for page mapping
        metadata = doc.get('metadata', {})
        if extraction_metadata:
            # Merge extraction metadata with existing metadata
            if 'page_map' in extraction_metadata:
                metadata['page_map'] = extraction_metadata['page_map']
                logger.info(f"📄 Page map from extraction: {len(metadata['page_map'])} pages")
        
        # ===== STEP 6: RUN ENHANCED ENTERPRISE SCAN =====
        logger.info("Step 6: Run enhanced enterprise scan")
        
        # Get scanner
        try:
            scanner_instance, scan_mode = get_scanner_safe()
        except Exception as e:
            logger.error(f"❌ Scanner failed: {e}")
            return json_response(500, error=f"Scanner unavailable: {e}")
        
        logger.info(f"✅ Using scanner: {scan_mode}")
        
        # Run ENHANCED scan with all context
        violations = []
        stats = {
            "scan_started": datetime.utcnow().isoformat(),
            "method": scan_mode,
            "text_length": len(text),
            "jurisdiction": jurisdiction,
            "has_briefing": bool(briefing),
            "has_image_analysis": bool(image_analysis),
            "errors": []
        }
        
        try:
            violations, scan_stats = scanner_instance.scan(
                text=text,
                document_id=doc_id,
                jurisdiction=jurisdiction,
                metadata=metadata,
                briefing=briefing,  # ✅ BRIEFING CONTEXT!
                image_analysis=image_analysis,  # ✅ IMAGE CONTEXT!
                organization_id=org_id
            )
            stats.update(scan_stats)
            
            logger.info(f"✅ Enhanced scan complete: {len(violations)} violations")
            
        except Exception as e:
            logger.error(f"❌ Enhanced scan execution failed: {e}")
            logger.exception(e)
            stats["errors"].append(f"Scan failed: {str(e)[:200]}")
            violations = []
        
        # ===== STEP 7: LANGUAGE SERVICE ANALYSIS (ALWAYS RUN) =====
        logger.info("Step 7: Run language service for PII detection")
        
        pii_items = []
        pii_summary = {}
        pii_analysis = {}
        
        # CRITICAL: Always run PII detection, even if scan failed/timed out
        try:
            from function_app_pkg.core.language_analyzer import enhance_scan_with_language_analysis
            
            logger.info("🔬 Running Language Service for PII detection...")
            
            # Detect document type intelligently
            document_type = 'marketing_material'  # default
            
            # Check filename for CV/Resume
            filename = doc.get('filename', '').lower()
            if any(term in filename for term in ['cv', 'resume', 'curriculum']):
                document_type = 'cv'
                logger.info(f"🔍 Detected CV/Resume from filename: {filename}")
            
            # Check briefing if available
            if briefing:
                content_type = briefing.get('content_type', '')
                dist = briefing.get('distribution_media', '')
                
                # Map to document types
                if 'cv' in content_type.lower() or 'resume' in content_type.lower():
                    document_type = 'cv'
                elif dist == 'website':
                    document_type = 'website'
                elif dist == 'email':
                    document_type = 'email'
                elif content_type:
                    document_type = content_type
            
            # Quick content-based detection
            if document_type == 'marketing_material':
                text_sample = text[:500].lower()
                cv_indicators = ['curriculum vitae', 'resume', 'work experience', 'education:', 'skills:', 'objective:', 'professional summary']
                
                if sum(1 for indicator in cv_indicators if indicator in text_sample) >= 2:
                    document_type = 'cv'
                    logger.info(f"🔍 Detected CV/Resume from content analysis")
            
            logger.info(f"📄 Document type for PII analysis: {document_type}")
            
            # Store detected type
            doc['detected_document_type'] = document_type
            
            # Run language analysis - ALWAYS, regardless of scan status
            language_results = enhance_scan_with_language_analysis(
                text=text,
                jurisdiction=jurisdiction,
                existing_violations=violations,  # May be empty if scan failed
                document_type=document_type
            )
            
            # Extract PII data
            pii_items = language_results.get('pii_detected', [])
            pii_summary = language_results.get('pii_summary', {})
            
            logger.info(f"📊 Language Service Results:")
            logger.info(f"   Document Type: {document_type}")
            logger.info(f"   PII items detected: {len(pii_items)}")
            logger.info(f"   PII summary count: {pii_summary.get('count', 0)}")
            
            if pii_items:
                logger.info(f"🔍 PII Types found: {list(set([item.get('type') for item in pii_items]))}")
                
                # Run AI analysis on PII with document type awareness
                try:
                    pii_analysis = _analyze_pii_with_ai(pii_items, text, jurisdiction, document_type)
                    if pii_analysis.get('enhanced_pii_items'):
                        pii_items = pii_analysis['enhanced_pii_items']
                        logger.info(f"🤖 AI enhanced {len(pii_items)} PII items")
                        
                        # Re-aggregate summary with AI enhancements
                        pii_summary = _aggregate_pii_summary(pii_items, pii_analysis, jurisdiction)
                except Exception as ai_error:
                    logger.warning(f"⚠️ AI PII analysis failed: {ai_error}")
                    pii_analysis = _fallback_pii_analysis(pii_items, jurisdiction)
                    pii_summary = _aggregate_pii_summary(pii_items, pii_analysis, jurisdiction)
            
            # CRITICAL: Only create PII violations for NON-CV documents
            if pii_items and document_type != 'cv':
                pii_violations = _create_pii_violations(pii_items, doc_id, jurisdiction)
                if pii_violations:
                    violations.extend(pii_violations)
                    logger.info(f"✅ Added {len(pii_violations)} PII violations")
            else:
                if document_type == 'cv':
                    logger.info(f"ℹ️ Document is CV/Resume - PII is expected and appropriate, not creating violations")
                    logger.info(f"   PII items logged for reference: {len(pii_items)}")

        except ImportError as ie:
            logger.error(f"❌ Language analyzer import failed: {ie}")
            logger.error(f"   Check that language_analyzer.py exists and is properly configured")
            stats["errors"].append(f"Language analyzer unavailable: {str(ie)[:100]}")
        except Exception as e:
            logger.error(f"❌ Language analysis failed: {e}")
            logger.exception(e)
            stats["errors"].append(f"Language analysis error: {str(e)[:100]}")
        
        # ===== STEP 8: GENERATE SMART QUESTIONS =====
        logger.info("Step 8: Generate smart compliance questions")
        
        questions = []
        try:
            if violations or pii_items:
                questions = _generate_smart_questions(violations, pii_summary, doc)
                
                if questions:
                    logger.info(f"✅ Generated {len(questions)} smart questions")
                    
                    # Store questions in document
                    doc['compliance_questions'] = questions
                    doc['questionnaire_status'] = 'pending'
                    doc['questions_generated_at'] = datetime.utcnow().isoformat() + 'Z'
                    doc['status'] = 'questions_generated'
                    doc['workflow_status'] = 'questions_generated'
                    
                    stats['questions_generated'] = len(questions)
                else:
                    logger.warning("⚠️ Question generation returned empty")
            else:
                logger.info("ℹ️ No violations found, skipping question generation")
                
        except Exception as e:
            logger.error(f"❌ Question generation failed: {e}")
            stats["errors"].append(f"Question generation: {str(e)[:100]}")
        
        # ===== STEP 9: CALCULATE RISK SCORE =====
        logger.info("Step 9: Calculate risk score")

        # ── Compliance risk (from scanner — already 0-90) ─────────────────
        compliance_risk = stats.get('overall_risk_score', 0)

        # ── PII risk (separate, capped at 20 so it can't dominate) ───────
        # PII in a document is a data protection concern, not a compliance
        # violation. It contributes to the overall score but is capped so
        # a CV full of names never hits 100% just from PII.
        pii_risk = 0
        if pii_summary and pii_summary.get('count', 0) > 0:
            raw_pii_risk = (
                pii_summary.get('critical_risk_count', 0) * 8  +
                pii_summary.get('high_risk_count',     0) * 5  +
                pii_summary.get('medium_risk_count',   0) * 2
            )
            pii_risk = min(20, raw_pii_risk)   # Hard cap: PII max 20 pts

        # ── Combined score ────────────────────────────────────────────────
        risk_score = min(100, compliance_risk + pii_risk)

        # ── Backfill PII component into score_explanation ────────────────
        score_explanation = stats.get('score_explanation', {})
        if score_explanation:
            pii_detail_parts = []
            if pii_summary.get('critical_risk_count', 0):
                pii_detail_parts.append(f"{pii_summary['critical_risk_count']} critical-risk PII item(s)")
            if pii_summary.get('high_risk_count', 0):
                pii_detail_parts.append(f"{pii_summary['high_risk_count']} high-risk PII item(s)")
            if pii_summary.get('medium_risk_count', 0):
                pii_detail_parts.append(f"{pii_summary['medium_risk_count']} medium-risk PII item(s)")

            pii_detail = (
                "No significant PII risk detected." if not pii_detail_parts
                else f"PII detected: {'; '.join(pii_detail_parts)}. (+{pii_risk} pts, capped at 20)"
            )

            score_explanation.setdefault('components', {})['pii_risk'] = {
                'score': pii_risk,
                'label': 'Data protection (PII)',
                'detail': pii_detail,
            }
            score_explanation.setdefault('calculation', {}).update({
                'pii_risk': pii_risk,
                'combined_score': risk_score,
            })

            # Plain-English overall summary for the score badge tooltip
            all_clean = (compliance_risk == 0 and pii_risk == 0)
            score_explanation['overall_summary'] = (
                "No issues detected. This document appears compliant."
                if all_clean else
                f"Score {risk_score}/100 — "
                + (score_explanation.get('summary', '') or '')
                + (f" {pii_detail}" if pii_risk > 0 else "")
            )
        else:
            # Scanner didn't produce an explanation (e.g. no violations path)
            score_explanation = {
                'overall_summary': (
                    f"Score {risk_score}/100. "
                    f"Compliance violations contributed {compliance_risk} pts; "
                    f"PII risk contributed {pii_risk} pts (capped at 20)."
                ),
                'components': {
                    'compliance_violations': {'score': compliance_risk, 'label': 'Regulatory compliance'},
                    'pii_risk':              {'score': pii_risk,        'label': 'Data protection (PII)'},
                },
                'calculation': {
                    'compliance_score': compliance_risk,
                    'pii_risk': pii_risk,
                    'combined_score': risk_score,
                }
            }

        # ── Determine outcome ─────────────────────────────────────────────
        if not violations and risk_score < 30:
            outcome = 'compliant'
            recommendation = '✅ GREEN: No violations detected'
        elif risk_score >= 80:
            outcome = 'non_compliant'
            recommendation = f'🚨 RED: High risk ({risk_score}/100)'
        elif risk_score >= 45:
            outcome = 'requires_review'
            recommendation = f'⚠️ AMBER: Review needed ({risk_score}/100)'
        else:
            outcome = 'compliant'
            recommendation = f'✅ GREEN: Minor issues ({risk_score}/100)'

        logger.info(f"📊 Compliance: {compliance_risk}/100  PII: {pii_risk}/20  Combined: {risk_score}/100  Outcome: {outcome}")

        # ===== STEP 9.5: AI WORKFLOW RECOMMENDATION =====
        logger.info("Step 9.5: AI workflow recommendation")

        workflow_recommendation = None
        try:
            from function_app_pkg.core.workflow_recommendation import recommend_workflow
            
            # Get available workflows for this org
            from function_app_pkg.core.database import get_container
            workflows_container = get_container('documents')
            
            workflows_query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'workflow'
            AND c.status = 'active'
            """
            
            available_workflows = list(workflows_container.query_items(
                query=workflows_query,
                parameters=[{"name": "@org_id", "value": org_id}],
                partition_key=org_id
            ))
            
            if available_workflows:
                logger.info(f"📋 Found {len(available_workflows)} active workflows")
                
                workflow_recommendation = recommend_workflow(
                    document=doc,
                    violations=violations,
                    risk_score=risk_score,
                    jurisdiction=jurisdiction,
                    available_workflows=available_workflows
                )
                
                logger.info(f"🤖 Recommended: {workflow_recommendation.get('recommended_workflow_name')}")
                logger.info(f"   Confidence: {workflow_recommendation.get('confidence')}")
                logger.info(f"   Reasoning: {workflow_recommendation.get('reasoning')[:100]}...")
            else:
                logger.info("ℹ️ No workflows configured for this organization")
                
        except Exception as e:
            logger.warning(f"⚠️ Workflow recommendation failed: {e}")
        
        # ===== STEP 10: UPDATE DATABASE =====
        logger.info("Step 10: Update database")
        
        try:
            # Ensure text is saved
            if text and len(text.strip()) > 0:
                doc['extracted_text'] = text
                doc['text_content'] = text
                doc['text_length'] = len(text)
            
            # Update document with scan results
            doc['status'] = 'scanned'
            doc['workflow_status'] = 'scanned'
            doc['compliance_outcome'] = outcome
            doc['risk_score'] = risk_score
            doc['violations'] = violations
            doc['violation_count'] = len(violations)
            doc['violations_count'] = len(violations)
            doc['scan_stats'] = stats
            doc['scanned_at'] = datetime.utcnow().isoformat() + 'Z'
            doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            doc['scan_duration_seconds'] = round(time.time() - start_time, 2)
            doc['scan_mode'] = scan_mode
            doc['recommendation'] = recommendation
            
            # Save PII data
            doc['pii_items'] = pii_items
            doc['pii_summary'] = pii_summary
            doc['pii_analysis'] = pii_analysis.get('ai_analysis', {}) if pii_analysis else {}
            
            # Save context used
            doc['scan_context'] = {
                'had_briefing': bool(briefing),
                'had_image_analysis': bool(image_analysis),
                'used_page_map': 'page_map' in metadata
            }
            
            logger.info(f"📋 Saving PII: {len(pii_items)} items, summary: {pii_summary.get('count', 0)}")
            if pii_items:
                logger.info(f"📊 PII types: {list(pii_summary.get('by_type', {}).keys())}")
            
            # Get actual org_id from doc
            actual_org_id = doc.get('organization_id')
            if not actual_org_id:
                logger.error(f"❌ Document has no organization_id")
                return json_response(500, error="Document missing organization_id")
            
            # Direct upsert to Cosmos
            from function_app_pkg.core.database import get_container
            container = get_container("documents")
            
            updated = container.upsert_item(doc)
            logger.info(f"✅ Document updated in database")
            
        except Exception as e:
            logger.error(f"❌ Database update failed: {e}")
            logger.exception(e)
            # Continue with response even if DB update fails
        
        
          # ===== STEP 10b: INCREMENT SCAN COUNTER =====
        try:
            new_count = increment_scan_count(org_id)
            logger.info(f"Scan count incremented for org {org_id}: {new_count}")
        except Exception as e:
            logger.warning(f"Failed to increment scan count: {e}")
        

        # ===== STEP 11: BUILD RESPONSE =====
        logger.info("Step 11: Build response")
        
        duration = time.time() - start_time
        
        response_data = {
            'document_id': doc_id,
            'filename': doc.get('filename', 'Unknown'),
            'jurisdiction': jurisdiction,
            'compliance_outcome': outcome,
            'risk_score': risk_score,
            'violations_count': len(violations),
            'questions_generated': len(questions),
            'questions': questions,
            'violations': violations,
            'recommendation': recommendation,
            'scan_mode': scan_mode,
            'rag_enabled': True,
            'duration_seconds': round(duration, 2),
            'text_length': len(text),
            'pii_summary': pii_summary,
            'pii_items_count': len(pii_items),
            'pii_items_preview': pii_items[:5] if pii_items else [],
            'scan_context': {
                'briefing_used': bool(briefing),
                'image_analysis_used': bool(image_analysis),
                'enterprise_intelligence': True
            },
            'stats': {
                'method': stats.get('method'),
                'text_length': stats.get('text_length'),
                'chunks_analyzed': stats.get('chunks_analyzed', 0),
                'violations_found': len(violations),
                'critical_count': stats.get('critical_count', 0),
                'high_count': stats.get('high_count', 0),
                'medium_count': stats.get('medium_count', 0),
                'low_count': stats.get('low_count', 0),
                'pii_detected': len(pii_items),
                'enterprise_mode': True
            },
            # ── Score breakdown (new fields) ─────────────────────────────
            'compliance_risk_score': compliance_risk,   # 0-90, violations only
            'pii_risk_score': pii_risk,                 # 0-20, PII only
            # risk_score (already in response) = compliance + pii

            'score_explanation': score_explanation,
            # score_explanation shape:
            # {
            #   overall_summary: "Human readable explanation of the final score",
            #   components: {
            #     compliance_violations: { score, label, detail },
            #     pii_risk:              { score, label, detail },
            #   },
            #   summary: "Violations-only summary sentence",
            #   doc_length_note: "...",
            #   calculation: { ... raw numbers ... }
            # }
            'database_state': {
                'status_updated': True,
                'expected_status': 'scanned',
                'expected_outcome': outcome,
                'expected_risk_score': risk_score,
                'organization_id': actual_org_id,
                'has_questions': len(questions) > 0,
                'has_pii_summary': bool(pii_summary),
                'pii_items_saved': len(pii_items)
            }
        }
        
        # Add AI insights if available
        if pii_analysis and pii_analysis.get('ai_analysis'):
            response_data['ai_insights'] = {
                'pii_risk_assessment': pii_analysis['ai_analysis'].get('risk_assessment', {}),
                'smart_suggestions': pii_analysis['ai_analysis'].get('smart_suggestions', [])
            }
        
        # Add language analysis if available
        if 'language_results' in locals():
            response_data['language_analysis'] = {
                'pii_count': language_results.get('pii_count', 0),
                'entity_count': language_results.get('entity_count', 0),
                'promotional_score': language_results.get('promotional_score', 0)
            }
        
        if stats.get("errors"):
            response_data['scan_errors'] = stats["errors"][:3]
        
        if stats.get("warnings"):
            response_data['scan_warnings'] = stats["warnings"]
        
        logger.info("=" * 80)
        logger.info(f"✅ ENHANCED ENTERPRISE SCAN COMPLETE: {outcome.upper()}")
        logger.info(f"   Document: {doc_id}")
        logger.info(f"   Questions: {len(questions)} generated")
        logger.info(f"   Context Used: Briefing={bool(briefing)}, Image={bool(image_analysis)}")
        logger.info(f"   PII: {pii_summary.get('count', 0)} items ({pii_summary.get('relevant_count', 0)} relevant)")
        logger.info(f"   PII Types: {list(pii_summary.get('by_type', {}).keys())}")
        logger.info(f"   Violations: {len(violations)}")
        logger.info(f"   Compliance Risk: {compliance_risk}/100")
        logger.info(f"   PII Risk: {pii_risk}/20")
        logger.info(f"   Combined Risk Score: {risk_score}/100")
        logger.info(f"   Duration: {duration:.2f}s")
        logger.info("=" * 80)
        
        gc.collect()
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ FATAL ERROR")
        logger.error(f"Error: {e}")
        logger.error("=" * 80)
        logger.exception(e)

        gc.collect()

        # Guard: only log audit if we got far enough to have these in scope
        _safe_org_id = locals().get('actual_org_id') or locals().get('org_id') or 'unknown'
        _safe_user = locals().get('current_user')
        _safe_doc_id = locals().get('doc_id') or 'unknown'
        _safe_doc = locals().get('doc') or {}
        _safe_duration = locals().get('duration') or (time.time() - start_time)

        if _safe_user:
            try:
                _log_scan_audit(
                    org_id=_safe_org_id,
                    user=_safe_user,
                    doc_id=_safe_doc_id,
                    doc=_safe_doc,
                    scan_result={'error': str(e)},
                    duration=_safe_duration,
                )
            except Exception:
                logger.warning("⚠️ Could not log audit for failed scan")

        return json_response(500, error=f"Scan failed: {str(e)[:200]}")