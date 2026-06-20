"""
Smart Auto-Assignment with AI Reasoning
"""
import os
import json
from openai import AzureOpenAI
from ..core.database import get_users_by_org, update_document
from dotenv import load_dotenv
load_dotenv()
def auto_assign_document(document: dict, org_id: str) -> dict:
    """
    Auto-assign document to best reviewer with AI reasoning.
    
    Returns: {
        'assigned_to': 'email@company.com',
        'assigned_to_name': 'John Doe',
        'reasoning': 'Alice is assigned because...',
        'confidence': 0.85,
        'workload_before': 5,
        'workload_after': 6
    }
    """
    # Get available reviewers
    users = get_users_by_org(org_id)
    reviewers = [u for u in users if any(
        role in u.get('roles', []) 
        for role in ['Compliance.Officer', 'Compliance.Reviewer', 'Legal.Advisor']
    )]
    
    if not reviewers:
        return {'error': 'No reviewers available'}
    
    # Get workloads
    from .document_assignments import _get_user_workload
    reviewer_data = []
    for r in reviewers:
        workload = _get_user_workload(r['email'], org_id)
        reviewer_data.append({
            'email': r['email'],
            'name': r.get('name', r['email']),
            'roles': r.get('roles', []),
            'current_workload': workload['total'],
            'urgent_items': workload.get('urgent', 0),
            'overdue_items': workload.get('overdue', 0)
        })
    
    # AI decides
    client = AzureOpenAI(
        api_key=os.getenv('AZURE_OPENAI_API_KEY'),
        api_version='2025-01-01-preview',
        azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
    )
    
    prompt = f"""You are an intelligent document assignment system. Choose the BEST reviewer for this document.

DOCUMENT:
- Filename: {document.get('filename')}
- Risk Score: {document.get('risk_score', 0)}/100
- Jurisdiction: {document.get('jurisdiction', 'UK')}
- Violations: {document.get('violations_count', 0)}
- Document Type: {document.get('briefing', {}).get('marketing_type', 'unknown')}

AVAILABLE REVIEWERS:
{json.dumps(reviewer_data, indent=2)}

ASSIGNMENT RULES:
1. Balance workload - prefer reviewers with fewer active items
2. Match expertise - Legal.Advisor for high-risk (70+), Compliance.Officer for medium (40-70)
3. Avoid overload - max 10 items per person
4. Consider urgency - avoid reviewers with many overdue items

OUTPUT JSON:
{{
  "assigned_to": "email@company.com",
  "reasoning": "Explain WHY in 1-2 sentences. Be specific about workload, expertise, or other factors.",
  "confidence": 0.85,
  "alternative": "backup@company.com"
}}

Be concise and actionable."""

    response = client.chat.completions.create(
        model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"}
    )
    
    result = json.loads(response.choices[0].message.content)
    
    # Get assigned reviewer details
    assigned = next((r for r in reviewer_data if r['email'] == result['assigned_to']), None)
    
    # Update document
    update_document(document['id'], {
        'assigned_to': result['assigned_to'],
        'assigned_to_name': assigned['name'] if assigned else result['assigned_to'],
        'assignment_reasoning': result['reasoning'],
        'assignment_confidence': result.get('confidence', 0.8),
        'assignment_method': 'ai_auto',
        'status': 'assigned'
    }, org_id)
    
    return {
        'assigned_to': result['assigned_to'],
        'assigned_to_name': assigned['name'] if assigned else result['assigned_to'],
        'reasoning': result['reasoning'],
        'confidence': result.get('confidence', 0.8),
        'workload_before': assigned['current_workload'] if assigned else 0,
        'workload_after': assigned['current_workload'] + 1 if assigned else 1,
        'alternative': result.get('alternative')
    }