"""
REGULATORY ADMIN API - COMPLETE VERSION
=======================================
Browse, search, and track regulations applied to documents

Endpoints:
- GET /regulations - Browse all regulations
- GET /regulations/search - Search regulations
- GET /regulations/{id} - Get regulation details
- GET /regulations/updates - Recent regulatory changes
- GET /regulations/stats - Database statistics
- GET /documents/{id}/applied-regulations - Laws applied to document
"""

import azure.functions as func
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..shared.http_utils import json_response

logger = logging.getLogger(__name__)


# =============================================================================
# BROWSE REGULATIONS
# =============================================================================

def handle_browse_regulations(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /regulations?jurisdiction=UK&category=marketing&limit=50&offset=0
    
    Browse all regulations with filtering
    """
    try:
        # Parse parameters
        jurisdiction = req.params.get('jurisdiction')
        category = req.params.get('category')
        risk_level = req.params.get('risk_level')
        regulator = req.params.get('regulator')
        limit = int(req.params.get('limit', '50'))
        offset = int(req.params.get('offset', '0'))
        search = req.params.get('search', '').strip()
        
        if not jurisdiction:
            return json_response(400, error="jurisdiction parameter required")
        
        # Get regulations from service
        try:
            from ..core.regulatory_data import regulatory_service
            
            all_provisions = regulatory_service.get_provisions(
                jurisdiction=jurisdiction,
                category=category,
                limit=1000
            )
        except ImportError:
            # Fallback to database query
            all_provisions = _get_regulations_from_db(jurisdiction, category)
        
        # Apply additional filters
        filtered = all_provisions
        
        if risk_level:
            filtered = [p for p in filtered if _get_attr(p, 'risk_level') == risk_level]
        
        if regulator:
            filtered = [p for p in filtered if regulator.lower() in _get_attr(p, 'regulator', '').lower()]
        
        if search:
            search_lower = search.lower()
            filtered = [
                p for p in filtered
                if search_lower in _get_attr(p, 'text', '').lower()
                or search_lower in _get_attr(p, 'title', '').lower()
                or search_lower in _get_attr(p, 'section_reference', '').lower()
            ]
        
        # Paginate
        total = len(filtered)
        provisions = filtered[offset:offset + limit]
        
        # Convert to API format
        regulations = []
        for prov in provisions:
            regulations.append(_provision_to_dict(prov))
        
        return json_response(200, data={
            'regulations': regulations,
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
                'has_more': offset + limit < total,
                'pages': (total + limit - 1) // limit
            },
            'filters': {
                'jurisdiction': jurisdiction,
                'category': category,
                'risk_level': risk_level,
                'regulator': regulator,
                'search': search or None
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Browse regulations error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# SEARCH REGULATIONS
# =============================================================================

def handle_search_regulations(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /regulations/search?q=risk+warnings&jurisdiction=UK&limit=20
    
    Full-text search across all regulations
    """
    try:
        import re
        
        search_query = req.params.get('q', '').strip()
        jurisdiction = req.params.get('jurisdiction')
        category = req.params.get('category')
        limit = int(req.params.get('limit', '20'))
        
        if not search_query:
            return json_response(400, error="Search query (q) required")
        
        # Get all provisions
        try:
            from ..core.regulatory_data import regulatory_service
            
            jurisdictions = [jurisdiction] if jurisdiction else ['UK', 'EU', 'US', 'ZA', 'GLOBAL']
            all_provisions = []
            
            for jur in jurisdictions:
                provisions = regulatory_service.get_provisions(jur, category=category, limit=500)
                all_provisions.extend(provisions)
        except ImportError:
            all_provisions = _get_regulations_from_db(jurisdiction, category)
        
        # Score and rank results
        results = []
        search_lower = search_query.lower()
        search_words = re.findall(r'\w+', search_lower)
        
        for prov in all_provisions:
            score = 0
            
            title = _get_attr(prov, 'title', '').lower()
            text = _get_attr(prov, 'text', '').lower()
            section_ref = _get_attr(prov, 'section_reference', '').lower()
            
            # Exact phrase match (highest score)
            if search_lower in title:
                score += 20
            if search_lower in section_ref:
                score += 15
            if search_lower in text:
                score += 10
            
            # Word matches
            for word in search_words:
                if len(word) < 3:
                    continue
                if word in title:
                    score += 5
                if word in text:
                    score += 2
            
            if score > 0:
                # Find context snippet
                snippet = _extract_snippet(text, search_lower, 150)
                
                results.append({
                    'regulation': _provision_to_dict(prov),
                    'score': score,
                    'snippet': snippet,
                    'match_type': 'title' if search_lower in title else 'text'
                })
        
        # Sort by score
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return json_response(200, data={
            'query': search_query,
            'jurisdiction': jurisdiction,
            'category': category,
            'results': results[:limit],
            'total_results': len(results),
            'showing': min(limit, len(results))
        })
        
    except Exception as e:
        logger.error(f"❌ Search regulations error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# GET REGULATION DETAILS
# =============================================================================

def handle_get_regulation_details(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /regulations/{regulationId}
    
    Get full regulation details including related provisions
    """
    try:
        regulation_id = req.route_params.get('regulationId')
        
        if not regulation_id:
            return json_response(400, error="Regulation ID required")
        
        # Try Cosmos DB first
        from ..core.database import get_container
        
        try:
            container = get_container('regulatory_data')
            
            query = "SELECT * FROM c WHERE c.id = @id"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@id", "value": regulation_id}],
                enable_cross_partition_query=True
            ))
            
            if items:
                regulation = items[0]
                
                # Get related provisions
                source_id = regulation.get('source_id')
                related = []
                
                if source_id:
                    related_query = """
                    SELECT * FROM c 
                    WHERE c.source_id = @source_id 
                    AND c.id != @id
                    ORDER BY c.section_reference
                    OFFSET 0 LIMIT 10
                    """
                    related = list(container.query_items(
                        query=related_query,
                        parameters=[
                            {"name": "@source_id", "value": source_id},
                            {"name": "@id", "value": regulation_id}
                        ],
                        enable_cross_partition_query=True
                    ))
                
                return json_response(200, data={
                    'regulation': regulation,
                    'related_provisions': related,
                    'total_related': len(related),
                    'source': 'database'
                })
        except Exception as db_err:
            logger.warning(f"Database lookup failed: {db_err}")
        
        # Fallback to regulatory service
        try:
            from ..core.regulatory_data import regulatory_service
            
            for jur in ['UK', 'EU', 'US', 'ZA', 'GLOBAL']:
                provisions = regulatory_service.get_provisions(jur, limit=1000)
                for prov in provisions:
                    if _get_attr(prov, 'id') == regulation_id:
                        return json_response(200, data={
                            'regulation': _provision_to_dict(prov, full_text=True),
                            'related_provisions': [],
                            'source': 'regulatory_service'
                        })
        except ImportError:
            pass
        
        return json_response(404, error="Regulation not found")
        
    except Exception as e:
        logger.error(f"❌ Get regulation error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# DOCUMENT APPLIED REGULATIONS
# =============================================================================

def handle_get_document_applied_regulations(req: func.HttpRequest, user) -> func.HttpResponse:
    """
    GET /documents/{documentId}/applied-regulations
    
    Get which regulations were checked/violated for a document
    Critical for audit trail - shows what laws were considered
    """
    try:
        from ..core.database import get_document, get_document_with_access_check
        
        doc_id = req.route_params.get('documentId')
        
        if not doc_id:
            return json_response(400, error="Document ID required")
        
        if not user:
            return json_response(401, error="Authentication required")
        
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        
        # Get document
        doc = get_document_with_access_check(doc_id, org_id)
        if not doc:
            return json_response(404, error="Document not found")
        
        jurisdiction = doc.get('jurisdiction')
        if not jurisdiction:
            return json_response(400, error="Document has no jurisdiction set")
        
        violations = doc.get('violations', [])
        
        # Build regulations map from violations
        applied_regulations = {}
        
        for violation in violations:
            # Extract regulation info
            regulation_ref = violation.get('regulation', '')
            citation = violation.get('regulation_citation', {})
            category = violation.get('category', 'unknown')
            
            # Create unique key
            reg_key = citation.get('regulation_id') or regulation_ref or category
            
            if reg_key not in applied_regulations:
                applied_regulations[reg_key] = {
                    'regulation_id': citation.get('regulation_id', ''),
                    'regulation_reference': regulation_ref,
                    'category': category,
                    'jurisdiction': jurisdiction,
                    'violations_found': 0,
                    'severity_distribution': {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
                    'sample_violations': [],
                    'regulatory_text_preview': citation.get('text', '')[:300] if citation.get('text') else ''
                }
            
            applied_regulations[reg_key]['violations_found'] += 1
            
            severity = violation.get('severity', 'MEDIUM').upper()
            if severity in applied_regulations[reg_key]['severity_distribution']:
                applied_regulations[reg_key]['severity_distribution'][severity] += 1
            
            if len(applied_regulations[reg_key]['sample_violations']) < 3:
                applied_regulations[reg_key]['sample_violations'].append({
                    'violation_id': violation.get('violation_id', violation.get('id', '')),
                    'matched_text': violation.get('matched_text', '')[:150],
                    'severity': severity,
                    'ai_reasoning': violation.get('ai_reasoning', '')[:200],
                    'remediation': violation.get('remediation', '')[:200],
                })
        
        # Convert to list and sort by violations count
        regulations_list = list(applied_regulations.values())
        regulations_list.sort(key=lambda x: x['violations_found'], reverse=True)
        
        # Get scan metadata
        scan_stats = doc.get('scan_stats', {})
        
        # Try to get total regulations available for jurisdiction
        total_jurisdiction_regs = 0
        try:
            from ..core.regulatory_data import regulatory_service
            total_jurisdiction_regs = len(regulatory_service.get_provisions(jurisdiction, limit=1000))
        except:
            pass
        
        return json_response(200, data={
            'document': {
                'id': doc_id,
                'filename': doc.get('filename'),
                'jurisdiction': jurisdiction,
                'risk_score': doc.get('risk_score', 0),
                'compliance_outcome': doc.get('compliance_outcome'),
                'total_violations': len(violations),
                'scanned_at': doc.get('scanned_at'),
            },
            'applied_regulations': regulations_list,
            'summary': {
                'total_regulations_triggered': len(regulations_list),
                'total_violations': len(violations),
                'jurisdiction_total_regulations': total_jurisdiction_regs,
                'coverage_percentage': round(len(regulations_list) / total_jurisdiction_regs * 100, 1) if total_jurisdiction_regs > 0 else 0,
            },
            'scan_metadata': {
                'scan_mode': scan_stats.get('method', 'unknown'),
                'regulations_retrieved': scan_stats.get('regulations_retrieved', 0),
                'scan_duration_seconds': scan_stats.get('scan_duration_seconds', doc.get('scan_duration_seconds', 0)),
                'chunks_analyzed': scan_stats.get('chunks_analyzed', 0),
            },
            'audit_note': 'This shows all regulations that were checked during the compliance scan and triggered violations.'
        })
        
    except Exception as e:
        logger.error(f"❌ Applied regulations error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# REGULATORY UPDATES
# =============================================================================

def handle_get_regulatory_updates(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /regulations/updates?jurisdiction=UK&days=30
    
    Get recent regulatory updates and changes
    """
    try:
        jurisdiction = req.params.get('jurisdiction')
        days = int(req.params.get('days', '30'))
        
        # Try regulation monitor
        try:
            from ..core.regulation_monitor import regulation_monitor
            
            updates = regulation_monitor.get_recent_updates(jurisdiction, days)
            
            # Group by impact
            high_impact = [u for u in updates if getattr(u, 'impact_level', '') == 'high']
            medium_impact = [u for u in updates if getattr(u, 'impact_level', '') == 'medium']
            low_impact = [u for u in updates if getattr(u, 'impact_level', '') == 'low']
            
            return json_response(200, data={
                'updates': [_update_to_dict(u) for u in updates],
                'summary': {
                    'total': len(updates),
                    'high_impact': len(high_impact),
                    'medium_impact': len(medium_impact),
                    'low_impact': len(low_impact),
                    'jurisdiction': jurisdiction,
                    'days': days
                },
                'recommendations': {
                    'rescan_affected_documents': len(high_impact) > 0,
                    'update_custom_rules': len(medium_impact) > 0,
                    'review_training_materials': len(updates) > 5
                }
            })
            
        except ImportError:
            # Return empty if monitor not available
            return json_response(200, data={
                'updates': [],
                'summary': {
                    'total': 0,
                    'jurisdiction': jurisdiction,
                    'days': days
                },
                'message': 'Regulation monitoring not configured'
            })
        
    except Exception as e:
        logger.error(f"❌ Regulatory updates error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# REGULATION STATISTICS
# =============================================================================

def handle_get_regulation_stats(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /regulations/stats
    
    Get regulation database statistics
    """
    try:
        stats = {
            'jurisdictions': {},
            'categories': {},
            'risk_levels': {},
            'total_regulations': 0,
            'last_updated': None
        }
        
        try:
            from ..core.regulatory_data import regulatory_service
            stats = regulatory_service.get_stats()
        except ImportError:
            # Build stats from database
            from ..core.database import get_container
            
            container = get_container('regulatory_data')
            
            # Count by jurisdiction
            query = """
            SELECT c.jurisdiction, COUNT(1) as count 
            FROM c 
            GROUP BY c.jurisdiction
            """
            
            try:
                results = list(container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))
                
                for r in results:
                    stats['jurisdictions'][r['jurisdiction']] = r['count']
                    stats['total_regulations'] += r['count']
            except:
                pass
        
        return json_response(200, data={
            'database_stats': stats,
            'last_updated': datetime.utcnow().isoformat() + 'Z',
            'source': 'Cosmos DB'
        })
        
    except Exception as e:
        logger.error(f"❌ Regulation stats error: {e}", exc_info=True)
        return json_response(500, error=str(e))


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_attr(obj, attr: str, default=''):
    """Safely get attribute from object or dict"""
    if hasattr(obj, attr):
        return getattr(obj, attr, default)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _provision_to_dict(prov, full_text: bool = False) -> dict:
    """Convert provision to dict format"""
    if isinstance(prov, dict):
        result = {
            'id': prov.get('id', ''),
            'jurisdiction': prov.get('jurisdiction', ''),
            'regulator': prov.get('regulator', ''),
            'section_reference': prov.get('section_reference', ''),
            'title': prov.get('title', ''),
            'category': prov.get('category', ''),
            'risk_level': prov.get('risk_level', ''),
            'effective_date': prov.get('effective_date', ''),
        }
        
        text = prov.get('text', '')
        if full_text:
            result['full_text'] = text
        else:
            result['text_preview'] = text[:300] + '...' if len(text) > 300 else text
        
        if prov.get('metadata'):
            result['penalty_info'] = prov['metadata'].get('penalty_info', '')
        
        return result
    
    # Handle dataclass/object
    return {
        'id': _get_attr(prov, 'id'),
        'jurisdiction': _get_attr(prov, 'jurisdiction'),
        'regulator': _get_attr(prov, 'regulator'),
        'section_reference': _get_attr(prov, 'section_reference'),
        'title': _get_attr(prov, 'title'),
        'category': _get_attr(prov, 'category'),
        'risk_level': _get_attr(prov, 'risk_level'),
        'effective_date': _get_attr(prov, 'effective_date'),
        'text_preview': _get_attr(prov, 'text', '')[:300] + '...' if len(_get_attr(prov, 'text', '')) > 300 else _get_attr(prov, 'text', ''),
    }


def _update_to_dict(update) -> dict:
    """Convert regulatory update to dict"""
    if hasattr(update, 'to_dict'):
        return update.to_dict()
    
    if isinstance(update, dict):
        return update
    
    return {
        'id': _get_attr(update, 'id'),
        'title': _get_attr(update, 'title'),
        'description': _get_attr(update, 'description'),
        'jurisdiction': _get_attr(update, 'jurisdiction'),
        'impact_level': _get_attr(update, 'impact_level'),
        'effective_date': _get_attr(update, 'effective_date'),
        'published_date': _get_attr(update, 'published_date'),
    }


def _extract_snippet(text: str, search_term: str, length: int = 150) -> str:
    """Extract snippet around search term"""
    text_lower = text.lower()
    pos = text_lower.find(search_term.lower())
    
    if pos == -1:
        return text[:length] + '...' if len(text) > length else text
    
    start = max(0, pos - length // 2)
    end = min(len(text), pos + len(search_term) + length // 2)
    
    snippet = text[start:end]
    
    if start > 0:
        snippet = '...' + snippet
    if end < len(text):
        snippet = snippet + '...'
    
    return snippet


def _get_regulations_from_db(jurisdiction: str = None, category: str = None) -> List[dict]:
    """Get regulations directly from database"""
    try:
        from ..core.database import get_container
        
        container = get_container('regulatory_data')
        
        conditions = []
        params = []
        
        if jurisdiction:
            conditions.append("c.jurisdiction = @jurisdiction")
            params.append({"name": "@jurisdiction", "value": jurisdiction})
        
        if category:
            conditions.append("c.category = @category")
            params.append({"name": "@category", "value": category})
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM c WHERE {where_clause} ORDER BY c.section_reference"
        
        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        return items
        
    except Exception as e:
        logger.warning(f"Database query failed: {e}")
        return []


def _get_org_id(user) -> Optional[str]:
    """Extract organization ID from user"""
    if user is None:
        return None
    if hasattr(user, 'organization_id'):
        return user.organization_id
    if isinstance(user, dict):
        return user.get('organization_id')
    return None