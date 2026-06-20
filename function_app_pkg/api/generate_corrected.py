"""
Generate AI-corrected compliance document
POST /documents/{documentId}/generate-corrected
"""
import logging
import os
import json
from datetime import datetime
from io import BytesIO
import azure.functions as func
from openai import AzureOpenAI
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_document, update_document
from function_app_pkg.core.storage import blob_storage

logger = logging.getLogger(__name__)

def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Generate AI-corrected version using ALL context:
    - Violations (all categories, severities, remediations)
    - Discussions (team feedback and concerns)
    - Legal advisory (recommendations, cited regulations)
    - PII items (for anonymization)
    - Briefing context (marketing type, audience, distribution)
    """
    
    try:
        # =====================================================================
        # STEP 1: AUTHENTICATION & AUTHORIZATION
        # =====================================================================
        if not user:
            return json_response(401, error="Not authenticated")
        
        org_id = getattr(user, 'organization_id', None) or user.get('organization_id')
        user_email = getattr(user, 'email', None) or user.get('email')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        logger.info(f"🤖 Generating corrected version for {doc_id} by {user_email}")
        
        # =====================================================================
        # STEP 2: GET DOCUMENT WITH FULL CONTEXT
        # =====================================================================
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")
        
        # Update status to generating
        update_document(doc_id, {
            'correction_status': 'generating',
            'correction_requested_at': datetime.utcnow().isoformat() + 'Z',
            'correction_requested_by': user_email,
        }, org_id)
        
        # =====================================================================
        # STEP 3: GATHER ALL CONTEXT
        # =====================================================================
        
        original_text = doc.get('extracted_text', '')
        if not original_text:
            update_document(doc_id, {'correction_status': 'failed'}, org_id)
            return json_response(400, error="No text extracted from document. Cannot generate correction.")
        
        violations = doc.get('violations', [])
        discussions = doc.get('discussions', [])
        briefing = doc.get('briefing', {})
        legal_advisory = doc.get('legal_advisory')
        legal_recommendation = doc.get('legal_recommendation')
        cited_regulations = doc.get('cited_regulations', [])
        legal_conditions = doc.get('legal_conditions', [])
        pii_items = doc.get('pii_items', [])
        jurisdiction = doc.get('jurisdiction', 'UK')
        risk_score = doc.get('risk_score', 0)
        
        # =====================================================================
        # STEP 4: BUILD COMPREHENSIVE AI PROMPT
        # =====================================================================
        
        # Format violations
        violations_text = ""
        if violations:
            violations_text = "VIOLATIONS TO FIX:\n\n"
            for idx, v in enumerate(violations[:20], 1):  # Limit to 20 to stay within token limits
                violations_text += f"{idx}. [{v.get('severity', 'MEDIUM')}] {v.get('category', 'Unknown')}\n"
                violations_text += f"   Issue: {v.get('description', '')}\n"
                violations_text += f"   Regulation: {v.get('regulation_citation', {}).get('section_reference', 'N/A') if isinstance(v.get('regulation_citation'), dict) else v.get('regulation_citation', 'N/A')}\n"
                violations_text += f"   Fix: {v.get('remediation', 'Not specified')}\n\n"
        
        # Format discussions (last 10)
        discussions_text = ""
        if discussions:
            recent_discussions = discussions[-10:]
            discussions_text = "TEAM DISCUSSIONS & CLARIFICATIONS:\n\n"
            for disc in recent_discussions:
                if not disc.get('is_ai_generated'):
                    author = disc.get('author_name') or disc.get('author_email', 'Team member')
                    discussions_text += f"• {author}: {disc.get('content', '')[:300]}\n"
        
        # Format legal advisory
        legal_text = ""
        if legal_advisory:
            legal_text = f"""
LEGAL ADVISORY (CRITICAL - FOLLOW THIS):

Recommendation: {legal_recommendation or 'Review required'}
Advisory: {legal_advisory}

Cited Regulations: {', '.join(cited_regulations) if cited_regulations else 'None specified'}

Conditions: {chr(10).join([f"• {c}" for c in legal_conditions]) if legal_conditions else 'None'}
"""
        
        # Format PII
        pii_text = ""
        if pii_items:
            pii_text = "PII TO ANONYMIZE:\n\n"
            for pii in pii_items[:15]:  # Limit to 15
                pii_text += f"• {pii.get('type', 'Unknown')}: {pii.get('text', '')}\n"
        
        # Truncate original text if too long
        max_text_length = 30000
        truncated_text = original_text[:max_text_length]
        if len(original_text) > max_text_length:
            truncated_text += "\n\n[... Document truncated due to length ...]"
        
        prompt = f"""You are a senior compliance editor specializing in {jurisdiction} financial regulations.

DOCUMENT CONTEXT:
- Jurisdiction: {jurisdiction}
- Filename: {doc.get('filename', 'document')}
- Risk Score: {risk_score}/100
- Briefing: {briefing.get('marketing_type', 'N/A')} | {briefing.get('distribution_media', 'N/A')} | Target: {briefing.get('target_audience', 'N/A')}

{legal_text}

{violations_text}

{discussions_text}

{pii_text}

ORIGINAL DOCUMENT TEXT:
{truncated_text}

---

YOUR TASK:

Generate a compliance-corrected version of this document that:

1. ADDRESSES ALL VIOLATIONS listed above with specific fixes
2. FOLLOWS the legal advisory recommendations exactly
3. ANONYMIZES all PII (replace with generic placeholders like "client.contact@example.com", "Client Name", etc.)
4. MAINTAINS the document's effectiveness and persuasiveness within regulatory boundaries
5. ADDS required disclaimers and risk warnings per {jurisdiction} regulations
6. KEEPS the marketing message compelling while ensuring compliance

OUTPUT FORMAT:
Return the complete corrected document text in markdown format with:
- Clear headings
- Properly formatted sections
- Required disclaimers prominently displayed
- All violations fixed
- All PII anonymized

IMPORTANT: 
- Be specific about changes made
- Maintain professional tone
- Ensure document is ready for approval
- Do NOT just remove problematic sections - improve them to be compliant
"""

        # =====================================================================
        # STEP 5: CALL GPT-4
        # =====================================================================
        
        logger.info("🤖 Calling GPT-4 for correction...")
        
        try:
            client = AzureOpenAI(
                api_key=os.getenv('AZURE_OPENAI_API_KEY'),
                api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2024-08-01-preview'),
                azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
                timeout=120.0
            )
            
            response = client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a senior compliance editor for {jurisdiction} financial marketing. Generate compliant, corrected documents."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                max_tokens=8000,
            )
            
            corrected_text = response.choices[0].message.content
            
            if not corrected_text:
                raise Exception("AI returned empty response")
            
            logger.info(f"✅ AI generated {len(corrected_text)} chars")
            
        except Exception as ai_err:
            logger.error(f"❌ AI correction failed: {ai_err}")
            update_document(doc_id, {'correction_status': 'failed'}, org_id)
            return json_response(500, error=f"AI correction failed: {str(ai_err)}")
        
        # =====================================================================
        # STEP 6: CONVERT TO PDF
        # =====================================================================
        
        logger.info("📄 Converting to PDF...")
        
        try:
            pdf_buffer = BytesIO()
            pdf_doc = SimpleDocTemplate(
                pdf_buffer,
                pagesize=letter,
                topMargin=1*inch,
                bottomMargin=1*inch,
                leftMargin=1*inch,
                rightMargin=1*inch
            )
            
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                textColor='#1e40af',
                spaceAfter=30,
                alignment=TA_CENTER
            )
            
            watermark_style = ParagraphStyle(
                'Watermark',
                parent=styles['Normal'],
                fontSize=10,
                textColor='#9333ea',
                alignment=TA_CENTER,
                spaceAfter=20
            )
            
            story = []
            
            # Title page
            story.append(Paragraph(f"COMPLIANCE-CORRECTED DOCUMENT", title_style))
            story.append(Spacer(1, 0.2*inch))
            story.append(Paragraph("AI-GENERATED COMPLIANCE CORRECTION", watermark_style))
            story.append(Spacer(1, 0.1*inch))
            story.append(Paragraph(f"Original: {doc.get('filename', 'document')}", styles['Normal']))
            story.append(Paragraph(f"Jurisdiction: {jurisdiction}", styles['Normal']))
            story.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", styles['Normal']))
            story.append(Paragraph(f"Violations Addressed: {len(violations)}", styles['Normal']))
            if pii_items:
                story.append(Paragraph(f"PII Anonymized: {len(pii_items)}", styles['Normal']))
            story.append(PageBreak())
            
            # Content
            for line in corrected_text.split('\n'):
                if line.strip():
                    if line.startswith('#'):
                        # Heading
                        story.append(Spacer(1, 0.2*inch))
                        story.append(Paragraph(line.replace('#', '').strip(), styles['Heading2']))
                        story.append(Spacer(1, 0.1*inch))
                    else:
                        story.append(Paragraph(line, styles['Normal']))
                        story.append(Spacer(1, 0.1*inch))
            
            pdf_doc.build(story)
            pdf_content = pdf_buffer.getvalue()
            
            logger.info(f"✅ PDF generated: {len(pdf_content)} bytes")
            
        except Exception as pdf_err:
            logger.error(f"❌ PDF generation failed: {pdf_err}")
            update_document(doc_id, {'correction_status': 'failed'}, org_id)
            return json_response(500, error=f"PDF generation failed: {str(pdf_err)}")
        
        # =====================================================================
        # STEP 7: UPLOAD TO BLOB STORAGE
        # =====================================================================
        
        logger.info("☁️ Uploading to blob storage...")
        
        try:
            blob_path = f"{org_id}/{doc_id}_corrected.pdf"
            
            blob_url, _ = blob_storage.upload_file(
                file_content=pdf_content,
                blob_path=blob_path,
                content_type='application/pdf',
                metadata={
                    'original_document_id': doc_id,
                    'generated_by': user_email,
                    'violations_addressed': str(len(violations)),
                    'pii_anonymized': str(len(pii_items)) if pii_items else '0',
                    'generated_at': datetime.utcnow().isoformat(),
                }
            )
            
            logger.info(f"✅ Uploaded to: {blob_url}")
            
        except Exception as upload_err:
            logger.error(f"❌ Upload failed: {upload_err}")
            update_document(doc_id, {'correction_status': 'failed'}, org_id)
            return json_response(500, error=f"Upload failed: {str(upload_err)}")
        
        # =====================================================================
        # STEP 8: UPDATE DOCUMENT
        # =====================================================================
        
        now = datetime.utcnow().isoformat() + 'Z'
        
        update_data = {
            'corrected_blob_url': blob_url,
            'corrected_blob_path': blob_path,
            'correction_status': 'generated',
            'correction_generated_at': now,
            'correction_generated_by': user_email,
            'correction_metadata': {
                'violations_addressed': len(violations),
                'pii_anonymized': len(pii_items) if pii_items else 0,
                'legal_advisory_applied': bool(legal_advisory),
                'discussions_considered': len(discussions),
                'text_length': len(corrected_text),
                'pdf_size_bytes': len(pdf_content),
            },
            'updated_at': now,
        }
        
        updated_doc = update_document(doc_id, update_data, org_id)
        
        logger.info(f"✅ Corrected version generated successfully")
        
        return json_response(200, data={
            'document_id': doc_id,
            'corrected_blob_url': blob_url,
            'corrected_blob_path': blob_path,
            'status': 'generated',
            'generated_at': now,
            'metadata': update_data['correction_metadata'],
            'message': 'AI-corrected version generated successfully',
        })
        
    except Exception as e:
        logger.error(f"❌ Generate corrected failed: {e}")
        logger.exception(e)
        try:
            update_document(doc_id, {'correction_status': 'failed'}, org_id)
        except:
            pass
        return json_response(500, error=str(e))