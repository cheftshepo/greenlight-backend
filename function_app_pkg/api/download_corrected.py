"""
Download AI-Corrected Document
Location: function_app_pkg/api/download_corrected.py

Corrected files are stored at:
  documents/{org_id}/{doc_id}_corrected.pdf
"""
import logging
import azure.functions as func

logger = logging.getLogger(__name__)


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """GET /documents/{documentId}/download-corrected — Download corrected PDF"""
    from function_app_pkg.shared.http_utils import json_response

    try:
        # =============================================================
        # AUTH
        # =============================================================
        if not user:
            return json_response(401, error="Not authenticated")

        org_id = getattr(user, 'organization_id', None)
        if not org_id and isinstance(user, dict):
            org_id = user.get('organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")

        # =============================================================
        # GET DOCUMENT RECORD
        # =============================================================
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")

        from function_app_pkg.core.database import get_document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")

        # Tenant isolation
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")

        # =============================================================
        # CHECK CORRECTED VERSION EXISTS
        # =============================================================
        if doc.get('correction_status') != 'generated':
            return json_response(
                404,
                error="Corrected version not available. Generate it first.",
            )

        corrected_blob_path = doc.get('corrected_blob_path')
        if not corrected_blob_path:
            return json_response(404, error="Corrected file path not found")

        # Build download filename
        original_name = doc.get('filename', 'document')
        base_name = original_name.rsplit('.', 1)[0] if '.' in original_name else original_name
        filename = f"{base_name}_CORRECTED.pdf"

        logger.info(f"📥 Downloading corrected: {corrected_blob_path}")

        # =============================================================
        # TRY SAS URL (fast path)
        # =============================================================
        from function_app_pkg.core.storage import blob_storage

        sas_url = blob_storage.generate_sas_url(corrected_blob_path, expiry_hours=1)

        if sas_url:
            logger.info(f"✅ Serving SAS redirect for corrected: {corrected_blob_path}")
            return func.HttpResponse(
                status_code=302,
                headers={
                    'Location': sas_url,
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Access-Control-Expose-Headers': 'Content-Disposition, Location',
                },
            )

        # =============================================================
        # FALLBACK: stream through backend
        # =============================================================
        logger.info(f"⚠️ No SAS key — streaming corrected through backend")

        try:
            file_content, content_type = blob_storage.download_file(corrected_blob_path)
        except FileNotFoundError:
            return json_response(404, error="Corrected file not found in storage")

        if not file_content:
            return json_response(404, error="Corrected file is empty")

        return func.HttpResponse(
            body=file_content,
            status_code=200,
            headers={
                'Content-Type': content_type or 'application/pdf',
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(file_content)),
                'Access-Control-Expose-Headers': 'Content-Disposition, Content-Length',
            },
        )

    except Exception as e:
        logger.error(f"❌ Download corrected failed: {e}")
        logger.exception(e)
        return json_response(500, error=str(e))