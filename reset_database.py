# #!/usr/bin/env python3
# """
# Reset Cosmos DB - Clear all data for fresh start
# Run: python reset_cosmos.py --confirm
# """

# import os
# import sys
# from dotenv import load_dotenv

# load_dotenv()

# def clear_container(container, name: str) -> int:
#     """Clear all items from a container"""
#     deleted = 0
#     try:
#         # Query all items
#         items = list(container.query_items(
#             query="SELECT c.id, c._partitionKey FROM c",
#             enable_cross_partition_query=True
#         ))
        
#         print(f"  Found {len(items)} items in {name}")
        
#         for item in items:
#             try:
#                 # Try to get partition key from item
#                 pk = item.get('_partitionKey') or item.get('partition_key') or item.get('jurisdiction') or item.get('organization_id') or item.get('id')
#                 container.delete_item(item=item['id'], partition_key=pk)
#                 deleted += 1
#             except Exception as e:
#                 # Try without partition key
#                 try:
#                     container.delete_item(item=item['id'], partition_key=item['id'])
#                     deleted += 1
#                 except:
#                     print(f"    ⚠️ Could not delete {item['id']}: {e}")
        
#         print(f"  ✅ Deleted {deleted} items from {name}")
        
#     except Exception as e:
#         print(f"  ❌ Error clearing {name}: {e}")
    
#     return deleted


# def main():
#     if '--confirm' not in sys.argv:
#         print("⚠️  This will DELETE ALL DATA from Cosmos DB!")
#         print("    Run with --confirm to proceed")
#         print("    Example: python reset_cosmos.py --confirm")
#         sys.exit(1)
    
#     print("=" * 60)
#     print("🗑️  COSMOS DB RESET")
#     print("=" * 60)
    
#     from azure.cosmos import CosmosClient
    
#     endpoint = os.getenv('COSMOS_ENDPOINT')
#     key = os.getenv('COSMOS_KEY')
#     db_name = os.getenv('COSMOS_DATABASE', 'compliance')
    
#     if not endpoint or not key:
#         print("❌ Missing COSMOS_ENDPOINT or COSMOS_KEY")
#         sys.exit(1)
    
#     client = CosmosClient(endpoint, key)
#     database = client.get_database_client(db_name)
    
#     # Containers to clear
#     containers_to_clear = [
#         "rules",           # Scraped regulations
#         "documents",       # Uploaded documents  
#         "audit_logs",      # Audit trail
#         "ai_conversations",# AI chat logs
#         "questionnaires",  # Q&A data
#     ]
    
#     # Optional - uncomment to also clear users/orgs
#     # containers_to_clear.extend(["users", "organizations"])
    
#     total_deleted = 0
    
#     for container_name in containers_to_clear:
#         print(f"\n📦 Clearing: {container_name}")
#         try:
#             container = database.get_container_client(container_name)
#             deleted = clear_container(container, container_name)
#             total_deleted += deleted
#         except Exception as e:
#             print(f"  ⚠️ Container {container_name} not found or error: {e}")
    
#     print("\n" + "=" * 60)
#     print(f"✅ RESET COMPLETE - Deleted {total_deleted} total items")
#     print("=" * 60)


# if __name__ == '__main__':
#     main()

#!/usr/bin/env python3
"""
Reset AI Search Index
Run: python reset_ai_search.py --confirm
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

def main():
    if '--confirm' not in sys.argv:
        print("⚠️  This will DELETE ALL DATA from AI Search index!")
        print("    Run with --confirm to proceed")
        sys.exit(1)
    
    print("=" * 60)
    print("🗑️  AI SEARCH INDEX RESET")
    print("=" * 60)
    
    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        print("❌ azure-search-documents not installed")
        print("   Run: pip install azure-search-documents")
        sys.exit(1)
    
    endpoint = os.getenv('AZURE_SEARCH_ENDPOINT')
    key = os.getenv('AZURE_SEARCH_KEY')
    index_name = os.getenv('AZURE_SEARCH_INDEX', 'regulatory-knowledge')
    
    if not endpoint or not key:
        print("❌ Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_KEY")
        sys.exit(1)
    
    print(f"📦 Index: {index_name}")
    print(f"🔗 Endpoint: {endpoint[:50]}...")
    
    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(key)
    )
    
    # Get all document IDs
    print("\n🔍 Finding documents...")
    
    try:
        results = list(client.search(search_text="*", top=1000, select=["id"]))
        print(f"   Found {len(results)} documents")
        
        if not results:
            print("✅ Index already empty")
            return
        
        # Delete in batches
        batch_size = 100
        deleted = 0
        
        for i in range(0, len(results), batch_size):
            batch = results[i:i + batch_size]
            docs_to_delete = [{"id": doc["id"]} for doc in batch]
            
            try:
                delete_results = client.delete_documents(docs_to_delete)
                for r in delete_results:
                    if r.succeeded:
                        deleted += 1
                print(f"   Deleted batch {i // batch_size + 1}: {deleted} total")
            except Exception as e:
                print(f"   ⚠️ Batch delete error: {e}")
        
        print(f"\n✅ Deleted {deleted} documents from AI Search")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()