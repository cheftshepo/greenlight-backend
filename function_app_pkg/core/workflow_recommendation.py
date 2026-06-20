"""
AI-Powered Workflow Recommendation Engine
==========================================
Analyzes documents and recommends optimal workflows with reasoning.
"""

import os
import json
import logging
from typing import Dict, List, Optional
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

def recommend_workflow(
    document: Dict,
    violations: List[Dict],
    risk_score: int,
    jurisdiction: str,
    available_workflows: List[Dict]
) -> Dict:
    """
    AI recommends which workflow a document should follow.
    
    Returns:
    {
        'recommended_workflow_id': str,
        'recommended_workflow_name': str,
        'confidence': float,
        'reasoning': str,
        'risk_factors': List[str],
        'alternative_workflows': List[Dict]
    }
    """
    try:
        client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
            timeout=30.0
        )
        
        # Build workflow context
        workflow_descriptions = []
        for wf in available_workflows:
            stages_summary = f"{len(wf.get('stages', []))} stages"
            workflow_descriptions.append({
                'id': wf.get('id'),
                'name': wf.get('name'),
                'description': wf.get('description', ''),
                'stages': stages_summary,
                'jurisdictions': wf.get('jurisdictions', [])
            })
        
        # Prepare violation summary
        violation_categories = {}
        for v in violations[:10]:  # Top 10
            cat = v.get('category', 'unknown')
            violation_categories[cat] = violation_categories.get(cat, 0) + 1
        
        prompt = f"""You are a compliance workflow expert. Recommend the BEST workflow for this document.

DOCUMENT ANALYSIS:
- Filename: {document.get('filename')}
- Jurisdiction: {jurisdiction}
- Risk Score: {risk_score}/100
- Violations Found: {len(violations)}
- Violation Types: {json.dumps(violation_categories)}

AVAILABLE WORKFLOWS:
{json.dumps(workflow_descriptions, indent=2)}

YOUR TASK:
1. Analyze document characteristics
2. Match to most appropriate workflow
3. Explain WHY this workflow fits best
4. Identify key risk factors that influenced decision
5. List alternative workflows with reasons why they're less suitable

OUTPUT JSON (strict format):
{{
  "recommended_workflow_id": "workflow_id_here",
  "recommended_workflow_name": "Workflow Name",
  "confidence": 0.85,
  "reasoning": "This document requires X because Y. The workflow includes stages for Z which address the main risks.",
  "risk_factors": ["cross_border_complexity", "high_risk_score", "multiple_violation_types"],
  "alternative_workflows": [
    {{
      "id": "other_workflow_id",
      "name": "Other Workflow",
      "why_not": "Lacks specialized review needed for cross-border issues"
    }}
  ]
}}

DECISION CRITERIA:
- Risk score 70+ → workflows with legal/senior review
- Cross-border (multiple jurisdictions) → workflows with international expertise
- PII violations → workflows with data protection specialists
- Financial claims → workflows with financial advisory
- Multiple high-severity violations → multi-stage approval required

Be specific and actionable. Your recommendation will be shown to users."""

        response = client.chat.completions.create(
            model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
            messages=[
                {
                    "role": "system",
                    "content": "You are a compliance workflow expert. Analyze documents and recommend optimal review workflows with clear reasoning."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        logger.info(f"✅ AI recommended workflow: {result.get('recommended_workflow_name')} (confidence: {result.get('confidence')})")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Workflow recommendation failed: {e}")
        # Fallback to default
        return {
            'recommended_workflow_id': available_workflows[0]['id'] if available_workflows else None,
            'recommended_workflow_name': available_workflows[0]['name'] if available_workflows else 'Standard Review',
            'confidence': 0.5,
            'reasoning': 'AI recommendation unavailable. Defaulting to standard workflow.',
            'risk_factors': ['ai_unavailable'],
            'alternative_workflows': [],
            'error': str(e)
        }
    
# =============================================================================
# GET WORKFLOW RECOMMENDATIONS
# =============================================================================

def handle_get_recommendations(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /workflows/recommendations
    Get AI-powered workflow recommendations for a document.
    
    Query params: document_id (required)
    """
    try:
        logger.info("🧠 Getting workflow recommendations...")
        
        document_id = req.params.get('document_id')
        if not document_id:
            return json_response(400, error="document_id query parameter required")
        
        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Get the document
        doc = get_document(document_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        # TENANT ISOLATION
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Get available workflows for this org
        db = get_db()
        container = db.get_container('documents')
        
        query = """
        SELECT c.id, c.name, c.description, c.stages, c.created_by
        FROM c 
        WHERE c.organization_id = @org_id 
        AND c.type = 'workflow'
        AND c.status = 'active'
        ORDER BY c.created_at DESC
        """
        
        workflows = list(container.query_items(
            query=query,
            parameters=[{"name": "@org_id", "value": org_id}],
            partition_key=org_id
        ))
        
        if not workflows:
            return json_response(200, data={
                'recommended_workflow_id': None,
                'recommended_workflow_name': None,
                'confidence': 0.0,
                'reasoning': 'No workflows available in your organization.',
                'risk_factors': [],
                'alternative_workflows': [],
                'workspace': _get_workspace_context(org_id),
            })
        
        # Analyze document for risk factors
        risk_factors = _analyze_document_for_risk_factors(doc)
        
        # Recommend workflow based on document analysis
        recommendation = _recommend_workflow(workflows, doc, risk_factors)
        
        # Get alternative workflows (top 2 runners-up)
        alternative_workflows = []
        for workflow in workflows:
            if workflow.get('id') != recommendation.get('id') and len(alternative_workflows) < 2:
                alternative_workflows.append({
                    'id': workflow.get('id'),
                    'name': workflow.get('name'),
                    'description': workflow.get('description', ''),
                    'why_not': 'Alternative option with similar requirements'
                })
        
        logger.info(f"✅ Generated workflow recommendation for document {document_id}")
        
        return json_response(200, data={
            'recommended_workflow_id': recommendation.get('id'),
            'recommended_workflow_name': recommendation.get('name'),
            'confidence': recommendation.get('confidence', 0.85),
            'reasoning': recommendation.get('reasoning', 'Document appears to be standard marketing material.'),
            'risk_factors': risk_factors,
            'alternative_workflows': alternative_workflows,
            'workspace': _get_workspace_context(org_id),
        })
        
    except Exception as e:
        logger.error(f"❌ Get recommendations failed: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get recommendations: {str(e)}")


# =============================================================================
# HELPER: Analyze document for risk factors
# =============================================================================

def _analyze_document_for_risk_factors(doc: Dict) -> List[str]:
    """Analyze document to identify risk factors for workflow selection."""
    risk_factors = []
    
    # Check risk score
    risk_score = doc.get('risk_score', 0)
    if risk_score >= 70:
        risk_factors.append('high_risk_score')
    elif risk_score >= 40:
        risk_factors.append('medium_risk_score')
    
    # Check violations
    violations_count = doc.get('violations_count', 0)
    if violations_count >= 5:
        risk_factors.append('multiple_violations')
    elif violations_count >= 1:
        risk_factors.append('has_violations')
    
    # Check jurisdiction
    jurisdiction = (doc.get('jurisdiction') or '').lower()
    if 'uk' in jurisdiction or 'fca' in jurisdiction:
        risk_factors.append('uk_regulated')
    if 'eu' in jurisdiction or 'esma' in jurisdiction:
        risk_factors.append('eu_regulated')
    if 'us' in jurisdiction or 'sec' in jurisdiction:
        risk_factors.append('us_regulated')
    
    # Check document type from briefing
    briefing = doc.get('briefing', {})
    marketing_type = briefing.get('marketing_type', '').lower()
    
    if 'pre' in marketing_type:
        risk_factors.append('pre_marketing')
    if 'product' in marketing_type:
        risk_factors.append('product_related')
    
    # Check if escalated before
    if doc.get('status') == 'escalated':
        risk_factors.append('previously_escalated')
    
    return risk_factors


# =============================================================================
# HELPER: Recommend workflow based on document analysis
# =============================================================================

def _recommend_workflow(workflows: List[Dict], doc: Dict, risk_factors: List[str]) -> Dict:
    """Recommend the most suitable workflow for a document."""
    if not workflows:
        return {}
    
    # Default to first workflow
    default_workflow = workflows[0]
    
    # If only one workflow, recommend it
    if len(workflows) == 1:
        return {
            'id': default_workflow.get('id'),
            'name': default_workflow.get('name'),
            'confidence': 0.9,
            'reasoning': 'Only workflow available in your organization.'
        }
    
    # Analyze document characteristics
    risk_score = doc.get('risk_score', 0)
    violations_count = doc.get('violations_count', 0)
    briefing = doc.get('briefing', {})
    marketing_type = briefing.get('marketing_type', '').lower()
    
    # Score each workflow based on suitability
    scored_workflows = []
    
    for workflow in workflows:
        score = 0.0
        reasoning_parts = []
        
        workflow_name = (workflow.get('name') or '').lower()
        workflow_desc = (workflow.get('description') or '').lower()
        
        # Check for comprehensive/review keywords (good for high risk)
        comprehensive_keywords = ['comprehensive', 'full', 'detailed', 'thorough', 'standard']
        express_keywords = ['express', 'quick', 'basic', 'simple']
        legal_keywords = ['legal', 'escalation', 'attorney']
        
        # High risk scores need comprehensive review
        if risk_score >= 60:
            if any(keyword in workflow_name for keyword in comprehensive_keywords):
                score += 0.3
                reasoning_parts.append('High risk score requires comprehensive review')
            elif any(keyword in workflow_name for keyword in express_keywords):
                score -= 0.2
        
        # Multiple violations need thorough review
        if violations_count >= 3:
            if any(keyword in workflow_name for keyword in comprehensive_keywords):
                score += 0.2
                reasoning_parts.append('Multiple violations require thorough review')
        
        # Pre-marketing often needs legal review
        if 'pre' in marketing_type:
            if any(keyword in workflow_name for keyword in legal_keywords):
                score += 0.25
                reasoning_parts.append('Pre-marketing often requires legal review')
        
        # If no special factors, prefer comprehensive/default workflows
        if not reasoning_parts and 'comprehensive' in workflow_name:
            score += 0.1
            reasoning_parts.append('Standard comprehensive review suitable for general documents')
        
        # Base confidence
        confidence = min(0.95, 0.7 + score)
        
        scored_workflows.append({
            'id': workflow.get('id'),
            'name': workflow.get('name'),
            'score': score,
            'confidence': confidence,
            'reasoning': '; '.join(reasoning_parts) if reasoning_parts else 'Appropriate for document type and risk profile'
        })
    
    # Sort by score (descending) and pick highest
    scored_workflows.sort(key=lambda x: x['score'], reverse=True)
    best_workflow = scored_workflows[0]
    
    # Ensure minimum confidence
    best_workflow['confidence'] = max(0.65, best_workflow['confidence'])
    
    return best_workflow