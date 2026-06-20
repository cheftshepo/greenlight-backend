"""
Enterprise Compliance Platform — Azure Functions App
=====================================================
Version : 2.1.1
Updated : 2025

All functions now have explicit authentication inline to work with Azure Functions.
"""

# =============================================================================
# SECTION 0 — IMPORTS & BOOTSTRAP
# =============================================================================

import asyncio
import csv
import io
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from collections import defaultdict

import azure.functions as func

from function_app_pkg.api import discussion_handlers, team_workload_handler
from function_app_pkg.api.auth import (
    AppRole,
    Permission,
    authenticate_request,
)
from function_app_pkg.shared.http_utils import json_response

from function_app_pkg.api.stripe_webhooks import handle_stripe_webhook
from function_app_pkg.api.billing import (
    handle_marketplace_resolve,
    handle_billing_overview,
    handle_change_plan,
    handle_billing_usage,
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logger.info("🚀 Compliance Platform v2.1.1 starting …")

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _user_dict(user):
    """Convert AuthenticatedUser to backward-compatible dict for sub-modules."""
    if user is None:
        return None
    if isinstance(user, dict):
        return user
    return {
        'id': user.user_id,
        'email': user.email,
        'name': user.name,
        'role': user.roles[0] if user.roles else 'Marketing.User',
        'roles': user.roles,
        'organization_id': user.organization_id,
        'organization_name': user.organization_name,
    }


def _check_roles(user, required_roles):
    """Check if user has any of the required roles."""
    if AppRole.SUPER_ADMIN.value in user.roles:
        return True
    return any(role.value in user.roles for role in required_roles)


# =============================================================================
# SECTION 1 — CORS / OPTIONS PREFLIGHT
# =============================================================================

@app.function_name(name="cors_handler")
@app.route(route="{*path}", methods=["OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def cors_preflight(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        "", status_code=204,
        headers={
            "Access-Control-Allow-Origin": req.headers.get("Origin", "*"),
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "86400",
        },
    )


# =============================================================================
# SECTION 2 — PUBLIC ENDPOINTS (no auth)
# =============================================================================

@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    from function_app_pkg.api.health import handle
    return handle(req)


@app.route(route="auth/login", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def login_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    from function_app_pkg.api.auth import handle_login
    return handle_login(req)


@app.route(route="jurisdictions", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def list_all_jurisdictions(req: func.HttpRequest) -> func.HttpResponse:
    from function_app_pkg.api.jurisdictions import handle
    return handle(req, user=None)


@app.route(route="verify/{certificateId}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def verify_certificate(req: func.HttpRequest) -> func.HttpResponse:
    from function_app_pkg.api.certificate import handle_verify_certificate
    return handle_verify_certificate(req)


# =============================================================================
# SECTION 3 — AUTHENTICATION & USER PROFILE
# =============================================================================

@app.route(route="auth/me", methods=["GET"])
def get_current_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Get current user profile"""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.auth import handle_me
    return handle_me(req, user)


@app.route(route="auth/verify", methods=["POST"])
def verify_token_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    from function_app_pkg.api.auth import verify_token
    return verify_token(req)


@app.route(route="users/profile", methods=["GET"])
def get_user_profile(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.user_management import handle_get_profile
    return handle_get_profile(req, user)


@app.route(route="users/profile", methods=["PUT", "PATCH"])
def update_user_profile(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.user_management import handle_update_profile
    return handle_update_profile(req, user)


@app.route(route="organization/members", methods=["GET"])
def list_org_members_lightweight(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.user_management import handle_list_org_members
    return handle_list_org_members(req, user)


# =============================================================================
# SECTION 4 — DOCUMENTS
# =============================================================================

# --- 4a. Literal paths ---

@app.route(route="documents", methods=["GET"])
def list_all_documents(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.list_documents import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/upload", methods=["POST"])
def upload_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.upload import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/delete-multiple", methods=["POST"])
def delete_multiple_documents(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.delete_documents import handle_delete_multiple
    return handle_delete_multiple(req, user=_user_dict(user))


# --- 4b. Single document CRUD ---

@app.route(route="documents/{documentId}", methods=["GET"])
def get_document_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.get_document import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/{documentId}", methods=["DELETE"])
def delete_document_record(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.delete_documents import handle_delete_single
    return handle_delete_single(req, user=_user_dict(user))


@app.route(route="documents/{documentId}", methods=["PATCH"])
def patch_document_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """PATCH /documents/{documentId} — update violation_resolutions, notes, tags, etc."""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, update_document, log_activity

    PATCHABLE = {"violation_resolutions", "notes", "tags", "custom_metadata", "internal_notes"}
    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    doc = get_document(doc_id, org_id)
    if not doc:
        return json_response(404, error="Document not found")

    try:
        body = req.get_json()
    except ValueError:
        return json_response(400, error="Invalid JSON body")

    updates = {k: v for k, v in body.items() if k in PATCHABLE}
    if not updates:
        return json_response(400, error=f"No patchable fields. Allowed: {', '.join(PATCHABLE)}")

    now = datetime.utcnow().isoformat() + "Z"
    updates.update(updated_at=now, updated_by=user.email)
    update_document(doc_id, updates, org_id)

    try:
        log_activity(
            org_id=org_id, activity_type="document_updated",
            description=f"Updated: {', '.join(updates.keys())}",
            user_email=user.email, user_name=user.name,
            resource_type="document", resource_id=doc_id,
            metadata={"fields_updated": list(updates.keys())},
        )
    except Exception as e:
        logger.warning(f"Failed to log activity: {e}")

    return json_response(200, data={"id": doc_id, "updated_fields": list(updates.keys()), "updated_at": now})


# --- 4c. Scan endpoints ---

@app.route(route="documents/scan/{documentId}", methods=["POST"])
def scan_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.scan import handle
    from function_app_pkg.core.cost_tracker import log_cost_event

    response = handle(req, user)

    if response.status_code == 200:
        try:
            scan_data = json.loads(response.get_body())
            log_cost_event(
                org_id=user.organization_id, user_email=user.email,
                resource_type="openai",
                usage={"input_tokens": scan_data.get("stats", {}).get("tokens_used", 0), "output_tokens": 0, "model": "gpt-4"},
                document_id=req.route_params.get("documentId"), operation="document_scan",
            )
        except Exception as e:
            logger.warning(f"Cost tracking failed: {e}")

    return response


@app.route(route="documents/{documentId}/rescan", methods=["POST"])
def rescan_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.rescan import handle
    return handle(req, user)


# --- 4d. AI chat endpoints ---

@app.route(route="documents/{documentId}/ai-chat", methods=["GET"])
def get_ai_chat_session(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container

    doc_id = req.route_params["documentId"]
    session_id = f"chat_{doc_id}_{user.user_id}"

    try:
        session = get_container("documents").read_item(item=session_id, partition_key=user.organization_id)
        return json_response(200, data={"messages": session.get("messages", []), "session_id": session_id})
    except Exception:
        return json_response(200, data={"messages": [], "session_id": session_id})


@app.route(route="documents/{documentId}/ai-chat", methods=["POST"])
def save_ai_chat_session(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container

    doc_id = req.route_params["documentId"]
    messages = req.get_json().get("messages", [])
    session_id = f"chat_{doc_id}_{user.user_id}"

    get_container("documents").upsert_item({
        "id": session_id, "type": "ai_chat_session",
        "organization_id": user.organization_id, "document_id": doc_id,
        "user_id": user.user_id, "user_email": user.email,
        "messages": messages, "message_count": len(messages),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    return json_response(200, data={"saved": True, "message_count": len(messages)})


@app.route(route="documents/{documentId}/ai-chat", methods=["DELETE"])
def delete_ai_chat_session(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container

    doc_id = req.route_params["documentId"]
    session_id = f"chat_{doc_id}_{user.user_id}"

    try:
        get_container("documents").delete_item(item=session_id, partition_key=user.organization_id)
    except Exception:
        pass
    return json_response(200, data={"cleared": True})


@app.route(route="documents/{documentId}/ai-chat/message", methods=["POST"])
def ai_chat_message(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container
    from openai import AzureOpenAI

    doc_id = req.route_params["documentId"]
    body = req.get_json()
    user_message = body.get("message", "").strip()
    history = body.get("history", [])

    if not user_message:
        return json_response(400, error="message required")

    container = get_container("documents")
    try:
        doc = container.read_item(item=doc_id, partition_key=user.organization_id)
    except Exception:
        docs = list(container.query_items(
            query="SELECT * FROM c WHERE c.id = @id AND c.type = 'document'",
            parameters=[{"name": "@id", "value": doc_id}],
            enable_cross_partition_query=True,
        ))
        doc = docs[0] if docs else {}

    violations_summary = "\n".join(
        f"- [{v.get('severity','?').upper()}] {v.get('description','')[:120]}"
        for v in (doc.get("violations") or [])[:10]
    )

    system_prompt = f"""You are a compliance AI assistant reviewing a specific document.

DOCUMENT : {doc.get('filename', 'Unknown')}
JURISDICTION : {doc.get('jurisdiction', 'Unknown')}
RISK SCORE : {doc.get('risk_score', 0)}/100
OUTCOME : {doc.get('compliance_outcome', 'unknown')}
VIOLATIONS ({doc.get('violations_count', 0)} total):
{violations_summary or 'None found'}
LEGAL ADVISORY : {doc.get('legal_advisory', 'None provided')}

Answer concisely. Reference specific violations and regulations when relevant."""

    client = AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"), timeout=30.0,
    )

    messages_payload = [{"role": "system", "content": system_prompt}]
    messages_payload += [{"role": h["role"], "content": h["content"]} for h in history[-8:]]
    messages_payload.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4"),
            messages=messages_payload, temperature=0.3, max_tokens=1000,
        )
        return json_response(200, data={"response": response.choices[0].message.content})
    except Exception as e:
        logger.exception(e)
        return json_response(500, error=f"AI chat failed: {str(e)[:200]}")


@app.route(route="documents/{documentId}/chat", methods=["POST"])
def chat_with_document_ai(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.chat import handle
    return handle(req, user)


# --- 4e. Document sub-resources ---

@app.route(route="documents/{documentId}/generate-questions", methods=["POST"])
def generate_document_questions(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.generate_questions import handle
    return handle(req, user)


@app.route(route="documents/{documentId}/briefing", methods=["POST"])
def create_compliance_briefing(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.briefing import handle
    return handle(req, user)


@app.route(route="documents/{documentId}/submit-answers", methods=["POST"])
def submit_questionnaire_answers(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api import submit_answers
    return asyncio.run(submit_answers.handle(req, user))


@app.route(route="documents/{documentId}/similar", methods=["GET"])
def get_similar_documents_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_db

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id
    limit = int(req.params.get("limit", "5"))
    risk_margin = int(req.params.get("risk_margin", "15"))

    doc = get_document(doc_id, org_id)
    if not doc:
        return json_response(404, error="Document not found")

    risk_score = doc.get("risk_score", 50)
    jurisdiction = doc.get("jurisdiction", "")
    container = get_db().get_container("documents")

    base_params = [
        {"name": "@org_id", "value": org_id}, {"name": "@doc_id", "value": doc_id},
        {"name": "@jurisdiction", "value": jurisdiction}, {"name": "@limit", "value": limit},
    ]

    similar = list(container.query_items(
        query="""
        SELECT c.id, c.filename, c.jurisdiction, c.risk_score, c.status,
               c.compliance_outcome, c.violations_count, c.assigned_to_name,
               c.approved_at, c.rejected_at, c.created_at, c.updated_at,
               c.briefing.marketing_type AS marketing_type
        FROM c
        WHERE c.organization_id=@org_id AND c.type='document' AND c.id!=@doc_id
          AND c.status IN ('approved','rejected','escalated')
          AND c.jurisdiction=@jurisdiction
          AND c.risk_score>=@risk_low AND c.risk_score<=@risk_high
        ORDER BY c.updated_at DESC OFFSET 0 LIMIT @limit
        """,
        parameters=base_params + [
            {"name": "@risk_low", "value": max(0, risk_score - risk_margin)},
            {"name": "@risk_high", "value": min(100, risk_score + risk_margin)},
        ],
        partition_key=org_id,
    ))

    if len(similar) < 3:
        seen = {d["id"] for d in similar}
        for fd in container.query_items(
            query="""
            SELECT c.id, c.filename, c.jurisdiction, c.risk_score, c.status,
                   c.compliance_outcome, c.violations_count, c.assigned_to_name,
                   c.approved_at, c.rejected_at, c.created_at, c.updated_at
            FROM c
            WHERE c.organization_id=@org_id AND c.type='document' AND c.id!=@doc_id
              AND c.status IN ('approved','rejected','escalated') AND c.jurisdiction=@jurisdiction
            ORDER BY c.updated_at DESC OFFSET 0 LIMIT @limit
            """,
            parameters=base_params, partition_key=org_id,
        ):
            if fd["id"] not in seen and len(similar) < limit:
                similar.append(fd)

    summary = {
        "total": len(similar),
        "approved": sum(1 for d in similar if d.get("status") == "approved"),
        "rejected": sum(1 for d in similar if d.get("status") == "rejected"),
        "escalated": sum(1 for d in similar if d.get("status") == "escalated"),
        "avg_risk_score": round(sum(d.get("risk_score", 0) for d in similar) / max(len(similar), 1), 1),
    }

    return json_response(200, data={
        "documents": similar, "summary": summary,
        "search_criteria": {"jurisdiction": jurisdiction, "risk_range": [max(0, risk_score - risk_margin), min(100, risk_score + risk_margin)]},
    })


@app.route(route="documents/{documentId}/export-report", methods=["GET"])
def export_document_report(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_decision_trail

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    doc = get_document(doc_id, org_id)
    if not doc:
        return json_response(404, error="Document not found")

    try:
        trail = get_decision_trail(doc_id, org_id)
    except Exception:
        trail = []

    try:
        pdf_buffer = _generate_compliance_report(doc, trail, user)
    except ImportError:
        return json_response(500, error="PDF library not installed. Run: pip install reportlab")

    filename = f"compliance-report-{doc.get('filename', doc_id)}-{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    return func.HttpResponse(
        body=pdf_buffer.getvalue(), status_code=200,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@app.route(route="documents/{documentId}/file", methods=["GET"])
def get_document_file(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document
    from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
    from datetime import datetime, timedelta
    import logging
    import os

    logger = logging.getLogger(__name__)
    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    try:
        # Get document metadata
        doc = get_document(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")

        # CRITICAL: The document ID in the path is the folder name
        # From diagnostic: dedcb91a-54d8-4fb7-b714-5e9b83e97ef5/6f2d86e3-723c-4d0c-b0c9-33652fec565e.pdf
        # The folder is the first UUID, the file is the second UUID
        
        # Try to get the parent folder from document metadata
        parent_id = doc.get("parent_id") or doc.get("folder_id") or doc.get("conversation_id")
        
        if parent_id:
            # Use the parent folder structure
            blob_path = f"{parent_id}/{doc_id}.pdf"
            logger.info(f"Using parent folder structure: {blob_path}")
        else:
            # The blob is stored in a folder named with the first part of the path
            # From your error, the folder appears to be 'dedcb91a-54d8-4fb7-b714-5e9b83e97ef5'
            # We need to find this from the document or construct it
            blob_path = f"{doc_id}/{doc_id}.pdf"
            logger.info(f"Using flat structure: {blob_path}")

        # Storage configuration - USE AzureWebJobsStorage which we know works
        conn_str = os.getenv("AzureWebJobsStorage")
        if not conn_str:
            conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        
        container_name = os.getenv("AZURE_STORAGE_CONTAINER", "documents")
        
        # Create blob client
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        
        # Try multiple possible paths (the diagnostic shows the correct one)
        possible_paths = [
            blob_path,
            f"{doc_id}/{doc_id}.pdf",
            f"documents/{doc_id}.pdf",
            doc_id if doc_id.endswith('.pdf') else f"{doc_id}.pdf",
        ]
        
        # Also try with the folder structure from the diagnostic
        # Since we know the folder is 'dedcb91a-54d8-4fb7-b714-5e9b83e97ef5', 
        # but we don't have that ID here, we need to find it
        
        # Let's list blobs to find the matching one (temporary debugging)
        container_client = blob_service_client.get_container_client(container_name)
        
        # Search for blobs containing this document ID
        matching_blobs = []
        try:
            blobs = container_client.list_blobs(name_starts_with=doc_id[:8])  # Search by prefix
            for blob in blobs:
                if doc_id in blob.name:
                    matching_blobs.append(blob.name)
                    logger.info(f"Found matching blob: {blob.name}")
        except Exception as e:
            logger.warning(f"Error searching blobs: {e}")
        
        if matching_blobs:
            # Use the first matching blob
            blob_path = matching_blobs[0]
            logger.info(f"Using found blob path: {blob_path}")
        else:
            # If no match found, try the standard pattern from diagnostic
            # The diagnostic shows the folder is the first UUID
            # We need to get this from the document's conversation_id or similar
            conversation_id = doc.get("conversation_id") or doc.get("folder_id")
            if conversation_id:
                blob_path = f"{conversation_id}/{doc_id}.pdf"
            else:
                # Last resort - assume the document ID itself is the folder
                # This will need to be fixed based on your data
                return json_response(404, error="Could not determine blob path structure")
        
        # Get blob client
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_path)
        
        # Check if blob exists
        if not blob_client.exists():
            # Log available blobs for debugging
            try:
                prefix = blob_path.split('/')[0] if '/' in blob_path else None
                if prefix:
                    blobs = list(container_client.list_blobs(name_starts_with=prefix, max_results=5))
                    logger.info(f"Available blobs with prefix '{prefix}': {[b.name for b in blobs]}")
            except Exception as e:
                logger.warning(f"Could not list blobs: {e}")
            
            return json_response(404, error=f"File not found at path: {blob_path}")

        # Generate SAS token using account key from environment
        account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        if not account_key:
            return json_response(500, error="Storage account key not configured")

        sas_token = generate_blob_sas(
            account_name=blob_service_client.account_name,
            container_name=container_name,
            blob_name=blob_path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(hours=1),
            start=datetime.utcnow() - timedelta(minutes=5)
        )
        
        sas_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{container_name}/{blob_path}?{sas_token}"
        logger.info(f"Generated SAS URL for {blob_path}")
        
        return func.HttpResponse(
            status_code=302,
            headers={
                "Location": sas_url,
                "Access-Control-Expose-Headers": "Location",
                "Access-Control-Allow-Origin": req.headers.get("Origin", "*"),
                "Access-Control-Allow-Credentials": "true"
            }
        )
        
    except Exception as e:
        logger.error(f"Error generating SAS URL: {str(e)}", exc_info=True)
        return json_response(500, error=f"Failed to access document: {str(e)}")
    
    
@app.route(route="documents/{documentId}/download", methods=["GET"])
def download_original(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.download_original import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/{documentId}/generate-corrected", methods=["POST"])
def generate_corrected(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.generate_corrected import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/{documentId}/download-corrected", methods=["GET"])
def download_corrected(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.download_corrected import handle
    return handle(req, user=_user_dict(user))


@app.route(route="documents/{documentId}/activity", methods=["GET"])
def get_document_activity(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_activity_feed

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    if not get_document(doc_id, org_id):
        return json_response(404, error="Document not found")

    activities = get_activity_feed(
        org_id=org_id, document_id=doc_id,
        days=int(req.params.get("days", "90")), limit=int(req.params.get("limit", "100")),
    )
    return json_response(200, data={"activities": activities, "total": len(activities), "document_id": doc_id})


@app.route(route="documents/{documentId}/audit-logs", methods=["GET"])
def get_document_audit_logs(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_audit_logs

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    if not get_document(doc_id, org_id):
        return json_response(404, error="Document not found")

    logs = get_audit_logs(
        org_id=org_id, resource_type="document", resource_id=doc_id,
        days=int(req.params.get("days", "90")), limit=int(req.params.get("limit", "100")),
    )
    return json_response(200, data={"logs": logs, "total": len(logs), "document_id": doc_id})


@app.route(route="documents/{documentId}/decision-trail", methods=["GET"])
def get_document_decision_trail(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_decision_trail

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    doc = get_document(doc_id, org_id)
    if not doc:
        return json_response(404, error="Document not found")

    decisions = get_decision_trail(doc_id, org_id)
    return json_response(200, data={
        "document_id": doc_id, "document_filename": doc.get("filename"),
        "current_status": doc.get("status"), "decisions": decisions, "total_decisions": len(decisions),
    })


@app.route(route="documents/{documentId}/ai-conversations", methods=["GET"])
def get_document_ai_conversations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_document, get_ai_conversations_for_document

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    if not get_document(doc_id, org_id):
        return json_response(404, error="Document not found")

    convs = get_ai_conversations_for_document(doc_id)
    return json_response(200, data={"document_id": doc_id, "conversations": convs, "total": len(convs)})


@app.route(route="documents/{documentId}/applied-regulations", methods=["GET"])
def get_document_applied_regulations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_get_document_applied_regulations
    return handle_get_document_applied_regulations(req, user)


@app.route(route="documents/{documentId}/notifications", methods=["GET"])
def get_document_notifications(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_notifications import handle_get_document_notifications
    return handle_get_document_notifications(req, user)


@app.route(route="documents/{documentId}/notifications/mark-read", methods=["POST"])
def mark_document_notifications_read(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_notifications import handle_mark_document_notifications_read
    return handle_mark_document_notifications_read(req, user)


# --- 4f. Document approval workflow ---

@app.route(route="documents/{documentId}/assign", methods=["POST"])
def assign_document_to_user(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.document_assignments import handle_assign_document
    return handle_assign_document(req, user)


@app.route(route="documents/{documentId}/assign-team", methods=["POST"])
def assign_document_to_team(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.teams import handle_assign_to_team
    return handle_assign_to_team(req, user)


@app.route(route="documents/{documentId}/submit-review", methods=["POST"])
def submit_document_for_review(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.approval import handle_submit_for_review
    return handle_submit_for_review(req, user)


@app.route(route="documents/{documentId}/approve", methods=["POST"])
def approve_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN, AppRole.LEGAL, AppRole.DLA_PIPER]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.approval import handle_approve
    return handle_approve(req, user)


@app.route(route="documents/{documentId}/reject", methods=["POST"])
def reject_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN, AppRole.LEGAL, AppRole.DLA_PIPER]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.approval import handle_reject
    return handle_reject(req, user)


@app.route(route="documents/{documentId}/escalate", methods=["POST"])
def escalate_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.approval import handle_escalate
    return handle_escalate(req, user)


# --- 4g. Multi-stage workflow actions ---

@app.route(route="documents/{documentId}/workflow", methods=["GET"])
def get_document_workflow_state(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_get_document_workflow
    return handle_get_document_workflow(req, user)


@app.route(route="documents/{documentId}/workflow/assign", methods=["POST"])
def assign_workflow_to_document(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_assign_workflow
    return handle_assign_workflow(req, user)


@app.route(route="documents/{documentId}/workflow/advance", methods=["POST"])
def advance_workflow_manually(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.workflows import handle_advance_workflow
    return handle_advance_workflow(req, user)


@app.route(route="documents/{documentId}/submit-workflow", methods=["POST"])
def submit_document_to_workflow(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_submit_document
    return handle_submit_document(req, user)


@app.route(route="documents/{documentId}/approve-stage", methods=["POST"])
def approve_workflow_stage(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_approve_stage
    return handle_approve_stage(req, user)


@app.route(route="documents/{documentId}/reject-stage", methods=["POST"])
def reject_workflow_stage(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_reject_stage
    return handle_reject_stage(req, user)


# --- 4h. Watchers ---

@app.route(route="documents/{documentId}/watchers", methods=["POST"])
def add_document_watcher(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_add_watcher
    return handle_add_watcher(req, user)


@app.route(route="documents/{documentId}/watchers", methods=["GET"])
def list_document_watchers(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_get_watchers
    return handle_get_watchers(req, user)


@app.route(route="documents/{documentId}/watchers/{email}", methods=["DELETE"])
def remove_document_watcher(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_remove_watcher
    return handle_remove_watcher(req, user)


# --- 4i. Certificates ---

@app.route(route="documents/{documentId}/generate-certificate", methods=["POST"])
def generate_compliance_certificate(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.certificate import handle_generate_certificate
    return handle_generate_certificate(req, user)


@app.route(route="documents/{documentId}/certificates", methods=["GET"])
def list_document_certificates(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.certificate import handle_list_certificates
    return handle_list_certificates(req, user)


# --- 4j. Discussions ---

@app.route(route="documents/{documentId}/discussions", methods=["GET"])
def list_discussions(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_list_discussions(req, user)


@app.route(route="documents/{documentId}/discussions", methods=["POST"])
def create_discussion(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_create_discussion(req, user)


@app.route(route="documents/{documentId}/discussions/search-users", methods=["GET"])
def search_discussion_users(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_search_users(req, user)


@app.route(route="documents/{documentId}/discussions/ai-contribution", methods=["POST"])
def ai_discussion_contribution(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_ai_contribution(req, user)


@app.route(route="documents/{documentId}/discussions/{discussionId}/reply", methods=["POST"])
def reply_to_discussion(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_reply_discussion(req, user)


@app.route(route="documents/{documentId}/discussions/{discussionId}/resolve", methods=["POST"])
def resolve_discussion(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return discussion_handlers.handle_resolve_discussion(req, user)


# =============================================================================
# SECTION 5 — ASSIGNMENTS
# =============================================================================

@app.route(route="assignments/my-queue", methods=["GET"])
def get_my_assignment_queue(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_get_my_queue
    return handle_get_my_queue(req, user)


@app.route(route="assignments/assignment-analytics", methods=["GET"])
def get_assignment_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.document_assignments import handle_get_assignment_analytics
    return handle_get_assignment_analytics(req, user)


@app.route(route="assignments/{assignmentId}", methods=["GET"])
def get_assignment_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_get_assignment
    return handle_get_assignment(req, user)


@app.route(route="assignments/{assignmentId}", methods=["PUT"])
def update_assignment_status(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_update_assignment
    return handle_update_assignment(req, user)


@app.route(route="assignments/{assignmentId}/full-context", methods=["GET"])
def get_assignment_full_context(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_get_assignment_with_context
    return handle_get_assignment_with_context(req, user)


@app.route(route="assignments/{assignmentId}/timeline", methods=["GET"])
def get_assignment_timeline(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_get_assignment_timeline
    return handle_get_assignment_timeline(req, user)


@app.route(route="assignments/{assignmentId}/decisions", methods=["GET"])
def get_assignment_decisions(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_get_assignment_decisions
    return handle_get_assignment_decisions(req, user)


@app.route(route="assignments/{assignmentId}/watchers", methods=["POST"])
def add_assignment_watcher(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_add_watcher
    return handle_add_watcher(req, user)


@app.route(route="assignments/{assignmentId}/comments", methods=["POST"])
def add_assignment_comment(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.document_assignments import handle_add_comment
    return handle_add_comment(req, user)


# =============================================================================
# SECTION 6 — TEAM / TEAMS
# =============================================================================

# --- 6a. /team/* (current-user) ---

@app.route(route="team/workload", methods=["GET"])
def get_team_workload(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    return team_workload_handler.handle_get_team_workload(req, user)


@app.route(route="team/activity", methods=["GET"])
def get_team_activity_feed(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_get_activity_feed
    return handle_get_activity_feed(req, user)


@app.route(route="team/queue", methods=["GET"])
def get_team_queue(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_get_team_queue
    return handle_get_team_queue(req, user)


@app.route(route="team/queue/{documentId}/claim", methods=["POST"])
def claim_from_team_queue(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_claim_from_queue
    return handle_claim_from_queue(req, user)


@app.route(route="team/members", methods=["GET"])
def get_team_members(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_get_team_members
    return handle_get_team_members(req, user)


# --- 6b. /teams/* (management) ---

@app.route(route="teams/my-queue", methods=["GET"])
def get_my_teams_queue(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.teams import handle_get_my_teams_queue
    return handle_get_my_teams_queue(req, user)


@app.route(route="teams", methods=["GET"])
def list_teams(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.teams import handle_list_teams
    return handle_list_teams(req, user)


@app.route(route="teams", methods=["POST"])
def create_team(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.teams import handle_create_team
    return handle_create_team(req, user)


@app.route(route="teams/{teamId}", methods=["GET"])
def get_team_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.teams import handle_get_team
    return handle_get_team(req, user)


@app.route(route="teams/{teamId}/dashboard", methods=["GET"])
def get_team_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.teams import handle_get_team_dashboard
    return handle_get_team_dashboard(req, user)


@app.route(route="teams/{teamId}/members", methods=["POST"])
def add_team_member(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.teams import handle_add_team_member
    return handle_add_team_member(req, user)


@app.route(route="teams/{teamId}/members/{email}", methods=["DELETE"])
def remove_team_member(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.teams import handle_remove_team_member
    return handle_remove_team_member(req, user)


@app.route(route="teams/{teamId}/members/{email}/role", methods=["PUT"])
def update_team_member_role(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.teams import handle_update_member_role
    return handle_update_member_role(req, user)


# =============================================================================
# SECTION 7 — LEGAL
# =============================================================================

@app.route(route="legal/queue", methods=["GET"])
def get_legal_queue(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.approval import handle_get_legal_queue
    return handle_get_legal_queue(req, user)


@app.route(route="legal/advisory", methods=["GET"])
def get_legal_advisory_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN, AppRole.ADMIN, AppRole.COMPLIANCE]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.database import get_container, get_users_by_role

    org_id = user.organization_id
    container = get_container("documents")
    docs = list(container.query_items(
        query="""
        SELECT * FROM c
        WHERE c.organization_id=@org_id AND c.type='document'
          AND (IS_DEFINED(c.legal_advisory)
               OR c.workflow_status IN ('legal_review','dla_piper_review','escalated'))
        ORDER BY c.updated_at DESC OFFSET 0 LIMIT 50
        """,
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id,
    ))

    legal_team = get_users_by_role(org_id, "Legal.Advisor")
    return json_response(200, data={
        "summary": {
            "total_advisory_cases": sum(1 for d in docs if d.get("legal_advisory")),
            "pending_review": sum(1 for d in docs if d.get("workflow_status") in ["legal_review", "dla_piper_review", "escalated"]),
            "legal_team_members": len(legal_team),
        },
        "recent_cases": docs[:10], "legal_team": legal_team,
        "your_role": user.roles[0] if user.roles else "unknown",
    })


@app.route(route="legal/my-advisories", methods=["GET"])
def get_my_legal_advisories(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.database import get_container

    org_id = user.organization_id
    days = int(req.params.get("days", "90"))
    limit = int(req.params.get("limit", "50"))
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"

    trail_entries = list(get_container("audit_logs").query_items(
        query="""
        SELECT * FROM c
        WHERE c.type='decision_trail' AND c.organization_id=@org_id
          AND c.decision_type='legal_advisory' AND c.decision_maker.email=@email
          AND c.created_at>=@cutoff
        ORDER BY c.created_at DESC
        """,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@email", "value": user.email},
            {"name": "@cutoff", "value": cutoff},
        ],
        enable_cross_partition_query=True, max_item_count=limit,
    ))

    doc_container = get_container("documents")
    results, seen = [], set()

    for entry in trail_entries:
        doc_id = entry.get("document_id")
        if not doc_id:
            continue
        doc = None
        if doc_id not in seen:
            seen.add(doc_id)
            try:
                doc = doc_container.read_item(item=doc_id, partition_key=org_id)
            except Exception:
                pass
        ctx = entry.get("decision_context", {})
        results.append({
            "advisory_id": entry.get("id"),
            "submitted_at": entry.get("decision_timestamp") or entry.get("created_at"),
            "advisory_text": ctx.get("advisory", ""),
            "recommendation": ctx.get("recommendation", ""),
            "cited_regulations": ctx.get("cited_regulations", []),
            "document_id": doc_id,
            "document_filename": entry.get("document_filename"),
            "document_status": doc.get("status") if doc else None,
            "workflow_status": doc.get("workflow_status") if doc else None,
            "risk_score": doc.get("risk_score") if doc else None,
            "jurisdiction": doc.get("jurisdiction") if doc else None,
            "violations_count": doc.get("violations_count") if doc else None,
            "outcome": doc.get("compliance_outcome") if doc else None,
        })

    total = len(results)
    return json_response(200, data={
        "advisories": results, "total": total, "period_days": days,
        "summary": {
            "total": total,
            "approve": sum(1 for r in results if r["recommendation"] == "approve"),
            "reject": sum(1 for r in results if r["recommendation"] == "reject"),
            "review": sum(1 for r in results if r["recommendation"] == "review"),
        },
    })


@app.route(route="legal/history", methods=["GET"])
def get_legal_history(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN, AppRole.ADMIN, AppRole.COMPLIANCE]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.database import get_container

    org_id = user.organization_id
    days = int(req.params.get("days", "180"))
    limit = int(req.params.get("limit", "100"))
    status_filter = req.params.get("status", "all")
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"

    conditions = [
        "c.organization_id=@org_id", "c.type='document'",
        "(c.status='escalated' OR IS_DEFINED(c.escalated_at) OR IS_DEFINED(c.legal_advisory) OR c.workflow_status IN ('legal_review','dla_piper_review','legal_reviewed','escalated'))",
        "(c.escalated_at>=@cutoff OR c.legal_reviewed_at>=@cutoff OR c.updated_at>=@cutoff)",
    ]
    params = [{"name": "@org_id", "value": org_id}, {"name": "@cutoff", "value": cutoff}]

    if status_filter == "approved":
        conditions.append("c.status='approved'")
    elif status_filter == "rejected":
        conditions.append("c.status='rejected'")
    elif status_filter == "pending":
        conditions.append("c.status NOT IN ('approved','rejected')")

    params.append({"name": "@limit", "value": limit})
    docs = list(get_container("documents").query_items(
        query=f"""
        SELECT c.id, c.filename, c.jurisdiction, c.risk_score, c.violations_count,
               c.status, c.workflow_status, c.compliance_outcome,
               c.escalated_at, c.escalated_by, c.escalated_by_name, c.escalation_reason,
               c.legal_advisory, c.legal_recommendation, c.legal_reviewed_by, c.legal_reviewed_at,
               c.cited_regulations, c.assigned_to, c.assigned_to_name, c.assigned_at,
               c.assignment_priority, c.assignment_deadline, c.ticket_id, c.team_name,
               c.approved_at, c.approved_by, c.approved_by_name,
               c.rejected_at, c.rejected_by, c.rejected_by_name,
               c.organization_id, c.uploaded_by_name, c.created_at, c.updated_at
        FROM c WHERE {' AND '.join(conditions)}
        ORDER BY c.updated_at DESC OFFSET 0 LIMIT @limit
        """,
        parameters=params, partition_key=org_id,
    ))

    def _stage(d):
        if d.get("status") == "approved": return "approved"
        if d.get("status") == "rejected": return "rejected"
        if d.get("legal_advisory"): return "advisory_given"
        if d.get("assigned_to"): return "assigned"
        if d.get("workflow_status") in ("legal_review", "dla_piper_review"): return "in_legal_review"
        return "escalated"

    for d in docs:
        d["journey_stage"] = _stage(d)

    stages = [d["journey_stage"] for d in docs]
    return json_response(200, data={
        "documents": docs,
        "summary": {
            "total": len(docs),
            "escalated": stages.count("escalated") + stages.count("in_legal_review"),
            "advisory_given": stages.count("advisory_given"),
            "assigned": stages.count("assigned"),
            "approved": stages.count("approved"),
            "rejected": stages.count("rejected"),
            "period_days": days,
        },
    })


@app.route(route="legal/documents/{documentId}/advise", methods=["POST"])
def legal_provide_advisory(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.database import get_document, update_document, save_decision_trail

    doc_id = req.route_params["documentId"]
    org_id = user.organization_id

    doc = get_document(doc_id, org_id)
    if not doc:
        return json_response(404, error="Document not found")

    body = req.get_json()
    advisory = body.get("advisory", "").strip()
    recommendation = body.get("recommendation", "review")
    cited_regulations = body.get("cited_regulations", [])

    if not advisory:
        return json_response(400, error="advisory text required")

    now = datetime.utcnow()
    save_decision_trail({
        "organization_id": org_id, "document_id": doc_id,
        "document_filename": doc.get("filename"),
        "decision": "advisory", "decision_type": "legal_advisory",
        "decision_maker": {"email": user.email, "name": user.name, "roles": user.roles},
        "decision_context": {"advisory": advisory, "recommendation": recommendation, "cited_regulations": cited_regulations},
        "decision_timestamp": now.isoformat() + "Z",
    })

    update_document(doc_id, {
        "legal_advisory": advisory, "legal_recommendation": recommendation,
        "legal_reviewed_by": user.email, "legal_reviewed_at": now.isoformat() + "Z",
        "workflow_status": "legal_reviewed", "updated_at": now.isoformat() + "Z",
    }, org_id)

    return json_response(200, data={
        "document_id": doc_id, "advisory_provided": True,
        "recommendation": recommendation, "reviewed_by": user.email,
    })


# =============================================================================
# SECTION 8 — WORKFLOWS
# =============================================================================

@app.route(route="workflows/pending-approvals", methods=["GET"])
def get_pending_approvals_list(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.workflows import handle_get_pending_approvals
    return handle_get_pending_approvals(req, user)


@app.route(route="workflows/recommendations", methods=["GET"])
def get_workflow_recommendations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_get_recommendations
    return handle_get_recommendations(req, user)


@app.route(route="workflows", methods=["GET"])
def list_workflows(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_list_workflows
    return handle_list_workflows(req, user)


@app.route(route="workflows", methods=["POST"])
def create_workflow(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.workflows import handle_create_workflow
    return handle_create_workflow(req, user)


@app.route(route="workflows/{workflowId}", methods=["GET"])
def get_workflow_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.workflows import handle_get_workflow
    return handle_get_workflow(req, user)


@app.route(route="workflows/{workflowId}", methods=["PUT"])
def update_workflow(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.workflows import handle_update_workflow
    return handle_update_workflow(req, user)


@app.route(route="workflows/{workflowId}", methods=["DELETE"])
def delete_workflow(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.workflows import handle_delete_workflow
    return handle_delete_workflow(req, user)


# =============================================================================
# SECTION 9 — REGULATIONS
# =============================================================================

@app.route(route="regulations/lookup", methods=["GET"])
def lookup_regulation(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_db
    from openai import AzureOpenAI

    reference = req.params.get("reference", "").strip()
    if not reference:
        return json_response(400, error="'reference' query parameter required")

    org_id = user.organization_id
    db = get_db()

    try:
        container = db.get_container("regulations")
        results = list(container.query_items(
            query="SELECT * FROM c WHERE CONTAINS(LOWER(c.reference),LOWER(@r)) OR CONTAINS(LOWER(c.section_reference),LOWER(@r)) OR CONTAINS(LOWER(c.title),LOWER(@r)) OFFSET 0 LIMIT 1",
            parameters=[{"name": "@r", "value": reference}],
            enable_cross_partition_query=True,
        ))
        if results:
            r = results[0]
            return json_response(200, data={
                "reference": reference, "title": r.get("title", reference),
                "text": r.get("text", r.get("full_text", "")),
                "summary": r.get("summary", ""), "effective_date": r.get("effective_date", ""),
                "source_url": r.get("source_url", ""), "source": "database"
            })
    except Exception as e:
        logger.warning(f"Database lookup failed: {e}")

    try:
        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"), api_version="2025-01-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        )
        resp = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4"),
            messages=[{"role": "user", "content": f'Explain this regulation: "{reference}". Include: full title, what it requires, who it applies to, key compliance requirements. Plain text only.'}],
            temperature=0.3, max_tokens=500,
        )
        ai_text = resp.choices[0].message.content
        try:
            db.get_container("regulations").upsert_item({
                "id": f"reg-{uuid.uuid4().hex[:8]}", "reference": reference,
                "section_reference": reference, "text": ai_text, "source": "ai_generated",
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "organization_id": org_id, "type": "regulation"
            })
        except Exception as e:
            logger.warning(f"Failed to cache regulation: {e}")
        return json_response(200, data={"reference": reference, "title": reference, "text": ai_text, "source": "ai_generated"})
    except Exception as e:
        return json_response(200, data={
            "reference": reference, "title": reference,
            "text": f'Could not retrieve regulation text for "{reference}".',
            "source": "unavailable", "error": str(e)
        })


@app.route(route="regulations/search", methods=["GET"])
def search_regulations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_search_regulations
    return handle_search_regulations(req, user)


@app.route(route="regulations/updates", methods=["GET"])
def get_regulatory_updates(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_get_regulatory_updates
    return handle_get_regulatory_updates(req, user)


@app.route(route="regulations/stats", methods=["GET"])
def get_regulation_stats(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_get_regulation_stats
    return handle_get_regulation_stats(req, user)


@app.route(route="regulations", methods=["GET"])
def browse_regulations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_browse_regulations
    return handle_browse_regulations(req, user)


@app.route(route="regulations/{regulationId}", methods=["GET"])
def get_regulation_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.regulatory_admin import handle_get_regulation_details
    return handle_get_regulation_details(req, user)


# =============================================================================
# SECTION 10 — ADMIN USER MANAGEMENT
# =============================================================================

@app.route(route="manage/users/workload", methods=["GET"])
def get_user_workload_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_get_workload_dashboard
    return handle_get_workload_dashboard(req, user)


@app.route(route="manage/users/invite", methods=["POST"])
def invite_user(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_create_user
    return handle_create_user(req, user)


@app.route(route="manage/users", methods=["GET"])
def list_organization_users(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_list_users
    return handle_list_users(req, user)


@app.route(route="manage/users/{userId}", methods=["GET"])
def get_user_details(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_get_user
    return handle_get_user(req, user)


@app.route(route="manage/users/{userId}", methods=["PUT"])
def update_user(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_update_user
    return handle_update_user(req, user)


@app.route(route="manage/users/{userId}", methods=["DELETE"])
def deactivate_user(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_delete_user
    return handle_delete_user(req, user)


@app.route(route="manage/users/{userId}/role", methods=["PUT"])
def update_user_role(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_update_user_role
    return handle_update_user_role(req, user)


@app.route(route="manage/users/{userId}/activity", methods=["GET"])
def get_user_activity(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_get_user_activity
    return handle_get_user_activity(req, user)


@app.route(route="manage/users/{userId}/decisions", methods=["GET"])
def get_user_decisions(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_get_user_decisions
    return handle_get_user_decisions(req, user)


@app.route(route="manage/overview", methods=["GET"])
def get_org_overview(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.user_management import handle_get_org_overview
    return handle_get_org_overview(req, user)


@app.route(route="manage/rules", methods=["GET"])
def get_admin_rules_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.custom_rules import custom_rules_engine
    from function_app_pkg.core.database import get_container

    org_id = user.organization_id
    rules = custom_rules_engine.get_rules_for_org(org_id, enabled_only=False)

    rule_stats: dict[str, int] = {}
    for doc in get_container("documents").query_items(
        query="SELECT c.violations FROM c WHERE c.organization_id=@org_id AND c.type='document'",
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id, max_item_count=1000,
    ):
        for v in doc.get("violations", []):
            if v.get("source") == "custom_rule" and v.get("rule_id"):
                rule_stats[v["rule_id"]] = rule_stats.get(v["rule_id"], 0) + 1

    return json_response(200, data={
        "rules": [{**r.to_dict(), "violations_count": rule_stats.get(r.id, 0)} for r in rules],
        "stats": {
            "total_rules": len(rules), "enabled_rules": sum(1 for r in rules if r.enabled),
            "disabled_rules": sum(1 for r in rules if not r.enabled),
            "total_violations": sum(rule_stats.values()),
            "most_common_rule": max(rule_stats, key=rule_stats.get) if rule_stats else None,
        },
    })


@app.route(route="manage/teams", methods=["GET"])
def get_admin_teams_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.database import get_teams_by_org, get_users_by_org

    org_id = user.organization_id
    teams = get_teams_by_org(org_id, include_archived=False)
    users = get_users_by_org(org_id)

    return json_response(200, data={
        "teams": [{
            "team_id": t["id"], "team_name": t["name"],
            "member_count": len(t.get("members", [])),
            "assignment_strategy": t.get("assignment_strategy", "least_loaded"),
            "documents_assigned": t.get("stats", {}).get("documents_assigned", 0),
            "documents_completed": t.get("stats", {}).get("documents_completed", 0),
            "is_active": not t.get("is_archived", False)
        } for t in teams],
        "total_teams": len(teams), "total_users": len(users),
        "available_users": [{"email": u.get("email"), "name": u.get("name"), "roles": u.get("roles", []), "department": u.get("department", "")} for u in users],
    })


# =============================================================================
# SECTION 11 — CERTIFICATES
# =============================================================================

@app.route(route="certificates", methods=["GET"])
def list_organization_certificates(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container

    org_id = user.organization_id
    docs = list(get_container("documents").query_items(
        query="SELECT c.id, c.filename, c.certificates, c.organization_id FROM c WHERE c.organization_id=@org_id AND c.type='document' AND ARRAY_LENGTH(c.certificates)>0",
        parameters=[{"name": "@org_id", "value": org_id}], partition_key=org_id,
    ))

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    all_certs, compliant, needs_review, this_month = [], 0, 0, 0

    for doc in docs:
        for cert in doc.get("certificates", []):
            all_certs.append({**cert, "document_id": doc["id"], "document_filename": doc.get("filename")})
            if cert.get("compliance_outcome") == "compliant": compliant += 1
            if cert.get("compliance_outcome") == "requires_review": needs_review += 1
            try:
                issued = datetime.fromisoformat(cert["issued_at"].replace("Z", "+00:00"))
                if issued.replace(tzinfo=None) >= month_start: this_month += 1
            except Exception: pass

    all_certs.sort(key=lambda x: x.get("issued_at", ""), reverse=True)
    return json_response(200, data={
        "certificates": all_certs, "total": len(all_certs),
        "stats": {"total_issued": len(all_certs), "compliant": compliant, "requires_review": needs_review, "this_month": this_month},
    })


@app.route(route="certificates/{certificateId}", methods=["GET"])
def get_certificate_by_id(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_container

    cert_id = req.route_params.get("certificateId")
    org_id = user.organization_id
    user_roles = user.roles
    container = get_container("documents")

    if AppRole.SUPER_ADMIN.value in user_roles:
        docs = list(container.query_items(
            query="SELECT * FROM c WHERE c.type='document' AND ARRAY_LENGTH(c.certificates)>0",
            parameters=[], enable_cross_partition_query=True
        ))
    else:
        docs = list(container.query_items(
            query="SELECT * FROM c WHERE c.organization_id=@org_id AND c.type='document' AND ARRAY_LENGTH(c.certificates)>0",
            parameters=[{"name": "@org_id", "value": org_id}], partition_key=org_id
        ))

    for doc in docs:
        for cert in doc.get("certificates", []):
            if cert.get("certificate_id") == cert_id:
                return json_response(200, data={**cert, "document_id": doc["id"], "document_filename": doc.get("filename")})

    return json_response(404, error="Certificate not found")


# =============================================================================
# SECTION 12 — NOTIFICATIONS
# =============================================================================

@app.route(route="notifications", methods=["GET"])
def get_user_notifications(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_get_notifications
    return handle_get_notifications(req, user)


@app.route(route="notifications/mark-all-read", methods=["POST"])
def mark_all_notifications_read(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_mark_all_notifications_read
    return handle_mark_all_notifications_read(req, user)


@app.route(route="notifications/{notificationId}/read", methods=["POST"])
def mark_notification_read(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.team_collaboration import handle_mark_notification_read
    return handle_mark_notification_read(req, user)


# =============================================================================
# SECTION 13 — ANALYTICS
# =============================================================================

@app.route(route="analytics", methods=["GET"])
def get_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.analytics import handle_get_analytics
    return handle_get_analytics(req, user)


@app.route(route="analytics/dashboard", methods=["GET"])
def get_analytics_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.analytics import handle_dashboard
    return handle_dashboard(req, user)


@app.route(route="analytics/compliance-score", methods=["GET"])
def get_compliance_score(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.analytics import handle_compliance_score
    return handle_compliance_score(req, user)


@app.route(route="analytics/violations", methods=["GET"])
def get_violations_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.analytics import handle_violations_analysis
    return handle_violations_analysis(req, user)


@app.route(route="analytics/user-activity", methods=["GET"])
def get_user_activity_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.analytics import handle_user_activity
    return handle_user_activity(req, user)


@app.route(route="analytics/user-performance", methods=["GET"])
def user_performance_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.advanced_analytics import handle_user_performance
    return handle_user_performance(req, user)


@app.route(route="analytics/violation-trends", methods=["GET"])
def violation_trends_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.advanced_analytics import handle_violation_trends
    return handle_violation_trends(req, user)


@app.route(route="analytics/cost-attribution", methods=["GET"])
def cost_attribution_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.advanced_analytics import handle_cost_attribution
    return handle_cost_attribution(req, user)


@app.route(route="analytics/document-lifecycle", methods=["GET"])
def document_lifecycle_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.advanced_analytics import handle_document_lifecycle
    return handle_document_lifecycle(req, user)


@app.route(route="analytics/sla-compliance", methods=["GET"])
def sla_compliance_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.advanced_analytics import handle_sla_compliance
    return handle_sla_compliance(req, user)


@app.route(route="analytics/costs", methods=["GET"])
def get_cost_analytics(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_db

    org_id = user.organization_id
    period_days = int(req.params.get("period", "30"))
    start_date = (datetime.utcnow() - timedelta(days=period_days)).isoformat() + "Z"

    events = list(get_db().get_container("documents").query_items(
        query="SELECT c.resource_type, c.cost_usd, c.usage, c.user_email, c.document_id, c.timestamp FROM c WHERE c.type='cost_event' AND c.organization_id=@org_id AND c.timestamp>=@start",
        parameters=[{"name": "@org_id", "value": org_id}, {"name": "@start", "value": start_date}],
        partition_key=org_id,
    ))

    total = sum(e.get("cost_usd", 0) for e in events)
    by_resource: dict[str, float] = defaultdict(float)
    by_user: dict[str, float] = defaultdict(float)
    by_doc: dict[str, float] = defaultdict(float)
    daily: dict[str, float] = defaultdict(float)

    for e in events:
        cost = e.get("cost_usd", 0)
        by_resource[e.get("resource_type", "unknown")] += cost
        by_user[e.get("user_email", "unknown")] += cost
        if e.get("document_id"): by_doc[e["document_id"]] += cost
        daily[e["timestamp"][:10]] += cost

    doc_count = len(by_doc)
    return json_response(200, data={
        "period_days": period_days, "total_cost_usd": round(total, 2),
        "document_count": doc_count,
        "avg_cost_per_document": round(total / doc_count if doc_count else 0, 2),
        "by_resource_type": {k: round(v, 2) for k, v in by_resource.items()},
        "by_user": {k: round(v, 2) for k, v in sorted(by_user.items(), key=lambda x: -x[1])[:10]},
        "top_expensive_documents": sorted([{"document_id": k, "cost": round(v, 2)} for k, v in by_doc.items()], key=lambda x: -x["cost"])[:10],
        "daily_breakdown": [{"date": k, "cost": round(v, 2)} for k, v in sorted(daily.items())],
    })


@app.route(route="analytics/export", methods=["GET"])
def export_analytics_csv(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.core.database import get_db

    org_id = user.organization_id
    export_type = req.params.get("type", "documents")
    period = int(req.params.get("period", "30"))
    start_date = (datetime.utcnow() - timedelta(days=period)).isoformat() + "Z"

    queries = {
        "documents": "SELECT c.id, c.filename, c.status, c.risk_score, c.violations_count, c.assigned_to, c.created_at, c.approved_at, c.rejected_at FROM c WHERE c.type='document' AND c.organization_id=@org_id AND c.created_at>=@start ORDER BY c.created_at DESC",
        "costs": "SELECT c.timestamp, c.resource_type, c.cost_usd, c.user_email, c.document_id FROM c WHERE c.type='cost_event' AND c.organization_id=@org_id AND c.timestamp>=@start ORDER BY c.timestamp DESC",
    }
    items = list(get_db().get_container("documents").query_items(
        query=queries.get(export_type, queries["documents"]),
        parameters=[{"name": "@org_id", "value": org_id}, {"name": "@start", "value": start_date}],
        partition_key=org_id,
    ))

    buf = io.StringIO()
    if items:
        writer = csv.DictWriter(buf, fieldnames=items[0].keys())
        writer.writeheader()
        writer.writerows(items)

    return func.HttpResponse(
        buf.getvalue(), status_code=200,
        headers={"Content-Type": "text/csv", "Content-Disposition": f'attachment; filename="{export_type}_{period}d.csv"'},
    )


# =============================================================================
# SECTION 14 — AUDIT LOGS
# =============================================================================

@app.route(route="audit/search", methods=["GET"])
def search_audit_logs(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.audit import handle_audit_search
    return handle_audit_search(req, user)


@app.route(route="audit/export", methods=["GET"])
def export_audit_logs_csv(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    from function_app_pkg.api.audit import handle_export_csv
    return handle_export_csv(req, user)


# =============================================================================
# SECTION 15 — CUSTOM RULES
# =============================================================================

@app.route(route="custom-rules", methods=["GET"])
def list_custom_rules(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.custom_rules import custom_rules_engine

    rules = custom_rules_engine.get_rules_for_org(user.organization_id)
    return json_response(200, data={"rules": [r.to_dict() for r in rules], "total": len(rules)})


@app.route(route="custom-rules", methods=["POST"])
def create_custom_rule(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.core.custom_rules import custom_rules_engine

    data = req.get_json()
    required = ["name", "description", "pattern", "severity", "remediation"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return json_response(400, error=f"Missing required fields: {missing}")

    rule = custom_rules_engine.create_rule(
        organization_id=user.organization_id,
        name=data["name"], description=data["description"],
        rule_type=data.get("rule_type", "keyword"), pattern=data["pattern"],
        severity=data["severity"], category=data.get("category", "custom"),
        remediation=data["remediation"], created_by=user.email,
    )
    return json_response(201, data={"rule": rule.to_dict(), "message": "Custom rule created"})


# =============================================================================
# SECTION 16 — SLA MANAGEMENT
# =============================================================================

@app.route(route="settings/sla", methods=["GET"])
def get_sla_config(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.sla_management import handle_get_sla_config
    return handle_get_sla_config(req, user)


@app.route(route="settings/sla", methods=["PUT"])
def update_sla_config(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.sla_management import handle_update_sla_config
    return handle_update_sla_config(req, user)


@app.route(route="sla/dashboard", methods=["GET"])
def get_sla_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.COMPLIANCE, AppRole.ADMIN, AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.sla_management import handle_sla_dashboard
    return handle_sla_dashboard(req, user)


# =============================================================================
# SECTION 17 — PLATFORM ADMIN (SuperAdmin only)
# =============================================================================

@app.route(route="platform/usage", methods=["GET"])
def get_platform_usage(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.platform_admin import handle_platform_usage
    return handle_platform_usage(req, user)


@app.route(route="platform/organizations", methods=["GET"])
def list_platform_organizations(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.platform_settings import handle_platform_organizations_list
    return handle_platform_organizations_list(req, user)


@app.route(route="platform/settings", methods=["GET"])
def get_platform_settings(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.platform_settings import handle_platform_settings_get
    return handle_platform_settings_get(req, user)


@app.route(route="platform/settings", methods=["PUT"])
def update_platform_settings(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.platform_settings import handle_platform_settings_update
    return handle_platform_settings_update(req, user)


@app.route(route="ml/training-data", methods=["GET"])
def export_ml_training_data(req: func.HttpRequest) -> func.HttpResponse:
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    if not _check_roles(user, [AppRole.SUPER_ADMIN]):
        return json_response(403, error="Insufficient permissions")
    
    from function_app_pkg.api.ml_export import handle_export_training_data
    return handle_export_training_data(req, user)


# =============================================================================
# SECTION 18 — PDF REPORT HELPER
# =============================================================================

def _generate_compliance_report(doc: dict, trail: list, user) -> io.BytesIO:
    """Build a professional compliance report PDF using ReportLab."""
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        )
    except ImportError:
        raise ImportError("ReportLab not installed. Run: pip install reportlab")

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm, leftMargin=20*mm, rightMargin=20*mm)
    styles = getSampleStyleSheet()

    for name, kwargs in [
        ("ReportTitle",  {"parent": styles["Title"],    "fontSize": 20, "spaceAfter": 6,  "textColor": HexColor("#1a1a2e")}),
        ("SectionHead",  {"parent": styles["Heading2"], "fontSize": 13, "spaceBefore": 16, "spaceAfter": 8, "textColor": HexColor("#1a1a2e")}),
        ("SubHead",      {"parent": styles["Heading3"], "fontSize": 11, "spaceBefore": 10, "spaceAfter": 4, "textColor": HexColor("#333355")}),
        ("BodyText2",    {"parent": styles["BodyText"], "fontSize": 9,  "leading": 13,    "textColor": HexColor("#333333")}),
        ("SmallGrey",    {"parent": styles["BodyText"], "fontSize": 8,  "textColor": HexColor("#888888")}),
        ("CenterSmall",  {"parent": styles["BodyText"], "fontSize": 8,  "alignment": TA_CENTER, "textColor": HexColor("#888888")}),
    ]:
        styles.add(ParagraphStyle(name=name, **kwargs))

    story = []

    story += [
        Paragraph("COMPLIANCE REVIEW REPORT", styles["ReportTitle"]),
        Paragraph(f"Generated {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')} by {user.name}", styles["SmallGrey"]),
        Spacer(1, 4*mm), HRFlowable(width="100%", thickness=1, color=HexColor("#1a1a2e")), Spacer(1, 6*mm),
    ]

    risk = doc.get("risk_score", 0)
    risk_label = "HIGH" if risk >= 70 else "MEDIUM" if risk >= 40 else "LOW"
    story.append(Paragraph("Document Information", styles["SectionHead"]))
    info_table = Table([
        ["Filename:", doc.get("filename", "N/A")], ["Document ID:", doc.get("id", "N/A")],
        ["Ticket:", doc.get("ticket_id", "N/A")], ["Jurisdiction:", doc.get("jurisdiction", "N/A")],
        ["Status:", doc.get("status", "N/A").replace("_", " ").title()],
        ["Compliance:", (doc.get("compliance_outcome") or "Pending").replace("_", " ").title()],
        ["Risk Score:", f"{risk}/100 ({risk_label})"],
        ["Violations:", str(len(doc.get("violations", [])) or doc.get("violations_count", 0))],
        ["Uploaded By:", doc.get("uploaded_by_name") or doc.get("uploaded_by", "N/A")],
        ["Upload Date:", doc.get("created_at", "N/A")[:19].replace("T", " ")],
        ["Assigned To:", doc.get("assigned_to_name") or doc.get("assigned_to", "Unassigned")],
        ["Team:", doc.get("team_name", "N/A")],
    ], colWidths=[35*mm, 130*mm])
    info_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 9), ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0,0), (0,-1), HexColor("#555555")), ("TEXTCOLOR", (1,0), (1,-1), HexColor("#222222")),
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(info_table)

    if doc.get("legal_advisory") or doc.get("legal_recommendation"):
        rec = doc.get("legal_recommendation", "N/A").replace("_", " ").title()
        story += [
            Spacer(1, 4*mm), Paragraph("Legal Advisory", styles["SectionHead"]),
            Paragraph(f"<b>Recommendation:</b> {rec}", styles["BodyText2"]),
            Paragraph(f"<b>Reviewed by:</b> {doc.get('legal_reviewed_by','N/A')} — {(doc.get('legal_reviewed_at',''))[:19].replace('T',' ')}", styles["SmallGrey"]),
            Spacer(1, 2*mm),
        ]
        if doc.get("legal_advisory"):
            story.append(Paragraph(doc["legal_advisory"], styles["BodyText2"]))

    violations = doc.get("violations", [])
    if violations:
        story += [Spacer(1, 4*mm), Paragraph(f"Violations ({len(violations)})", styles["SectionHead"]), Spacer(1, 2*mm)]
        for idx, v in enumerate(violations[:10]):
            story += [
                Paragraph(f"<b>[{v.get('severity','MEDIUM').upper()}]</b> {v.get('category','General')}", styles["BodyText2"]),
                Paragraph(v.get("description", ""), styles["BodyText2"]),
            ]
            if v.get("remediation"):
                story.append(Paragraph(f"<i>Remediation: {v['remediation']}</i>", styles["SmallGrey"]))
            story.append(Spacer(1, 3*mm))
        if len(violations) > 10:
            story.append(Paragraph(f"... and {len(violations) - 10} more violations", styles["SmallGrey"]))

    if trail:
        story += [Spacer(1, 4*mm), Paragraph(f"Decision Trail ({len(trail)})", styles["SectionHead"])]
        for entry in trail[:5]:
            maker = entry.get("decision_maker", {})
            story += [
                Paragraph(
                    f"<b>{(entry.get('decision') or entry.get('decision_type','')).replace('_',' ').title()}</b> "
                    f"by {maker.get('name') or maker.get('email','Unknown')} — "
                    f"{(entry.get('created_at') or entry.get('timestamp',''))[:19].replace('T',' ')}",
                    styles["BodyText2"]),
                Spacer(1, 2*mm),
            ]

    if doc.get("recommendations"):
        story += [Spacer(1, 4*mm), Paragraph("AI Recommendations", styles["SectionHead"])]
        for r in doc["recommendations"][:5]:
            story.append(Paragraph(f"&bull; {r}", styles["BodyText2"]))

    story += [
        Spacer(1, 10*mm), HRFlowable(width="100%", thickness=0.5, color=HexColor("#cccccc")),
        Paragraph(f"Auto-generated. Document ID: {doc.get('id')} | Org: {doc.get('organization_id','N/A')}", styles["CenterSmall"]),
    ]

    pdf.build(story)
    buffer.seek(0)
    return buffer

# =============================================================================
# SECTION 19 — AZURE MARKETPLACE BILLING
# =============================================================================

@app.route(route="marketplace/webhook", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def marketplace_webhook_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Marketplace webhook — no auth decorator, validated by JWT from Microsoft"""
    from function_app_pkg.api.marketplace_webhooks import handle_marketplace_webhook
    return handle_marketplace_webhook(req)


@app.route(route="marketplace/resolve", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def marketplace_resolve_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/marketplace/resolve — resolve landing page token, provision org"""
    return handle_marketplace_resolve(req)


@app.route(route="billing", methods=["GET"])
def billing_overview_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/billing — plan, usage, subscription status"""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    return handle_billing_overview(req, user)


@app.route(route="billing/change-plan", methods=["POST"])
def billing_change_plan_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/billing/change-plan — initiate plan change via Marketplace API"""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    return handle_change_plan(req, user)


@app.route(route="billing/usage", methods=["GET"])
def billing_usage_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/billing/usage — current usage summary"""
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    return handle_billing_usage(req, user)