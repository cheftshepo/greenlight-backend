"""
DATABASE MIGRATION SCRIPT
=========================
Run this to update existing documents with new team collaboration fields.
Also creates recommended indexes.

Usage:
    python -m function_app_pkg.core.migrate_database

Or call migrate_all() from your code.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

def get_cosmos_client():
    """Get Cosmos DB client for migration"""
    from azure.cosmos import CosmosClient
    
    endpoint = os.getenv('COSMOS_ENDPOINT')
    key = os.getenv('COSMOS_KEY')
    database_name = os.getenv('COSMOS_DATABASE', 'compliance-db')
    
    client = CosmosClient(endpoint, key)
    return client.get_database_client(database_name)


def migrate_documents(database) -> Dict:
    """Add new team collaboration fields to existing documents"""
    container = database.get_container_client('documents')
    
    # Query all documents
    query = "SELECT * FROM c WHERE c.type = 'document'"
    documents = list(container.query_items(
        query=query,
        enable_cross_partition_query=True
    ))
    
    updated = 0
    skipped = 0
    errors = 0
    
    new_fields = {
        'watchers': [],
        'discussions': [],
        'team_id': '',
        'team_name': '',
        'assigned_to': '',
        'assigned_to_name': '',
        'assigned_by': '',
        'assigned_at': None,
        'assignment_status': 'unassigned',
        'assignment_priority': 'medium',
        'assignment_deadline': None,
        'assignment_sla_hours': None,
        'assignment_notes': [],
        'assignment_reason': '',
        'ticket_id': '',
        'handoff_history': [],
        'last_activity_at': None,
    }
    
    for doc in documents:
        try:
            needs_update = False
            
            for field, default_value in new_fields.items():
                if field not in doc:
                    doc[field] = default_value
                    needs_update = True
            
            # Set last_activity_at if not present
            if not doc.get('last_activity_at'):
                doc['last_activity_at'] = doc.get('updated_at') or doc.get('created_at') or datetime.utcnow().isoformat() + 'Z'
                needs_update = True
            
            if needs_update:
                container.upsert_item(doc)
                updated += 1
                logger.info(f"✅ Updated document: {doc['id']}")
            else:
                skipped += 1
                
        except Exception as e:
            errors += 1
            logger.error(f"❌ Failed to update document {doc.get('id')}: {e}")
    
    return {
        'total': len(documents),
        'updated': updated,
        'skipped': skipped,
        'errors': errors
    }


def migrate_users(database) -> Dict:
    """Add notification settings to existing users"""
    container = database.get_container_client('users')
    
    query = "SELECT * FROM c WHERE c.type = 'user'"
    users = list(container.query_items(
        query=query,
        enable_cross_partition_query=True
    ))
    
    updated = 0
    skipped = 0
    errors = 0
    
    default_notification_settings = {
        'email_on_assignment': True,
        'email_on_mention': True,
        'email_on_approval': True,
        'email_on_rejection': True,
        'email_on_escalation': True,
        'email_digest': 'daily',  # none, daily, weekly
        'push_notifications': True,
    }
    
    for user in users:
        try:
            needs_update = False
            
            if 'notification_settings' not in user:
                user['notification_settings'] = default_notification_settings
                needs_update = True
            else:
                # Add any missing notification settings
                for key, value in default_notification_settings.items():
                    if key not in user['notification_settings']:
                        user['notification_settings'][key] = value
                        needs_update = True
            
            # Add department and job_title if missing
            if 'department' not in user:
                user['department'] = ''
                needs_update = True
            
            if 'job_title' not in user:
                user['job_title'] = ''
                needs_update = True
            
            if needs_update:
                container.upsert_item(user)
                updated += 1
                logger.info(f"✅ Updated user: {user.get('email')}")
            else:
                skipped += 1
                
        except Exception as e:
            errors += 1
            logger.error(f"❌ Failed to update user {user.get('email')}: {e}")
    
    return {
        'total': len(users),
        'updated': updated,
        'skipped': skipped,
        'errors': errors
    }


def ensure_containers(database) -> Dict:
    """Ensure all required containers exist"""
    from azure.cosmos import PartitionKey, exceptions
    
    containers_config = {
        'organizations': '/id',
        'users': '/organization_id',
        'documents': '/organization_id',
        'rules': '/jurisdiction',
        'custom_rules': '/organization_id',
        'jurisdictions': '/id',
        'audit_logs': '/partition_key',
        'ai_conversations': '/document_id',
        'questionnaires': '/organization_id',
        'analytics': '/organization_id',
        'notifications': '/organization_id',  # New container (optional, can use audit_logs)
    }
    
    created = []
    existed = []
    
    for name, partition_key in containers_config.items():
        try:
            database.get_container_client(name).read()
            existed.append(name)
        except exceptions.CosmosResourceNotFoundError:
            try:
                database.create_container(
                    id=name,
                    partition_key=PartitionKey(path=partition_key)
                )
                created.append(name)
                logger.info(f"✅ Created container: {name}")
            except Exception as e:
                logger.error(f"❌ Failed to create container {name}: {e}")
    
    return {
        'created': created,
        'existed': existed
    }


def create_indexes(database) -> List[str]:
    """
    Document recommended indexes.
    
    NOTE: Cosmos DB creates indexes automatically based on indexing policy.
    These are recommendations for optimizing specific queries.
    """
    
    recommended_indexes = """
    ╔══════════════════════════════════════════════════════════════════════════════╗
    ║                    RECOMMENDED COSMOS DB INDEXING POLICY                     ║
    ╠══════════════════════════════════════════════════════════════════════════════╣
    ║                                                                              ║
    ║  DOCUMENTS CONTAINER                                                         ║
    ║  ───────────────────                                                         ║
    ║  Composite indexes for common queries:                                       ║
    ║                                                                              ║
    ║  1. Assignment queue query:                                                  ║
    ║     ["/organization_id ASC", "/type ASC", "/assignment_status ASC",          ║
    ║      "/assignment_priority ASC", "/assigned_at ASC"]                         ║
    ║                                                                              ║
    ║  2. Team documents query:                                                    ║
    ║     ["/organization_id ASC", "/type ASC", "/team_id ASC",                    ║
    ║      "/assignment_status ASC"]                                               ║
    ║                                                                              ║
    ║  3. User assignments query:                                                  ║
    ║     ["/organization_id ASC", "/type ASC", "/assigned_to ASC",                ║
    ║      "/assignment_status ASC"]                                               ║
    ║                                                                              ║
    ║  4. Pending approvals:                                                       ║
    ║     ["/organization_id ASC", "/type ASC", "/workflow_status ASC",            ║
    ║      "/status ASC"]                                                          ║
    ║                                                                              ║
    ║  AUDIT_LOGS CONTAINER                                                        ║
    ║  ─────────────────────                                                       ║
    ║  1. Activity feed:                                                           ║
    ║     ["/organization_id ASC", "/type ASC", "/timestamp DESC"]                 ║
    ║                                                                              ║
    ║  2. User notifications:                                                      ║
    ║     ["/organization_id ASC", "/type ASC", "/recipient_email ASC",            ║
    ║      "/read ASC", "/created_at DESC"]                                        ║
    ║                                                                              ║
    ║  3. Decision trail:                                                          ║
    ║     ["/organization_id ASC", "/type ASC", "/document_id ASC",                ║
    ║      "/created_at DESC"]                                                     ║
    ║                                                                              ║
    ╚══════════════════════════════════════════════════════════════════════════════╝
    
    To apply these, update your container's indexing policy in Azure Portal:
    
    1. Go to Azure Portal → Cosmos DB Account → Data Explorer
    2. Select the container → Settings → Indexing Policy
    3. Add composite indexes under "compositeIndexes" array
    
    Example indexing policy for documents container:
    
    {
      "indexingMode": "consistent",
      "automatic": true,
      "includedPaths": [
        { "path": "/*" }
      ],
      "excludedPaths": [
        { "path": "/extracted_text/*" },
        { "path": "/text_content/*" },
        { "path": "/_etag/?" }
      ],
      "compositeIndexes": [
        [
          { "path": "/type", "order": "ascending" },
          { "path": "/assignment_status", "order": "ascending" },
          { "path": "/assignment_priority", "order": "ascending" }
        ],
        [
          { "path": "/type", "order": "ascending" },
          { "path": "/team_id", "order": "ascending" },
          { "path": "/assignment_status", "order": "ascending" }
        ],
        [
          { "path": "/type", "order": "ascending" },
          { "path": "/assigned_to", "order": "ascending" },
          { "path": "/assignment_status", "order": "ascending" }
        ]
      ]
    }
    """
    
    print(recommended_indexes)
    return []


def migrate_all():
    """Run all migrations"""
    print("=" * 60)
    print("DATABASE MIGRATION FOR TEAM COLLABORATION")
    print("=" * 60)
    
    try:
        database = get_cosmos_client()
        
        # 1. Ensure containers exist
        print("\n📦 Ensuring containers exist...")
        containers_result = ensure_containers(database)
        print(f"   Created: {containers_result['created']}")
        print(f"   Existed: {containers_result['existed']}")
        
        # 2. Migrate documents
        print("\n📄 Migrating documents...")
        docs_result = migrate_documents(database)
        print(f"   Total: {docs_result['total']}")
        print(f"   Updated: {docs_result['updated']}")
        print(f"   Skipped: {docs_result['skipped']}")
        print(f"   Errors: {docs_result['errors']}")
        
        # 3. Migrate users
        print("\n👤 Migrating users...")
        users_result = migrate_users(database)
        print(f"   Total: {users_result['total']}")
        print(f"   Updated: {users_result['updated']}")
        print(f"   Skipped: {users_result['skipped']}")
        print(f"   Errors: {users_result['errors']}")
        
        # 4. Show index recommendations
        print("\n📊 Index recommendations:")
        create_indexes(database)
        
        print("\n" + "=" * 60)
        print("✅ MIGRATION COMPLETE")
        print("=" * 60)
        
        return {
            'success': True,
            'containers': containers_result,
            'documents': docs_result,
            'users': users_result
        }
        
    except Exception as e:
        print(f"\n❌ MIGRATION FAILED: {e}")
        logger.exception(e)
        return {
            'success': False,
            'error': str(e)
        }


# Schema documentation
SCHEMA_SUMMARY = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                         SCHEMA CHANGES SUMMARY                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  DOCUMENTS CONTAINER - NEW FIELDS                                            ║
║  ════════════════════════════════                                            ║
║                                                                              ║
║  Team & Assignment:                                                          ║
║  • team_id: string           - ID of assigned team                           ║
║  • team_name: string         - Name of assigned team                         ║
║  • assigned_to: string       - Email of current assignee                     ║
║  • assigned_to_name: string  - Name of assignee                              ║
║  • assigned_by: string       - Who assigned it                               ║
║  • assigned_at: datetime     - When assigned                                 ║
║  • assignment_status: string - unassigned|pending|in_progress|completed      ║
║  • assignment_priority: str  - urgent|high|medium|low                        ║
║  • assignment_deadline: dt   - SLA deadline                                  ║
║  • assignment_sla_hours: num - Hours allowed                                 ║
║  • assignment_notes: array   - Notes/comments on assignment                  ║
║  • assignment_reason: string - How assignee was selected                     ║
║  • ticket_id: string         - IT-style ticket (TKT-XXXXXXXX)                ║
║  • handoff_history: array    - History of assignment transfers               ║
║                                                                              ║
║  Collaboration:                                                              ║
║  • watchers: string[]        - Emails of users watching doc                  ║
║  • discussions: array        - Threaded comments with @mentions              ║
║  • last_activity_at: dt      - Last activity timestamp                       ║
║                                                                              ║
║  ────────────────────────────────────────────────────────────────────────    ║
║                                                                              ║
║  TEAMS (stored in documents container with type='team')                      ║
║  ══════════════════════════════════════════════════════                      ║
║  • id: string                - team_XXXXXXXXXXXX                             ║
║  • type: 'team'              - Distinguishes from documents                  ║
║  • organization_id: string   - Owner organization                            ║
║  • name: string              - Team name                                     ║
║  • description: string       - Team description                              ║
║  • assignment_strategy: str  - round_robin|least_loaded|risk_based|manual    ║
║  • jurisdictions: string[]   - Limit to specific jurisdictions               ║
║  • max_concurrent_per_member - Workload cap                                  ║
║  • default_sla_hours: number - Default deadline                              ║
║  • escalation_chain: array   - Escalation path                               ║
║  • members: TeamMember[]     - Team members with roles                       ║
║  • settings: object          - Team settings                                 ║
║  • stats: object             - Team statistics                               ║
║                                                                              ║
║  TeamMember:                                                                 ║
║  • email, name, role (team_lead|senior_reviewer|reviewer|junior|observer)    ║
║  • added_at, added_by, role_updated_at, role_updated_by                      ║
║                                                                              ║
║  ────────────────────────────────────────────────────────────────────────    ║
║                                                                              ║
║  USERS CONTAINER - NEW FIELDS                                                ║
║  ════════════════════════════                                                ║
║  • department: string        - User's department                             ║
║  • job_title: string         - User's job title                              ║
║  • notification_settings:    - Email/push preferences                        ║
║    - email_on_assignment     - Notify when assigned                          ║
║    - email_on_mention        - Notify when @mentioned                        ║
║    - email_on_approval       - Notify when doc approved/rejected             ║
║    - email_digest            - none|daily|weekly                             ║
║                                                                              ║
║  ────────────────────────────────────────────────────────────────────────    ║
║                                                                              ║
║  AUDIT_LOGS CONTAINER - NEW TYPES                                            ║
║  ════════════════════════════════                                            ║
║  • type: 'notification'      - User notifications                            ║
║  • type: 'activity'          - Activity feed entries                         ║
║  • type: 'decision_trail'    - Approval/rejection decisions                  ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(SCHEMA_SUMMARY)
    
    response = input("\nRun migration? (yes/no): ")
    if response.lower() == 'yes':
        migrate_all()
    else:
        print("Migration cancelled.")




def migrate_organizations_for_saas(database) -> dict:
    """
    Backfill SaaS billing fields onto all existing organization documents.

    Existing enterprise orgs get:
        auth_type = 'entra_enterprise'
        scans_per_month = -1  (unlimited)
        max_users = -1        (unlimited)
        subscription_status = 'active'
        signup_completed = True
        + all other new fields with safe defaults

    Safe to run multiple times — skips documents that already have the fields.

    Usage:
        database = get_cosmos_client()
        result = migrate_organizations_for_saas(database)
        print(result)
    """
    container = database.get_container_client('organizations')

    orgs = list(container.query_items(
        query="SELECT * FROM c WHERE c.type = 'organization'",
        enable_cross_partition_query=True,
    ))

    updated = 0
    skipped = 0
    errors = 0

    # Fields to add with safe defaults for existing enterprise orgs
    new_fields = {
        'auth_type':              'entra_enterprise',
        'owner_oid':              None,
        'stripe_customer_id':     None,
        'stripe_subscription_id': None,
        'subscription_status':    'active',
        'signup_completed':       True,
        'scans_this_month':       0,
        'scans_per_month':        -1,       # enterprise = unlimited
        'max_users':              -1,       # enterprise = unlimited
        'payment_failed_at':      None,
        'payment_failure_count':  0,
        'activated_at':           None,
        'usage_reset_at':         None,
    }

    for org in orgs:
        try:
            needs_update = any(field not in org for field in new_fields)

            if not needs_update:
                skipped += 1
                continue

            for field, default in new_fields.items():
                if field not in org:
                    org[field] = default

            # Ensure updated_at is set
            from datetime import datetime
            org['updated_at'] = datetime.utcnow().isoformat() + 'Z'

            container.upsert_item(org)
            updated += 1
            logger.info(f"Migrated org: {org.get('id')} ({org.get('name')})")

        except Exception as e:
            errors += 1
            logger.error(f"Failed to migrate org {org.get('id')}: {e}")

    result = {
        'total': len(orgs),
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
    }
    logger.info(f"SaaS migration complete: {result}")
    return result


def migrate_users_for_saas(database) -> dict:
    """
    Add auth_type and invite fields to existing user documents.
    Safe to run multiple times.
    """
    container = database.get_container_client('users')

    users = list(container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user'",
        enable_cross_partition_query=True,
    ))

    updated = 0
    skipped = 0
    errors = 0

    new_fields = {
        'auth_type':   'entra_enterprise',
        'invited_by':  None,
        'invited_at':  None,
    }

    for user in users:
        try:
            needs_update = any(field not in user for field in new_fields)

            if not needs_update:
                skipped += 1
                continue

            for field, default in new_fields.items():
                if field not in user:
                    user[field] = default

            container.upsert_item(user)
            updated += 1

        except Exception as e:
            errors += 1
            logger.error(f"Failed to migrate user {user.get('email')}: {e}")

    result = {
        'total': len(users),
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
    }
    logger.info(f"User SaaS migration complete: {result}")
    return result


# =========================================================================
# Add these two calls to your existing migrate_all() function:
#
#     # 4. SaaS billing fields
#     print("\n💳 Migrating organizations for SaaS billing...")
#     saas_orgs_result = migrate_organizations_for_saas(database)
#     print(f"   Updated: {saas_orgs_result['updated']}")
#     print(f"   Skipped: {saas_orgs_result['skipped']}")
#
#     # 5. User auth_type fields
#     print("\n👤 Migrating users for SaaS...")
#     saas_users_result = migrate_users_for_saas(database)
#     print(f"   Updated: {saas_users_result['updated']}")
#     print(f"   Skipped: {saas_users_result['skipped']}")
# =========================================================================
