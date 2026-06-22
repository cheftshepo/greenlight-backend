"""
Cosmos DB Database Layer - ENHANCED FOR TEAM COLLABORATION + LEGAL
==================================================================
Singleton pattern with thread-safe initialization.
Supports multi-tenant isolation, teams, notifications, activity feeds.

File: function_app_pkg/core/database.py
"""

import os
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import uuid
from enum import Enum

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.cosmos.container import ContainerProxy

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

# =============================================================================
# ENUMS & CONSTANTS
# =============================================================================

class UserRole(Enum):
    """Application roles - must match auth.py AppRole"""
    # Platform level
    SUPER_ADMIN = "Platform.SuperAdmin"
    
    # Organization level
    ORG_ADMIN = "Organization.Admin"
    
    # Legal & Advisory
    LEGAL_ADVISOR = "Legal.Advisor"
    DLA_PIPER = "DLAPiper.Advisory"
    
    # Compliance team
    COMPLIANCE_OFFICER = "Compliance.Officer"
    COMPLIANCE_REVIEWER = "Compliance.Reviewer"
    
    # Marketing/Standard users
    MARKETING_USER = "Marketing.User"
    MARKETING_VIEWER = "Marketing.Viewer"


# Permission enum for granular access control
class Permission(Enum):
    """Granular permissions"""
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_VIEW_OWN = "document:view:own"
    DOCUMENT_VIEW_ALL = "document:view:all"
    DOCUMENT_DELETE_OWN = "document:delete:own"
    DOCUMENT_DELETE_ALL = "document:delete:all"
    SCAN_INITIATE = "scan:initiate"
    SCAN_VIEW_RESULTS = "scan:view:results"
    APPROVAL_SUBMIT = "approval:submit"
    APPROVAL_APPROVE = "approval:approve"
    APPROVAL_REJECT = "approval:reject"
    APPROVAL_ESCALATE = "approval:escalate"
    APPROVAL_ESCALATE_TO_LEGAL = "approval:escalate:legal"
    ASSIGNMENT_VIEW_OWN = "assignment:view:own"
    ASSIGNMENT_VIEW_TEAM = "assignment:view:team"
    ASSIGNMENT_ASSIGN = "assignment:assign"
    ASSIGNMENT_REASSIGN = "assignment:reassign"
    TEAM_VIEW = "team:view"
    TEAM_CREATE = "team:create"
    TEAM_MANAGE = "team:manage"
    USER_VIEW_ORG = "user:view:org"
    USER_INVITE = "user:invite"
    USER_MANAGE = "user:manage"
    USER_CHANGE_ROLES = "user:change:roles"
    ANALYTICS_VIEW_OWN = "analytics:view:own"
    ANALYTICS_VIEW_ORG = "analytics:view:org"
    ANALYTICS_VIEW_PLATFORM = "analytics:view:platform"
    SETTINGS_VIEW = "settings:view"
    SETTINGS_MANAGE = "settings:manage"
    RULES_VIEW = "rules:view"
    RULES_CREATE = "rules:create"
    RULES_MANAGE = "rules:manage"
    AUDIT_VIEW_OWN = "audit:view:own"
    AUDIT_VIEW_ORG = "audit:view:org"
    AUDIT_EXPORT = "audit:export"
    LEGAL_REVIEW = "legal:review"
    LEGAL_ADVISE = "legal:advise"
    PLATFORM_MANAGE_ORGS = "platform:manage:orgs"
    PLATFORM_VIEW_USAGE = "platform:view:usage"


# Role -> Permissions mapping
ROLE_PERMISSIONS: Dict[UserRole, List[Permission]] = {
    UserRole.SUPER_ADMIN: list(Permission),  # All permissions
    
    UserRole.ORG_ADMIN: [
        Permission.DOCUMENT_UPLOAD, Permission.DOCUMENT_VIEW_ALL, Permission.DOCUMENT_DELETE_ALL,
        Permission.SCAN_INITIATE, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_SUBMIT, Permission.APPROVAL_APPROVE, Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE, Permission.APPROVAL_ESCALATE_TO_LEGAL,
        Permission.ASSIGNMENT_VIEW_TEAM, Permission.ASSIGNMENT_ASSIGN, Permission.ASSIGNMENT_REASSIGN,
        Permission.TEAM_VIEW, Permission.TEAM_CREATE, Permission.TEAM_MANAGE,
        Permission.USER_VIEW_ORG, Permission.USER_INVITE, Permission.USER_MANAGE, Permission.USER_CHANGE_ROLES,
        Permission.ANALYTICS_VIEW_ORG,
        Permission.SETTINGS_VIEW, Permission.SETTINGS_MANAGE,
        Permission.RULES_VIEW, Permission.RULES_CREATE, Permission.RULES_MANAGE,
        Permission.AUDIT_VIEW_ORG, Permission.AUDIT_EXPORT,
    ],
    
    UserRole.LEGAL_ADVISOR: [
        Permission.DOCUMENT_VIEW_ALL, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_APPROVE, Permission.APPROVAL_REJECT,
        Permission.LEGAL_REVIEW, Permission.LEGAL_ADVISE,
        Permission.ASSIGNMENT_VIEW_TEAM,
        Permission.ANALYTICS_VIEW_ORG,
        Permission.AUDIT_VIEW_ORG,
    ],
    
    UserRole.DLA_PIPER: [
        Permission.DOCUMENT_VIEW_ALL, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_APPROVE, Permission.APPROVAL_REJECT,
        Permission.LEGAL_REVIEW, Permission.LEGAL_ADVISE,
        Permission.AUDIT_VIEW_ORG,
    ],
    
    UserRole.COMPLIANCE_OFFICER: [
        Permission.DOCUMENT_UPLOAD, Permission.DOCUMENT_VIEW_ALL,
        Permission.SCAN_INITIATE, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_SUBMIT, Permission.APPROVAL_APPROVE, Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE, Permission.APPROVAL_ESCALATE_TO_LEGAL,
        Permission.ASSIGNMENT_VIEW_TEAM, Permission.ASSIGNMENT_ASSIGN, Permission.ASSIGNMENT_REASSIGN,
        Permission.TEAM_VIEW,
        Permission.ANALYTICS_VIEW_ORG,
        Permission.RULES_VIEW,
        Permission.AUDIT_VIEW_ORG,
    ],
    
    UserRole.COMPLIANCE_REVIEWER: [
        Permission.DOCUMENT_UPLOAD, Permission.DOCUMENT_VIEW_ALL,
        Permission.SCAN_INITIATE, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_SUBMIT, Permission.APPROVAL_APPROVE, Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE,
        Permission.ASSIGNMENT_VIEW_OWN, Permission.ASSIGNMENT_VIEW_TEAM,
        Permission.TEAM_VIEW,
        Permission.ANALYTICS_VIEW_OWN,
        Permission.RULES_VIEW,
        Permission.AUDIT_VIEW_OWN,
    ],
    
    UserRole.MARKETING_USER: [
        Permission.DOCUMENT_UPLOAD, Permission.DOCUMENT_VIEW_OWN, Permission.DOCUMENT_DELETE_OWN,
        Permission.SCAN_INITIATE, Permission.SCAN_VIEW_RESULTS,
        Permission.APPROVAL_SUBMIT,
        Permission.ASSIGNMENT_VIEW_OWN,
        Permission.ANALYTICS_VIEW_OWN,
        Permission.AUDIT_VIEW_OWN,
    ],
    
    UserRole.MARKETING_VIEWER: [
        Permission.DOCUMENT_VIEW_OWN,
        Permission.SCAN_VIEW_RESULTS,
        Permission.ANALYTICS_VIEW_OWN,
    ],
}


class TeamRole:
    """Roles within a team"""
    LEAD = "team_lead"
    SENIOR = "senior_reviewer"
    REVIEWER = "reviewer"
    JUNIOR = "junior_reviewer"
    OBSERVER = "observer"


class AssignmentStatus:
    UNASSIGNED = "unassigned"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class SubscriptionTier:
    TRIAL = "trial"
    BASIC = "basic"
    CORE = "core"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


# Subscription limits
TIER_LIMITS = {
    SubscriptionTier.TRIAL: {
        'scans_per_month': 10,
        'jurisdictions': 1,
        'users': 3,
        'custom_rules': False,
        'advisory_hours': 0,
        'teams': 1
    },
    SubscriptionTier.BASIC: {
        'scans_per_month': 100,
        'jurisdictions': 3,
        'users': 10,
        'custom_rules': False,
        'advisory_hours': 2,
        'teams': 3
    },
    SubscriptionTier.CORE: {
        'scans_per_month': 500,
        'jurisdictions': 10,
        'users': 50,
        'custom_rules': True,
        'advisory_hours': 10,
        'teams': 10
    },
    SubscriptionTier.PREMIUM: {
        'scans_per_month': -1,
        'jurisdictions': -1,
        'users': -1,
        'custom_rules': True,
        'advisory_hours': 50,
        'teams': -1
    },
    SubscriptionTier.ENTERPRISE: {
        'scans_per_month': -1,
        'jurisdictions': -1,
        'users': -1,
        'custom_rules': True,
        'advisory_hours': -1,
        'teams': -1
    }
}


# =============================================================================
# SINGLETON DATABASE CLIENT
# =============================================================================

class CosmosDBClient:
    """Thread-safe singleton Cosmos DB client"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if CosmosDBClient._initialized:
            return
        
        with CosmosDBClient._lock:
            if CosmosDBClient._initialized:
                return
            
            try:
                endpoint = os.getenv('COSMOS_ENDPOINT')
                key = os.getenv('COSMOS_KEY')
                database_name = os.getenv('COSMOS_DATABASE', 'compliance-db')
                
                if not endpoint or not key:
                    raise ValueError("COSMOS_ENDPOINT and COSMOS_KEY required")
                
                self.client = CosmosClient(endpoint, key)
                self.database = self.client.get_database_client(database_name)
                
                self._container_configs = {
                    'organizations': '/id',
                    'users': '/organization_id',
                    'documents': {
                        'partition_key': '/organization_id',
                        'indexing_policy': {
                            'automatic': True,
                            'indexingMode': 'consistent',
                            'includedPaths': [
                                {
                                    'path': '/*'
                                }
                            ],
                            'excludedPaths': [
                                {
                                    'path': '/"_etag"/?'
                                }
                            ],
                            'compositeIndexes': [
                                [
                                    {
                                        'path': '/assignment_priority',
                                        'order': 'ascending'
                                    },
                                    {
                                        'path': '/created_at',
                                        'order': 'ascending'
                                    }
                                ],
                                [
                                    {
                                        'path': '/assignment_priority',
                                        'order': 'ascending'
                                    },
                                    {
                                        'path': '/assigned_at',
                                        'order': 'ascending'
                                    }
                                ]
                            ]
                        }
                    },
                    'rules': '/jurisdiction',
                    'custom_rules': '/organization_id',
                    'jurisdictions': '/id',
                    'audit_logs': '/partition_key',
                    'ai_conversations': '/document_id',
                    'questionnaires': '/organization_id',
                    'analytics': '/organization_id',
                    'notifications': '/organization_id',
                }
                
                self._containers: Dict[str, ContainerProxy] = {}
                
                CosmosDBClient._initialized = True
                logger.info(f"✅ Cosmos DB initialized: {database_name}")
                
            except Exception as e:
                logger.error(f"❌ Cosmos DB initialization failed: {e}")
                raise
    
    def get_container(self, name: str) -> ContainerProxy:
        """Get or create a container"""
        if name not in self._containers:
            try:
                self._containers[name] = self.database.get_container_client(name)
                self._containers[name].read()
            except exceptions.CosmosResourceNotFoundError:
                partition_key_path = self._container_configs.get(name, '/id')
                self._containers[name] = self.database.create_container(
                    id=name,
                    partition_key=PartitionKey(path=partition_key_path)
                )
                logger.info(f"✅ Created container: {name}")
        
        return self._containers[name]


# =============================================================================
# MODULE-LEVEL ACCESSORS
# =============================================================================

_db_instance: Optional[CosmosDBClient] = None

def get_db() -> CosmosDBClient:
    """Get the singleton database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = CosmosDBClient()
    return _db_instance


def get_container(name: str) -> ContainerProxy:
    """Get a container by name"""
    return get_db().get_container(name)


def get_cosmos_container() -> ContainerProxy:
    """Legacy: Get documents container"""
    return get_container('documents')


# =============================================================================
# ORGANIZATION CRUD
# =============================================================================

def create_organization(org_data: Dict) -> Dict:
    """Create a new organization"""
    container = get_container('organizations')
    
    org = {
        'id': org_data.get('id', str(uuid.uuid4())),
        'type': 'organization',
        'name': org_data.get('name', ''),
        'azure_tenant_id': org_data.get('azure_tenant_id', ''),
        'subscription_tier': org_data.get('subscription_tier', SubscriptionTier.TRIAL),
        'subscription_expires': org_data.get('subscription_expires', (datetime.utcnow() + timedelta(days=14)).isoformat() + 'Z'),
        'jurisdictions': org_data.get('jurisdictions', ['UK']),
        'custom_rules_enabled': org_data.get('custom_rules_enabled', False),
        'advisory_hours_remaining': org_data.get('advisory_hours_remaining', 0),
        'settings': org_data.get('settings', {}),
        'created_at': datetime.utcnow().isoformat() + 'Z',
        'updated_at': datetime.utcnow().isoformat() + 'Z',
    }
    
    container.create_item(body=org)
    logger.info(f"✅ Organization created: {org['id']}")
    return org


def get_organization(org_id: str) -> Optional[Dict]:
    """Get organization by ID"""
    if not org_id:
        return None
    
    container = get_container('organizations')
    
    try:
        return container.read_item(item=org_id, partition_key=org_id)
    except exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logger.error(f"❌ Get organization failed: {e}")
        return None


def update_organization(org_id: str, updates: Dict) -> Optional[Dict]:
    """Update an organization"""
    container = get_container('organizations')
    
    try:
        org = container.read_item(item=org_id, partition_key=org_id)
        org.update(updates)
        org['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        return container.upsert_item(org)
    except exceptions.CosmosResourceNotFoundError:
        return None


def get_organization_by_tenant(tenant_id: str) -> Optional[Dict]:
    """Get organization by Azure tenant ID"""
    container = get_container('organizations')
    
    query = "SELECT * FROM c WHERE c.azure_tenant_id = @tid"
    items = list(container.query_items(
        query=query,
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True
    ))
    
    return items[0] if items else None


# =============================================================================
# USER CRUD
# =============================================================================

def create_user(user_data: Dict) -> Dict:
    """Create a new user"""
    container = get_container('users')
    
    user = {
        'id': user_data.get('id', str(uuid.uuid4())),
        'type': 'user',
        'email': user_data.get('email', '').lower(),
        'name': user_data.get('name', ''),
        'azure_oid': user_data.get('azure_oid', ''),
        'organization_id': user_data.get('organization_id', ''),
        'organization_name': user_data.get('organization_name', ''),
        'azure_tenant_id': user_data.get('azure_tenant_id', ''),
        'roles': user_data.get('roles', [UserRole.MARKETING_USER.value]),
        'department': user_data.get('department', ''),
        'job_title': user_data.get('job_title', ''),
        'phone': user_data.get('phone', ''),
        'notification_settings': user_data.get('notification_settings', {
            'email_on_assignment': True,
            'email_on_mention': True,
            'email_on_approval': True,
            'email_on_rejection': True,
            'email_on_escalation': True,
            'email_digest': 'daily'
        }),
        'is_active': user_data.get('is_active', True),
        'is_service_principal': user_data.get('is_service_principal', False),
        'created_at': datetime.utcnow().isoformat() + 'Z',
        'last_login': datetime.utcnow().isoformat() + 'Z',
        'login_count': 1,
        'documents_uploaded': 0,
    }
    
    container.create_item(body=user)
    logger.info(f"✅ User created: {user['email']}")
    return user


def get_user(user_id: str, org_id: str = None) -> Optional[Dict]:
    """Get user by ID"""
    container = get_container('users')
    
    try:
        if org_id:
            return container.read_item(item=user_id, partition_key=org_id)
        else:
            query = "SELECT * FROM c WHERE c.id = @id AND c.type = 'user'"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@id", "value": user_id}],
                enable_cross_partition_query=True
            ))
            return items[0] if items else None
    except exceptions.CosmosResourceNotFoundError:
        return None


def get_user_by_email(email: str) -> Optional[Dict]:
    """Get user by email"""
    if not email:
        return None
    
    container = get_container('users')
    
    query = "SELECT * FROM c WHERE c.email = @email AND c.type = 'user'"
    items = list(container.query_items(
        query=query,
        parameters=[{"name": "@email", "value": email.lower()}],
        enable_cross_partition_query=True
    ))
    
    return items[0] if items else None


def get_users_by_org(org_id: str, include_inactive: bool = False, limit: int = 500) -> List[Dict]:
    """Get all users in an organization"""
    container = get_container('users')
    
    if include_inactive:
        query = "SELECT * FROM c WHERE c.organization_id = @org_id AND c.type = 'user'"
    else:
        query = "SELECT * FROM c WHERE c.organization_id = @org_id AND c.type = 'user' AND c.is_active = true"
    
    return list(container.query_items(
        query=query,
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id,
        max_item_count=limit
    ))


def get_users_by_role(org_id: str, role: str) -> List[Dict]:
    """Get all users with a specific role in an organization"""
    container = get_container('users')
    
    query = """
    SELECT * FROM c 
    WHERE c.organization_id = @org_id 
    AND c.type = 'user' 
    AND c.is_active = true
    AND ARRAY_CONTAINS(c.roles, @role)
    """
    
    return list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@role", "value": role}
        ],
        partition_key=org_id
    ))


def update_user(user_id: str, updates: Dict, org_id: str = None) -> Optional[Dict]:
    """Update a user"""
    user = get_user(user_id, org_id)
    if not user:
        return None
    
    container = get_container('users')
    user.update(updates)
    user['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    
    return container.upsert_item(user)


# =============================================================================
# USER ACTIVITY & DECISIONS (for admin views)
# =============================================================================

def get_user_activity(user_email: str, org_id: str, days: int = 30) -> List[Dict]:
    """Get recent activity for a user"""
    container = get_container('audit_logs')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    query = """
    SELECT * FROM c 
    WHERE c.organization_id = @org_id 
    AND c.user_email = @email
    AND c.timestamp >= @cutoff
    ORDER BY c.timestamp DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@email", "value": user_email},
            {"name": "@cutoff", "value": cutoff}
        ],
        enable_cross_partition_query=True,
        max_item_count=200
    ))


def get_decisions_by_user(user_email: str, org_id: str, days: int = 30) -> List[Dict]:
    """Get all approval/rejection decisions made by a user"""
    container = get_container('audit_logs')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    query = """
    SELECT * FROM c 
    WHERE c.organization_id = @org_id 
    AND c.type = 'decision_trail'
    AND c.decision_maker.email = @email
    AND c.timestamp >= @cutoff
    ORDER BY c.timestamp DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@email", "value": user_email},
            {"name": "@cutoff", "value": cutoff}
        ],
        enable_cross_partition_query=True
    ))


def get_org_analytics_summary(org_id: str, days: int = 30) -> Dict:
    """Get organization analytics summary"""
    container = get_container('documents')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    # Get document counts
    docs_query = """
    SELECT 
        c.status,
        COUNT(1) as count
    FROM c 
    WHERE c.organization_id = @org_id 
    AND c.type = 'document'
    AND c.created_at >= @cutoff
    GROUP BY c.status
    """
    
    # Note: Cosmos DB doesn't support GROUP BY well, so we do it in Python
    all_docs = list(container.query_items(
        query="SELECT c.status FROM c WHERE c.organization_id = @org_id AND c.type = 'document' AND c.created_at >= @cutoff",
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@cutoff", "value": cutoff}
        ],
        partition_key=org_id
    ))
    
    status_counts = {}
    for doc in all_docs:
        status = doc.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return {
        'period_days': days,
        'total_documents': len(all_docs),
        'by_status': status_counts,
        'approved': status_counts.get('approved', 0),
        'rejected': status_counts.get('rejected', 0),
        'pending': status_counts.get('pending_review', 0) + status_counts.get('scanned', 0),
        'escalated': status_counts.get('escalated', 0),
    }


# =============================================================================
# DOCUMENT CRUD
# =============================================================================

def create_document(doc_data: Dict) -> Dict:
    """Create a new document"""
    container = get_container('documents')
    
    now = datetime.utcnow()
    
    doc = {
        'id': doc_data.get('id', str(uuid.uuid4())),
        'type': 'document',
        'organization_id': doc_data.get('organization_id', ''),
        'filename': doc_data.get('filename', ''),
        'mimetype': doc_data.get('mimetype', ''),
        'size_bytes': doc_data.get('size_bytes', 0),
        'blob_name': doc_data.get('blob_name', ''),
        'storage_path': doc_data.get('storage_path', ''),
        # ✅ FIX: explicit blob fields from upload.py
        'blob_url': doc_data.get('blob_url', ''),
        'blob_path': doc_data.get('blob_path', ''),
        'blob_container': doc_data.get('blob_container', 'documents'),
        'jurisdiction': doc_data.get('jurisdiction', ''),
        'uploaded_by': doc_data.get('uploaded_by', ''),
        'uploaded_by_name': doc_data.get('uploaded_by_name', ''),
        
        # Status & workflow
        'status': doc_data.get('status', 'uploaded'),
        'workflow_status': doc_data.get('workflow_status', ''),
        'compliance_outcome': doc_data.get('compliance_outcome', ''),
        'risk_score': doc_data.get('risk_score', 0),
        'violations': doc_data.get('violations', []),
        'violations_count': doc_data.get('violations_count', 0),
        
        # Assignment
        'assigned_to': doc_data.get('assigned_to', ''),
        'assigned_to_name': doc_data.get('assigned_to_name', ''),
        'assigned_by': doc_data.get('assigned_by', ''),
        'assigned_at': doc_data.get('assigned_at'),
        'assignment_status': doc_data.get('assignment_status', AssignmentStatus.UNASSIGNED),
        'assignment_priority': doc_data.get('assignment_priority', 'medium'),
        'assignment_deadline': doc_data.get('assignment_deadline'),
        'assignment_sla_hours': doc_data.get('assignment_sla_hours'),
        'assignment_notes': doc_data.get('assignment_notes', []),
        'assignment_reason': doc_data.get('assignment_reason', ''),
        'ticket_id': doc_data.get('ticket_id', ''),
        'handoff_history': doc_data.get('handoff_history', []),
        
        # Team
        'team_id': doc_data.get('team_id', ''),
        'team_name': doc_data.get('team_name', ''),
        
        # Collaboration
        'watchers': doc_data.get('watchers', []),
        'discussions': doc_data.get('discussions', []),
        'last_activity_at': now.isoformat() + 'Z',
        
        # Escalation
        'escalated': doc_data.get('escalated', False),
        'escalated_at': doc_data.get('escalated_at'),
        'escalated_by': doc_data.get('escalated_by'),
        'escalation_target': doc_data.get('escalation_target'),
        'escalation_reason': doc_data.get('escalation_reason'),
        
        # Timestamps
        'created_at': now.isoformat() + 'Z',
        'updated_at': now.isoformat() + 'Z',
    }
    
    for key, value in doc_data.items():
        if key not in doc:
            doc[key] = value
    
    container.create_item(body=doc)
    logger.info(f"✅ Document created: {doc['id']}")
    return doc


def get_document(doc_id: str, org_id: str = None) -> Optional[Dict]:
    """Get document by ID — always enforces org isolation when org_id is provided"""
    if not doc_id:
        return None

    container = get_container('documents')

    try:
        if org_id:
            # Fast path: direct read with partition key
            doc = container.read_item(item=doc_id, partition_key=org_id)
            # ✅ FIX: double-check org matches even on direct read
            if doc.get('organization_id') != org_id:
                logger.error(
                    f"🚫 Partition key matched but org mismatch: "
                    f"requested={org_id} stored={doc.get('organization_id')}"
                )
                return None
            if doc.get('type') != 'document':
                return None
            return doc
        else:
            # ⚠️ Cross-partition only used internally (e.g. super admin scan)
            # Never expose this path to user-facing API calls
            query = "SELECT * FROM c WHERE c.id = @id AND c.type = 'document'"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@id", "value": doc_id}],
                enable_cross_partition_query=True
            ))
            return items[0] if items else None

    except exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logger.error(f"❌ Get document failed [{doc_id}]: {e}")
        return None


def get_document_with_access_check(doc_id: str, org_id: str) -> Optional[Dict]:
    """Get document only if it belongs to the organization"""
    doc = get_document(doc_id, org_id)
    if doc and doc.get('organization_id') == org_id:
        return doc
    return None


def update_document(doc_id: str, updates: Dict, org_id: str = None) -> Optional[Dict]:
    """Update a document"""
    doc = get_document(doc_id, org_id)
    if not doc:
        return None
    
    container = get_container('documents')
    doc.update(updates)
    doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    
    return container.upsert_item(doc)


def list_documents_by_organization(
    org_id: str,
    status: str = None,
    jurisdiction: str = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
    """List documents for an organization, optionally filtered by status and jurisdiction"""
    # ✅ FIX: hard block — never run a cross-org query
    if not org_id or not org_id.strip():
        logger.error("🚫 list_documents_by_organization called with no org_id — blocked")
        return []

    container = get_container('documents')

    conditions = ["c.organization_id = @org_id", "c.type = 'document'"]
    params = [{"name": "@org_id", "value": org_id}]

    if status:
        conditions.append("c.status = @status")
        params.append({"name": "@status", "value": status})

    if jurisdiction:
        conditions.append("c.jurisdiction = @jurisdiction")
        params.append({"name": "@jurisdiction", "value": jurisdiction})

    query = f"""
    SELECT * FROM c
    WHERE {' AND '.join(conditions)}
    ORDER BY c.created_at DESC
    OFFSET {offset} LIMIT {limit}
    """

    return list(container.query_items(
        query=query,
        parameters=params,
        partition_key=org_id
    ))
# =============================================================================
# TEAM CRUD
# =============================================================================

def create_team(team_data: Dict) -> Dict:
    """Create a new team"""
    container = get_container('documents')
    
    now = datetime.utcnow()
    
    team = {
        'id': team_data.get('id', f"team_{uuid.uuid4().hex[:12]}"),
        'type': 'team',
        'organization_id': team_data.get('organization_id', ''),
        'name': team_data.get('name', ''),
        'description': team_data.get('description', ''),
        'assignment_strategy': team_data.get('assignment_strategy', 'least_loaded'),
        'jurisdictions': team_data.get('jurisdictions', []),
        'max_concurrent_per_member': team_data.get('max_concurrent_per_member', 10),
        'default_sla_hours': team_data.get('default_sla_hours', 48),
        'escalation_chain': team_data.get('escalation_chain', [TeamRole.LEAD, 'Organization.Admin']),
        'members': team_data.get('members', []),
        'settings': team_data.get('settings', {
            'auto_assign_on_upload': False,
            'require_senior_for_high_risk': True,
            'notify_team_on_new_document': True,
        }),
        'stats': team_data.get('stats', {
            'documents_assigned': 0,
            'documents_completed': 0,
            'avg_completion_hours': 0,
            'last_assigned_member': ''
        }),
        'created_by': team_data.get('created_by', ''),
        'created_at': now.isoformat() + 'Z',
        'updated_at': now.isoformat() + 'Z',
        'is_archived': False
    }
    
    container.create_item(body=team)
    logger.info(f"✅ Team created: {team['name']} ({team['id']})")
    return team


def get_team(team_id: str, org_id: str) -> Optional[Dict]:
    """Get team by ID"""
    container = get_container('documents')
    
    try:
        team = container.read_item(item=team_id, partition_key=org_id)
        if team.get('type') == 'team' and team.get('organization_id') == org_id:
            return team
        return None
    except exceptions.CosmosResourceNotFoundError:
        return None


def get_teams_by_org(org_id: str, include_archived: bool = False) -> List[Dict]:
    """Get all teams for an organization"""
    container = get_container('documents')
    
    conditions = ["c.organization_id = @org_id", "c.type = 'team'"]
    if not include_archived:
        conditions.append("(NOT IS_DEFINED(c.is_archived) OR c.is_archived = false)")
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.name ASC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id
    ))


def update_team(team_id: str, updates: Dict, org_id: str) -> Optional[Dict]:
    """Update a team"""
    team = get_team(team_id, org_id)
    if not team:
        return None
    
    container = get_container('documents')
    team.update(updates)
    team['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    
    return container.upsert_item(team)


def get_user_teams(user_email: str, org_id: str) -> List[Dict]:
    """Get all teams a user belongs to"""
    all_teams = get_teams_by_org(org_id)
    user_teams = []
    
    for team in all_teams:
        for member in team.get('members', []):
            if member.get('email', '').lower() == user_email.lower():
                user_teams.append({
                    **team,
                    'your_role': member.get('role', TeamRole.REVIEWER)
                })
                break
    
    return user_teams


# =============================================================================
# NOTIFICATIONS
# =============================================================================

def create_notification(notification_data: Dict) -> Dict:
    """Create a notification"""
    container = get_container('audit_logs')
    
    now = datetime.utcnow()
    month = now.strftime('%Y-%m')
    org_id = notification_data.get('organization_id', '')
    
    notification = {
        'id': notification_data.get('id', f"notif_{uuid.uuid4().hex[:12]}"),
        'type': 'notification',
        'partition_key': f"{org_id}_{month}",
        'organization_id': org_id,
        'recipient_email': notification_data.get('recipient_email', ''),
        'notification_type': notification_data.get('notification_type', ''),
        'title': notification_data.get('title', ''),
        'message': notification_data.get('message', ''),
        'document_id': notification_data.get('document_id'),
        'team_id': notification_data.get('team_id'),
        'discussion_id': notification_data.get('discussion_id'),
        'created_by': notification_data.get('created_by'),
        'created_at': now.isoformat() + 'Z',
        'read': False,
        'read_at': None,
    }
    
    container.create_item(body=notification)
    return notification


def get_user_notifications(
    user_email: str,
    org_id: str,
    unread_only: bool = False,
    limit: int = 50
) -> List[Dict]:
    """Get notifications for a user"""
    container = get_container('audit_logs')
    
    conditions = [
        "c.type = 'notification'",
        "c.organization_id = @org_id",
        "c.recipient_email = @email"
    ]
    
    if unread_only:
        conditions.append("c.read = false")
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.created_at DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@email", "value": user_email}
        ],
        enable_cross_partition_query=True,
        max_item_count=limit
    ))


def mark_notification_read(notification_id: str, org_id: str, user_email: str) -> bool:
    """Mark a notification as read"""
    container = get_container('audit_logs')
    
    query = """
    SELECT * FROM c 
    WHERE c.id = @id 
    AND c.organization_id = @org_id 
    AND c.recipient_email = @email
    AND c.type = 'notification'
    """
    
    notifications = list(container.query_items(
        query=query,
        parameters=[
            {"name": "@id", "value": notification_id},
            {"name": "@org_id", "value": org_id},
            {"name": "@email", "value": user_email}
        ],
        enable_cross_partition_query=True
    ))
    
    if not notifications:
        return False
    
    notification = notifications[0]
    notification['read'] = True
    notification['read_at'] = datetime.utcnow().isoformat() + 'Z'
    
    container.upsert_item(notification)
    return True


# =============================================================================
# AUDIT LOGGING
# =============================================================================

def log_action(
    org_id: str,
    user_id: str,
    user_email: str,
    user_roles: List[str],
    action: str,
    resource_type: str,
    resource_id: str,
    resource_name: str = '',
    details: Dict = None,
    success: bool = True
) -> Dict:
    """Log an action to audit trail"""
    container = get_container('audit_logs')
    
    now = datetime.utcnow()
    month = now.strftime('%Y-%m')
    
    log_entry = {
        'id': f"log_{uuid.uuid4().hex[:12]}",
        'type': 'audit_log',
        'partition_key': f"{org_id}_{month}",
        'organization_id': org_id,
        'user_id': user_id,
        'user_email': user_email,
        'user_roles': user_roles,
        'action': action,
        'resource_type': resource_type,
        'resource_id': resource_id,
        'resource_name': resource_name,
        'details': details or {},
        'success': success,
        'timestamp': now.isoformat() + 'Z',
        'ip_address': details.get('ip_address') if details else None,
        'user_agent': details.get('user_agent') if details else None,
    }
    
    container.create_item(body=log_entry)
    return log_entry


def get_audit_logs(
    org_id: str,
    resource_type: str = None,
    resource_id: str = None,
    user_email: str = None,
    action: str = None,
    days: int = 30,
    limit: int = 100
) -> List[Dict]:
    """Query audit logs"""
    container = get_container('audit_logs')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    conditions = [
        "c.type = 'audit_log'",
        "c.organization_id = @org_id",
        "c.timestamp >= @cutoff"
    ]
    params = [
        {"name": "@org_id", "value": org_id},
        {"name": "@cutoff", "value": cutoff}
    ]
    
    if resource_type:
        conditions.append("c.resource_type = @resource_type")
        params.append({"name": "@resource_type", "value": resource_type})
    
    if resource_id:
        conditions.append("c.resource_id = @resource_id")
        params.append({"name": "@resource_id", "value": resource_id})
    
    if user_email:
        conditions.append("c.user_email = @user_email")
        params.append({"name": "@user_email", "value": user_email})
    
    if action:
        conditions.append("c.action = @action")
        params.append({"name": "@action", "value": action})
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.timestamp DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
        max_item_count=limit
    ))


# =============================================================================
# ACTIVITY FEED
# =============================================================================

def log_activity(
    org_id: str,
    user_email: str,
    user_name: str,
    action: str,
    document_id: str = None,
    document_name: str = None,
    team_id: str = None,
    details: Dict = None
) -> Dict:
    """Log an activity to the feed"""
    container = get_container('audit_logs')
    
    now = datetime.utcnow()
    month = now.strftime('%Y-%m')
    
    activity = {
        'id': f"activity_{uuid.uuid4().hex[:12]}",
        'type': 'activity',
        'partition_key': f"{org_id}_{month}",
        'organization_id': org_id,
        'user_email': user_email,
        'user_name': user_name,
        'action': action,
        'document_id': document_id,
        'document_name': document_name,
        'team_id': team_id,
        'details': details or {},
        'timestamp': now.isoformat() + 'Z'
    }
    
    container.create_item(body=activity)
    return activity


def get_activity_feed(
    org_id: str,
    user_email: str = None,
    document_id: str = None,
    days: int = 7,
    limit: int = 50
) -> List[Dict]:
    """Get activity feed"""
    container = get_container('audit_logs')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    conditions = [
        "c.type = 'activity'",
        "c.organization_id = @org_id",
        "c.timestamp >= @cutoff"
    ]
    params = [
        {"name": "@org_id", "value": org_id},
        {"name": "@cutoff", "value": cutoff}
    ]
    
    if user_email:
        conditions.append("c.user_email = @user_email")
        params.append({"name": "@user_email", "value": user_email})
    
    if document_id:
        conditions.append("c.document_id = @document_id")
        params.append({"name": "@document_id", "value": document_id})
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.timestamp DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
        max_item_count=limit
    ))


# =============================================================================
# DECISION TRAIL
# =============================================================================

def save_decision_trail(decision_data: Dict) -> Dict:
    """Save a decision trail entry"""
    container = get_container('audit_logs')
    
    now = datetime.utcnow()
    month = now.strftime('%Y-%m')
    org_id = decision_data.get('organization_id', '')
    
    decision = {
        'id': decision_data.get('id', f"decision_{uuid.uuid4().hex[:12]}"),
        'type': 'decision_trail',
        'partition_key': f"{org_id}_{month}",
        'organization_id': org_id,
        'document_id': decision_data.get('document_id'),
        'document_filename': decision_data.get('document_filename'),
        'decision': decision_data.get('decision'),
        'decision_type': decision_data.get('decision_type'),
        'decision_maker': decision_data.get('decision_maker', {}),
        'decision_context': decision_data.get('decision_context', {}),
        'document_state_at_decision': decision_data.get('document_state_at_decision', {}),
        'ai_context': decision_data.get('ai_context', {}),
        'is_ai_override': decision_data.get('is_ai_override', False),
        'override_details': decision_data.get('override_details'),
        'assignment_context': decision_data.get('assignment_context', {}),
        'regulations_considered': decision_data.get('regulations_considered', []),
        'jurisdiction': decision_data.get('jurisdiction'),
        'request_metadata': decision_data.get('request_metadata', {}),
        'time_to_decision_hours': decision_data.get('time_to_decision_hours'),
        'decision_timestamp': decision_data.get('decision_timestamp', now.isoformat() + 'Z'),
        'created_at': now.isoformat() + 'Z',
        'timestamp': now.isoformat() + 'Z',
    }
    
    container.create_item(body=decision)
    logger.info(f"✅ Decision trail saved: {decision['id']}")
    return decision


def get_decision_trail(document_id: str, org_id: str) -> List[Dict]:
    """Get decision trail for a document"""
    container = get_container('audit_logs')
    
    query = """
    SELECT * FROM c 
    WHERE c.type = 'decision_trail'
    AND c.organization_id = @org_id 
    AND c.document_id = @doc_id
    ORDER BY c.created_at DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[
            {"name": "@org_id", "value": org_id},
            {"name": "@doc_id", "value": document_id}
        ],
        enable_cross_partition_query=True
    ))


# =============================================================================
# ANALYTICS EVENTS
# =============================================================================

def save_analytics_event(event_data: Dict) -> Dict:
    """Save an analytics event"""
    container = get_container('analytics')
    
    now = datetime.utcnow()
    org_id = event_data.get('organization_id', '')
    
    event = {
        'id': event_data.get('id', f"event_{uuid.uuid4().hex[:12]}"),
        'type': event_data.get('type', 'analytics_event'),
        'organization_id': org_id,
        'event_type': event_data.get('event_type', event_data.get('type')),
        'event_subtype': event_data.get('event_subtype', ''),
        'document_id': event_data.get('document_id'),
        'user_email': event_data.get('user_email'),
        'metrics': event_data.get('metrics', {}),
        'dimensions': event_data.get('dimensions', {}),
        'timestamp': now.isoformat() + 'Z',
    }
    
    for key, value in event_data.items():
        if key not in event:
            event[key] = value
    
    container.create_item(body=event)
    return event


def get_analytics_events(
    org_id: str,
    event_type: str = None,
    days: int = 30,
    limit: int = 1000
) -> List[Dict]:
    """Query analytics events"""
    container = get_container('analytics')
    
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    conditions = [
        "c.organization_id = @org_id",
        "c.timestamp >= @cutoff"
    ]
    params = [
        {"name": "@org_id", "value": org_id},
        {"name": "@cutoff", "value": cutoff}
    ]
    
    if event_type:
        conditions.append("c.event_type = @event_type")
        params.append({"name": "@event_type", "value": event_type})
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.timestamp DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=params,
        partition_key=org_id,
        max_item_count=limit
    ))


# =============================================================================
# AI CONVERSATIONS
# =============================================================================

def save_ai_conversation(conversation_data: Dict) -> Dict:
    """Save an AI conversation"""
    container = get_container('ai_conversations')
    
    now = datetime.utcnow()
    
    conversation = {
        'id': conversation_data.get('id', f"conv_{uuid.uuid4().hex[:12]}"),
        'type': 'ai_conversation',
        'document_id': conversation_data.get('document_id', ''),
        'organization_id': conversation_data.get('organization_id', ''),
        'user_email': conversation_data.get('user_email', ''),
        'messages': conversation_data.get('messages', []),
        'context': conversation_data.get('context', {}),
        'created_at': now.isoformat() + 'Z',
        'updated_at': now.isoformat() + 'Z',
    }
    
    container.create_item(body=conversation)
    return conversation


def get_ai_conversations_for_document(document_id: str, limit: int = 20) -> List[Dict]:
    """Get AI conversations for a document"""
    container = get_container('ai_conversations')
    
    query = """
    SELECT * FROM c 
    WHERE c.document_id = @doc_id 
    AND c.type = 'ai_conversation'
    ORDER BY c.created_at DESC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[{"name": "@doc_id", "value": document_id}],
        partition_key=document_id,
        max_item_count=limit
    ))


# =============================================================================
# ASSIGNMENT HELPERS
# =============================================================================

def get_user_assignments(
    user_email: str,
    org_id: str,
    status: str = None,
    include_team: bool = True
) -> List[Dict]:
    """Get documents assigned to a user"""
    container = get_container('documents')
    
    conditions = [
        "c.organization_id = @org_id",
        "c.type = 'document'",
        "c.assigned_to = @email"
    ]
    params = [
        {"name": "@org_id", "value": org_id},
        {"name": "@email", "value": user_email}
    ]
    
    if status:
        conditions.append("c.assignment_status = @status")
        params.append({"name": "@status", "value": status})
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.assignment_priority ASC, c.assigned_at ASC
    """
    
    return list(container.query_items(
        query=query,
        parameters=params,
        partition_key=org_id
    ))


def get_team_assignments(team_id: str, org_id: str, status: str = None) -> List[Dict]:
    """Get documents assigned to a team"""
    container = get_container('documents')
    
    conditions = [
        "c.organization_id = @org_id",
        "c.type = 'document'",
        "c.team_id = @team_id"
    ]
    params = [
        {"name": "@org_id", "value": org_id},
        {"name": "@team_id", "value": team_id}
    ]
    
    if status:
        conditions.append("c.assignment_status = @status")
        params.append({"name": "@status", "value": status})
    
    query = f"""
    SELECT * FROM c 
    WHERE {' AND '.join(conditions)}
    ORDER BY c.assignment_priority ASC, c.assigned_at ASC
    """
    
    return list(container.query_items(
        query=query,
        parameters=params,
        partition_key=org_id
    ))


def get_unassigned_documents(org_id: str, limit: int = 100) -> List[Dict]:
    """Get unassigned documents in the queue"""
    container = get_container('documents')
    
    query = """
    SELECT * FROM c 
    WHERE c.organization_id = @org_id 
    AND c.type = 'document'
    AND c.status IN ('scanned', 'pending_review', 'uploaded')
    AND (NOT IS_DEFINED(c.assigned_to) OR c.assigned_to = null OR c.assigned_to = '')
    ORDER BY c.created_at ASC
    """
    
    return list(container.query_items(
        query=query,
        parameters=[{"name": "@org_id", "value": org_id}],
        partition_key=org_id,
        max_item_count=limit
    ))


def get_user_workload(user_email: str, org_id: str) -> Dict:
    """Get workload summary for a user"""
    assignments = get_user_assignments(user_email, org_id)
    
    now = datetime.utcnow()
    
    pending = 0
    in_progress = 0
    at_risk = 0
    breached = 0
    
    for doc in assignments:
        status = doc.get('assignment_status', '')
        deadline = doc.get('assignment_deadline', '')
        
        if status == AssignmentStatus.PENDING:
            pending += 1
        elif status == AssignmentStatus.IN_PROGRESS:
            in_progress += 1
        
        if deadline:
            try:
                deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                now_tz = now.replace(tzinfo=deadline_dt.tzinfo)
                hours_remaining = (deadline_dt - now_tz).total_seconds() / 3600
                
                if hours_remaining < 0:
                    breached += 1
                elif hours_remaining < 4:
                    at_risk += 1
            except:
                pass
    
    return {
        'user_email': user_email,
        'total_active': len(assignments),
        'pending': pending,
        'in_progress': in_progress,
        'at_risk': at_risk,
        'breached': breached,
        'capacity_used': len(assignments)
    }


# =============================================================================
# TRAINING METADATA
# =============================================================================

def collect_training_metadata(document_id: str, org_id: str) -> Dict:
    """Collect training metadata for a document (for ML improvement)"""
    doc = get_document(document_id, org_id)
    if not doc:
        return {}
    
    decisions = get_decision_trail(document_id, org_id)
    conversations = get_ai_conversations_for_document(document_id)
    
    return {
        'document_id': document_id,
        'organization_id': org_id,
        'jurisdiction': doc.get('jurisdiction'),
        'risk_score': doc.get('risk_score'),
        'violations_count': doc.get('violations_count'),
        'compliance_outcome': doc.get('compliance_outcome'),
        'final_status': doc.get('status'),
        'ai_overrides': len([d for d in decisions if d.get('is_ai_override')]),
        'human_decisions': len(decisions),
        'ai_conversations': len(conversations),
        'time_to_decision_hours': decisions[0].get('time_to_decision_hours') if decisions else None,
    }

# =============================================================================
# MODELS
# =============================================================================

class Document:
    """Document model for database operations"""
    
    def __init__(self, data: Dict = None):
        self.data = data or {}
        self.id = self.data.get('id')
        self.type = self.data.get('type', 'document')
        self.organization_id = self.data.get('organization_id')
        
    def to_dict(self) -> Dict:
        """Convert to dictionary for database operations"""
        return self.data.copy()
    
    @classmethod
    def create(cls, data: Dict) -> 'Document':
        """Create a new Document instance"""
        return cls(data)
    
    def save(self) -> 'Document':
        """Save document to database"""
        if not self.organization_id:
            raise ValueError("organization_id required")
        
        container = get_container('documents')
        
        if not self.data.get('id'):
            self.data['id'] = str(uuid.uuid4())
            self.id = self.data['id']
        
        self.data['type'] = 'document'
        self.data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        if not self.data.get('created_at'):
            self.data['created_at'] = self.data['updated_at']
        
        result = container.create_item(body=self.data)
        return Document(result)
    
    def update(self, updates: Dict) -> 'Document':
        """Update document in database"""
        if not self.id:
            raise ValueError("Document ID required")
        
        self.data.update(updates)
        self.data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        container = get_container('documents')
        result = container.upsert_item(self.data)
        return Document(result)
    
    @staticmethod
    def get_by_id(document_id: str, org_id: str = None) -> Optional['Document']:
        """Get document by ID"""
        doc_data = get_document(document_id, org_id)
        return Document(doc_data) if doc_data else None


class AIConversationModel:
    """AI Conversation model for database operations"""
    
    def __init__(self, data: Dict = None):
        self.data = data or {}
        self.id = self.data.get('id')
        self.document_id = self.data.get('document_id')
        self.organization_id = self.data.get('organization_id')
        
    def to_dict(self) -> Dict:
        """Convert to dictionary for database operations"""
        return self.data.copy()
    
    @classmethod
    def create(cls, data: Dict) -> 'AIConversationModel':
        """Create a new AIConversation instance"""
        return cls(data)
    
    def save(self) -> 'AIConversationModel':
        """Save AI conversation to database"""
        if not self.document_id:
            raise ValueError("document_id required")
        
        container = get_container('ai_conversations')
        
        if not self.data.get('id'):
            self.data['id'] = f"conv_{uuid.uuid4().hex[:12]}"
            self.id = self.data['id']
        
        self.data['type'] = 'ai_conversation'
        self.data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        
        if not self.data.get('created_at'):
            self.data['created_at'] = self.data['updated_at']
        
        result = container.create_item(body=self.data)
        return AIConversationModel(result)
    
    @staticmethod
    def get_by_document(document_id: str, limit: int = 50) -> List[Dict]:
        """Get AI conversations for a document"""
        return get_ai_conversations_for_document(document_id, limit)


# Export the models
__all__ = [
    'get_db', 'get_container', 'get_cosmos_container',
    'create_organization', 'get_organization', 'update_organization', 'get_organization_by_tenant',
    'create_user', 'get_user', 'get_user_by_email', 'get_users_by_org', 'get_users_by_role', 'update_user',
    'get_user_activity', 'get_decisions_by_user', 'get_org_analytics_summary',
    'create_document', 'get_document', 'get_document_with_access_check', 'update_document', 'list_documents_by_organization',
    'create_team', 'get_team', 'get_teams_by_org', 'update_team', 'get_user_teams',
    'create_notification', 'get_user_notifications', 'mark_notification_read',
    'log_action', 'get_audit_logs',
    'log_activity', 'get_activity_feed',
    'save_decision_trail', 'get_decision_trail',
    'save_analytics_event', 'get_analytics_events',
    'save_ai_conversation', 'get_ai_conversations_for_document',
    'get_user_assignments', 'get_team_assignments', 'get_unassigned_documents', 'get_user_workload',
    'collect_training_metadata',
    # Models
    'Document',
    'AIConversationModel'
]