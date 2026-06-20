"""
Microsoft Entra ID (Azure AD) Authentication for SaaS
=====================================================
UPDATED: Added Legal role, clearer permission structure, marketplace org handling

Setup required in Azure Portal:
1. Register app in Entra ID
2. Expose an API (set Application ID URI)
3. Configure API permissions
4. Set up app roles (see APP_ROLES below)
"""

import azure.functions as func
import logging
import os
import jwt
import requests
from functools import wraps
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class EntraConfig:
    """Configuration for Microsoft Entra ID"""
    
    TENANT_ID = os.getenv('AZURE_TENANT_ID', '')
    CLIENT_ID = os.getenv('AZURE_CLIENT_ID', '')
    CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET', '')
    
    # Entra External ID tenant — all self-service users share this tid
    EXTERNAL_ID_TENANT_ID = os.getenv('ENTRA_EXTERNAL_ID_TENANT_ID', '')
    
    # For multi-tenant SaaS, use 'common' or 'organizations'
    AUTHORITY = os.getenv('AZURE_AUTHORITY', 'https://login.microsoftonline.com/common')
    
    # FIXED: Accept BOTH audience formats
    @classmethod
    def get_valid_audiences(cls) -> List[str]:
        """Return all valid audience values"""
        audiences = [
            cls.CLIENT_ID,
            f"api://{cls.CLIENT_ID}",
        ]
        custom_uri = os.getenv('AZURE_API_URI', '')
        if custom_uri:
            audiences.append(custom_uri)
        return [a for a in audiences if a]
    
    JWKS_URL = 'https://login.microsoftonline.com/common/discovery/v2.0/keys'
    
    _jwks_cache = None
    _jwks_cache_time = None
    JWKS_CACHE_DURATION = timedelta(hours=24)


class SubscriptionTier(Enum):
    """SaaS subscription tiers"""
    TRIAL = "trial"
    BASIC = "basic"
    CORE = "core"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class AppRole(Enum):
    """
    Application roles - configure these in Entra ID App Registration
    
    Role Hierarchy:
    ---------------
    Platform.SuperAdmin  - Anthropic/Platform team - can do everything across all orgs
    Organization.Admin   - Org admin - manages their org's users, settings, workflows
    Legal.Advisor        - Legal team - reviews escalated documents, provides advisory
    DLAPiper.Advisory    - External advisory (DLA Piper) - reviews escalated complex cases
    Compliance.Officer   - Compliance team lead - approves/rejects, escalates
    Compliance.Reviewer  - Compliance reviewer - reviews assigned docs, can approve/reject
    Marketing.User       - Standard user - uploads docs, views own docs
    Marketing.Viewer     - Read-only - can only view approved docs
    """
    # Platform level (cross-org)
    SUPER_ADMIN = "Platform.SuperAdmin"
    
    # Organization level
    ADMIN = "Organization.Admin"
    
    # Legal & Advisory
    LEGAL = "Legal.Advisor"
    DLA_PIPER = "DLAPiper.Advisory"
    
    # Compliance team
    COMPLIANCE = "Compliance.Officer"
    COMPLIANCE_REVIEWER = "Compliance.Reviewer"
    
    # Marketing/Standard users
    MARKETING = "Marketing.User"
    MARKETING_VIEWER = "Marketing.Viewer"


# =============================================================================
# PERMISSION DEFINITIONS
# =============================================================================

class Permission(Enum):
    """Granular permissions"""
    # Document permissions
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_VIEW_OWN = "document:view:own"
    DOCUMENT_VIEW_ALL = "document:view:all"
    DOCUMENT_DELETE_OWN = "document:delete:own"
    DOCUMENT_DELETE_ALL = "document:delete:all"
    
    # Scan permissions
    SCAN_INITIATE = "scan:initiate"
    SCAN_VIEW_RESULTS = "scan:view:results"
    
    # Approval permissions
    APPROVAL_SUBMIT = "approval:submit"
    APPROVAL_APPROVE = "approval:approve"
    APPROVAL_REJECT = "approval:reject"
    APPROVAL_ESCALATE = "approval:escalate"
    APPROVAL_ESCALATE_TO_LEGAL = "approval:escalate:legal"
    
    # Assignment permissions
    ASSIGNMENT_VIEW_OWN = "assignment:view:own"
    ASSIGNMENT_VIEW_TEAM = "assignment:view:team"
    ASSIGNMENT_ASSIGN = "assignment:assign"
    ASSIGNMENT_REASSIGN = "assignment:reassign"
    
    # Team permissions
    TEAM_VIEW = "team:view"
    TEAM_CREATE = "team:create"
    TEAM_MANAGE = "team:manage"
    
    # User management
    USER_VIEW_ORG = "user:view:org"
    USER_INVITE = "user:invite"
    USER_MANAGE = "user:manage"
    USER_CHANGE_ROLES = "user:change:roles"
    
    # Analytics
    ANALYTICS_VIEW_OWN = "analytics:view:own"
    ANALYTICS_VIEW_ORG = "analytics:view:org"
    ANALYTICS_VIEW_PLATFORM = "analytics:view:platform"
    
    # Settings
    SETTINGS_VIEW = "settings:view"
    SETTINGS_MANAGE = "settings:manage"
    
    # Custom rules
    RULES_VIEW = "rules:view"
    RULES_CREATE = "rules:create"
    RULES_MANAGE = "rules:manage"
    
    # Audit
    AUDIT_VIEW_OWN = "audit:view:own"
    AUDIT_VIEW_ORG = "audit:view:org"
    AUDIT_EXPORT = "audit:export"
    
    # Legal specific
    LEGAL_REVIEW = "legal:review"
    LEGAL_ADVISE = "legal:advise"
    
    # Platform admin
    PLATFORM_MANAGE_ORGS = "platform:manage:orgs"
    PLATFORM_VIEW_USAGE = "platform:view:usage"
    PLATFORM_MANAGE_BILLING = "platform:manage:billing"


# Role -> Permissions mapping
ROLE_PERMISSIONS: Dict[AppRole, List[Permission]] = {
    AppRole.SUPER_ADMIN: list(Permission),  # All permissions
    
    AppRole.ADMIN: [
        # Documents
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_VIEW_ALL,
        Permission.DOCUMENT_DELETE_ALL,
        # Scans
        Permission.SCAN_INITIATE,
        Permission.SCAN_VIEW_RESULTS,
        # Approvals
        Permission.APPROVAL_SUBMIT,
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE,
        Permission.APPROVAL_ESCALATE_TO_LEGAL,
        # Assignments
        Permission.ASSIGNMENT_VIEW_TEAM,
        Permission.ASSIGNMENT_ASSIGN,
        Permission.ASSIGNMENT_REASSIGN,
        # Teams
        Permission.TEAM_VIEW,
        Permission.TEAM_CREATE,
        Permission.TEAM_MANAGE,
        # Users
        Permission.USER_VIEW_ORG,
        Permission.USER_INVITE,
        Permission.USER_MANAGE,
        Permission.USER_CHANGE_ROLES,
        # Analytics
        Permission.ANALYTICS_VIEW_ORG,
        # Settings
        Permission.SETTINGS_VIEW,
        Permission.SETTINGS_MANAGE,
        # Rules
        Permission.RULES_VIEW,
        Permission.RULES_CREATE,
        Permission.RULES_MANAGE,
        # Audit
        Permission.AUDIT_VIEW_ORG,
        Permission.AUDIT_EXPORT,
    ],
    
    AppRole.LEGAL: [
        # Documents (view all for review)
        Permission.DOCUMENT_VIEW_ALL,
        # Scans
        Permission.SCAN_VIEW_RESULTS,
        # Approvals (legal can approve/reject escalated)
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        # Legal specific
        Permission.LEGAL_REVIEW,
        Permission.LEGAL_ADVISE,
        # Assignments
        Permission.ASSIGNMENT_VIEW_TEAM,
        # Analytics
        Permission.ANALYTICS_VIEW_ORG,
        # Audit
        Permission.AUDIT_VIEW_ORG,
    ],
    
    AppRole.DLA_PIPER: [
        # Documents (only escalated to them)
        Permission.DOCUMENT_VIEW_ALL,
        # Scans
        Permission.SCAN_VIEW_RESULTS,
        # Approvals
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        # Legal specific
        Permission.LEGAL_REVIEW,
        Permission.LEGAL_ADVISE,
        # Audit
        Permission.AUDIT_VIEW_ORG,
    ],
    
    AppRole.COMPLIANCE: [
        # Documents
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_VIEW_ALL,
        # Scans
        Permission.SCAN_INITIATE,
        Permission.SCAN_VIEW_RESULTS,
        # Approvals
        Permission.APPROVAL_SUBMIT,
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE,
        Permission.APPROVAL_ESCALATE_TO_LEGAL,
        # Assignments
        Permission.ASSIGNMENT_VIEW_TEAM,
        Permission.ASSIGNMENT_ASSIGN,
        Permission.ASSIGNMENT_REASSIGN,
        # Teams
        Permission.TEAM_VIEW,
        # Analytics
        Permission.ANALYTICS_VIEW_ORG,
        # Rules
        Permission.RULES_VIEW,
        # Audit
        Permission.AUDIT_VIEW_ORG,
    ],
    
    AppRole.COMPLIANCE_REVIEWER: [
        # Documents
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_VIEW_ALL,
        # Scans
        Permission.SCAN_INITIATE,
        Permission.SCAN_VIEW_RESULTS,
        # Approvals
        Permission.APPROVAL_SUBMIT,
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        Permission.APPROVAL_ESCALATE,
        # Assignments (view own + team)
        Permission.ASSIGNMENT_VIEW_OWN,
        Permission.ASSIGNMENT_VIEW_TEAM,
        # Teams
        Permission.TEAM_VIEW,
        # Analytics
        Permission.ANALYTICS_VIEW_OWN,
        # Rules
        Permission.RULES_VIEW,
        # Audit
        Permission.AUDIT_VIEW_OWN,
    ],
    
    AppRole.MARKETING: [
        # Documents
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_VIEW_OWN,
        Permission.DOCUMENT_DELETE_OWN,
        # Scans
        Permission.SCAN_INITIATE,
        Permission.SCAN_VIEW_RESULTS,
        # Approvals
        Permission.APPROVAL_SUBMIT,
        # Assignments
        Permission.ASSIGNMENT_VIEW_OWN,
        # Analytics
        Permission.ANALYTICS_VIEW_OWN,
        # Audit
        Permission.AUDIT_VIEW_OWN,
    ],
    
    AppRole.MARKETING_VIEWER: [
        # Documents (read-only, approved only)
        Permission.DOCUMENT_VIEW_OWN,
        # Scans
        Permission.SCAN_VIEW_RESULTS,
        # Analytics
        Permission.ANALYTICS_VIEW_OWN,
    ],
}


def get_permissions_for_roles(roles: List[str]) -> List[str]:
    """Get all permissions for a list of role strings"""
    permissions = set()
    for role_str in roles:
        try:
            role = AppRole(role_str)
            role_perms = ROLE_PERMISSIONS.get(role, [])
            permissions.update(p.value for p in role_perms)
        except ValueError:
            continue
    return list(permissions)


def has_permission(user_roles: List[str], permission: Permission) -> bool:
    """Check if any of the user's roles grant a specific permission"""
    for role_str in user_roles:
        try:
            role = AppRole(role_str)
            if permission in ROLE_PERMISSIONS.get(role, []):
                return True
        except ValueError:
            continue
    return False


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class AuthenticatedUser:
    """Validated user from JWT token"""
    user_id: str
    email: str
    name: str
    tenant_id: str
    roles: List[str]
    permissions: List[str]
    subscription_tier: SubscriptionTier
    organization_id: str
    organization_name: str
    token_expires: datetime
    raw_claims: Dict
    
    def has_role(self, role: AppRole) -> bool:
        return role.value in self.roles
    
    def has_any_role(self, *roles: AppRole) -> bool:
        return any(r.value in self.roles for r in roles)
    
    def has_permission(self, permission: Permission) -> bool:
        return permission.value in self.permissions
    
    def is_compliance_or_admin(self) -> bool:
        return self.has_any_role(
            AppRole.COMPLIANCE, 
            AppRole.COMPLIANCE_REVIEWER,
            AppRole.ADMIN,
            AppRole.SUPER_ADMIN
        )
    
    def is_legal_team(self) -> bool:
        return self.has_any_role(AppRole.LEGAL, AppRole.DLA_PIPER)
    
    def is_platform_admin(self) -> bool:
        return self.has_role(AppRole.SUPER_ADMIN)
    
    def is_org_admin(self) -> bool:
        return self.has_any_role(AppRole.ADMIN, AppRole.SUPER_ADMIN)
    
    def can_review_escalated(self) -> bool:
        """Can review documents escalated to legal"""
        return self.has_any_role(AppRole.LEGAL, AppRole.DLA_PIPER, AppRole.SUPER_ADMIN)
    
    def can_access_document(self, doc_organization_id: str) -> bool:
        if self.has_role(AppRole.SUPER_ADMIN):
            return True
        if self.has_role(AppRole.DLA_PIPER):
            return True  # DLA Piper can see escalated from any org
        return self.organization_id == doc_organization_id


@dataclass  
class Organization:
    """Tenant/Organization in your SaaS"""
    id: str
    name: str
    azure_tenant_id: str
    subscription_tier: SubscriptionTier
    subscription_expires: datetime
    jurisdictions: List[str]
    custom_rules_enabled: bool
    advisory_hours_remaining: float
    created_at: datetime
    settings: Dict


# =============================================================================
# JWT TOKEN VALIDATION
# =============================================================================

class TokenValidationError(Exception):
    """Raised when token validation fails"""
    pass


def get_jwks_keys() -> Dict:
    """Fetch and cache JWKS from Microsoft"""
    now = datetime.utcnow()
    
    if (EntraConfig._jwks_cache and EntraConfig._jwks_cache_time and 
        now - EntraConfig._jwks_cache_time < EntraConfig.JWKS_CACHE_DURATION):
        return EntraConfig._jwks_cache
    
    try:
        response = requests.get(EntraConfig.JWKS_URL, timeout=10)
        response.raise_for_status()
        
        EntraConfig._jwks_cache = response.json()
        EntraConfig._jwks_cache_time = now
        
        logger.info(f"✅ JWKS keys refreshed: {len(EntraConfig._jwks_cache.get('keys', []))} keys")
        return EntraConfig._jwks_cache
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch JWKS: {e}")
        if EntraConfig._jwks_cache:
            logger.warning("⚠️ Using expired JWKS cache")
            return EntraConfig._jwks_cache
        raise TokenValidationError(f"Cannot fetch signing keys: {e}")


def get_signing_key(token: str) -> str:
    """Extract the correct signing key for a token"""
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get('kid')
        
        if not kid:
            raise TokenValidationError("Token missing key ID (kid)")
        
        jwks = get_jwks_keys()
        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                from jwt.algorithms import RSAAlgorithm
                return RSAAlgorithm.from_jwk(key)
        
        raise TokenValidationError(f"No matching key found for kid: {kid}")
        
    except jwt.exceptions.DecodeError as e:
        raise TokenValidationError(f"Invalid token format: {e}")


def validate_token(token: str) -> Dict:
    """Validate a Microsoft Entra ID JWT token"""
    try:
        signing_key = get_signing_key(token)
        valid_audiences = EntraConfig.get_valid_audiences()
        
        if not valid_audiences:
            raise TokenValidationError("No valid audiences configured. Set AZURE_CLIENT_ID.")
        
        unverified = jwt.decode(token, options={"verify_signature": False})
        token_audience = unverified.get('aud', '')
        
        if token_audience not in valid_audiences:
            logger.error(f"❌ Audience mismatch! Token: {token_audience}, Expected: {valid_audiences}")
            raise TokenValidationError(f"Invalid audience: {token_audience}")
        
        decoded = jwt.decode(
            token,
            signing_key,
            algorithms=['RS256'],
            audience=valid_audiences,
            options={
                'verify_exp': True,
                'verify_iat': True,
                'verify_aud': True,
                'verify_iss': False,
            }
        )
        
        issuer = decoded.get('iss', '')
        valid_issuers = [
            'https://login.microsoftonline.com/',
            'https://sts.windows.net/',
        ]
        if not any(issuer.startswith(vi) for vi in valid_issuers):
            raise TokenValidationError(f"Invalid issuer: {issuer}")
        
        logger.info(f"✅ Token validated for: {decoded.get('preferred_username', decoded.get('sub', 'service'))}")
        return decoded
        
    except jwt.ExpiredSignatureError:
        raise TokenValidationError("Token has expired")
    except jwt.InvalidAudienceError as e:
        raise TokenValidationError(f"Invalid audience: {e}")
    except jwt.InvalidTokenError as e:
        raise TokenValidationError(f"Invalid token: {e}")


# =============================================================================
# ORGANIZATION & USER MANAGEMENT
# =============================================================================

def get_or_create_organization(tenant_id: str, claims: Dict) -> Organization:
    """
    Dual-track org resolution.

    Enterprise path:  tid = company's own Azure AD tenant → 1 org per tenant
    Self-service path: tid = our External ID tenant       → 1 org per user (oid)
    Marketplace path:  org already created during landing page flow

    Marketplace customers authenticate with their company Azure AD.
    Their org was already provisioned when they clicked "Configure Account"
    in the Azure Portal. So they'll hit the enterprise path, but we need
    to check if a marketplace org already exists for their tenant.
    """
    external_tid = EntraConfig.EXTERNAL_ID_TENANT_ID

    if external_tid and tenant_id == external_tid:
        return _get_or_create_selfservice_org(claims)
    else:
        return _get_or_create_enterprise_org(tenant_id, claims)


def _get_or_create_enterprise_org(tenant_id: str, claims: Dict) -> Organization:
    """
    Enterprise + Marketplace org lookup.

    First checks for a marketplace org linked to this tenant,
    then falls back to standard enterprise org creation.
    """
    from function_app_pkg.core.database import get_container
    import uuid

    container = get_container('organizations')

    # Check for ANY org with this tenant (enterprise or marketplace)
    items = list(container.query_items(
        query=(
            "SELECT * FROM c "
            "WHERE c.type = 'organization' "
            "AND c.azure_tenant_id = @tid"
        ),
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))

    if items:
        d = items[0]
        return Organization(
            id=d['id'],
            name=d['name'],
            azure_tenant_id=d.get('azure_tenant_id', ''),
            subscription_tier=SubscriptionTier(d.get('subscription_tier', 'enterprise')),
            subscription_expires=datetime(2099, 12, 31),
            jurisdictions=d.get('jurisdictions', ['UK']),
            custom_rules_enabled=d.get('custom_rules_enabled', True),
            advisory_hours_remaining=d.get('advisory_hours_remaining', 999999),
            created_at=datetime.fromisoformat(d['created_at'].replace('Z', '')),
            settings=d.get('settings', {}),
        )

    # First login from this tenant — create enterprise org
    new_org = {
        'id': str(uuid.uuid4()),
        'type': 'organization',
        'auth_type': 'entra_enterprise',
        'owner_oid': None,
        'name': claims.get('tenant_name', f"Organization-{tenant_id[:8]}"),
        'azure_tenant_id': tenant_id,
        'subscription_tier': 'enterprise',
        'subscription_status': 'active',
        'subscription_expires': datetime(2099, 12, 31).isoformat() + 'Z',
        'signup_completed': True,
        'jurisdictions': ['UK', 'ZA', 'US'],
        'custom_rules_enabled': True,
        'advisory_hours_remaining': 999999,
        # Marketplace / billing fields (safe defaults)
        'marketplace_subscription_id': None,
        'marketplace_offer_id': None,
        'marketplace_plan_id': None,
        'stripe_customer_id': None,
        'stripe_subscription_id': None,
        'scans_this_month': 0,
        'scans_per_month': -1,
        'max_users': -1,
        'payment_failed_at': None,
        'payment_failure_count': 0,
        'activated_at': datetime.utcnow().isoformat() + 'Z',
        'created_at': datetime.utcnow().isoformat() + 'Z',
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'settings': {},
    }

    container.create_item(body=new_org)
    logger.info(f"Enterprise org created: {new_org['id']} for tenant {tenant_id}")

    return Organization(
        id=new_org['id'],
        name=new_org['name'],
        azure_tenant_id=tenant_id,
        subscription_tier=SubscriptionTier.ENTERPRISE,
        subscription_expires=datetime(2099, 12, 31),
        jurisdictions=['UK', 'ZA', 'US'],
        custom_rules_enabled=True,
        advisory_hours_remaining=999999,
        created_at=datetime.utcnow(),
        settings={},
    )


def _get_or_create_selfservice_org(claims: Dict) -> Organization:
    """
    Self-service org lookup by user oid (for Entra External ID).

    Key: uses `oid` (user object ID) as the org owner, NOT `tid`.
    """
    from function_app_pkg.core.database import get_container
    import uuid

    user_oid = claims.get('oid', '')
    if not user_oid:
        raise TokenValidationError("External ID token missing 'oid' claim")

    container = get_container('organizations')

    items = list(container.query_items(
        query=(
            "SELECT * FROM c "
            "WHERE c.type = 'organization' "
            "AND c.auth_type = 'entra_external' "
            "AND c.owner_oid = @oid"
        ),
        parameters=[{"name": "@oid", "value": user_oid}],
        enable_cross_partition_query=True,
    ))

    if items:
        d = items[0]
        tier = d.get('subscription_tier', 'trial')
        return Organization(
            id=d['id'],
            name=d['name'],
            azure_tenant_id=d.get('azure_tenant_id', ''),
            subscription_tier=SubscriptionTier(tier),
            subscription_expires=datetime(2099, 12, 31),
            jurisdictions=d.get('jurisdictions', ['UK']),
            custom_rules_enabled=d.get('custom_rules_enabled', False),
            advisory_hours_remaining=d.get('advisory_hours_remaining', 0),
            created_at=datetime.fromisoformat(d['created_at'].replace('Z', '')),
            settings=d.get('settings', {}),
        )

    # First login — create trial org
    email = claims.get('preferred_username', claims.get('email', ''))
    org_name = (
        claims.get('extension_OrgName')
        or claims.get('name', '')
        or (email.split('@')[0] + "'s Organisation" if email else 'New Organisation')
    )

    new_org = {
        'id': str(uuid.uuid4()),
        'type': 'organization',
        'auth_type': 'entra_external',
        'owner_oid': user_oid,
        'name': org_name,
        'azure_tenant_id': EntraConfig.EXTERNAL_ID_TENANT_ID,
        'subscription_tier': 'trial',
        'subscription_status': 'trialing',
        'subscription_expires': datetime(2099, 12, 31).isoformat() + 'Z',
        'signup_completed': False,
        'marketplace_subscription_id': None,
        'marketplace_offer_id': None,
        'marketplace_plan_id': None,
        'stripe_customer_id': None,
        'stripe_subscription_id': None,
        'jurisdictions': ['UK'],
        'custom_rules_enabled': False,
        'advisory_hours_remaining': 0,
        'scans_this_month': 0,
        'scans_per_month': 10,
        'max_users': 3,
        'payment_failed_at': None,
        'payment_failure_count': 0,
        'activated_at': None,
        'created_at': datetime.utcnow().isoformat() + 'Z',
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'settings': {},
    }

    container.create_item(body=new_org)
    logger.info(f"Self-service trial org created: {new_org['id']} (oid {user_oid})")

    return Organization(
        id=new_org['id'],
        name=new_org['name'],
        azure_tenant_id=EntraConfig.EXTERNAL_ID_TENANT_ID,
        subscription_tier=SubscriptionTier.TRIAL,
        subscription_expires=datetime(2099, 12, 31),
        jurisdictions=['UK'],
        custom_rules_enabled=False,
        advisory_hours_remaining=0,
        created_at=datetime.utcnow(),
        settings={},
    )


def get_or_create_user(claims: Dict, organization: Organization) -> Dict:
    """Get existing user or create from Entra claims"""
    from function_app_pkg.core.database import get_user_by_email, create_user, update_user
    import uuid
    
    email = claims.get('preferred_username', 
                       claims.get('email',
                       claims.get('azp',
                       claims.get('sub', 'unknown')))).lower()
    
    is_service_principal = 'preferred_username' not in claims and 'email' not in claims
    
    if is_service_principal:
        app_id = claims.get('azp', claims.get('sub', 'unknown'))
        email = f"service-principal-{app_id}@{organization.azure_tenant_id}"
        name = f"Service Principal ({app_id[:8]}...)"
    else:
        name = claims.get('name', email.split('@')[0])
    
    existing_user = get_user_by_email(email)
    
    # Get roles from token
    entra_roles = claims.get('roles', [])
    
    logger.info(f"🎫 Token claims for {email}: roles={entra_roles}")
    
    if existing_user:
        # Update roles from token every login
        existing_user['roles'] = entra_roles if entra_roles else [AppRole.MARKETING.value]
        update_user(existing_user['id'], {'roles': existing_user['roles']}, organization.id)
        logger.info(f"✅ Updated existing user {email} with roles: {existing_user['roles']}")
        return existing_user
    
    # Service principals get admin role by default
    if is_service_principal and not entra_roles:
        entra_roles = [AppRole.SUPER_ADMIN.value]
    
    # Default role if none in token
    if not entra_roles:
        logger.warning(f"⚠️ No roles in token for {email}, using default Marketing.User")
        entra_roles = [AppRole.MARKETING.value]
    
    new_user = {
        'id': str(uuid.uuid4()),
        'type': 'user',
        'azure_oid': claims.get('oid', claims.get('sub')),
        'email': email,
        'name': name,
        'roles': entra_roles,
        'organization_id': organization.id,
        'organization_name': organization.name,
        'azure_tenant_id': claims.get('tid'),
        'is_service_principal': is_service_principal,
        'created_at': datetime.utcnow().isoformat() + "Z",
        'last_login': datetime.utcnow().isoformat() + "Z",
        'is_active': True
    }
    
    create_user(new_user)
    logger.info(f"✅ Created user: {email} with roles: {entra_roles}")
    
    return new_user


# =============================================================================
# REQUEST AUTHENTICATION
# =============================================================================

def authenticate_request(req: func.HttpRequest) -> Tuple[Optional[AuthenticatedUser], Optional[str]]:
    """Authenticate an incoming request"""
    auth_header = (
            req.headers.get('Authorization') or 
            req.headers.get('authorization') or
            req.headers.get('AUTHORIZATION') or
            ''
        )
    if not auth_header:
        return None, "Missing Authorization header"
    
    if not auth_header.startswith('Bearer '):
        return None, "Invalid Authorization header format. Expected: Bearer <token>"
    
    token = auth_header[7:]
    
    if not token:
        return None, "Empty token"
    
    try:
        claims = validate_token(token)
        
        tenant_id = claims.get('tid')
        if not tenant_id:
            issuer = claims.get('iss', '')
            if '/v2.0' in issuer:
                tenant_id = issuer.split('/')[-2]
            else:
                tenant_id = issuer.split('/')[-1] if '/' in issuer else None
        
        if not tenant_id:
            return None, "Token missing tenant ID"
        
        organization = get_or_create_organization(tenant_id, claims)
        user_data = get_or_create_user(claims, organization)
        
        # Calculate permissions from roles
        user_permissions = get_permissions_for_roles(user_data.get('roles', []))
        
        auth_user = AuthenticatedUser(
            user_id=user_data['id'],
            email=user_data['email'],
            name=user_data['name'],
            tenant_id=tenant_id,
            roles=user_data.get('roles', []),
            permissions=user_permissions,
            subscription_tier=organization.subscription_tier,
            organization_id=organization.id,
            organization_name=organization.name,
            token_expires=datetime.fromtimestamp(claims.get('exp', 0)),
            raw_claims=claims
        )
        
        return auth_user, None
        
    except TokenValidationError as e:
        logger.warning(f"⚠️ Token validation failed: {e}")
        return None, str(e)
    except Exception as e:
        logger.error(f"❌ Auth error: {e}", exc_info=True)
        return None, "Authentication failed"


# =============================================================================
# DECORATORS
# =============================================================================

def require_auth(handler):
    """Decorator to require authentication"""
    @wraps(handler)
    def wrapper(req: func.HttpRequest) -> func.HttpResponse:
        from function_app_pkg.shared.http_utils import json_response
        
        user, error = authenticate_request(req)
        if error:
            return json_response(401, error=error)
        
        return handler(req, user)
    return wrapper


def require_role(*required_roles: AppRole):
    """Decorator to require specific role(s)"""
    def decorator(handler):
        @wraps(handler)
        def wrapper(req: func.HttpRequest) -> func.HttpResponse:
            from function_app_pkg.shared.http_utils import json_response
            
            user, error = authenticate_request(req)
            if error:
                return json_response(401, error=error)
            
            user_roles = set(user.roles)
            required = {r.value for r in required_roles}
            
            # Super admin always has access
            if AppRole.SUPER_ADMIN.value in user_roles:
                return handler(req, user)
            
            if not user_roles.intersection(required):
                return json_response(403, error=f"Required role: {', '.join(required)}")
            
            return handler(req, user)
        return wrapper
    return decorator


def require_permission(*required_permissions: Permission):
    """Decorator to require specific permission(s)"""
    def decorator(handler):
        @wraps(handler)
        def wrapper(req: func.HttpRequest) -> func.HttpResponse:
            from function_app_pkg.shared.http_utils import json_response
            
            user, error = authenticate_request(req)
            if error:
                return json_response(401, error=error)
            
            # Check if user has all required permissions
            for perm in required_permissions:
                if not user.has_permission(perm):
                    return json_response(403, error=f"Missing permission: {perm.value}")
            
            return handler(req, user)
        return wrapper
    return decorator


def require_subscription(*allowed_tiers: SubscriptionTier):
    """Decorator to require subscription tier"""
    def decorator(handler):
        @wraps(handler)
        def wrapper(req: func.HttpRequest) -> func.HttpResponse:
            from function_app_pkg.shared.http_utils import json_response
            
            user, error = authenticate_request(req)
            if error:
                return json_response(401, error=error)
            
            if user.subscription_tier not in allowed_tiers:
                return json_response(403, error=f"Requires {', '.join(t.value for t in allowed_tiers)} subscription")
            
            return handler(req, user)
        return wrapper
    return decorator


# =============================================================================
# HANDLERS
# =============================================================================

def handle_login(req: func.HttpRequest) -> func.HttpResponse:
    """Validate token and return user info"""
    from function_app_pkg.shared.http_utils import json_response
    
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return json_response(200, {
        "authenticated": True,
        "user": {
            "user_id": user.user_id,
            "email": user.email,
            "name": user.name,
            "roles": user.roles,
            "permissions": user.permissions,
            "organization": {
                "id": user.organization_id,
                "name": user.organization_name,
                "subscription_tier": user.subscription_tier.value
            }
        }
    })


def handle_me(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """Get current user profile with permissions"""
    from function_app_pkg.shared.http_utils import json_response

    if user is None:
        user, error = authenticate_request(req)
        if error:
            return json_response(401, error=error)

    return json_response(200, data={
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "roles": user.roles,
        "permissions": user.permissions,
        "organization_id": user.organization_id,
        "organization_name": user.organization_name,
        "subscription_tier": user.subscription_tier.value,
        "capabilities": {
            "can_upload": user.has_permission(Permission.DOCUMENT_UPLOAD),
            "can_scan": user.has_permission(Permission.SCAN_INITIATE),
            "can_approve": user.has_permission(Permission.APPROVAL_APPROVE),
            "can_reject": user.has_permission(Permission.APPROVAL_REJECT),
            "can_escalate": user.has_permission(Permission.APPROVAL_ESCALATE),
            "can_escalate_to_legal": user.has_permission(Permission.APPROVAL_ESCALATE_TO_LEGAL),
            "can_manage_users": user.has_permission(Permission.USER_MANAGE),
            "can_manage_teams": user.has_permission(Permission.TEAM_MANAGE),
            "can_view_org_analytics": user.has_permission(Permission.ANALYTICS_VIEW_ORG),
            "can_review_legal": user.has_permission(Permission.LEGAL_REVIEW),
            "is_platform_admin": user.is_platform_admin(),
            "is_org_admin": user.is_org_admin(),
            "is_legal_team": user.is_legal_team(),
        }
    })


def verify_token(req: func.HttpRequest) -> func.HttpResponse:
    """Verify token is valid"""
    from function_app_pkg.shared.http_utils import json_response
    
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    return json_response(200, {"authenticated": True})


def verify_token_with_role(req: func.HttpRequest, required_role: str) -> func.HttpResponse:
    """Verify token and check role"""
    from function_app_pkg.shared.http_utils import json_response
    
    user, error = authenticate_request(req)
    if error:
        return json_response(401, error=error)
    
    role_mapping = {
        'compliance_officer': AppRole.COMPLIANCE.value,
        'admin': AppRole.ADMIN.value,
        'marketing': AppRole.MARKETING.value,
        'legal': AppRole.LEGAL.value,
    }
    
    mapped_role = role_mapping.get(required_role, required_role)
    
    if mapped_role not in user.roles and AppRole.SUPER_ADMIN.value not in user.roles:
        return json_response(403, error=f"Required role: {required_role}")
    
    return json_response(200, {"authenticated": True, "authorized": True})


# =============================================================================
# TENANT ISOLATION HELPERS
# =============================================================================

def filter_by_organization(query: str, user: AuthenticatedUser) -> Tuple[str, List[Dict]]:
    """Add organization filter for tenant isolation"""
    if user.has_role(AppRole.SUPER_ADMIN) or user.has_role(AppRole.DLA_PIPER):
        return query, []
    
    if 'WHERE' in query.upper():
        query += " AND c.organization_id = @org_id"
    else:
        query += " WHERE c.organization_id = @org_id"
    
    return query, [{"name": "@org_id", "value": user.organization_id}]


def check_document_access(doc: Dict, user: AuthenticatedUser) -> bool:
    """Check if user can access a document"""
    if user.has_role(AppRole.SUPER_ADMIN):
        return True
    if user.has_role(AppRole.DLA_PIPER):
        return doc.get('workflow_status') == 'dla_piper_review'
    if user.has_role(AppRole.LEGAL):
        return doc.get('workflow_status') in ['escalated', 'legal_review']
    return doc.get('organization_id') == user.organization_id


# =============================================================================
# BACKWARDS COMPATIBILITY
# =============================================================================

def _get_user_from_request(req: func.HttpRequest) -> Optional[Dict]:
    """Backwards compatible user extraction"""
    user, _ = authenticate_request(req)
    if user:
        return {
            'id': user.user_id,
            'email': user.email,
            'name': user.name,
            'role': user.roles[0] if user.roles else 'marketing',
            'organization_id': user.organization_id
        }
    return None


# Stub handlers for unused endpoints
def handle_register(req): 
    from function_app_pkg.shared.http_utils import json_response
    return json_response(400, error="Use Microsoft login instead")

def handle_refresh_token(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(400, error="Use MSAL refresh instead")

def handle_forgot_password(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(400, error="Use Microsoft account recovery")

def handle_reset_password(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(400, error="Use Microsoft account recovery")

def handle_get_profile(req): return handle_me(req)

def handle_update_profile(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(501, error="Not implemented")

def handle_change_password(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(400, error="Use Microsoft account settings")

def handle_list_users(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(501, error="Not implemented")

def handle_get_user(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(501, error="Not implemented")

def handle_update_user_roles(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(501, error="Not implemented")

def handle_update_user_status(req):
    from function_app_pkg.shared.http_utils import json_response
    return json_response(501, error="Not implemented")


# =============================================================================
# APP ROLES CONFIGURATION (For Azure Portal setup)
# =============================================================================

APP_ROLES_CONFIG = """
Configure these roles in Azure Portal > App Registration > App roles:

1. Platform.SuperAdmin
   - Display name: Platform Super Admin
   - Allowed member types: Users/Groups
   - Value: Platform.SuperAdmin
   - Description: Full platform access across all organizations

2. Organization.Admin
   - Display name: Organization Admin  
   - Allowed member types: Users/Groups
   - Value: Organization.Admin
   - Description: Manages organization users, settings, and workflows

3. Legal.Advisor
   - Display name: Legal Advisor
   - Allowed member types: Users/Groups
   - Value: Legal.Advisor
   - Description: Reviews escalated documents, provides legal advisory

4. DLAPiper.Advisory
   - Display name: DLA Piper Advisory
   - Allowed member types: Users/Groups
   - Value: DLAPiper.Advisory
   - Description: External legal advisory for complex escalations

5. Compliance.Officer
   - Display name: Compliance Officer
   - Allowed member types: Users/Groups
   - Value: Compliance.Officer
   - Description: Compliance team lead - approves, rejects, escalates

6. Compliance.Reviewer
   - Display name: Compliance Reviewer
   - Allowed member types: Users/Groups
   - Value: Compliance.Reviewer
   - Description: Reviews assigned documents, can approve/reject

7. Marketing.User
   - Display name: Marketing User
   - Allowed member types: Users/Groups
   - Value: Marketing.User
   - Description: Standard user - uploads docs, views own docs

8. Marketing.Viewer
   - Display name: Marketing Viewer
   - Allowed member types: Users/Groups
   - Value: Marketing.Viewer
   - Description: Read-only access to approved documents
"""