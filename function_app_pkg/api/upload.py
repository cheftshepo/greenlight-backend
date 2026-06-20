"""
Document Upload Handler
Location: function_app_pkg/api/upload.py

Blob storage structure:
  documents/
    └── {org_id}/
        └── {doc_id}.{ext}

Each organization's files are isolated in their own folder.
"""

import logging
import uuid
from datetime import datetime
import azure.functions as func

logger = logging.getLogger(__name__)


def _get_user_attr(user, attr: str, default=None):
    """Safely get attribute from user (works for both objects and dicts)"""
    if user is None:
        return default
    if hasattr(user, attr):
        val = getattr(user, attr, default)
        if val is not None:
            return val
    if isinstance(user, dict):
        return user.get(attr, default)
    return default


def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """POST /documents/upload — WITH FULL AUDIT TRAIL"""
    from function_app_pkg.shared.http_utils import json_response

    start_time = datetime.utcnow()

    try:
        # =================================================================
        # STEP 1: AUTHENTICATION
        # =================================================================
        if not user:
            return json_response(401, error="Not authenticated")

        org_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email', 'unknown@unknown.com')
        user_id = (_get_user_attr(user, 'id')
                   or _get_user_attr(user, 'user_id')
                   or user_email)
        user_roles = _get_user_attr(user, 'roles', [])
        user_name = _get_user_attr(user, 'name', user_email)

        if not org_id:
            return json_response(400, error="Organization ID required")

        logger.info(f"📤 Upload request from {user_email} (org: {org_id})")

        # =================================================================
        # STEP 2: GET JURISDICTION
        # =================================================================
        jurisdiction = _extract_jurisdiction(req)

        if not jurisdiction:
            logger.error(f"❌ Upload rejected: No jurisdiction provided by {user_email}")
            logger.error(f"   Params: {dict(req.params)}")
            logger.error(f"   Form: {dict(req.form) if hasattr(req, 'form') and req.form else 'None'}")
            logger.error(f"   Files keys: {list(req.files.keys()) if req.files else 'None'}")
            return json_response(
                400,
                error="Jurisdiction is required. Please select a jurisdiction before uploading.",
            )

        jurisdiction = jurisdiction.upper().strip()
        logger.info(f"✅ Jurisdiction accepted: {jurisdiction}")

        # =================================================================
        # STEP 3: GET FILE
        # =================================================================
        if not req.files:
            return json_response(400, error="No file uploaded")

        file = req.files.get('file')
        if not file:
            file_keys = [k for k in req.files.keys() if k != 'jurisdiction']
            if file_keys:
                file = req.files.get(file_keys[0])
            else:
                return json_response(400, error="No file in request")

        filename = file.filename
        file_content = file.read()

        if not file_content or len(file_content) == 0:
            return json_response(400, error="Empty file")

        file_size = len(file_content)
        logger.info(f"📤 Upload: {filename} ({file_size} bytes) | Jurisdiction: {jurisdiction}")

        # =================================================================
        # STEP 4: EXTRACT TEXT
        # =================================================================
        from function_app_pkg.core.text_extractor import extractor

        extraction_start = datetime.utcnow()
        extraction_result = extractor.extract(file_content, filename)
        extraction_duration = (datetime.utcnow() - extraction_start).total_seconds()

        if not extraction_result.get('success'):
            _log_upload_failure(
                org_id=org_id,
                user_id=user_id,
                user_email=user_email,
                user_roles=user_roles,
                filename=filename,
                reason=f"Text extraction failed: {extraction_result.get('error')}",
            )
            return json_response(
                400,
                error=f"Text extraction failed: {extraction_result.get('error')}",
            )

        text = extraction_result.get('text', '')
        extraction_method = extraction_result.get('method', 'unknown')
        extraction_metadata = extraction_result.get('metadata', {})

        if len(text.strip()) < 10:
            _log_upload_failure(
                org_id=org_id,
                user_id=user_id,
                user_email=user_email,
                user_roles=user_roles,
                filename=filename,
                reason="No readable text in document",
            )
            return json_response(400, error="No readable text in document")

        logger.info(f"📝 Extracted {len(text)} chars using {extraction_method}")

        # =================================================================
        # STEP 5: GENERATE IDS (must come BEFORE blob upload)
        # =================================================================
        doc_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + 'Z'
        file_ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else 'unknown'

        # =================================================================
        # STEP 6: UPLOAD TO BLOB STORAGE
        #
        # ✅ CRITICAL FIX: Store blob_path for reliable retrieval
        #
        # Folder layout:  {org_id}/{doc_id}.{ext}
        # This keeps every organisation's documents isolated.
        # =================================================================
        from function_app_pkg.core.storage import blob_storage

        blob_path = f"{org_id}/{doc_id}.{file_ext}"
        content_type = _content_type_for_ext(file_ext)

        try:
            blob_url, blob_path_confirmed = blob_storage.upload_file(
                file_content=file_content,
                blob_path=blob_path,
                content_type=content_type,
                metadata={
                    'organization_id': org_id,
                    'document_id': doc_id,
                    'filename': filename,
                    'uploaded_by': user_email,
                },
            )
            logger.info(f"✅ File uploaded to blob: {blob_path_confirmed}")
        except Exception as blob_err:
            logger.error(f"❌ Blob upload failed: {blob_err}")
            return json_response(500, error=f"File storage failed: {blob_err}")

        # =================================================================
        # STEP 7: CREATE DOCUMENT RECORD IN COSMOS DB
        # ✅ CRITICAL FIX: Store blob_path in document record
        # =================================================================
        from function_app_pkg.core.database import create_document

        doc_data = {
            'id': doc_id,
            'organization_id': org_id,
            'uploaded_by': user_email,
            'uploaded_by_id': user_id,
            'uploaded_by_name': user_name,
            'filename': filename,
            'file_size_bytes': file_size,
            'file_type': file_ext,
            'content_type': content_type,
            'text_content': text,
            'extracted_text': text,
            'text_length': len(text),
            'extraction_method': extraction_method,
            'extraction_metadata': extraction_metadata,
            'extraction_duration_seconds': extraction_duration,
            'jurisdiction': jurisdiction,
            'status': 'uploaded',
            'workflow_status': 'uploaded',
            'created_at': now,
            'updated_at': now,
            'violations_count': 0,
            'risk_score': 0,
            'scan_count': 0,
            # ✅ Blob references — THIS IS THE FIX
            'blob_url': blob_url,
            'blob_path': blob_path_confirmed,  # ← Store full path for retrieval
            'blob_container': 'documents',
            # Watcher initialization
            'watchers': [],  # Empty array, ready for users to watch
        }

        doc = create_document(doc_data)
        logger.info(f"✅ Document created: {doc_id} | Jurisdiction: {jurisdiction} | Blob: {blob_path_confirmed}")

        # =================================================================
        # STEP 8: AUDIT LOG
        # =================================================================
        _log_audit(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            doc_id=doc_id,
            filename=filename,
            jurisdiction=jurisdiction,
            file_size=file_size,
            file_ext=file_ext,
            text=text,
            extraction_method=extraction_method,
            extraction_duration=extraction_duration,
        )

        # =================================================================
        # STEP 9: DECISION TRAIL
        # =================================================================
        _save_upload_decision_trail(
            doc_id=doc_id,
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            user_roles=user_roles,
            jurisdiction=jurisdiction,
            file_size=file_size,
            text=text,
            extraction_method=extraction_method,
            filename=filename,
        )

        # =================================================================
        # STEP 10: ANALYTICS EVENT
        # =================================================================
        _save_upload_analytics(
            org_id=org_id,
            doc_id=doc_id,
            user_email=user_email,
            user=user,
            file_size=file_size,
            text=text,
            extraction_method=extraction_method,
            extraction_duration=extraction_duration,
            jurisdiction=jurisdiction,
            file_ext=file_ext,
            start_time=start_time,
        )

        # =================================================================
        # STEP 11: RESPONSE
        # =================================================================
        upload_duration = (datetime.utcnow() - start_time).total_seconds()

        return json_response(201, data={
            'document_id': doc_id,
            'filename': filename,
            'jurisdiction': jurisdiction,
            'text_length': len(text),
            'file_size_bytes': file_size,
            'extraction_method': extraction_method,
            'extraction_duration_seconds': round(extraction_duration, 2),
            'upload_duration_seconds': round(upload_duration, 2),
            'status': 'uploaded',
            'blob_path': blob_path_confirmed,
            'message': (
                f'Upload successful. Document ready for compliance scan '
                f'under {jurisdiction} regulations.'
            ),
            'next_steps': [
                'Complete the briefing form to provide context',
                'Run compliance scan to check for violations',
            ],
        })

    except Exception as e:
        logger.error(f"❌ Upload failed: {e}")
        logger.exception(e)
        return json_response(500, error=str(e))


# =========================================================================
# HELPERS
# =========================================================================

def _extract_jurisdiction(req: func.HttpRequest) -> str | None:
    """Try every possible source for the jurisdiction value."""
    jurisdiction = None

    # 1. Query params
    jurisdiction = req.params.get('jurisdiction')
    if jurisdiction:
        logger.info(f"📍 Jurisdiction from params: {jurisdiction}")
        return jurisdiction

    # 2. Form data
    if hasattr(req, 'form') and req.form:
        jurisdiction = req.form.get('jurisdiction')
        if jurisdiction:
            logger.info(f"📍 Jurisdiction from form: {jurisdiction}")
            return jurisdiction

    # 3. Multipart field uploaded as a "file" (Azure Functions quirk)
    try:
        content_type = req.headers.get('Content-Type', '')
        if 'multipart/form-data' in content_type:
            for key in req.files:
                if key == 'jurisdiction':
                    jurisdiction = req.files.get(key).read().decode('utf-8')
                    logger.info(f"📍 Jurisdiction from files: {jurisdiction}")
                    return jurisdiction
    except Exception as parse_err:
        logger.warning(f"⚠️ Form parse attempt: {parse_err}")

    # 4. JSON body fallback
    try:
        body = req.get_json()
        if body and isinstance(body, dict):
            jurisdiction = body.get('jurisdiction')
            if jurisdiction:
                logger.info(f"📍 Jurisdiction from JSON: {jurisdiction}")
                return jurisdiction
    except Exception:
        pass

    return None


def _content_type_for_ext(ext: str) -> str:
    """Return a sensible Content-Type for common document extensions."""
    mapping = {
        'pdf': 'application/pdf',
        'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls': 'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'ppt': 'application/vnd.ms-powerpoint',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'txt': 'text/plain',
        'csv': 'text/csv',
        'html': 'text/html',
        'htm': 'text/html',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
    }
    return mapping.get(ext.lower(), 'application/octet-stream')


# =========================================================================
# AUDIT / ANALYTICS HELPERS (keep handle() readable)
# =========================================================================

def _log_audit(*, org_id, user_id, user_email, user_roles, doc_id,
               filename, jurisdiction, file_size, file_ext, text,
               extraction_method, extraction_duration):
    try:
        from function_app_pkg.core.database import get_db
        db = get_db()
        db.log_action(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            action='document.upload',
            resource_type='document',
            resource_id=doc_id,
            resource_name=filename,
            details={
                'jurisdiction': jurisdiction,
                'file_size_bytes': file_size,
                'file_type': file_ext,
                'text_length': len(text),
                'extraction_method': extraction_method,
                'extraction_duration_seconds': extraction_duration,
            },
            success=True,
        )
        logger.info("📝 Audit logged: document.upload")
    except Exception as audit_err:
        logger.warning(f"⚠️ Audit logging failed: {audit_err}")


def _save_upload_decision_trail(*, doc_id, org_id, user_id, user_email,
                                user_name, user_roles, jurisdiction,
                                file_size, text, extraction_method, filename):
    try:
        from function_app_pkg.core.database import save_decision_trail
        save_decision_trail({
            'document_id': doc_id,
            'organization_id': org_id,
            'event_type': 'upload',
            'decision': 'uploaded',
            'decision_maker': {
                'user_id': user_id,
                'email': user_email,
                'name': user_name,
                'roles': user_roles,
            },
            'document_state_at_decision': {
                'status': 'uploaded',
                'jurisdiction': jurisdiction,
                'file_size_bytes': file_size,
                'text_length': len(text),
            },
            'decision_context': {
                'filename': filename,
                'extraction_method': extraction_method,
                'extraction_success': True,
            },
        })
        logger.info("📋 Decision trail: upload event recorded")
    except Exception as dt_err:
        logger.warning(f"⚠️ Decision trail failed: {dt_err}")


def _save_upload_analytics(*, org_id, doc_id, user_email, user, file_size,
                           text, extraction_method, extraction_duration,
                           jurisdiction, file_ext, start_time):
    try:
        from function_app_pkg.core.database import save_analytics_event
        upload_duration = (datetime.utcnow() - start_time).total_seconds()
        save_analytics_event({
            'organization_id': org_id,
            'event_type': 'document_upload',
            'document_id': doc_id,
            'user_email': user_email,
            'metrics': {
                'file_size_bytes': file_size,
                'text_length': len(text),
                'extraction_duration_seconds': extraction_duration,
                'total_upload_duration_seconds': upload_duration,
                'extraction_method': extraction_method,
            },
            'dimensions': {
                'jurisdiction': jurisdiction,
                'file_type': file_ext,
                'user_department': _get_user_attr(user, 'department', 'unknown'),
            },
        })
        logger.info("📊 Analytics: upload event recorded")
    except Exception as analytics_err:
        logger.warning(f"⚠️ Analytics logging failed: {analytics_err}")


def _log_upload_failure(org_id: str, user_id: str, user_email: str,
                        user_roles: list, filename: str, reason: str):
    """Log failed upload attempts for analytics"""
    try:
        from function_app_pkg.core.database import get_db, save_analytics_event
        db = get_db()
        db.log_action(
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_roles=user_roles,
            action='document.upload.failed',
            resource_type='document',
            resource_id='',
            resource_name=filename,
            details={'reason': reason},
            success=False,
            error_message=reason,
        )
        save_analytics_event({
            'organization_id': org_id,
            'event_type': 'upload_failure',
            'user_email': user_email,
            'metrics': {'failure_count': 1},
            'dimensions': {'reason': reason[:100], 'filename': filename},
        })
    except Exception as e:
        logger.warning(f"⚠️ Failed to log upload failure: {e}")