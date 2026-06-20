"""
Download Original Document
Location: function_app_pkg/api/download_original.py

Returns either:
  - A 302 redirect to a time-limited SAS URL (fast, preferred)
  - The raw file bytes streamed through the backend (fallback)
"""
import logging
import azure.functions as func

logger = logging.getLogger(__name__)


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """GET /documents/{documentId}/download — Download original file"""
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

        # Tenant isolation check
        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")

        # =============================================================
        # RESOLVE BLOB PATH
        # =============================================================
        blob_path = doc.get('blob_path')

        # Backwards compat: old docs may have stored the path differently
        if not blob_path:
            blob_path = doc.get('file_path') or doc.get('storage_path')

        if not blob_path:
            return json_response(
                404,
                error="Original file not available — no storage path on record.",
            )

        filename = doc.get('filename', 'document')
        content_type = doc.get('content_type', 'application/octet-stream')

        logger.info(f"📥 Download request: {filename} | blob: {blob_path}")

        # =============================================================
        # TRY SAS URL (fast — browser downloads direct from Azure)
        # =============================================================
        from function_app_pkg.core.storage import blob_storage

        sas_url = blob_storage.generate_sas_url(blob_path, expiry_hours=1)

        if sas_url:
            logger.info(f"✅ Serving SAS redirect for: {blob_path}")
            return func.HttpResponse(
                status_code=302,
                headers={
                    'Location': sas_url,
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Access-Control-Expose-Headers': 'Content-Disposition, Location',
                },
            )

        # =============================================================
        # FALLBACK: stream bytes through the backend
        # =============================================================
        logger.info(f"⚠️ No SAS key — streaming through backend: {blob_path}")

        try:
            file_content, detected_type = blob_storage.download_file(blob_path)
        except FileNotFoundError:
            return json_response(404, error="File not found in storage")

        return func.HttpResponse(
            body=file_content,
            status_code=200,
            headers={
                'Content-Type': detected_type or content_type,
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(file_content)),
                'Access-Control-Expose-Headers': 'Content-Disposition, Content-Length',
            },
        )

    except Exception as e:
        logger.error(f"❌ Download failed: {e}")
        logger.exception(e)
        return json_response(500, error=str(e))