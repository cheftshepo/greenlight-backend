"""
CERTIFICATE API HANDLER - WITH ROBUST JSON HANDLING
===================================================
Handles missing/invalid JSON gracefully
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

import azure.functions as func

from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.core.database import get_document, get_organization
from function_app_pkg.core.certificate_generator import (
    get_certificate_generator,
    CertificateError,
    CertificateValidationError,
    CertificateGenerationError,
    CertificateStorageError
)

logger = logging.getLogger(__name__)


def _safe_get_json(req: func.HttpRequest) -> Dict[str, Any]:
    """
    Safely parse JSON from request, returning empty dict if invalid
    
    Handles:
    - Empty body
    - Invalid JSON
    - Non-JSON content types
    """
    try:
        body = req.get_json()
        return body if body else {}
    except ValueError:
        # Not JSON or empty body - this is OK for certificate generation
        logger.debug("No JSON body in request (optional)")
        return {}
    except Exception as e:
        logger.warning(f"Unexpected error parsing JSON: {e}")
        return {}


def handle_generate_certificate(req: func.HttpRequest, user: Any) -> func.HttpResponse:
    """
    POST /documents/{documentId}/generate-certificate
    Generate compliance certificate (Premium feature)
    
    Request Body (ALL OPTIONAL):
    {
        "reviewer_name": "John Smith",
        "reviewer_email": "john@example.com",
        "notes": "Additional comments..."
    }
    
    If no body provided, uses user's name/email as defaults
    """
    try:
        # Extract document ID from route
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            logger.warning("Certificate request missing documentId")
            return json_response(400, error="Document ID required")
        
        # Get user organization
        org_id = _get_user_org_id(user)
        if not org_id:
            logger.warning(f"Certificate request from user without org: {user}")
            return json_response(400, error="Organization ID required")
        
        # Fetch document
        doc = get_document(doc_id, org_id)
        if not doc:
            logger.warning(f"Certificate requested for non-existent doc: {doc_id}")
            return json_response(404, error="Document not found")
        
        # Verify document is scanned
        if doc.get('status') != 'scanned':
            logger.info(f"Certificate requested for unscanned doc: {doc_id}")
            return json_response(
                400, 
                error="Document must be scanned first",
                data={'document_status': doc.get('status')}
            )
        
        # Parse request body (safely - empty body is OK)
        body = _safe_get_json(req)
        logger.info(f"Certificate request body: {body}")
        
        # Get organization details
        org = get_organization(org_id)
        org_name = org.get('name', 'Unknown Organization') if org else 'Unknown Organization'
        
        # Prepare certificate data
        cert_params = _prepare_certificate_params(doc, user, org_name, body)
        
        # Generate and store certificate
        generator = get_certificate_generator()
        result = generator.generate_and_store(**cert_params)
        
        # Log success
        logger.info(
            f"✅ Certificate generated: {result.certificate_id} "
            f"for doc {doc_id} by {getattr(user, 'email', 'unknown')}"
        )
        
        # Return response
        response_data = result.to_dict()
        response_data['message'] = 'Certificate generated successfully'
        
        return json_response(201, data=response_data)
        
    except CertificateValidationError as e:
        logger.warning(f"Certificate validation error: {e}")
        return json_response(400, error=f"Validation error: {str(e)}")
    
    except CertificateGenerationError as e:
        logger.error(f"Certificate generation failed: {e}")
        return json_response(500, error=f"Generation failed: {str(e)}")
    
    except CertificateStorageError as e:
        logger.error(f"Certificate storage failed: {e}")
        return json_response(500, error=f"Storage failed: {str(e)}")
    
    except CertificateError as e:
        logger.error(f"Certificate error: {e}")
        return json_response(500, error=str(e))
    
    except Exception as e:
        logger.error(f"Unexpected certificate error: {e}", exc_info=True)
        return json_response(500, error="An unexpected error occurred")


def handle_list_certificates(req: func.HttpRequest, user: Any) -> func.HttpResponse:
    """GET /documents/{documentId}/certificates - List all certificates"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        org_id = _get_user_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        certificates = doc.get('certificates', [])
        certificates.sort(key=lambda x: x.get('issued_at', ''), reverse=True)
        
        return json_response(200, data={
            'document_id': doc_id,
            'document_filename': doc.get('filename'),
            'certificates': certificates,
            'total': len(certificates)
        })
        
    except Exception as e:
        logger.error(f"Failed to list certificates: {e}", exc_info=True)
        return json_response(500, error="Failed to retrieve certificates")


def handle_get_certificate(req: func.HttpRequest, user: Any) -> func.HttpResponse:
    """GET /documents/{documentId}/certificates/{certificateId}"""
    try:
        doc_id = req.route_params.get('documentId')
        cert_id = req.route_params.get('certificateId')
        
        if not doc_id or not cert_id:
            return json_response(400, error="Document ID and Certificate ID required")
        
        org_id = _get_user_org_id(user)
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        certificates = doc.get('certificates', [])
        cert = next(
            (c for c in certificates if c.get('certificate_id') == cert_id),
            None
        )
        
        if not cert:
            return json_response(404, error="Certificate not found")
        
        return json_response(200, data=cert)
        
    except Exception as e:
        logger.error(f"Failed to get certificate: {e}", exc_info=True)
        return json_response(500, error="Failed to retrieve certificate")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_user_org_id(user: Any) -> str:
    """Extract organization ID from user object"""
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id', '')
    return ''


def _prepare_certificate_params(
    doc: Dict[str, Any],
    user: Any,
    org_name: str,
    body: Dict[str, Any]
) -> Dict[str, Any]:
    """Prepare parameters for certificate generation"""
    # User info - handle both dict and object
    if isinstance(user, dict):
        user_email = user.get('email', 'unknown')
        user_name = user.get('name', '')
    else:
        user_email = getattr(user, 'email', 'unknown')
        user_name = getattr(user, 'name', '')
    
    # Request body (optional fields with defaults)
    reviewer_name = body.get('reviewer_name', user_name)
    reviewer_email = body.get('reviewer_email', user_email)
    notes = body.get('notes', '')
    
    # Document data
    doc_id = doc.get('id')
    filename = doc.get('filename', 'Unknown')
    jurisdiction = doc.get('jurisdiction', 'UK')
    compliance_outcome = doc.get('compliance_outcome', 'unknown')
    risk_score = doc.get('risk_score', 0)
    violations_count = doc.get('violations_count', 0)
    
    # Scan date
    scan_date = (
        doc.get('scanned_at') or 
        doc.get('scan_completed_at') or
        doc.get('created_at') or
        datetime.utcnow().isoformat() + 'Z'
    )
    
    org_id = doc.get('organization_id')
    
    return {
        'document_id': doc_id,
        'document_filename': filename,
        'organization_id': org_id,
        'organization_name': org_name,
        'jurisdiction': jurisdiction,
        'compliance_outcome': compliance_outcome,
        'risk_score': risk_score,
        'scan_date': scan_date,
        'violations_count': violations_count,
        'issued_by': user_email,
        'reviewer_name': reviewer_name,
        'reviewer_email': reviewer_email,
        'notes': notes
    }
def handle_verify_certificate(req: func.HttpRequest) -> func.HttpResponse:
    """
    PUBLIC endpoint - no auth required
    GET /verify/{certificateId}
    
    Verifies a certificate and returns public details
    """
    try:
        cert_id = req.route_params.get('certificateId')
        if not cert_id:
            return json_response(400, error="Certificate ID required")
        
        from function_app_pkg.core.database import get_container
        container = get_container('documents')
        
        # Search all documents for this certificate (public verification)
        query = """
        SELECT * FROM c 
        WHERE c.type = 'document'
        AND ARRAY_LENGTH(c.certificates) > 0
        """
        
        docs = list(container.query_items(
            query=query,
            parameters=[],
            enable_cross_partition_query=True
        ))
        
        # Find the certificate
        for doc in docs:
            for cert in doc.get('certificates', []):
                if cert.get('certificate_id') == cert_id:
                    # Return public-safe information only
                    return json_response(200, data={
                        'certificate_id': cert.get('certificate_id'),
                        'document_filename': doc.get('filename'),
                        'organization_name': cert.get('organization_name', 'Organization'),
                        'jurisdiction': cert.get('jurisdiction', 'GLOBAL'),
                        'compliance_outcome': cert.get('compliance_outcome', 'unknown'),
                        'risk_score': cert.get('risk_score', 0),
                        'scan_date': cert.get('scan_date'),
                        'issued_at': cert.get('issued_at'),
                        'reviewer_name': cert.get('reviewer_name', ''),
                        'expires_at': cert.get('expires_at'),
                    })
        
        return json_response(404, error="Certificate not found or invalid")
        
    except Exception as e:
        logger.error(f"❌ Certificate verification error: {e}", exc_info=True)
        return json_response(500, error="Verification failed")

__all__ = [
    'handle_generate_certificate',
    'handle_list_certificates', 
    'handle_get_certificate',
    'handle_verify_certificate'
]