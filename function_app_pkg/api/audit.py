"""Audit API - Search, Export, and Dashboard endpoints - FIXED VERSION"""
import azure.functions as func
import logging
import json
import csv
import io
from datetime import datetime, timedelta
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)


def _get_user_attr(user, attr, default=None):
    """Safely get attribute from user object"""
    if user is None:
        return default
    if isinstance(user, dict):
        return user.get(attr, default)
    return getattr(user, attr, default)


def _get_audit_container():
    """Get the audit_logs container - FIXED"""
    try:
        from function_app_pkg.core.database import get_container
        container = get_container("audit_logs")
        if container:
            logger.info("✅ Got audit_logs container")
            return container
        # Fallback to audits if audit_logs doesn't exist
        container = get_container("audits")
        if container:
            logger.info("⚠️ Using audits container (fallback)")
            return container
        logger.error("❌ No audit container found")
        return None
    except Exception as e:
        logger.error(f"Failed to get audit container: {e}")
        return None


def handle_audit_search(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    Search audit logs with filtering and pagination
    GET /audit/search?page=1&limit=50&action_type=document_uploaded&user_email=test@example.com
    """
    try:
        logger.info(f"🔍 Audit search called by user: {_get_user_attr(user, 'email', 'unknown')}")
        logger.info(f"🔍 Query params: {dict(req.params)}")
        
        # Parse query parameters
        page = int(req.params.get('page', '1'))
        limit = int(req.params.get('limit', '50'))
        action_type = req.params.get('action_type', '')
        user_email_filter = req.params.get('user_email', '')
        document_id = req.params.get('document_id', '')
        date_from = req.params.get('date_from', '')
        date_to = req.params.get('date_to', '')
        
        logger.info(f"🔍 Parsed params: page={page}, limit={limit}, action_type={action_type}")
        
        # Calculate offset
        offset = (page - 1) * limit
        
        # Get organization_id for tenant isolation
        organization_id = _get_user_attr(user, 'organization_id')
        logger.info(f"🔍 Organization ID: {organization_id}")
        
        # Get container
        container = _get_audit_container()
        
        if not container:
            logger.error("❌ No audit container available")
            # Return empty results if container doesn't exist
            return json_response(200, data={
                'audit_logs': [],
                'metadata': {
                    'total_items': 0,
                    'current_page': page,
                    'total_pages': 0,
                    'has_next': False,
                    'has_previous': False
                }
            })
        
        logger.info(f"✅ Got audit container: {container.id}")
        
        # Build query
        conditions = []
        parameters = []
        
        # Always filter by organization for tenant isolation
        if organization_id:
            conditions.append("c.organization_id = @org_id")
            parameters.append({"name": "@org_id", "value": organization_id})
        else:
            # If no organization_id, return empty for safety
            logger.warning("⚠️ No organization_id in user context")
            return json_response(200, data={
                'audit_logs': [],
                'metadata': {
                    'total_items': 0,
                    'current_page': page,
                    'total_pages': 0,
                    'has_next': False,
                    'has_previous': False
                }
            })
        
        if action_type and action_type != 'all':
            conditions.append("c.action_type = @action_type")
            parameters.append({"name": "@action_type", "value": action_type})
        
        if user_email_filter:
            conditions.append("CONTAINS(LOWER(c.user_email), LOWER(@user_email))")
            parameters.append({"name": "@user_email", "value": user_email_filter})
        
        if document_id:
            conditions.append("c.document_id = @doc_id")
            parameters.append({"name": "@doc_id", "value": document_id})
        
        if date_from:
            conditions.append("c.timestamp >= @date_from")
            parameters.append({"name": "@date_from", "value": date_from})
        
        if date_to:
            conditions.append("c.timestamp <= @date_to")
            parameters.append({"name": "@date_to", "value": date_to})
        
        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "c.organization_id = @org_id"
        
        # If no conditions were added, we still need the org filter
        if not conditions:
            where_clause = "c.organization_id = @org_id"
        
        logger.info(f"🔍 Query where clause: {where_clause}")
        logger.info(f"🔍 Query parameters: {parameters}")
        
        # Count total items
        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_clause}"
        try:
            logger.info(f"🔍 Running count query: {count_query}")
            count_result = list(container.query_items(
                query=count_query,
                parameters=parameters,
                enable_cross_partition_query=True  # Important for partitioned containers
            ))
            total_items = count_result[0] if count_result else 0
            logger.info(f"🔍 Found {total_items} total items")
        except Exception as count_error:
            logger.error(f"❌ Count query failed: {count_error}")
            total_items = 0
        
        # Query audit logs with pagination
        query = f"""
            SELECT * FROM c 
            WHERE {where_clause}
            ORDER BY c.timestamp DESC
            OFFSET @offset LIMIT @limit
        """
        parameters.append({"name": "@offset", "value": offset})
        parameters.append({"name": "@limit", "value": limit})
        
        logger.info(f"🔍 Running main query: {query}")
        
        try:
            audit_logs = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True  # Important for partitioned containers
            ))
            logger.info(f"✅ Retrieved {len(audit_logs)} audit logs")
        except Exception as query_error:
            logger.error(f"❌ Audit query failed: {query_error}", exc_info=True)
            audit_logs = []
        
        # Calculate pagination metadata
        total_pages = (total_items + limit - 1) // limit if total_items > 0 else 0
        
        return json_response(200, data={
            'audit_logs': audit_logs,
            'metadata': {
                'total_items': total_items,
                'current_page': page,
                'items_per_page': limit,
                'total_pages': total_pages,
                'has_next': page < total_pages,
                'has_previous': page > 1,
                'filters_applied': {
                    'action_type': action_type or None,
                    'user_email': user_email_filter or None,
                    'document_id': document_id or None,
                    'date_range': {
                        'from': date_from or None,
                        'to': date_to or None
                    }
                }
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Audit search error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to search audit logs: {str(e)}")


def handle_export(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    Export audit logs as CSV
    GET /audit/export?format=csv
    """
    try:
        export_format = req.params.get('format', 'csv').lower()
        
        if export_format != 'csv':
            return json_response(400, error="Only CSV export is currently supported")
        
        organization_id = _get_user_attr(user, 'organization_id')
        
        if not organization_id:
            return json_response(400, error="Organization ID required for export")
        
        # Get container
        container = _get_audit_container()
        
        if not container:
            return json_response(404, error="Audit logs not available")
        
        # Query all audit logs for organization (last 90 days)
        date_cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        
        query = """
            SELECT * FROM c 
            WHERE c.organization_id = @org_id 
            AND c.timestamp >= @date_cutoff
            ORDER BY c.timestamp DESC
        """
        
        parameters = [
            {"name": "@org_id", "value": organization_id},
            {"name": "@date_cutoff", "value": date_cutoff}
        ]
        
        logger.info(f"📤 Exporting audit logs for org: {organization_id}")
        
        try:
            audit_logs = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            logger.info(f"✅ Retrieved {len(audit_logs)} logs for export")
        except Exception as e:
            logger.error(f"❌ Export query failed: {e}")
            audit_logs = []
        
        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header row
        writer.writerow([
            'Timestamp',
            'Action Type',
            'User Email',
            'User Role',
            'Document ID',
            'Filename',
            'Details',
            'IP Address'
        ])
        
        # Data rows
        for log in audit_logs:
            details = log.get('details', {})
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except:
                    details = {}
            
            writer.writerow([
                log.get('timestamp', ''),
                log.get('action_type', ''),
                log.get('user_email', ''),
                ', '.join(log.get('user_roles', [])),
                log.get('document_id', ''),
                details.get('filename', ''),
                json.dumps(details) if details else '',
                log.get('ip_address', '')
            ])
        
        csv_content = output.getvalue()
        output.close()
        
        # Return CSV response
        return func.HttpResponse(
            csv_content,
            status_code=200,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename="audit_logs_{datetime.utcnow().strftime("%Y%m%d")}.csv"',
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Audit export error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to export audit logs: {str(e)}")


def handle_dashboard(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    Get dashboard metrics from audit logs
    GET /audit/dashboard?date_range=7d
    """
    try:
        date_range = req.params.get('date_range', '7d')
        organization_id = _get_user_attr(user, 'organization_id')
        
        if not organization_id:
            return json_response(400, error="Organization ID required")
        
        # Parse date range
        if date_range == '24h':
            days = 1
        elif date_range == '7d':
            days = 7
        elif date_range == '30d':
            days = 30
        elif date_range == '90d':
            days = 90
        else:
            days = 7
        
        date_cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        # Get container
        container = _get_audit_container()
        
        if not container:
            # Return default metrics if container doesn't exist
            return json_response(200, data={
                'metrics': {
                    'total_actions': 0,
                    'uploads': 0,
                    'scans': 0,
                    'approvals': 0,
                    'rejections': 0,
                    'escalations': 0
                },
                'date_range': date_range,
                'period_start': date_cutoff,
                'period_end': datetime.utcnow().isoformat()
            })
        
        # Query for metrics
        try:
            # Get all logs in date range
            query = """
                SELECT c.action, c.action_type FROM c 
                WHERE c.organization_id = @org_id 
                AND c.timestamp >= @date_cutoff
            """
            
            parameters = [
                {"name": "@org_id", "value": organization_id},
                {"name": "@date_cutoff", "value": date_cutoff}
            ]
            
            logs = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            
            # Count by action type
            metrics = {
                'total_actions': len(logs),
                'uploads': 0,
                'scans': 0,
                'approvals': 0,
                'rejections': 0,
                'escalations': 0,
                'deletions': 0
            }
            
            for log in logs:
                action = log.get('action', '').lower()
                action_type = log.get('action_type', '').lower()
                
                if 'upload' in action or 'upload' in action_type:
                    metrics['uploads'] += 1
                elif 'scan' in action or 'scan' in action_type:
                    metrics['scans'] += 1
                elif 'approv' in action:
                    metrics['approvals'] += 1
                elif 'reject' in action:
                    metrics['rejections'] += 1
                elif 'escalat' in action:
                    metrics['escalations'] += 1
                elif 'delet' in action:
                    metrics['deletions'] += 1
            
            logger.info(f"📊 Dashboard metrics: {metrics}")
            
        except Exception as query_error:
            logger.error(f"❌ Dashboard query failed: {query_error}")
            metrics = {
                'total_actions': 0,
                'uploads': 0,
                'scans': 0,
                'approvals': 0,
                'rejections': 0,
                'escalations': 0,
                'deletions': 0
            }
        
        return json_response(200, data={
            'metrics': metrics,
            'date_range': date_range,
            'period_start': date_cutoff,
            'period_end': datetime.utcnow().isoformat(),
            'organization_id': organization_id
        })
        
    except Exception as e:
        logger.error(f"❌ Dashboard metrics error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get dashboard metrics: {str(e)}")

def handle_export_csv(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    GET /audit/export?format=csv&start_date=2024-01-01&end_date=2024-12-31
    Export audit logs as CSV
    """
    try:
        org_id = _get_user_attr(user, 'organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        # Parse params
        format_type = req.params.get('format', 'csv').lower()
        start_date = req.params.get('start_date', (datetime.utcnow() - timedelta(days=90)).isoformat() + 'Z')
        end_date = req.params.get('end_date', datetime.utcnow().isoformat() + 'Z')
        
        if format_type != 'csv':
            return json_response(400, error="Only CSV format supported currently")
        
        container = _get_audit_container()
        if not container:
            return json_response(404, error="Audit logs not available")
        
        # Query ALL org audit logs in date range
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id 
        AND c.timestamp >= @start_date
        AND c.timestamp <= @end_date
        ORDER BY c.timestamp DESC
        """
        
        logs = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@start_date", "value": start_date},
                {"name": "@end_date", "value": end_date}
            ],
            enable_cross_partition_query=True
        ))
        
        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            'Timestamp', 'Action', 'User Email', 'User Roles',
            'Resource Type', 'Resource ID', 'Resource Name',
            'Details', 'IP Address', 'Success'
        ])
        
        # Rows
        for log in logs:
            writer.writerow([
                log.get('timestamp', ''),
                log.get('action', ''),
                log.get('user_email', ''),
                ', '.join(log.get('user_roles', [])),
                log.get('resource_type', ''),
                log.get('resource_id', ''),
                log.get('resource_name', ''),
                json.dumps(log.get('details', {})),
                log.get('ip_address', ''),
                log.get('success', True)
            ])
        
        csv_content = output.getvalue()
        output.close()
        
        filename = f"audit_logs_{org_id}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        
        return func.HttpResponse(
            csv_content,
            status_code=200,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Audit export failed: {e}", exc_info=True)
        return json_response(500, error=f"Export failed: {str(e)}")


def handle_get_document_history(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """
    Get audit history for a specific document
    GET /audit/documents/{documentId}/history
    """
    try:
        document_id = req.route_params.get('documentId')
        
        if not document_id:
            return json_response(400, error="Document ID required")
        
        organization_id = _get_user_attr(user, 'organization_id')
        
        if not organization_id:
            return json_response(400, error="Organization ID required")
        
        # Get container
        container = _get_audit_container()
        
        if not container:
            return json_response(200, data={
                'document_id': document_id,
                'history': [],
                'total_events': 0
            })
        
        # Query audit logs for document
        query = """
            SELECT * FROM c 
            WHERE c.document_id = @doc_id
            AND c.organization_id = @org_id
            ORDER BY c.timestamp DESC
        """
        
        parameters = [
            {"name": "@doc_id", "value": document_id},
            {"name": "@org_id", "value": organization_id}
        ]
        
        logger.info(f"📜 Getting document history for: {document_id}")
        
        try:
            history = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            logger.info(f"✅ Found {len(history)} history events")
        except Exception as e:
            logger.error(f"❌ Document history query failed: {e}")
            history = []
        
        return json_response(200, data={
            'document_id': document_id,
            'history': history,
            'total_events': len(history),
            'organization_id': organization_id
        })
        
    except Exception as e:
        logger.error(f"❌ Document history error: {e}", exc_info=True)
        return json_response(500, error=f"Failed to get document history: {str(e)}")


# Alias functions for compatibility with function_app.py
handle_search = handle_audit_search
handle_export_audit_trail = handle_export

# Export all functions
__all__ = [
    'handle_audit_search',
    'handle_search',
    'handle_export',
    'handle_export_audit_trail',
    'handle_dashboard',
    'handle_get_document_history'
]