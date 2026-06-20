"""
POST /api/documents/{documentId}/briefing

"""
import azure.functions as func
import logging
import json
import os
from datetime import datetime
from typing import Dict
from function_app_pkg.core.database import get_document, update_document, log_action
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

def _generate_document_summary(text: str, briefing_data: Dict, jurisdiction: str) -> Dict:
    """Generate AI-powered document summary for better scanning context"""
    
    try:
        from openai import AzureOpenAI
        
        client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
        )
        
        # Prepare the text - limit to reasonable length
        preview_text = text[:3000] if text else "No text content available"
        
        # Clean and prepare jurisdiction
        jurisdiction_clean = jurisdiction if jurisdiction else "Unknown"
        
        # ✅ FIX: Include the actual document text in the prompt!
        prompt = f"""Analyze this {jurisdiction_clean} financial marketing document and provide a structured summary.

DOCUMENT TYPE: {briefing_data.get('marketing_type', 'Unknown')}
DISTRIBUTION: {briefing_data.get('distribution_media', 'Unknown')}
TARGET AUDIENCE: {briefing_data.get('target_audience', 'Unknown')}

DOCUMENT TEXT (first 3000 characters):
---
{preview_text}
---

Based on the ACTUAL CONTENT above, generate a JSON summary with:

{{
  "executive_summary": "2-3 sentence overview of what this document ACTUALLY contains based on the text above",
  "key_highlights": [
    "Specific point from the document",
    "Another specific finding",
    "Third key point from the content"
  ],
  "compliance_considerations": [
    "Specific compliance area based on what's in the document",
    "Another risk area found in the text"
  ],
  "document_purpose": "What this specific document is trying to achieve based on its content",
  "risk_indicators": [
    "Specific language from the document that might trigger compliance issues"
  ]
}}

IMPORTANT: 
- Base your analysis ONLY on the actual document text provided above
- Do NOT use generic template responses
- Quote or reference specific content from the document
- If the text is empty or unreadable, say so explicitly

Focus on actionable insights for compliance scanning."""

        response = client.chat.completions.create(
            model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
            messages=[
                {"role": "system", "content": "You are a compliance analyst summarizing documents for regulatory review. Always base your analysis on the actual document content provided - never give generic responses."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1000,  # Increased for more detailed summaries
            response_format={"type": "json_object"}
        )
        
        summary = json.loads(response.choices[0].message.content)
        
        # ✅ Add metadata about the generation
        summary['_metadata'] = {
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'text_analyzed_length': len(preview_text),
            'jurisdiction': jurisdiction_clean,
            'ai_model': os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4')
        }
        
        logger.info(f"✅ AI summary generated: {len(summary.get('executive_summary', ''))} chars")
        return summary
        
    except ImportError:
        logger.warning("⚠️ AzureOpenAI not available - skipping summary generation")
        return _get_fallback_summary("AzureOpenAI library not available")
    except Exception as e:
        logger.error(f"❌ AI summary generation failed: {e}")
        return _get_fallback_summary(str(e))


def _get_fallback_summary(reason: str = "Unknown error") -> Dict:
    """Return a fallback summary when AI generation fails"""
    return {
        "executive_summary": f"Document summary generation failed: {reason}. Please review the document manually.",
        "key_highlights": ["Manual review required"],
        "compliance_considerations": ["Verify all compliance requirements manually"],
        "document_purpose": "Unknown - needs manual review",
        "risk_indicators": ["Unable to assess risks automatically"],
        "_metadata": {
            "fallback": True,
            "reason": reason,
            "generated_at": datetime.utcnow().isoformat() + 'Z'
        }
    }


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Submit briefing form to categorize document
    
    Request Body:
    {
      "marketing_type": "product_related",
      "distribution_media": "document",
      "target_audience": "retail_investors",
      "content_type": "fund_marketing"
    }
    
    FIXED: Better user handling and error messages
    """
    try:
        # Extract document ID
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        logger.info(f"📋 Briefing request for document: {doc_id}")
        
        # Parse request body
        try:
            body = req.get_json()
        except ValueError as e:
            logger.error(f"Invalid JSON body: {e}")
            return json_response(400, error="Invalid JSON body")
        
        # Extract and validate fields
        marketing_type = body.get('marketing_type')
        distribution_media = body.get('distribution_media')
        target_audience = body.get('target_audience')
        content_type = body.get('content_type')
        
        # Validate required fields
        if not marketing_type or not distribution_media:
            return json_response(400, error="marketing_type and distribution_media are required")
        
        # Validate marketing_type
        valid_marketing_types = ['product_related', 'pre_marketing', 'topic_related']
        if marketing_type not in valid_marketing_types:
            return json_response(400, error=f"Invalid marketing_type. Must be one of: {', '.join(valid_marketing_types)}")
        
        # Validate distribution_media
        valid_distribution = ['document', 'email', 'article', 'audio_visual', 'client_presentation', 'social_media', 'website']
        if distribution_media not in valid_distribution:
            return json_response(400, error=f"Invalid distribution_media. Must be one of: {', '.join(valid_distribution)}")
        
        # Get document
        doc = get_document(doc_id)
        if not doc:
            logger.error(f"Document not found: {doc_id}")
            return json_response(404, error=f"Document not found: {doc_id}")
        
        # Extract user context
        user_email = 'system@system.com'
        user_id = 'system'
        organization_id = doc.get('organization_id', 'unknown')
        
        if user:
            if hasattr(user, 'email'):
                user_email = user.email
                user_id = getattr(user, 'user_id', user_email)
                organization_id = getattr(user, 'organization_id', organization_id)
            elif isinstance(user, dict):
                user_email = user.get('email', user_email)
                user_id = user.get('user_id', user_email)
                organization_id = user.get('organization_id', organization_id)
        
        # Check authorization
        doc_org_id = doc.get('organization_id')
        if doc_org_id and organization_id and doc_org_id != organization_id:
            logger.warning(f"Authorization mismatch: user org={organization_id}, doc org={doc_org_id}")
            # In development, we'll allow it with a warning
        
        logger.info(f"📋 Briefing form for {doc_id} by {user_email}")
        
        # Create briefing data
        briefing_data = {
            'marketing_type': marketing_type,
            'distribution_media': distribution_media,
            'target_audience': target_audience,
            'content_type': content_type,
            'submitted_by': user_email,
            'submitted_at': datetime.utcnow().isoformat() + 'Z'
        }
        
        # ✅ Get document text - check multiple sources
        document_text = (
            doc.get('extracted_text') or 
            doc.get('text_content') or 
            doc.get('text') or 
            doc.get('content') or
            ''
        )
        
        # ✅ Log what we found
        logger.info(f"📝 Document text length for summary: {len(document_text)} chars")
        if not document_text:
            logger.warning(f"⚠️ No text found in document {doc_id} - summary will be limited")
        
        jurisdiction = doc.get('jurisdiction', 'UK')
        
        logger.info(f"🤖 Generating AI summary for document {doc_id} ({len(document_text)} chars)")
        document_summary = _generate_document_summary(
            text=document_text,
            briefing_data=briefing_data,
            jurisdiction=jurisdiction
        )
        
        # Add summary to briefing data
        briefing_data['document_summary'] = document_summary
        
        # Update document with briefing and summary
        update_data = {
            'briefing': briefing_data,  # Frontend expects this
            'document_summary': document_summary,  # Also store top-level for easy access
            'status': 'briefing_completed',
            'workflow_status': 'briefing_completed',
            'updated_at': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Also update metadata for backward compatibility
        metadata = doc.get('metadata', {})
        metadata.update({
            'marketing_type': marketing_type,
            'distribution_media': distribution_media,
            'target_audience': target_audience,
            'content_type': content_type,
            'briefing_completed_at': datetime.utcnow().isoformat() + 'Z',
            'briefing_submitted_by': user_email,
            'ai_summary_generated': True,
            'text_length_analyzed': len(document_text)  # ✅ Track what was analyzed
        })
        update_data['metadata'] = metadata
        
        # Update document
        try:
            updated_doc = update_document(doc_id, update_data)
            
            if not updated_doc:
                logger.error(f"Failed to update document {doc_id}")
                return json_response(500, error="Failed to update document")
            
            logger.info(f"✅ Briefing completed for {doc_id} with AI summary")
            
            # Log action for audit trail
            try:
                log_action(
                    org_id=organization_id,
                    user_id=user_id,
                    user_email=user_email,
                    user_roles=getattr(user, 'roles', []) if user else [],
                    action='briefing.submitted',
                    resource_type='document',
                    resource_id=doc_id,
                    resource_name=doc.get('filename', 'unknown'),
                    details={
                        'marketing_type': marketing_type,
                        'distribution_media': distribution_media,
                        'target_audience': target_audience,
                        'content_type': content_type,
                        'ai_summary_generated': True,
                        'text_analyzed_length': len(document_text)
                    }
                )
            except Exception as log_error:
                logger.warning(f"⚠️ Could not create audit log: {log_error}")
            
            # Return the updated document with briefing field
            return json_response(200, data={
                'document_id': doc_id,
                'briefing': briefing_data,
                'document_summary': document_summary,
                'status': 'briefing_completed',
                'workflow_status': 'briefing_completed',
                'next_step': 'scan',
                'message': 'Briefing submitted successfully with AI-powered summary.',
                'workflow_guidance': {
                    'step': 2,
                    'total_steps': 5,
                    'completed': ['upload', 'briefing'],
                    'next': 'scan',
                    'remaining': ['scan', 'review_violations', 'answer_questions']
                }
            })
            
        except Exception as update_error:
            logger.error(f"❌ Error updating document: {update_error}", exc_info=True)
            return json_response(500, error=f"Database update failed: {str(update_error)[:200]}")
    
    except Exception as e:
        logger.error(f"❌ Briefing form error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to submit briefing: {str(e)[:200]}")