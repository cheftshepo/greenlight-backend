"""
Generate Compliance Questions API - COMPLETE VERSION
"""
import azure.functions as func
import logging
from function_app_pkg.shared.http_utils import json_response
from datetime import datetime

logger = logging.getLogger(__name__)


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    Generate compliance questions for document
    Complete implementation with proper error handling
    """
    logger.info("=" * 80)
    logger.info("🎯 GENERATE QUESTIONS ENDPOINT")
    logger.info("=" * 80)
    
    try:
        # Get document ID from route
        doc_id = req.route_params.get('documentId')
        
        if not doc_id:
            logger.error("❌ No document ID in request")
            return json_response(400, error="Document ID is required")
        
        logger.info(f"📄 Document ID: {doc_id}")
        
        # Get document with verification
        from function_app_pkg.core.database import get_document_with_verification
        
        # Try to get org_id from user if available
        org_id = None
        if user:
            if hasattr(user, 'organization_id'):
                org_id = user.organization_id
            elif isinstance(user, dict):
                org_id = user.get('organization_id')
        
        logger.info(f"🏢 User org_id: {org_id or 'Not available'}")
        
        # Get document
        doc = get_document_with_verification(doc_id, org_id)
        
        if not doc:
            logger.error(f"❌ Document not found: {doc_id}")
            
            # Additional debugging
            logger.error("🔍 Attempting database diagnostics...")
            try:
                from function_app_pkg.core.database import get_db
                db = get_db()
                container = db.get_container("documents")
                
                # Check if ANY documents exist
                test_query = "SELECT COUNT(1) as count FROM c WHERE c.type = 'document'"
                count_result = list(container.query_items(
                    query=test_query,
                    enable_cross_partition_query=True
                ))
                
                if count_result:
                    total_docs = count_result[0].get('count', 0)
                    logger.error(f"📊 Total documents in database: {total_docs}")
                
            except Exception as diag_error:
                logger.error(f"Diagnostics failed: {diag_error}")
            
            return json_response(404, error=f"Document not found: {doc_id}")
        
        logger.info(f"✅ Document retrieved: {doc.get('filename')}")
        logger.info(f"📁 Organization: {doc.get('organization_id')}")
        
        # Check document status
        status = doc.get('status', 'unknown')
        logger.info(f"📊 Document status: {status}")
        
        if status not in ['scanned', 'uploaded', 'briefing_completed']:
            logger.warning(f"⚠️ Unusual document status: {status}")
        
        # Get text content with multiple fallbacks
        text = (doc.get('text_content') or 
                doc.get('extracted_text') or 
                doc.get('text') or 
                '')
        
        if not text or len(text.strip()) < 10:
            logger.error(f"❌ Document has no readable text")
            logger.error(f"📊 text_content: {len(doc.get('text_content', ''))} chars")
            logger.error(f"📊 extracted_text: {len(doc.get('extracted_text', ''))} chars")
            logger.error(f"📊 Available fields: {list(doc.keys())[:20]}")
            
            return json_response(
                400, 
                error="Document has no readable text. Please re-upload or re-scan."
            )
        
        logger.info(f"📝 Text length: {len(text)} chars")
        
        # Get violations and scan results
        violations = doc.get('violations', [])
        risk_score = doc.get('risk_score', 0)
        compliance_outcome = doc.get('compliance_outcome', 'unknown')
        
        logger.info(f"🚨 Violations: {len(violations)}")
        logger.info(f"📊 Risk score: {risk_score}/100")
        logger.info(f"✅ Outcome: {compliance_outcome}")
        
        # Generate questions using AI
        logger.info("🤖 Generating compliance questions...")
        
        from function_app_pkg.core.question_generator import generate_questions
        
        questions = generate_questions(
            text=text,
            violations=violations,
            jurisdiction=doc.get('jurisdiction', 'UK'),
            document_type=doc.get('document_type', 'marketing_material'),
            risk_score=risk_score
        )
        
        if not questions:
            logger.error("❌ Question generation failed")
            return json_response(500, error="Failed to generate questions")
        
        logger.info(f"✅ Generated {len(questions)} questions")
        
        # Update document with questions
        logger.info("💾 Saving questions to document...")
        
        from function_app_pkg.core.database import get_db
        
        # Update fields directly in the doc dict
        doc['compliance_questions'] = questions
        doc['questions_generated_at'] = datetime.utcnow().isoformat() + 'Z'
        doc['questionnaire_status'] = 'pending'
        doc['status'] = 'questions_generated'
        doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        # Get actual org_id for partition key
        actual_org_id = doc.get('organization_id')
        
        # Direct upsert to Cosmos
        db = get_db()
        container = db.get_container('documents')
        
        try:
            updated_doc = container.upsert_item(doc)
            logger.info("✅ Document updated with questions")
        except Exception as e:
            logger.error(f"❌ Failed to update document: {e}")
            # Don't fail the request - questions were generated successfully
            logger.warning("⚠️ Continuing despite update failure")
            updated_doc = doc  # Use original doc
        
        # Build response
        response_data = {
            'document_id': doc_id,
            'filename': doc.get('filename'),
            'organization_id': actual_org_id,
            'jurisdiction': doc.get('jurisdiction', 'UK'),
            'document_type': doc.get('document_type', 'marketing_material'),
            'questions': questions,
            'questions_count': len(questions),
            'generated_at': doc.get('questions_generated_at'),
            'risk_score': risk_score,
            'violations_count': len(violations),
            'compliance_outcome': compliance_outcome,
            'status': 'questions_generated',
            'instructions': {
                'how_to_answer': 'Answer each question with yes, no, or uncertain. Provide notes to explain your answer.',
                'severity_legend': {
                    'critical': 'Must be addressed immediately',
                    'high': 'Should be addressed before publication',
                    'medium': 'Should be reviewed and considered',
                    'low': 'Minor issues for improvement'
                },
                'next_step': 'Submit your answers to receive AI-powered compliance guidance'
            }
        }
        
        logger.info("=" * 80)
        logger.info(f"✅ SUCCESS: Generated {len(questions)} questions")
        logger.info("=" * 80)
        
        return json_response(200, data=response_data)
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ FATAL ERROR in question generation")
        logger.error(f"Error: {e}")
        logger.error("=" * 80)
        logger.exception(e)
        
        return json_response(500, error=f"Question generation failed: {str(e)[:200]}")


# =============================================================================
# HELPER FUNCTIONS (if needed by other modules)
# =============================================================================

def validate_document_for_questions(doc: dict) -> tuple[bool, str]:
    """
    Validate if document is ready for question generation
    Returns: (is_valid, error_message)
    """
    if not doc:
        return False, "Document not found"
    
    # Check for text content
    text = doc.get('text_content') or doc.get('extracted_text') or ''
    if not text or len(text.strip()) < 10:
        return False, "Document has no readable text"
    
    # Check if already scanned
    status = doc.get('status', '')
    if status not in ['scanned', 'uploaded', 'briefing_completed', 'questions_generated']:
        return False, f"Document status '{status}' not ready for questions"
    
    return True, ""


def get_question_summary(questions: list) -> dict:
    """
    Generate summary statistics for questions
    Used by analytics and reporting
    """
    if not questions:
        return {
            'total': 0,
            'by_severity': {},
            'by_category': {},
            'required_count': 0
        }
    
    by_severity = {}
    by_category = {}
    required_count = 0
    
    for q in questions:
        # Count by severity
        severity = q.get('severity', 'medium')
        by_severity[severity] = by_severity.get(severity, 0) + 1
        
        # Count by category
        category = q.get('category', 'general')
        by_category[category] = by_category.get(category, 0) + 1
        
        # Count required
        if q.get('required', False):
            required_count += 1
    
    return {
        'total': len(questions),
        'by_severity': by_severity,
        'by_category': by_category,
        'required_count': required_count
    }


def format_questions_for_frontend(questions: list) -> list:
    """
    Format questions for frontend consumption
    Ensures consistent structure
    """
    formatted = []
    
    for idx, q in enumerate(questions):
        formatted_question = {
            'question_id': q.get('question_id', f"q_{idx+1}"),
            'question': q.get('verification_question') or q.get('question', ''),
            'verification_question': q.get('verification_question', ''),
            'category': q.get('category', 'general'),
            'severity': q.get('severity', 'medium'),
            'help_text': q.get('help_text', ''),
            'exact_fix': q.get('exact_fix', ''),
            'regulatory_reference': q.get('regulatory_reference', ''),
            'required': q.get('required', True),
            'answer_options': ['yes', 'no', 'uncertain'],
            'location': q.get('location', ''),
            'generated_at': q.get('generated_at', datetime.utcnow().isoformat() + 'Z')
        }
        formatted.append(formatted_question)
    
    return formatted


def get_questions_by_severity(questions: list, severity: str) -> list:
    """
    Filter questions by severity level
    Used by priority sorting
    """
    return [q for q in questions if q.get('severity', '').lower() == severity.lower()]


def get_critical_questions(questions: list) -> list:
    """
    Get only critical questions
    Used for high-priority workflows
    """
    return get_questions_by_severity(questions, 'critical')