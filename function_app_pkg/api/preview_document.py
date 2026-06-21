"""
Document Preview Handler
Location: function_app_pkg/api/preview_document.py

GET /documents/{documentId}/preview

Returns a short-lived SAS URL suitable for inline browser preview
(iframe, PDF viewer, image tag). Unlike /download which forces
attachment, this sets Content-Disposition: inline.
"""
import logging
import azure.functions as func

logger = logging.getLogger(__name__)


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """GET /documents/{documentId}/preview — inline preview SAS URL"""
    from function_app_pkg.shared.http_utils import json_response

    try:
        # ── Auth ──────────────────────────────────────────────────────
        if not user:
            return json_response(401, error="Not authenticated")

        org_id = getattr(user, 'organization_id', None)
        if not org_id and isinstance(user, dict):
            org_id = user.get('organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")

        # ── Document lookup ───────────────────────────────────────────
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")

        from function_app_pkg.core.database import get_document
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")

        if doc.get('organization_id') != org_id:
            return json_response(403, error="Access denied")

        # ── Resolve blob path ─────────────────────────────────────────
        blob_path = (
            doc.get('blob_path')
            or doc.get('file_path')
            or doc.get('storage_path')
        )
        if not blob_path:
            return json_response(
                404,
                error="No file stored for this document. Please re-upload.",
            )

        filename = doc.get('filename', 'document')
        content_type = doc.get('content_type', 'application/octet-stream')

        logger.info(f"👁️ Preview request: {filename} | blob: {blob_path}")

        from function_app_pkg.core.storage import blob_storage

        # ── Option A: SAS redirect (preferred — browser loads direct) ─
        # inline disposition so PDFs/images open in browser, not download
        sas_url = blob_storage.generate_sas_url(
            blob_path,
            expiry_hours=1,
            disposition=f'inline; filename="{filename}"',
        )

        if sas_url:
            logger.info(f"✅ Preview SAS URL generated for: {blob_path}")
            # Return the URL as JSON so the frontend can load it in an
            # iframe / <object> / <img> without a page navigation.
            return json_response(200, data={
                'preview_url': sas_url,
                'filename': filename,
                'content_type': content_type,
                'expires_in_seconds': 3600,
            })

        # ── Option B: stream inline through backend (no storage key) ──
        logger.info(f"⚠️ No SAS key — streaming preview through backend")

        try:
            file_content, detected_type = blob_storage.download_file(blob_path)
        except FileNotFoundError:
            return json_response(404, error="File not found in storage")

        if not file_content:
            return json_response(404, error="File is empty")

        return func.HttpResponse(
            body=file_content,
            status_code=200,
            headers={
                'Content-Type': detected_type or content_type,
                'Content-Disposition': f'inline; filename="{filename}"',
                'Content-Length': str(len(file_content)),
                'Cache-Control': 'private, max-age=300',
                'Access-Control-Expose-Headers': 'Content-Disposition, Content-Length',
            },
        )

    except Exception as e:
        logger.error(f"❌ Preview failed: {e}")
        logger.exception(e)
        return json_response(500, error=str(e))