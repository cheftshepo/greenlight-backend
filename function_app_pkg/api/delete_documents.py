"""Delete documents endpoint - supports single, multiple, or all documents"""
import azure.functions as func
import logging
from function_app_pkg.core.database import get_document, list_documents_by_organization, get_db
from function_app_pkg.shared.http_utils import json_response
from function_app_pkg.api.audit_integration import log_document_action, ACTION_TYPES

logger = logging.getLogger(__name__)


def _get_user_attr(user, attr, default=None):
    if user is None:
        return default
    if isinstance(user, dict):
        return user.get(attr, default)
    return getattr(user, attr, default)


def _user_to_dict(user):
    if user is None:
        return None
    if isinstance(user, dict):
        return user
    return {
        'user_id': _get_user_attr(user, 'user_id'),
        'email': _get_user_attr(user, 'email'),
        'name': _get_user_attr(user, 'name'),
        'organization_id': _get_user_attr(user, 'organization_id'),
        'roles': _get_user_attr(user, 'roles', []),
    }


def handle_delete_single(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """DELETE /documents/{documentId}"""
    try:
        doc_id = req.route_params.get('documentId')
        if not doc_id:
            return json_response(400, error="Document ID required")

        organization_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email', 'unknown')

        if not organization_id:
            return json_response(400, error="Organization ID required")

        doc = get_document(doc_id, organization_id)
        if not doc:
            return json_response(404, error=f"Document not found: {doc_id}")

        if doc.get('organization_id') != organization_id:
            logger.warning(f"🚫 Unauthorized delete attempt: {user_email} → {doc_id}")
            return json_response(403, error="You don't have access to delete this document")

        # ── Delete from blob storage first ──────────────────────────────────
        blob_path = doc.get('blob_path') or doc.get('blob_name')
        if blob_path:
            try:
                from function_app_pkg.core.storage import blob_storage
                blob_storage.delete_file(blob_path)
                logger.info(f"🗑️ Blob deleted: {blob_path}")
            except Exception as blob_err:
                # Log but don't block — DB record deletion is more important
                logger.warning(f"⚠️ Blob delete failed (continuing): {blob_err}")

        # ── Delete from Cosmos DB ────────────────────────────────────────────
        db = get_db()
        container = db.get_container('documents')

        try:
            container.delete_item(item=doc_id, partition_key=organization_id)
            logger.info(f"🗑️ Document deleted: {doc_id} by {user_email}")
        except Exception as delete_error:
            logger.error(f"Cosmos delete failed: {delete_error}")
            return json_response(500, error=f"Failed to delete from database: {str(delete_error)}")

        try:
            log_document_action(
                document_id=doc_id,
                action_type=ACTION_TYPES["DELETE"],
                user_info=_user_to_dict(user),
                details={'filename': doc.get('filename'), 'jurisdiction': doc.get('jurisdiction')}
            )
        except Exception as audit_error:
            logger.warning(f"Audit logging failed: {audit_error}")

        return json_response(200, {
            'deleted': True,
            'document_id': doc_id,
            'filename': doc.get('filename'),
            'message': f'Document {doc.get("filename")} deleted successfully'
        })

    except Exception as e:
        logger.error(f"❌ Delete document error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to delete document: {str(e)}")


def handle_delete_multiple(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """POST /documents/delete-multiple"""
    try:
        try:
            body = req.get_json()
            document_ids = body.get('document_ids', [])
        except ValueError:
            return json_response(400, error="Invalid JSON body")

        if not document_ids or not isinstance(document_ids, list):
            return json_response(400, error="document_ids array is required")

        organization_id = _get_user_attr(user, 'organization_id')
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_dict = _user_to_dict(user)

        if not organization_id:
            return json_response(400, error="Organization ID required")

        deleted = []
        failed = []
        unauthorized = []

        db = get_db()
        container = db.get_container('documents')

        # Try to import blob storage once
        try:
            from function_app_pkg.core.storage import blob_storage
            blob_available = True
        except Exception:
            blob_available = False
            logger.warning("⚠️ Blob storage unavailable — skipping blob cleanup")

        for doc_id in document_ids:
            try:
                doc = get_document(doc_id, organization_id)
                if not doc:
                    failed.append({'id': doc_id, 'reason': 'not_found'})
                    continue

                if doc.get('organization_id') != organization_id:
                    unauthorized.append({'id': doc_id, 'reason': 'access_denied'})
                    continue

                # Delete blob
                if blob_available:
                    blob_path = doc.get('blob_path') or doc.get('blob_name')
                    if blob_path:
                        try:
                            blob_storage.delete_file(blob_path)
                        except Exception as blob_err:
                            logger.warning(f"⚠️ Blob delete failed for {doc_id}: {blob_err}")

                # Delete from Cosmos
                try:
                    container.delete_item(item=doc_id, partition_key=organization_id)
                    deleted.append({'id': doc_id, 'filename': doc.get('filename')})

                    try:
                        log_document_action(
                            document_id=doc_id,
                            action_type=ACTION_TYPES["DELETE"],
                            user_info=user_dict,
                            details={'filename': doc.get('filename'), 'batch_delete': True}
                        )
                    except Exception:
                        pass

                except Exception as delete_err:
                    logger.error(f"Failed to delete {doc_id}: {delete_err}")
                    failed.append({'id': doc_id, 'reason': str(delete_err)})

            except Exception as e:
                failed.append({'id': doc_id, 'reason': str(e)})

        logger.info(f"🗑️ Batch delete by {user_email}: {len(deleted)} deleted, {len(failed)} failed, {len(unauthorized)} unauthorized")

        return json_response(200, {
            'deleted_count': len(deleted),
            'failed_count': len(failed),
            'unauthorized_count': len(unauthorized),
            'deleted': deleted,
            'failed': failed if failed else None,
            'unauthorized': unauthorized if unauthorized else None,
            'message': f'Deleted {len(deleted)} of {len(document_ids)} documents'
        })

    except Exception as e:
        logger.error(f"❌ Batch delete error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to delete documents: {str(e)}")


def handle_delete_all(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """DELETE /documents/delete-all?confirm=true  (Admin only)"""
    try:
        if req.params.get('confirm', '').lower() != 'true':
            return json_response(400, error="Must include ?confirm=true to delete all documents")

        user_roles = _get_user_attr(user, 'roles', [])
        user_email = _get_user_attr(user, 'email', 'unknown')
        user_dict = _user_to_dict(user)

        if not any(role in ['Organization.Admin', 'Platform.SuperAdmin'] for role in user_roles):
            return json_response(403, error="Only admins can delete all documents")

        organization_id = _get_user_attr(user, 'organization_id')
        if not organization_id:
            return json_response(400, error="Organization ID required")

        # ← FIXED: was list_documents (doesn't exist), now list_documents_by_organization
        docs = list_documents_by_organization(org_id=organization_id, limit=1000)

        if not docs:
            return json_response(200, {'deleted_count': 0, 'message': 'No documents to delete'})

        db = get_db()
        container = db.get_container('documents')

        try:
            from function_app_pkg.core.storage import blob_storage
            blob_available = True
        except Exception:
            blob_available = False

        deleted_count = 0
        failed_count = 0

        for doc in docs:
            try:
                doc_id = doc.get('id') or doc.get('document_id')

                if blob_available:
                    blob_path = doc.get('blob_path') or doc.get('blob_name')
                    if blob_path:
                        try:
                            blob_storage.delete_file(blob_path)
                        except Exception:
                            pass

                container.delete_item(item=doc_id, partition_key=organization_id)
                deleted_count += 1

                try:
                    log_document_action(
                        document_id=doc_id,
                        action_type=ACTION_TYPES["DELETE"],
                        user_info=user_dict,
                        details={'filename': doc.get('filename'), 'delete_all_operation': True}
                    )
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Failed to delete {doc.get('id')}: {e}")
                failed_count += 1

        logger.warning(f"🗑️ DELETE ALL by {user_email}: {deleted_count} deleted, {failed_count} failed")

        return json_response(200, {
            'deleted_count': deleted_count,
            'failed_count': failed_count,
            'message': f'Deleted {deleted_count} documents for organization',
            'warning': 'This operation cannot be undone'
        })

    except Exception as e:
        logger.error(f"❌ Delete all error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to delete all documents: {str(e)}")